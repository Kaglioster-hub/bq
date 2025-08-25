const C="bq-v1";
self.addEventListener("install",e=>{
  e.waitUntil(caches.open(C).then(c=>c.addAll(["/","/app.js","/styles.css","/config.json","/i18n/it.json","/i18n/en.json"])));
});
self.addEventListener("fetch",e=>{
  const u=new URL(e.request.url);
  if(u.pathname.startsWith("/api/odds")){
    e.respondWith(caches.match(e.request).then(cached=>{
      const net=fetch(e.request).then(r=>{caches.open(C).then(c=>c.put(e.request,r.clone()));return r;}).catch(()=>cached||Response.error());
      return cached||net;
    }));
  }else{
    e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));
  }
});
