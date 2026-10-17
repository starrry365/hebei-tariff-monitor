/* 列筛选面板「能滚」且「滚了不自己关」：打开漏斗弹层，在清单里滚一屏，
 * 断言面板仍开着、清单真的滚动了；再滚页面确认外部滚动仍会收起（关闭逻辑没被废掉）。
 *
 * 用法（run_checks.py 自动调用；也可手动）：
 *   python probes/tools/cdp_launch.py
 *   python probes/tools/ct_browser/cdp_desktop_step.py nav "file:///.../docs/index.html"
 *   python probes/tools/ct_browser/cdp_desktop_step.py eval probes/tools/page_cf_scroll.js
 *
 * 为什么单独一个探针：2026-10-17 用户报「筛选的小面包（弹层）无法滑动，一滑
 * 自己就关掉了」。根因是 buildCfPop 里
 *     document.addEventListener("scroll", () => cfClose(), true)
 * 挂在捕获段。scroll 事件不冒泡，但捕获阶段仍从 document 自上而下经过，
 * 于是 .cfl 里滚一下也会收到 target=.cfl 的事件 ⇒ 立刻 cfClose()。
 * 修法：target 在面板内就放过；顺带给 .cfl 加 overscroll-behavior:contain
 * （触屏滚到底别把滚动链传到文档，否则又会变成「外部滚动」而收起）。
 *
 * 判据（修复后实测基线）：
 *   · 弹层打开成功，清单可滚（scrollHeight > clientHeight，说明有得滚）
 *   · 清单滚动后 panel 仍 open、scrollTop 仍 > 0
 *   · 页面滚动后 panel 关闭（关掉「索性永不关闭」这种假修复）
 * 返回 JSON 字符串。
 */
(async function () {
  var $s = function (s) { return document.querySelector(s); };
  var wait = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  function nextFrame() { return new Promise(function (r) { requestAnimationFrame(function () { r(); }); }); }

  /* 前置：当前网的行数据必须在内存里，否则面板是空的、量不出滚动 */
  var net = document.documentElement.dataset.net;
  try {
    if (typeof netReady === "function" && !netReady(net) && typeof loadNet === "function") await loadNet(net);
  } catch (e) { return JSON.stringify({ ok: false, err: "loadNet " + e }); }

  if (typeof setView === "function") setView("list");
  /* 回到页首再开面板：靠近底部时 cfOpen 会向上翻，位置不影响判据，但省得混淆 */
  window.scrollTo(0, 0);
  await nextFrame();

  /* 选一个取值最多的列（名称/目标客户这类长文本列最容易超过一屏）。
     #tb 是 tbody，表头在它的兄弟 thead 里，直接全局取 .cfx。
     ≤640px 卡片视图把 thead 隐藏，入口换成 #mcf 下拉 —— 走真正的窄屏入口，
     别在探针里点一个用户点不到的元素。 */
  var btns = [].slice.call(document.querySelectorAll(".cfx"));
  var mcf = $s("#mcf");
  var mcfVisible = !!(mcf && mcf.offsetWidth > 0);
  var want = "name";
  var pick = null;
  for (var i = 0; i < btns.length; i++) {
    if ((btns[i].dataset.cf || "") === want) { pick = btns[i]; break; }
  }
  pick = pick || btns[0];
  var key = (pick && pick.dataset.cf) || want;

  if (mcfVisible) {
    if (!mcf.querySelector('option[value="' + key + '"]')) key = "ty";
    mcf.value = key;
    mcf.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    if (!pick) return JSON.stringify({ ok: false, err: "没有找到表头漏斗 .cfx" });
    pick.click();
  }
  var via = mcfVisible ? "#mcf" : "thead .cfx";
  await nextFrame();
  var pop = $s("#cfpop");
  if (!pop) return JSON.stringify({ ok: false, err: "点击后没有 #cfpop" });

  var list = pop.querySelector(".cfl");
  var opened = pop.classList.contains("open");
  var scrollable = list.scrollHeight > list.clientHeight + 4;

  if (!opened) return JSON.stringify({ ok: false, err: "面板没打开", key: key });

  /* 在清单里滚一屏 —— 等价于用户手指/滚轮划一下 */
  list.scrollTop = Math.min(list.scrollHeight, list.clientHeight * 1.2);
  await nextFrame(); await wait(60); await nextFrame();

  var afterList = {
    open: pop.classList.contains("open"),
    scrollTop: Math.round(list.scrollTop),
    scrollH: list.scrollHeight,
    clientH: list.clientHeight
  };

  /* 外部滚动应当仍然收起面板：这一条防「为了不关而干脆关掉关闭逻辑」 */
  window.scrollTo(0, 260);
  await nextFrame(); await wait(60); await nextFrame();
  var afterPage = { open: pop.classList.contains("open"), y: Math.round(window.scrollY) };

  var ok = opened && scrollable && afterList.open && afterList.scrollTop > 0
        && !afterPage.open;
  return JSON.stringify({
    ok: ok,
    via: via,
    key: key,
    opened: opened,
    scrollable: scrollable,
    afterList: afterList,
    afterPage: afterPage
  }, null, 1);
})()
