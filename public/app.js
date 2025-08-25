let cfg, T = {}, lang = localStorage.getItem("bq_lang") || "it";
const esc = s => String(s||"").replace(/[&<>"]/g, m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[m]));
const qs = s => document.querySelector(s);

async function j(p){ const r=await fetch(p); return r.json(); }
async function loadI18n(){ T[lang] = await j(`/i18n/${lang}.json`); }
function t(k){ return (T[lang] && T[lang][k]) || k; }
function applyI18n(){ document.querySelectorAll("[data-i18n]").forEach(el=>el.textContent=t(el.dataset.i18n)); }

function referral(sport, book){ return `/api/track?sport=${encodeURIComponent(sport)}&book=${encodeURIComponent(book||"")}`; }

function render(data, sport){
  const list = data.sports?.[sport] || [];
  const ts = data.updated_at ? new Date(data.updated_at*1000) : new Date();
  qs("#updated").textContent = `${t("updated")||"Aggiornato"}: ${ts.toLocaleString()}`;

  qs("#values").innerHTML = list.flatMap(m => (m.values||[]).slice(0,1).map(v=>({m,v}))).slice(0,12).map(({m,v}) => `
    <article class="card">
      <h3 class="font-semibold">${esc(m.home)} vs ${esc(m.away)}</h3>
      <p class="text-yellow-400">${esc(v.team)} @ <b>${v.price}</b> <span class="text-gray-400">(${t("avg")||"media"} ${v.avg})</span></p>
      <p class="text-xs text-gray-400">${t("book")||"Bookmaker"}: ${esc(v.book)}</p>
      <a class="btn" href="${referral(sport, v.book)}">${t("openBookmaker")||"Apri bookmaker"}</a>
    </article>`).join("");

  qs("#surebets").innerHTML = list.filter(m=>m.surebet).slice(0,6).map(m =>
    `<li>${esc(m.home)} vs ${esc(m.away)} → <span class="text-green-400">${t("margin")||"Margine"} ${m.surebet.margin}%</span></li>`
  ).join("");

  qs("#events").innerHTML = list.slice(0,30).map(m => `
    <article class="card">
      <h3 class="font-semibold">${esc(m.home)} vs ${esc(m.away)}</h3>
      <p class="text-xs text-gray-400">${esc(m.time)}</p>
      ${m.values?.length ? `<p class="text-yellow-400 mt-1">🔥 ${t("value")||"Value bet:"} ${esc(m.values[0].team)} @ <b>${m.values[0].price}</b></p>` : ""}
      ${m.surebet ? `<p class="text-green-400">${t("surebet")||"Surebet:"} ${m.surebet.margin}%</p>` : ""}
      <a class="btn" href="${referral(sport, (m.values?.[0]?.book||""))}">${t("openBookmaker")||"Apri bookmaker"}</a>
    </article>`).join("");
}

async function init(){
  cfg = await j("/config.json");
  await loadI18n(); applyI18n();

  const sel = qs("#sport");
  sel.innerHTML = cfg.sports.map(s=>`<option value="${s.key}">${s.label}</option>`).join("");
  sel.value = cfg.sports[0].key;

  async function refresh(){
    const s = sel.value;
    const r = await fetch(`/api/odds?sport=${encodeURIComponent(s)}`, { headers: { "Accept":"application/json" } });
    const data = await r.json();
    render(data, s);
  }
  sel.addEventListener("change", refresh);
  document.getElementById("lang-it").onclick = ()=>{ lang="it"; localStorage.setItem("bq_lang","it"); loadI18n().then(()=>{ applyI18n(); refresh(); }); };
  document.getElementById("lang-en").onclick = ()=>{ lang="en"; localStorage.setItem("bq_lang","en"); loadI18n().then(()=>{ applyI18n(); refresh(); }); };

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
  await refresh(); setInterval(refresh, cfg.ui.refreshMs||300000);
}
init();
