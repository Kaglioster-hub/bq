# BettyQuotes API Monolith – completo e compatto
import os, time, json, hashlib, requests, io, gzip, csv, glob
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl, quote

# ------- ENV -------
API_KEY   = os.getenv("ODDS_API_KEY","")
REGION    = os.getenv("BQ_REGION","eu")
SPORTS    = [s.strip() for s in os.getenv("BQ_SPORTS","soccer_epl").split(",") if s.strip()]
VAL_THR   = float(os.getenv("BQ_VALUE_THRESHOLD","0.07"))
ARB_GAP   = float(os.getenv("BQ_SUREBET_MARGIN","0.02"))
TTL       = int(os.getenv("BQ_TTL_SECONDS","300"))
TTL_MIN   = int(os.getenv("BQ_TTL_MIN","120"))
TTL_MAX   = int(os.getenv("BQ_TTL_MAX","600"))
SOON_MIN  = int(os.getenv("BQ_TTL_SOON_MINUTES","90"))
DO_GZIP   = os.getenv("BQ_GZIP","1")=="1"
SALT      = os.getenv("BQ_HASH_SALT","salt")
FORCE_TOKEN=os.getenv("BQ_FORCE_TOKEN","changeme")
ALLOW     = [d.strip() for d in os.getenv("BQ_TRACK_DOMAIN_ALLOWLIST","").split(",") if d.strip()]
UTM       = os.getenv("BQ_UTM","")
TRACK_PPS = int(os.getenv("BQ_TRACK_PPS","2"))
PV_PPS    = int(os.getenv("BQ_PV_PPS","5"))
ALERT_WEBHOOK=os.getenv("BQ_ALERT_WEBHOOK","")
ALERT_THRESHOLD=float(os.getenv("BQ_ALERT_THRESHOLD","3.0"))

# ------- UTILS -------
def _implied(p): return 1/p if p>0 else 0
def _sha(b): return hashlib.sha256(b).hexdigest()
def _load(path,default): 
    try: 
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except: return default
def _save(path,data):
    with open(path,"w",encoding="utf-8") as f: json.dump(data,f,separators=(",",":"))
def _f_cache(s): return f"/tmp/odds_{s}.json"
def _f_rl(name): return f"/tmp/rl_{name}.json"
def _ip(req):
    xf=(req.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xf or (req.headers.get("x-real-ip") or "0.0.0.0")
def _hip(ip): return hashlib.sha256((SALT+ip).encode()).hexdigest()[:16]
def _gzip_if(req,res,raw:bytes):
    if DO_GZIP and "gzip" in (req.headers.get("accept-encoding","").lower()):
        buf=io.BytesIO(); 
        with gzip.GzipFile(fileobj=buf,mode="wb",compresslevel=6) as g: g.write(raw)
        res.headers["Content-Encoding"]="gzip"; res.body=buf.getvalue()
    else: res.body=raw

def _throttle(key,max_per_sec:int):
    now=int(time.time()); db=_load(_f_rl(key),{})
    hits=db.get(str(now),0)
    if hits>=max_per_sec: return False
    db[str(now)]=hits+1
    for k in list(db.keys()):
        if int(k)<now-2: db.pop(k,None)
    _save(_f_rl(key),db); return True

# ------- ODDS -------
BASE="https://api.the-odds-api.com/v4/sports/{sport}/odds"
def _fetch_api(sport):
    r=requests.get(BASE.format(sport=sport),params={
        "apiKey":API_KEY,"regions":REGION,"markets":"h2h","oddsFormat":"decimal"
    }, timeout=(4,8))
    r.raise_for_status(); return r.json()

def _enrich(matches):
    out=[]
    for m in matches:
        best, values = {}, []
        for b in m.get("bookmakers",[]):
            for mk in b.get("markets",[]):
                outs=mk.get("outcomes",[])
                if not outs: continue
                avg=sum(o["price"] for o in outs)/len(outs)
                for o in outs:
                    if o["price"]>avg*(1+VAL_THR):
                        values.append({"team":o["name"],"price":o["price"],"book":b["title"],"avg":round(avg,2)})
                    if o["name"] not in best or o["price"]>best[o["name"]]:
                        best[o["name"]]=o["price"]
        sure=None
        if best:
            ps=sum(_implied(p) for p in best.values())
            if ps < (1-ARB_GAP):
                sure={"margin":round((1-ps)*100,2),"best":best}
        out.append({
            "id":m.get("id"),"home":m.get("home_team"),"away":m.get("away_team"),
            "time":m.get("commence_time"),"values":values,"surebet":sure
        })
    return out

def _adaptive_ttl(items):
    try:
        now=datetime.now(timezone.utc).timestamp()
        soon=False
        for it in items:
            t=it.get("time"); 
            if not t: continue
            ts=datetime.fromisoformat(t.replace("Z","+00:00")).timestamp()
            if 0 <= (ts-now) <= SOON_MIN*60: soon=True; break
        return TTL_MIN if soon else TTL_MAX
    except: return TTL

def handle_odds(req,res):
    if req.method not in ("GET","HEAD"): res.status_code=405; res.body=b""; return res
    sport=(req.query.get("sport") or SPORTS[0]).strip()
    if sport not in SPORTS: sport=SPORTS[0]
    cf=_f_cache(sport)
    if os.path.exists(cf) and (time.time()-os.path.getmtime(cf) < TTL):
        data=_load(cf,{"list":[]}); fresh=False
    else:
        try:
            data={"list":_enrich(_fetch_api(sport))}
            _save(cf,data); fresh=True
        except:
            data=_load(cf,{"list":[]}); fresh=False
    pl={"updated_at":int(time.time()),"sports":{sport:data["list"]},"stale":not fresh}
    raw=json.dumps(pl,separators=(",",":")).encode("utf-8")
    res.status_code=200; res.headers["Content-Type"]="application/json; charset=utf-8"
    res.headers["Cache-Control"]=f"public, s-maxage={TTL}, stale-while-revalidate={TTL*6}"
    res.headers["X-BQ-TTL-ADAPT"]=str(_adaptive_ttl(data["list"]))
    res.headers["ETag"]=_sha(raw)
    _gzip_if(req,res,raw)
    # alerts
    if ALERT_WEBHOOK:
        for m in data["list"]:
            if m.get("surebet") and m["surebet"]["margin"]>=ALERT_THRESHOLD:
                try: requests.post(ALERT_WEBHOOK,json={"sport":sport,**m},timeout=2)
                except: pass
    return res

# ------- TRACK -------
def _load_refmap():
    try: 
        with open("public/referrals.json","r",encoding="utf-8") as f: return json.load(f)
    except: return {"default":""}

def _resolve_ref(sport, book):
    ref=_load_refmap()
    if sport in ref and isinstance(ref[sport],dict) and book in ref[sport]: return ref[sport][book]
    if sport in ref and isinstance(ref[sport],str): return ref[sport]
    return ref.get("default","")

def handle_track(req,res):
    ip=_hip(_ip(req))
    if not _throttle(f"track_{ip}", TRACK_PPS): res.status_code=429; res.body=b""; return res
    q=req.query
    to=(q.get("to") or "").strip()
    sport=(q.get("sport") or "").strip(); book=(q.get("book") or "").strip()
    if not to: to=_resolve_ref(sport,book)
    if not to: res.status_code=302; res.headers["Location"]="/"; return res
    host=urlparse(to).hostname or ""
    if not any(host.endswith(d) for d in ALLOW): res.status_code=302; res.headers["Location"]="/"; return res
    if UTM:
        u=urlparse(to); q=dict(parse_qsl(u.query))
        for kv in UTM.split("&"):
            if "=" in kv: k,v=kv.split("=",1); q.setdefault(k,v)
        to=urlunparse((u.scheme,u.netloc,u.path,u.params,urlencode(q),u.fragment))
    try: 
        with open("/tmp/referrals.log","a",encoding="utf-8") as f: f.write(f"{int(time.time())}|{ip}|{sport}|{book}|{to}\n")
    except: pass
    res.status_code=302; res.headers["Location"]=quote(to,safe=":/?=&"); return res

# ------- PV -------
def handle_pv(req,res):
    ip=_hip(_ip(req))
    if not _throttle(f"pv_{ip}", PV_PPS): res.status_code=204; res.body=b""; return res
    path=req.query.get("p","/")
    try:
        with open("/tmp/pv.log","a",encoding="utf-8") as f: f.write(f"{int(time.time())}|{ip}|{path}\n")
    except: pass
    res.status_code=204; res.body=b""; return res

# ------- EXPORT -------
def handle_export(req,res):
    out={"ts":int(time.time()),"sports":{}}
    for p in glob.glob("/tmp/odds_*.json"):
        s=os.path.basename(p).replace("odds_","").replace(".json","")
        js=_load(p,{"list":[]}); out["sports"][s]=js["list"]
    raw=json.dumps(out,separators=(",",":")).encode("utf-8")
    res.status_code=200; res.headers["Content-Type"]="application/json; charset=utf-8"; _gzip_if(req,res,raw); return res

def handle_export_csv(req,res):
    buf=io.StringIO(); w=csv.writer(buf)
    w.writerow(["sport","match","time","value_count","surebet_margin"])
    for p in glob.glob("/tmp/odds_*.json"):
        s=os.path.basename(p).replace("odds_","").replace(".json","")
        js=_load(p,{"list":[]})
        for m in js["list"]:
            w.writerow([s, f"{m.get('home')} vs {m.get('away')}", m.get('time'), len(m.get('values') or []), (m.get('surebet') or {}).get('margin',"")])
    raw=buf.getvalue().encode("utf-8")
    res.status_code=200; res.headers["Content-Type"]="text/csv; charset=utf-8"; res.headers["Content-Disposition"]="attachment; filename=bq_export.csv"; res.body=raw; return res

def handle_export_xls(req,res):
    # SpreadsheetML (XML) – niente dipendenze
    rows=[]
    rows.append("<Row><Cell><Data ss:Type='String'>sport</Data></Cell><Cell><Data ss:Type='String'>match</Data></Cell><Cell><Data ss:Type='String'>time</Data></Cell><Cell><Data ss:Type='Number'>value_count</Data></Cell><Cell><Data ss:Type='Number'>surebet_margin</Data></Cell></Row>")
    for p in glob.glob("/tmp/odds_*.json"):
        s=os.path.basename(p).replace("odds_","").replace(".json","")
        js=_load(p,{"list":[]})
        for m in js["list"]:
            vc=len(m.get("values") or []); mg=(m.get("surebet") or {}).get("margin","")
            match=f"{m.get('home')} vs {m.get('away')}".replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            tm=(m.get("time") or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            rows.append(f"<Row><Cell><Data ss:Type='String'>{s}</Data></Cell><Cell><Data ss:Type='String'>{match}</Data></Cell><Cell><Data ss:Type='String'>{tm}</Data></Cell><Cell><Data ss:Type='Number'>{vc}</Data></Cell><Cell><Data ss:Type='Number'>{mg if mg!='' else 0}</Data></Cell></Row>")
    xml=("""<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="BettyQuotes"><Table>"""+
         "".join(rows)+
         "</Table></Worksheet></Workbook>")
    data=xml.encode("utf-8")
    res.status_code=200; res.headers["Content-Type"]="application/vnd.ms-excel"; res.headers["Content-Disposition"]="attachment; filename=bq_export.xls"; res.body=data; return res

# ------- ADMIN / HEALTH / OPS -------
def handle_admin(req,res):
    if (req.query.get("token") or "")!=FORCE_TOKEN: res.status_code=403; res.body=b""; return res
    pv=sum(1 for _ in open("/tmp/pv.log","r",encoding="utf-8")) if os.path.exists("/tmp/pv.log") else 0
    rf=sum(1 for _ in open("/tmp/referrals.log","r",encoding="utf-8")) if os.path.exists("/tmp/referrals.log") else 0
    res.status_code=200; res.headers["Content-Type"]="application/json; charset=utf-8"
    res.body=json.dumps({"pv":pv,"referrals":rf,"sports":SPORTS}).encode("utf-8"); return res

def handle_health(req,res):
    st={}
    for p in glob.glob("/tmp/odds_*.json"):
        s=os.path.basename(p).replace("odds_","").replace(".json","")
        age=int(time.time()-os.path.getmtime(p)); js=_load(p,{"list":[]})
        st[s]={"events":len(js["list"]),"age_sec":age}
    res.status_code=200; res.headers["Content-Type"]="application/json; charset=utf-8"; res.body=json.dumps({"ts":int(time.time()),"sports":st}).encode("utf-8"); return res

def handle_warmup(req,res):
    if (req.query.get("token") or "")!=FORCE_TOKEN: res.status_code=403; res.body=b""; return res
    ok=True
    for s in SPORTS:
        try: _save(_f_cache(s), {"list":_enrich(_fetch_api(s))})
        except: ok=False
    res.status_code=200 if ok else 207; res.headers["Content-Type"]="application/json"; res.body=b'{"ok":%s}'%(b"true" if ok else b"false"); return res

def handle_flush(req,res):
    if (req.query.get("token") or "")!=FORCE_TOKEN: res.status_code=403; res.body=b""; return res
    for p in glob.glob("/tmp/odds_*.json")+["/tmp/pv.log","/tmp/referrals.log"]:
        try: os.remove(p)
        except: pass
    res.status_code=200; res.headers["Content-Type"]="application/json"; res.body=b'{"flushed":true}'; return res

# ------- HONEYPOT / DEMO -------
def handle_honeypot(req,res):
    ip=_ip(req)
    try: 
        with open("/tmp/honeypot.log","a",encoding="utf-8") as f: f.write(f"{int(time.time())}|{ip}\n")
    except: pass
    res.status_code=403; res.body=b""; return res

def handle_reseed(req,res):
    demo=[{
        "id":"demo-1","home":"Demo United","away":"Sample City",
        "time":datetime.now(timezone.utc).replace(microsecond=0).isoformat()+"Z",
        "values":[{"team":"Demo","price":2.1,"book":"DemoBook","avg":2.0}],
        "surebet":{"margin":2.5,"best":{"Demo":2.1,"Sample":3.4,"Draw":3.2}}
    }]
    _save("public/sample_odds.json",{"list":demo})
    res.status_code=200; res.headers["Content-Type"]="application/json"; res.body=b'{"demo":true}'; return res

# ------- ROUTER -------
def handler(req,res):
    path=req.url.split("?")[0]
    if path.endswith("/odds"):         return handle_odds(req,res)
    if path.endswith("/track"):        return handle_track(req,res)
    if path.endswith("/pv"):           return handle_pv(req,res)
    if path.endswith("/export"):       return handle_export(req,res)
    if path.endswith("/export_csv"):   return handle_export_csv(req,res)
    if path.endswith("/export_xls"):   return handle_export_xls(req,res)
    if path.endswith("/admin"):        return handle_admin(req,res)
    if path.endswith("/health"):       return handle_health(req,res)
    if path.endswith("/warmup"):       return handle_warmup(req,res)
    if path.endswith("/flush"):        return handle_flush(req,res)
    if path.endswith("/honeypot"):     return handle_honeypot(req,res)
    if path.endswith("/reseed_demo"):  return handle_reseed(req,res)
    res.status_code=404; res.body=b"{}"; return res
