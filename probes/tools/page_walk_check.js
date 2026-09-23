/* 交互全路径遍历：把所有能点的控件都真实点一遍，捕获全部异常。
 *
 * 用法：
 *   python probes/tools/cdp_launch.py
 *   python probes/tools/ct_browser/cdp_desktop_step.py nav \
 *          "file:///D:/.../cloud/tariff/docs/index.html"
 *   python probes/tools/ct_browser/cdp_desktop_step.py eval \
 *          probes/tools/page_walk_check.js out.json
 * 通过判据：errs 为空、bind 全是 function、两次连跑（不重载）结果逐字节一致。
 *
 * 为什么需要它：单看「页面能打开、标签都在」根本发现不了断路。
 * 本页曾有一个 TypeError 发生在 renderNet() 内部的 renderOverview()，
 * 而 renderNet() 处在初始化链上 —— 一处抛异常 ⇒ 它之后的 readHash/
 * 事件绑定（#kw / DIMS / 页签 / 主题 / 翻页 / 排序）全都没执行，
 * 表现就是「大部分功能点了没反应」，而页面上看不出任何异常。
 *
 * 输出 JSON 字符串，含：
 *   bind   初始化是否把事件全装上（全是 function 才算过）
 *   views  四个视图页签是否都能切、对应 section 是否显示
 *   nets   每个网：条数、每个筛选下拉逐 option 试值后的结果条数、异常
 *   misc   排序逐项、翻页、行展开、主题、chips、关键词
 *   misc.geom  滚动 600px 后的常驻层几何 + 命中测试（筛选控件是否真能点到）
 *              —— 这一项专治「看着都在、其实点不到」
 *   errs   捕获到的全部异常
 */
(function () {
  var errs = [];
  function E(m) { if (errs.length < 200) errs.push(m); }
  window.addEventListener("error", function (e) {
    E("onerror: " + (e.message || e) + " @" + (e.lineno || "?") + ":" + (e.colno || "?"));
  }, true);
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason; E("reject: " + (r && r.message ? r.message : String(r)));
  });

  var $s = function (s) { return document.querySelector(s); };
  var $a = function (s) { return [].slice.call(document.querySelectorAll(s)); };
  function safe(tag, fn) { try { fn(); } catch (e) { E("THROW[" + tag + "] " + (e && e.message)); } }
  /* 🔴 判据必须用**筛选后的结果数**（页面全局 view），不能用 rowsOf(NET)：
     后者是「本网全部行」（含下架、不含筛选），拿它当判据会让所有筛选
     看起来都「没生效」—— 实测踩过，白排查一轮。
     view 在「在售」页签下就是筛选后的在售条数。 */
  function live() { try { return typeof view !== "undefined" ? view.length : -1; } catch (e) { return -2; } }
  function full() { try { return typeof rowsOf === "function" ? rowsOf(NET).length : -1; } catch (e) { return -2; } }
  function tbRows() { var t = $s("#tb"); return t ? t.children.length : -1; }
  function fire(el, ev) { el.dispatchEvent(new Event(ev, { bubbles: true })); }
  /* 段间清理。🔴 每个子测试前都要清：chips/关键词这类测试会留下筛选，
     而下一步若恰好还在同一个网，switchNet() 里 `if(c!==NET)` 不成立就**不清筛选**，
     于是残渣叠加 —— 表现为「后面所有测量都异常但不报错」（实测踩过：
     关键词 5G 本该几十条却量到 0，差点误判成搜索坏了）。 */
  function clean() { safe("clean", function () { resetAll(); }); }

  var R = { bind: {}, views: {}, nets: {}, misc: {}, errs: errs };

  /* ══ 起点自清理 ══
     上一次运行（或用户随手点过）可能把排序键 / 各维度筛选留在任意状态。
     不先归零的话，「刷新后跑」与「连跑两次」结果不同 —— 自动化检查的大忌。
     直接用页面自己的 resetAll()：它会把 sortK 钉回 "on"、清空全部维度、
     关掉页签并重算。🔴 不要在探针里另写一套复位逻辑 —— 两套迟早漂移
     （且漂移时表现为「页面坏了」，实际是探针没跟上）。 */
  safe("boot-reset", function () {
    resetAll();                                   /* 清维度 + 钉回 sortK/sortD */
    if (typeof STA !== "undefined") STA = "0";    /* 页签回「在售」（resetAll 不管它） */
    if (typeof syncTabs === "function") syncTabs();
    if (typeof pg !== "undefined") pg = 0;
    setView("ov");
    apply();
  });

  /* ══ A. 初始化完整性：事件绑定装上了没 ══ */
  function tp(sel, prop) { var e = $s(sel); return e ? (typeof e[prop]) : "NO-EL"; }
  R.bind = {
    kw: tp("#kw", "oninput"), theme: tp("#themeBtn", "onclick"),
    csv: tp("#csv", "onclick"), offline: tp("#offline", "onclick"),
    prev: tp("#prev", "onclick"), next: tp("#next", "onclick"),
    reset: tp("#reset", "onclick"), dir: tp("#dir", "onclick"),
    sort: tp("#sort", "onchange"), histTog: tp("#histTog", "onclick"),
    dims: {}, tabBtns: $a("#vtab .tab").length,
    staBtns: $a("#tabs button").length, thSort: $a("th[data-k]").length,
    sortOpts: $a("#sort option").length
  };
  (typeof DIMS !== "undefined" ? DIMS : []).forEach(function (k) {
    R.bind.dims[k] = tp("#" + k, "onchange");
  });

  /* ══ B. 四个视图页签 ══
     🔴 量之前先把网钉到第一个：总览页的 KPI 卡数量**随网而变**（联通/广电没有
        地市维度 ⇒ 少「地市专属」那张卡，也不显示政企）。不钉住的话，B 段测的
        就是「上一轮结束时遗留在哪个网」—— 同一份脚本连跑两次会得到 7 vs 5，
        幂等判据直接失效，而页面本身完全正常。 */
  var NETKEYS = (typeof NETS !== "undefined") ? Object.keys(NETS) : [];
  safe("views-net", function () { if (NETKEYS.length) switchNet(NETKEYS[0]); });
  ["ov", "list", "hist", "about"].forEach(function (v) {
    var o = {};
    safe("view:" + v, function () {
      setView(v);
      o.view = (typeof VIEW !== "undefined" ? VIEW : "?");
      var sec = $s("#v-" + v);
      o.shown = !!sec && sec.classList.contains("on");
      o.onTabs = $a("#vtab .tab.on").length;
      /* 该视图的关键容器是否有内容（空容器＝渲染函数没跑或跑挂了） */
      if (v === "ov") o.kpi = $a("#ovStats .stat").length, o.bars = $a(".bfill").length;
      if (v === "hist") o.nodes = $a("#histBox .tnode").length;
      if (v === "list") o.tbRows = tbRows();
    });
    R.views[v] = o;
  });

  /* ══ C. 每个网 × 每个筛选下拉 × 每个 option ══ */
  var SELIDS = ["sc", "ow", "cat", "ty", "ct", "pf", "gf", "cf", "bw", "chx", "on", "off", "chg"];
  NETKEYS.forEach(function (c) {
    var rec = { total: 0, sel: {}, offline: null };
    safe("switchNet:" + c, function () { switchNet(c); setView("list"); });
    rec.total = live();
    rec.name = (typeof net === "function" && net() && net().sh) ? net().sh : "";
    rec.offline = rec.total;   /* rowsOf 给的是在售数 */

    SELIDS.forEach(function (id) {
      var el = $s("#" + id);
      if (!el) { rec.sel[id] = "NO-EL"; return; }
      if (el.offsetParent === null && getComputedStyle(el).display === "none") { rec.sel[id] = "HIDDEN"; return; }
      var vs = [].slice.call(el.options).map(function (o) { return o.value; });
      var res = [];
      vs.forEach(function (val) {
        var n0 = errs.length;
        safe("sel:" + c + "#" + id + "=" + val, function () {
          el.value = val; fire(el, "change");
        });
        res.push({ v: val, n: live(), rows: tbRows(), bad: errs.length > n0 });
      });
      /* 复原 */
      safe("sel-restore:" + c + "#" + id, function () { el.value = ""; fire(el, "change"); });
      rec.sel[id] = { opts: vs.length, res: res };
    });
    R.nets[c] = rec;
  });

  /* ══ D. 排序 / 翻页 / 行展开 / 主题 / chips / 关键词 ══ */
  if (NETKEYS.length) safe("restore-move", function () { switchNet(NETKEYS[0]); setView("list"); });

  var m = R.misc;
  clean();
  m.sort = [];
  $a("#sort option").forEach(function (o) {
    var n0 = errs.length;
    safe("sort:" + o.value, function () {
      $s("#sort").value = o.value; fire($s("#sort"), "change");
    });
    m.sort.push({ v: o.value, n: live(), bad: errs.length > n0 });
  });
  /* 复位到**第一个真实存在的键**。🔴 别硬编码 "o"：排序键表里根本没有 o，
     F["o"].def 会抛 TypeError 并打断后续 apply() —— 那是探针自己造的假故障，
     而它长得和真 bug 一模一样（后面的关键词筛选会跟着全废）。 */
  safe("sort-reset", function () {
    var o = $s("#sort").options[0];
    $s("#sort").value = o ? o.value : sortK; fire($s("#sort"), "change");
  });

  m.dirsort = "-";
  safe("dir", function () { $s("#dir").click(); m.dirsort = String(sortD); });
  safe("th-sort", function () {
    var th = $a("th[data-k]")[0];
    if (th) th.click();
    m.thSortK = (typeof sortK !== "undefined" ? sortK : "?");
  });

  clean();
  m.pages = [];
  safe("pg-next", function () {
    for (var i = 0; i < 3; i++) { $s("#next").click(); m.pages.push(pg); }
  });
  safe("pg-prev", function () {
    for (var i = 0; i < 5; i++) { $s("#prev").click(); m.pages.push(pg); }
  });

  m.detail = "?";
  safe("row-detail", function () {
    var btn = $a("#tb .det,.exp,[data-det]")[0] || $a("#tb tr td button")[0];
    if (btn) { btn.click(); m.detail = "clicked"; } else m.detail = "no-btn";
  });

  m.theme = [];
  safe("theme-x2", function () {
    var b = $s("#themeBtn");
    b.click(); m.theme.push(document.documentElement.getAttribute("data-theme"));
    b.click(); m.theme.push(document.documentElement.getAttribute("data-theme"));
    m.themeLS = (function () { try { return localStorage.getItem("hb-tariff-theme"); } catch (e) { return "ERR:" + e.name; } })();
  });

  clean();
  m.chips = [];
  safe("chips", function () {
    $a("#chips button[data-p]").forEach(function (b) {
      var n0 = errs.length;
      b.click();
      m.chips.push({ p: b.dataset.p, n: live(), bad: errs.length > n0 });
    });
  });

  m.chipx = "?";
  safe("chip-x", function () {
    var x = $a("#stat .fchip")[0];
    if (x) { x.click(); m.chipx = "clicked"; } else m.chipx = "no-chip";
  });

  clean();
  m.kw = [];
  safe("kw", function () {
    ["5G", "宽带", "政企", "iptv", "zzzz-none"].forEach(function (q) {
      var n0 = errs.length;
      var el = $s("#kw"); el.value = q; fire(el, "input");
      m.kw.push({ q: q, n: live(), rows: tbRows(), bad: errs.length > n0 });
    });
    var el = $s("#kw"); el.value = ""; fire(el, "input");
  });

  /* ══ D2. 归属（个人 / 政企）专项对账 ══
     这是本轮新增的维度，两个必查点：
       ① 移动选「政企」真的能筛出结果，且「个人 + 政企 = 全部」；
       ② 只在上游**真有 type1 字段**的网显示该下拉（联通/电信/广电上游没有
          这个字段 ⇒ 必须隐藏，而不是显示一个永远筛出 0 条的假控件）。 */
  clean();
  m.ow = {};
  safe("ow-check", function () {
    switchNet("move"); setView("list");
    /* 🔴 必须遍历**真实 option.value**，不能硬编码上游原始值 "1"/"2"：
       页面把 type1 归一成了中文（option value = "个人" / "政企"），
       塞一个不存在的值进去浏览器会把 select.value 静默重置为 ""，
       于是「筛选没生效」—— 看着像页面坏了，其实是探针假设错了。 */
    var el = $s("#ow"), out = {};
    [].slice.call(el.options).forEach(function (o) {
      el.value = o.value; fire(el, "change");
      out[o.value === "" ? "全部" : o.value] = live();
    });
    el.value = ""; fire(el, "change");
    m.ow = out;
    m.owSum = (out["个人"] || 0) + (out["政企"] || 0) === out["全部"];
    /* 与数据层交叉对账：页面筛出的数必须等于行数据里逐条统计的数。
       两者同源才说明「归属」这条链（构建期写入 → 页面读取 → 过滤）是通的。 */
    m.owData = (function () {
      var rs = rowsOf("move"), gq = 0, gr = 0;
      rs.forEach(function (d) { if (d.ow === "政企") gq++; else if (d.ow === "个人") gr++; });
      return { total: rs.length, gq: gq, gr: gr };
    })();
    m.owMatch = (m.owData.gq === m.ow["政企"]) && (m.owData.gr === m.ow["个人"]);
    var vis = {};
    (typeof NETS !== "undefined" ? Object.keys(NETS) : []).forEach(function (c) {
      switchNet(c);
      var e = $s("#ow");
      vis[c] = e ? (e.offsetParent !== null ||
        getComputedStyle(e).display !== "none") : "NO-EL";
    });
    m.owVisible = vis;
    switchNet("move"); setView("list");
  });

  /* ══ D3. 在售 / 已下架 / 全部 三个状态页签 ══
     三者条数必须满足：在售 + 已下架 = 全部（同一时刻口径自洽）。 */
  m.sta = [];
  safe("sta-tabs", function () {
    $a("#tabs button").forEach(function (b) {
      var n0 = errs.length;
      /* disabled 是**正确行为**的证据：上游不提供下架数据的网（如移动），
         「已下架」页签必须禁用；点它会被 onclick 里的 if(b.disabled)return 挡掉。 */
      var dis = !!b.disabled;
      b.click();
      m.sta.push({ v: b.dataset.v, dis: dis, n: live(), rows: tbRows(),
                   bad: errs.length > n0 });
    });
    var f = $a("#tabs button")[0]; if (f) f.click();
  });

  m.reset = "?";
  safe("reset", function () { $s("#reset").click(); m.reset = String(live()); });

  /* ══ F. 常驻层几何自检：滚动之后筛选控件是否还**可见、可点** ══
     ★ 这一类故障点按钮的遍历**测不到**：控件都在、事件也绑上了，
       只是被别的层压在底下（或粘性被 overflow 破坏）。2026-09-23 用户报的
       「筛选框不对」就是它 —— .nav / #vtab / .bar 三层都写 sticky;top:0，
       滚动后页签和 13 个下拉全被顶栏盖住，只剩筛选栏底部那行芯片露出。
     ★ 判据（滚动 600px 后）：
         vt.top == 0                      页签钉在视口顶
         bar.top == vt.h                  筛选栏紧贴页签下沿（不留缝、不重叠）
         th.top  == vt.h + bar.h          表头紧贴筛选栏下沿
         命中测试三个点分别落在 #sc / #kw / .tab 上 —— 这是「点得到」的直接证据，
         只看 rect 是不够的（被盖住时 rect 完全正常，elementFromPoint 才会暴露）。 */
  safe("geom", function () {
    if (NETKEYS.length) switchNet(NETKEYS[0]);
    setView("list");
    scrollTo(0, 600);
    function box(sel) {
      var e = document.querySelector(sel); if (!e) return null;
      var r = e.getBoundingClientRect();
      return { top: Math.round(r.top), h: Math.round(r.height) };
    }
    function hit(sel) {
      var e = document.querySelector(sel); if (!e) return null;
      var r = e.getBoundingClientRect();
      var t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!t) return null;
      return { id: t.id || "", cls: String(t.className || ""), tag: t.tagName };
    }
    var vt = box("#vtab"), bar = box("#bar"), th = box("thead th");
    var hSc = hit("#sc"), hKw = hit("#kw"), hTab = hit("#vtab .tab");
    m.geom = {
      vtabTop: vt && vt.top, vtabH: vt && vt.h,
      barTop: bar && bar.top, barH: bar && bar.h,
      thTop: th && th.top,
      hitSc: hSc && hSc.id, hitKw: hKw && hKw.id,
      hitTab: hTab && (hTab.cls + "|" + hTab.tag),
      /* 三项都必须 true；任一为 false 就是「滚动后筛选不可用」 */
      vtabOk: !!vt && vt.top === 0,
      barOk: !!vt && !!bar && Math.abs(bar.top - vt.h) <= 1,
      thOk: !!th && !!bar && Math.abs(th.top - (vt.h + bar.h)) <= 2,
      hitOk: !!hSc && hSc.id === "sc" && !!hKw && hKw.id === "kw"
             && !!hTab && /(^|\s)tab(\s|$)/.test(hTab.cls)
    };
    scrollTo(0, 0);
  });

  /* 视图切回总览，页签复原 */
  safe("final", function () { setView("ov"); });

  return JSON.stringify(R);
})()
