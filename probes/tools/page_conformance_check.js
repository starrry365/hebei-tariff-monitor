/* 筛选口径对账 —— **页面侧消费者**（配合 cloud/tariff/conformance.py 的 oracle）。
 *
 * 为什么必须有一个「消费者」
 * -------------------------
 * conformance.py 只产出期望值（cases.json），它从诞生起就**没有任何消费方** ——
 * 文档里写着「页面里 fetch('/cases.json') 即可逐条比对」，但页面里根本没有这段代码。
 * 于是「有对账机制」是句空话：oracle 每天照常生成，对不上也没人知道。
 * 这个脚本就是那个缺掉的消费者，但**不写进 template.html** ——
 * 生产页面不该为了自检背一段调试代码（还会把 cases.json 暴露给访问者）。
 *
 * 做法：把每个用例的取值**灌进真实 DOM 控件**（#kw/#ty/#ct/#pf/#on/#off/#bw），
 *      调页面自己的 apply()，读页面自己的 view.length —— 走的是用户点下拉框的同一条路。
 *      ⚠️ 不走 rowsOf(NET).filter(...) 复刻：那就是「拿页面验页面」，发现不了系统性算错。
 *
 * 输入：全局 CONF_CASES（由 run_conformance.py 内联进来）、CONF_NET。
 * 输出：JSON 字符串（不抛异常；失败只体现在 mismatches 非空）。
 *
 * 用法（一般不直接调，走 python probes/tools/run_conformance.py）。
 */
(function () {
  var CASES = (typeof CONF_CASES !== "undefined") ? CONF_CASES : [];
  var TARGET = (typeof CONF_NET !== "undefined") ? CONF_NET : "move";
  var DIMS = (typeof DIMS !== "undefined" && DIMS.length) ? DIMS
    : ["sc", "ow", "cat", "ty", "ct", "pf", "gf", "cf", "bw", "chx", "on", "off", "chg"];
  function el(id) { return document.getElementById(id); }

  /* ★ 每个用例前必须把全部维度清干净：页面在多次 eval 之间**保留状态**，
     不清就会出现「同一份脚本连跑两次结果不同」—— 自动化检查最忌讳这个。
     STA 一律置「在售」：oracle 侧是对**全量 rows** 求值的，而移动那网 rows 全是在售
     （已下架页签恒 0 条），两边口径才对齐。 */
  function reset() {
    if (typeof NET !== "undefined" && NET !== TARGET && typeof switchNet === "function") {
      switchNet(TARGET);
    }
    if (typeof STA !== "undefined") { STA = "0"; }
    if (typeof syncTabs === "function") { syncTabs(); }
    var kw = el("kw"); if (kw) { kw.value = ""; }
    for (var i = 0; i < DIMS.length; i++) { var e = el(DIMS[i]); if (e) { e.value = ""; } }
  }

  var all = [], mism = [], zero = 0, errs = [];
  for (var i = 0; i < CASES.length; i++) {
    var c = CASES[i] || {}, q = c.q || {};
    try {
      reset();
      var kw = el("kw"); if (kw) { kw.value = q.kw || ""; }
      var map = { ty: q.ty, ct: q.ct, pf: q.pf, on: q.on, off: q.off, bw: q.bw };
      for (var k in map) {
        if (map[k] !== undefined && map[k] !== "") {
          var e = el(k);
          if (!e) { throw new Error("页面缺控件 #" + k); }
          e.value = map[k];
          /* 控件里没有这个取值（option 不存在）⇒ 赋值会静默留空，
             得到「未筛选」的答案。必须显式报错，否则等于把用例当通过了。 */
          if (e.value !== map[k]) { throw new Error("#" + k + " 无此取值: " + map[k]); }
        }
      }
      apply();
      var got = (typeof view !== "undefined" && view) ? view.length : -1;
      var rec = { tag: c.tag, net: TARGET, want: c.expect, got: got, ok: (got === c.expect) };
      all.push(rec);
      if (!rec.ok) { mism.push(rec); }
      if (got === 0) { zero++; }
    } catch (ex) {
      errs.push({ tag: c.tag, error: String(ex && ex.message || ex) });
    }
  }
  return JSON.stringify({
    net: TARGET, cases: CASES.length, ran: all.length,
    mismatches: mism, errors: errs, zero: zero, all: all
  });
})()
