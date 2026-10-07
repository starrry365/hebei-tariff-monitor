/* sh.html 时间线页签的 DOM 级断言（真实 Chrome 里 eval 跑）。
 * 用法：cdp_desktop_step.py nav "file:///.../docs/sh.html"
 *       cdp_desktop_step.py eval probes/tools/sh_page_e2e_check.js out.json
 * 断言：
 *   1. 数据容器齐：ROWS / CATS / HIST 且无模板占位符残留
 *   2. 表格渲染：行数 == ROWS 中在售+已下架（默认全部）
 *   3. 视图页签：默认明细视图可见、历史视图隐藏；点「变化历史」互换
 *   4. 时间线渲染：节点数 == HIST 去重天数；首个节点头部有日期；
 *      HIST 有样本时 tnet / tcap 在位；样本含 rows 的要有 .dc 对照卡且 .drow 行数对
 *   5. 点开节点：.tbd 显示；DIFF_SHOW 折叠按钮（>6 张卡时）出现
 *   6. 切回明细：表格回来
 * 返回 JSON，断言失败体现在 ok=false。
 */
(function () {
  var out = { ok: true, checks: [] };
  function chk(name, cond, detail) {
    out.checks.push({ name: name, ok: !!cond, detail: detail == null ? "" : String(detail) });
    if (!cond) out.ok = false;
  }
  function $(s) { return document.querySelector(s); }
  function $$(s) { return [].slice.call(document.querySelectorAll(s)); }

  /* ★ 跨 eval 状态重置（与 page_e2e_check.js 头部同一纪律）：页面在多次
     eval 之间保留筛选状态（CAT/ST/FAV/COLF/kw），不重置的话第二次 eval
     测到的就不是全量 —— 实测 508 → 87 就是这么来的。 */
  try {
    CAT = ""; ST = ""; FAV = false; COLF = {};
    var kwEl = $("#kw"); if (kwEl) kwEl.value = "";
    $$("#catChips button").forEach(function (x) { x.classList.toggle("on", !x.dataset.c); });
    $$("#tabs button").forEach(function (x) { x.classList.toggle("on", x.dataset.st === ""); });
    var ft = $("#favTog"); if (ft) ft.classList.remove("on");
    view();
  } catch (e) { /* 首次 eval 时 view 等可能未定义，页面刚加载本来就是干净态 */ }

  /* 1. 数据容器 */
  chk("ROWS 容器", typeof ROWS !== "undefined" && ROWS.length > 0,
      typeof ROWS === "undefined" ? "undefined" : ROWS.length);
  chk("CATS 容器", typeof CATS !== "undefined");
  chk("HIST 容器", typeof HIST !== "undefined", typeof HIST === "undefined" ? "undefined" : HIST.length);
  var html = document.documentElement.innerHTML;
  chk("无占位符", html.indexOf("__ROWS__") < 0 && html.indexOf("__HIST__") < 0
      && html.indexOf("__METAJS__") < 0);

  /* 2. 明细表渲染 */
  var rows1 = $$("#tb tr.row").length;
  chk("明细行数 == ROWS", rows1 === ROWS.length, rows1 + " vs " + ROWS.length);

  /* 2b. 有效期列（2026-10-07 对齐主界面；条款不设独立列，只验详情「办理必读」）*/
  chk("表头 7 列", document.querySelectorAll("thead th").length === 7,
      document.querySelectorAll("thead th").length + " 列");
  chk("有效期单元格渲染", $$("#tb tr.row td.vpc").length === rows1,
      $$("#tb tr.row td.vpc").length + " / " + rows1);
  var vpHit = 0;
  $$("#tb tr.row td.vpc").forEach(function (t) { if (t.querySelector(".vptg")) vpHit++; });
  chk("有效期徽章有命中", vpHit > 0, vpHit + " 行带徽章");
  /* 条款判据仍活着（详情「办理必读」依赖它），但不再有列表单元格/筛选 */
  chk("condHitsS 判据在位", typeof condHitsS === "function");
  chk("表头无条款列", !document.querySelector('th[data-cf="cond"]') &&
      !$$("#tb tr.row td.ctc").length);

  /* 3. 视图页签切换 */
  chk("默认明细可见", !$("#viewTbl").hidden && $("#viewHist").hidden);
  var histBtn = document.querySelector('#vtabs button[data-v="hist"]');
  chk("历史页签在位", !!histBtn);
  histBtn.click();
  chk("切换后历史可见", $("#viewTbl").hidden && !$("#viewHist").hidden);

  /* 4. 时间线渲染 */
  var days = {};
  (HIST || []).forEach(function (x) {
    var d = x.d || String(x.ts || "").slice(0, 10);
    days[d] = 1;
  });
  var nDay = Object.keys(days).length;
  var nodes = $$("#histBox .tnode").length;
  chk("节点数 == 去重天数", nodes === nDay, nodes + " vs " + nDay);
  if (nodes) {
    var first = $("#histBox .tnode .thd .ts");
    chk("首节点有日期", first && /^\d{4}-\d{2}-\d{2}$/.test(first.textContent.trim()),
        first && first.textContent.trim());
  }
  /* 有样本的轮次：tnet 在位；对照卡行数对 */
  var withSmp = (HIST || []).filter(function (x) { return (x.smp || []).some(function (s) { return s.k !== "more"; }); });
  if (withSmp.length) {
    chk("tnet 在位", $$("#histBox .tnet").length >= 1);
    var rec = withSmp[0];
    var cSmp = (rec.smp || []).filter(function (s) { return s.k === "c" && s.rows && s.rows.length; })[0];
    if (cSmp) {
      var dc = $("#histBox .dc");
      chk("对照卡在位", !!dc);
      if (dc) {
        var drows = $$("#histBox .dc .drow").length;
        chk("对照卡行数 == rows", drows === cSmp.rows.length, drows + " vs " + cSmp.rows.length);
        chk("变更行高亮", $$("#histBox .dc .drow.chg").length ===
            cSmp.rows.filter(function (r) { return r[3]; }).length);
      }
    }
    /* 展开节点（第一个 thd）后 tbd 可见 */
    $("#histBox .thd").click();
    chk("节点可展开", $("#histBox .tnode.open") && $$("#histBox .tnode.open .tbd").length === 1);
  } else if (!nodes) {
    chk("空历史提示", /还没有/.test($("#histBox").textContent));
  }
  chk("histNote 文案", /次巡检/.test($("#histNote").textContent), $("#histNote").textContent);

  /* 5. 统计卡：30 天变更 */
  var sls = $$("#stats .sl").map(function (e) { return e.textContent; });
  chk("统计卡含 30 天变更", sls.indexOf("30 天变更") >= 0, sls.join("|"));

  /* 6. 切回明细 */
  document.querySelector('#vtabs button[data-v="tbl"]').click();
  chk("切回明细可见", !$("#viewTbl").hidden && $("#viewHist").hidden);
  chk("切回后表格仍在", $$("#tb tr.row").length === ROWS.length);

  return JSON.stringify(out);
})()
