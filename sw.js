// hobby baseball：サイト一式をスマホに保存しておき、電波が弱くても開けるようにする
// ・サイト本体（index.html）：まず最新を取りに行く（2.5秒で返ってこなければ保存分）。古い画面・古いアイコンが一瞬出ないように
// ・画像（アイコン・演出の絵）：URLに中身の目印（?v=…）が付いている。目印ごとに保存するので、絵を変えたら必ず新しい絵になる
// ・データ（data/*.json）：まず最新を取りに行き、取れなければ保存しておいたものを使う
// ・中継プログラム（速報）・天気など、ほかのサイトへの通信には手を出さない
const CACHE = "hb-v11";
const SHELL = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  // 前の版で保存したもの（古いアイコン・古い画面）は全部消す
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

// 保存するときの名前：「?t=」などを外したURL（画像の ?v= は残す）
const keyOf = url => { const u = new URL(url); const v = u.searchParams.get("v"); u.search = ""; if (v && /\.(png|jpg)$/.test(u.pathname)) u.search = "?v=" + v; return u.toString(); };
const put = (req, res) => { if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(keyOf(req.url), cp)); } return res; };
const withTimeout = (p, ms) => new Promise((ok, ng) => { const t = setTimeout(() => ng(new Error("timeout")), ms); p.then(v => { clearTimeout(t); ok(v); }, e => { clearTimeout(t); ng(e); }); });

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // ほかのサイトへの通信はそのまま

  // データ：最新を優先（取れなければ保存分）
  if (url.pathname.includes("/data/") && url.pathname.endsWith(".json")) {
    e.respondWith(fetch(req).then(res => put(req, res)).catch(() => caches.match(keyOf(req.url)).then(r => r || Response.error())));
    return;
  }

  // サイト本体（画面を開くとき）：最新を優先。電波が弱くて2.5秒たっても返ってこなければ保存分
  if (req.mode === "navigate") {
    const key = keyOf(url.origin + url.pathname.replace(/\/$/, "/index.html"));
    const fresh = fetch(req).then(res => { if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(key, cp)); } return res; });
    e.respondWith(withTimeout(fresh, 2500).catch(() => caches.match(key).then(hit => hit || fresh)));
    return;
  }

  // 画像：目印（?v=）付きは、その目印の保存分があればそれ（中身は変わらない）。目印なしは最新を優先
  if (/\.(png|jpg)$/.test(url.pathname)) {
    if (url.searchParams.has("v")) e.respondWith(caches.match(keyOf(req.url)).then(hit => hit || fetch(req).then(res => put(req, res))));
    else e.respondWith(fetch(req).then(res => put(req, res)).catch(() => caches.match(keyOf(req.url)).then(r => r || Response.error())));
    return;
  }
  // manifest など：最新を優先
  if (/\.(json|webmanifest|svg|ico)$/.test(url.pathname)) {
    e.respondWith(fetch(req).then(res => put(req, res)).catch(() => caches.match(keyOf(req.url)).then(r => r || Response.error())));
  }
  // 「?t=」付きの index.html（新しい版があるかの確認）などは、そのまま通信する
});
