/* 河北四网资费查询页 —— Service Worker（PWA 离线壳）
 *
 * 策略（一句话：**页面求新，壳子求快**）：
 *   · 导航请求（index.html）→ network-first。
 *     页面的数据是每天巡检重建的；宁可等网络，也不给用户看昨天的价目表。
 *     网络失败时才退回缓存（地铁里 / 断网时还能看上次那份，比白屏强）。
 *   · 其他同源静态资源（图标 / manifest）→ cache-first。
 *     它们几乎不变，走缓存省一次往返。
 *
 * 🔴 故意**不**在 install 里预缓存 index.html：这一页内嵌了四网全量数据，
 *    未压缩 11 MB 左右。预缓存会让「安装」阶段卡十几秒甚至失败，
 *    而用户第一次打开本来就会把页面拉下来 —— 那时顺手写进缓存即可。
 *
 * 🔴 改了这个文件里任何**缓存名单**，必须同时 bump CACHE 版本号。
 *    否则老用户永远停在旧壳子上（activate 里只清「版本号不同」的缓存）。
 */
const CACHE = "hbtm-shell-v1";

/* 只预缓存「壳」：体积小、几乎不变 */
const SHELL = [
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/apple-touch-icon.png",
  "./icons/favicon-32.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.all(SHELL.map((u) =>
        c.add(u).catch(() => null)   // 单个资源 404 不该让整个 install 失败
      )))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE)
                                  .map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isNavigation(req, url) {
  return req.mode === "navigate"
    || url.pathname.endsWith("/")
    || url.pathname.endsWith("index.html");
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch (_) {
    return;
  }
  if (url.origin !== self.location.origin) return;   // 跨域一概不管

  if (isNavigation(req, url)) {
    e.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("./index.html", copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match("./index.html").then((r) => r || caches.match(req)))
    );
    return;
  }

  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    }))
  );
});
