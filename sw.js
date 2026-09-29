// J SPORTS ペナントレース：サイト一式をスマホに保存しておき、ホーム画面から開いた瞬間に表示する（電波が弱くても開ける）
// ・サイト本体（index.html）：保存しておいたものをすぐに出し、裏で最新を取ってきて保存し直す（次に開いたときに新しくなる）
//   ただし「?v=」付き（新しい版への切り替え）で開いたときは、必ず最新を取りに行く
// ・データ（data/*.json）：まず最新を取りに行き、取れなければ保存しておいたものを使う
// ・中継プログラム（速報）・天気など、ほかのサイトへの通信には手を出さない
const CACHE = "jsp-v1";
const SHELL = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

// 保存するときの名前：「?t=」「?v=」などを外したURL
const keyOf = url => { const u = new URL(url); u.search = ""; return u.toString(); };

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // ほかのサイトへの通信はそのまま

  // データ：最新を優先（取れなければ保存分）
  if (url.pathname.includes("/data/") && url.pathname.endsWith(".json")) {
    e.respondWith(fetch(req).then(res => {
      if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(keyOf(req.url), cp)); }
      return res;
    }).catch(() => caches.match(keyOf(req.url)).then(r => r || Response.error())));
    return;
  }

  // サイト本体（画面を開くとき）
  if (req.mode === "navigate") {
    const fresh = fetch(req).then(res => {
      if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(keyOf(url.origin + url.pathname.replace(/\/$/, "/index.html")), cp)); }
      return res;
    });
    // 新しい版への切り替え（?v=）は最新を待つ。ふだんは保存分をすぐに出して、裏で保存し直す
    if (url.searchParams.has("v")) { e.respondWith(fresh.catch(() => caches.match(keyOf(url.origin + url.pathname.replace(/\/$/, "/index.html"))))); return; }
    e.respondWith(caches.match(keyOf(url.origin + url.pathname.replace(/\/$/, "/index.html"))).then(hit => {
      if (hit) { e.waitUntil(fresh.catch(() => {})); return hit; }
      return fresh.catch(() => caches.match("./index.html"));
    }));
    return;
  }

  // そのほか（manifest など）：保存分があればそれ、なければ取りに行って保存
  if (/\.(json|png|svg|ico|webmanifest)$/.test(url.pathname)) {
    e.respondWith(caches.match(keyOf(req.url)).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(keyOf(req.url), cp)); }
      return res;
    })));
  }
  // 「?t=」付きの index.html（新しい版があるかの確認）などは、そのまま通信する
});
