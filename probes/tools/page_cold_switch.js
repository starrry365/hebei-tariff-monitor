/* 切网「冷载入」不塌陷：把一个网从内存里摘掉再点它，验证载入态不会让整页塌陷。
 *
 * 用法（run_checks.py 自动调用；也可手动）：
 *   python probes/tools/cdp_launch.py
 *   python probes/tools/ct_browser/cdp_desktop_step.py nav "file:///.../docs/index.html"
 *   python probes/tools/ct_browser/cdp_desktop_step.py eval probes/tools/page_cold_switch.js
 *
 * 为什么单独一个探针：walk / e2e 都会先 loadNet 预热（不预热它们自己会假红），
 * 于是**永远走热路径**，测不到「数据还没到」的那一段。而真实用户第一次点
 * 某个网时走的就是冷路径 —— 2026-10-17 实测（修复前）：
 *   #tb 清空 ⇒ 整页高度 5846 → 958、滚动位置 2200 被夹到 170，
 *   停 335ms 再长回 9558。用户描述为「切运营商时闪一下」。
 * 修复：applyWhenReady 锁住 .tblwrap 的 min-height 再换成占位行。
 * 本探针就是这个修复的护栏 —— 删掉锁高，这里立刻红。
 *
 * 判据（修复后实测基线）：
 *   · 载入期间 docH ≥ before 的 92%（塌陷会给一个很小的值，如 16%）
 *   · 载入期间 scrollY 变化 ≤ 40px（滚动位置被夹走是最刺眼的那一下）
 *   · 载入期间 .tblwrap 高度 ≥ before 的 95%
 *   · 数据最终到位（rows > 0）且锁高被解开
 * 返回 JSON 字符串。
 */
(async function () {
  var $s = function (s) { return document.querySelector(s); };
  var tb = $s("#tb"), nav = $s("#netnav"), stat = $s("#stat");
  var FR = [];
  function now() { return Math.round(performance.now() * 10) / 10; }

  function snap(t) {
    var w = tb.closest(".tblwrap");
    return {
      t: t,
      net: document.documentElement.dataset.net,
      tbH: Math.round(tb.getBoundingClientRect().height),
      rows: tb.querySelectorAll("tr.row").length,
      loadRow: tb.querySelectorAll("tr.tload").length,
      wrapH: w ? Math.round(w.offsetHeight) : -1,
      wrapMin: w ? w.style.minHeight : "",
      docH: Math.round(document.documentElement.scrollHeight),
      y: Math.round(window.scrollY)
    };
  }

  /* 摘掉电信的内存数据（脚本标签仍在，重新 loadNet 会真的再执行一遍）
     —— 等价于「预热还没轮到它」的那一刻。挑电信是因为它行数最少，
     加载最快，探针耗时可控。 */
  var TARGET = "telecom";
  try {
    var N = NETS[TARGET];
    if (!N) return JSON.stringify({ ok: false, err: "no net " + TARGET });
    delete N.rows; delete N.cols;
    delete window.__NET_DATA__[TARGET];
  } catch (e) { return JSON.stringify({ ok: false, err: String(e) }); }

  /* 起点归位：本探针可能接在 walk 之后跑（那时视图停在「总览」），
     列表里没有行就量不到塌陷。用页面自己的 switchNet/setView 复位 ——
     别在探针里另写一套状态机（两套必然漂移）。 */
  try {
    if (typeof setView === "function") setView("list");
    if (typeof switchNet === "function" && typeof NET_LS !== "undefined") switchNet(NET_LS[0]);
  } catch (e) { /* 复位失败不拦截，下面的比值会把异常暴露出来 */ }

  /* 滚到列表中部：顶部切换时塌陷不明显，用户真正会踩到的是「读到一半切网」 */
  window.scrollTo(0, Math.min(2200, Math.max(0, document.documentElement.scrollHeight - 2000)));
  await new Promise(function (r) { requestAnimationFrame(function () { r(); }); });
  FR.push(snap("before"));

  var btns = nav.querySelectorAll(".netbtn"), idx = -1;
  for (var i = 0; i < btns.length; i++) {
    if (new RegExp(NETS[TARGET].nm || TARGET).test(btns[i].textContent)) idx = i;
  }
  (btns[idx >= 0 ? idx : 0]).click();

  await new Promise(function (res) {
    var t0 = performance.now();
    (function loop() {
      FR.push(snap("f" + Math.round(performance.now() - t0)));
      if (performance.now() - t0 < 1100) requestAnimationFrame(loop);
      else res();
    })();
  });

  var b = FR[0];
  var loadF = FR.filter(function (f) { return f.loadRow > 0; });
  var win = loadF.length ? loadF : FR.slice(1);      // 没测到占位行也算（可能快到来不及采）
  var docRatio = b.docH ? Math.min.apply(null, win.map(function (f) { return f.docH; })) / b.docH : 0;
  var wrapRatio = b.wrapH ? Math.min.apply(null, win.map(function (f) { return f.wrapH; })) / b.wrapH : 0;
  var dy = Math.max.apply(null, FR.map(function (f) { return Math.abs(f.y - b.y); }));
  var last = FR[FR.length - 1];
  var ok = docRatio >= 0.92 && wrapRatio >= 0.95 && dy <= 40
        && last.rows > 0 && last.net === TARGET && last.wrapMin === "";

  return JSON.stringify({
    ok: ok,
    docRatio: Math.round(docRatio * 1000) / 1000,
    wrapRatio: Math.round(wrapRatio * 1000) / 1000,
    dy: dy,
    loadFrames: loadF.length,
    before: b,
    during: loadF[0] || null,
    after: { net: last.net, rows: last.rows, docH: last.docH, wrapMin: last.wrapMin }
  }, null, 1);
})()
