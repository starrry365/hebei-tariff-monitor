/* 页面级端到端断言（在真实 Chrome 里跑，不是 jsdom）。
 *
 * 用法：
 *   python probes/tools/cdp_launch.py
 *   python probes/tools/ct_browser/cdp_desktop_step.py nav "file:///<...>/cloud/tariff/docs/index.html"
 *   python probes/tools/ct_browser/cdp_desktop_step.py eval probes/tools/page_e2e_check.js out.json
 *
 * 断言四网各自的关键 UI 行为：
 *   · 地域筛选 #sc 的档位与置灰（只有一档地域的网，另一档必须置灰）
 *   · ★ 地市维度（2026-10-04 改回**正向**断言）：有 #ct 的网，深链 ct=城市
 *        必须筛准（恰好等于该市专属条数，faa6d2e 口径）；无 #ct 的网（cbn）
 *        深链带 ct 必须一字不变。历史：10-03 白天曾整块下线（当时是反向断言），
 *        当晚用户要求恢复筛选，反向断言随之过时 —— 四网全红且报的全是正确行为；
 *        「恢复一半」的形态（控件没了 match 还在筛 / match 删了数据还在）由
 *        这两条分叉各防一种。
 *   · 幽灵塞值：不存在的维度手工塞值必须**不生效**（否则等于拿不存在的维度筛）
 *   · 已下架页签的可用性与条数
 *   · 类型**两级**：#cat 大类的顺序与条数（必须与数据逐条对齐）、
 *     以及大类 → 细分的**联动置灰**（否则能选出「加装包 + 5G套餐」这种不存在的组合）
 *   · 渠道 #chx、流量 #gf、通话 #cf 三档筛选真的生效
 *   · 生效筛选被渲染成**可点掉的标签**，且点 × 真的摘掉那一个条件
 *
 * 🔴 每次测量前**必须自己重置全部筛选维度**（resetAll）：
 *   页面在多次 eval 之间**保留状态**（上一次把某个维度留成非空，下一次 eval 的
 *   view 就不是全量了）。不重置的话，同一份脚本「刷新后跑」和「连跑两次」结果
 *   不同 —— 自动化检查最忌讳这个。
 *
 * 返回：JSON 字符串（直接给 python 解析），不抛异常 —— 断言失败只体现在 ok=false。
 */
(function () {
  function txt(el) { return String((el && el.textContent) || ""); }
  function opts(sel) { return [].slice.call($(sel).options); }

  /* 把界面恢复成「刚打开」：所有筛选维度清空 + 回到「在售」页签。
     不假设调用方已经刷新页面 —— 这个脚本要能连跑多次、且与执行顺序无关。
     ★ 维度列表优先从页面全局 DIMS 取（页面自己那份是唯一权威）；
       取不到才退回显式列表 —— 硬编码一份迟早与页面漂移。 */
  function resetAll() {
    var ids = null;
    try {
      if (typeof DIMS !== "undefined" && DIMS.length) {
        ids = ["#kw"].concat(DIMS.map(function (k) { return "#" + k; }));
      }
    } catch (e) { ids = null; }
    if (!ids) {
      ids = ["#kw", "#sc", "#cat", "#ty", "#pf", "#gf", "#cf",
             "#bw", "#chx", "#on", "#off", "#chg"];
    }
    /* ★★ 地址栏也要抹干净：walk / 人工 / 其他脚本会留下带筛选的 hash
       （实测：#net=cbn&kw=宽带&v=ov&on=7&off=30 —— walk 遍历遗留）。
       本脚本后面构造深链用的正是「在现有 hash 上追加」—— 里面带着遗留
       条件时，赋值 location.hash 会触发 hashchange → readHash 把旧条件
       （连同旧 net）全部恢复，view 立刻错位：2026-10-04 实测 walk 之后跑
       本脚本，move 大类套餐筛出 0 条、深链 ct 对不上 —— 遗留 hash 把
       NET 都切走了，而后面的断言还在按 move 对账。
       replaceState 不触发 hashchange（不会半路执行 readHash），只把地址栏
       抹掉；控件本来就由下面的清空负责，apply() 会用 syncHash 写回干净快照。 */
    try {
      if (location.hash) { history.replaceState(null, "", location.pathname + location.search); }
    } catch (e) {}
    for (var i = 0; i < ids.length; i++) {
      var e = $(ids[i]);
      if (e) { e.value = ""; }
    }
    try { if (typeof syncDims === "function") { syncDims(); } } catch (e) {}
    /* ★ 视图也要恢复成列表：视图（v）不是 select 控件、不在 DIMS 里，walk 遍历
       结束时停在 setView("ov")（总览）—— ov 视图下 view.length 不是列表行数，
       本脚本后面所有条数断言（cityProbe / catFilterSample …）都会拿到 0
       （2026-10-04 实测：before=5280 正常、深链 readHash 读出 v=ov 后 got=0）。
       恢复视图后 apply→syncHash 写出的快照才不含 v=ov，深链测试的基准才是列表。 */
    try { if (typeof setView === "function") { setView("list"); } } catch (e) {}
    try { if (typeof STA !== "undefined") { STA = "0"; } } catch (e) {}
    try { if (typeof pg !== "undefined") { pg = 0; } } catch (e) {}
    try { clearChips(); } catch (e) {}
    try { syncTabs(); } catch (e) {}
    try { apply(); } catch (e) {}
  }

  /* 当前页签（在售）下应当被计入的条数 —— 用来和页面 view 长度对账。
     直接和 rowsOf() 全量比会差出下架那批，那是误报。 */
  function inTab(d) {
    return !(STA !== "" && (d.st ? 1 : 0) !== +STA);
  }
  function wantBy(pred) {
    return rowsOf(NET).filter(function (d) { return inTab(d) && pred(d); }).length;
  }

  function snap(netCode) {
    var o = { net: netCode };
    if (typeof NET === "undefined" || typeof net !== "function") {
      o.error = "页面未加载完（NET/net 不存在）";
      return o;
    }
    NET = netCode;
    try { renderNav(); } catch (e) {}
    try { renderNet(); } catch (e) {}
    resetAll();

    var n = net() || {};
    o.netname = n.nm || "";
    o.sub = txt($("#sub")).slice(0, 170);

    var sc = $("#sc");
    o.scOptions = opts("#sc").map(function (x) {
      return { v: x.value, t: x.text, disabled: !!x.disabled };
    });
    o.scChoosable = o.scOptions.filter(function (x) { return !x.disabled; })
                               .map(function (x) { return x.v; });

    var rs = rowsOf(NET);
    o.rows = rs.length;
    o.ctyRows = rs.filter(function (d) { return (d.cty || []).length; }).length;
    o.pwRows = rs.filter(function (d) { return !!d.pw; }).length;

    o.tabs = [].slice.call(document.querySelectorAll("#tabs button")).map(function (b) {
      return { t: b.textContent, disabled: !!b.disabled };
    });

    /* ══ 地市维度（2026-10-04 改回正向断言）══
       历史：10-03 白天曾整块下线（当时这里是三条反向断言），当晚用户要求恢复筛选
       —— 反向断言就此过时，四网全红且报的都是「正确行为」（深链生效反被叫幽灵）。
       现在的分叉：
         · #ct 存在的网（move/telecom/unicom）：挑第一个可点（专属>0）的城市，
           深链 ct=该市，条数必须**恰好等于**该市专属数（faa6d2e 口径）——
           只验「变了」验不出「筛错市」；
         · #ct 不存在的网（cbn，上游无地市粒度）：保留幽灵断言 ——
           深链带 ct 必须一字不变（防止哪天恢复一半：控件没了、match 还在筛）。 */
    o.ctEl = !!$("#ct");
    /* 分支判据必须是「可见」而不是「存在」：cbn（无地市维度）的 #ct 在 DOM 里
       但被 display:none 隐藏 —— readHash 对隐藏控件拒绝写入（深链无效），
       那网应走幽灵断言；按「存在」分支的话会在隐藏控件上做正向对账，
       误报「深链条数不符」（2026-10-04 实测 cbn 212 ≠ 0 就是这么来的）。 */
    o.ctUsable = !!o.ctEl && $("#ct").style.display !== "none";
    o.chipTextsNoCity = [].map.call(document.querySelectorAll("#stat .fchip"),
                                    function (b) { return txt(b); })
      .filter(function (t) { return t.indexOf("城市") >= 0 || t.indexOf("地市") >= 0; });
    if (!o.ctUsable) {
      /* 幽灵值：走深链那条最真实的路径。🔴 必须把 ct 参数**追加**在现有 hash 后面：
         换掉会连 net / st 一起丢，readHash 把 STA 置成「全部」，条数从「在售」
         跳到「全部」—— 那是测试自己造的变化，与 ct 毫无关系（实测踩过）。 */
      (function () {
        var h0 = location.hash || "#", before = view.length;
        o.ghostRowsBefore = before;
        try {
          var extra = "ct=%E7%9F%B3%E5%AE%B6%E5%BA%84";        // ct=石家庄
          location.hash = h0 + (h0.length > 1 ? "&" : "") + extra;
          if (typeof readHash === "function") { readHash(); }
          if (typeof apply === "function") { apply(); }
          o.ghostHashRows = view.length;
          o.ghostUnaffected = (view.length === before);
        } catch (e) {
          o.ghostErr = String(e && e.message || e);
        } finally {
          try { location.hash = h0; } catch (e) {}
          if (typeof readHash === "function") { readHash(); }
          try { clearChips(); } catch (e) {}
          apply();
        }
      })();
    } else {
      /* 正向：深链 ct=城市 必须筛准。resetAll 在 snap() 开头跑过，此时基准是干净的。 */
      var cit = opts("#ct").filter(function (x) {
        return x.value && x.value !== "_none" && !x.disabled;
      })[0] || null;
      o.cityProbe = null;
      if (cit) {
        try {
          var b0 = view.length, h0c = location.hash || "#";
          location.hash = h0c + (h0c.length > 1 ? "&" : "")
            + "ct=" + encodeURIComponent(cit.value);
          if (typeof readHash === "function") { readHash(); }
          apply();
          o.cityProbe = { city: cit.value, before: b0, got: view.length,
                          want: wantBy(function (d) {
                            return (d.cty || []).indexOf(cit.value) >= 0;
                          }) };
        } catch (e) {
          o.cityProbeErr = String(e && e.message || e);
        } finally {
          try { location.hash = h0c; } catch (e) {}
          if (typeof readHash === "function") { readHash(); }
          var ctSel = $("#ct"); if (ctSel) { ctSel.value = ""; }
          try { clearChips(); } catch (e) {}
          apply();
        }
      }
    }

    /* ★★ 「已下架」页签的条数（地市那一层已下线，这里只剩页签自身的可用性）。
       记录它是为了验证：本网有下架数据时该页签可点，且点进去条数与数据一致。 */
    if ((o.tabs || []).some(function (t) { return /已下架/.test(t.t) && !t.disabled; })) {
      try {
        STA = "1"; syncTabs();
        try { clearChips(); } catch (e) {}
        apply();
        o.rowsOnStop = view.length;
      } catch (e) { o.stopTabErr = String(e && e.message || e); }
      resetAll();
    }

    /* ══ 类型两级 ══
       ① 大类选项必须按 CAT_ORDER（构建脚本注入）的顺序出现 ——
          否则页面上「套餐 / 加装包 / …」的次序会与构建日志对不上，逐日比对就没法做。
       ② 选一个大类 ⇒ view 必须**恰好等于**该大类的条数（不是"大于 0"就行：
          那只能证明筛选没把结果滤光，证明不了它筛对了）。
       ③ 联动的反面：不属于该大类的细分项必须**全被置灰**，
          属于的必须**全可点**；漏一个就能选出不存在的组合。 */
    var catVals = opts("#cat").map(function (x) { return x.value; }).filter(Boolean);
    o.catOptions = catVals;
    try {
      var wantOrder = CAT_ORDER.filter(function (c) { return catVals.indexOf(c) >= 0; });
      o.catOrderOk = (wantOrder.join("|") === catVals.join("|"));
    } catch (e) { o.catOrderOk = null; }

    if (catVals.length) {
      var c0 = catVals[0];
      $("#cat").value = c0;
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
      o.catFilterSample = { cat: c0, rows: view.length, want: wantBy(function (d) { return d.cat === c0; }) };
      // 联动只检**非空**细分项：空值那项（「全部细分」）永远可点，不参与联动
      o.tyLinkBad = opts("#ty").filter(function (x) {
        if (!x.value) { return false; }
        var sameCat = (x.dataset.cat === c0);
        return sameCat ? !!x.disabled : !x.disabled;
      }).map(function (x) { return x.value + (x.disabled ? "(该可点却被灰)" : "(该灰却可点)"); });
      resetAll();
    }

    /* ══ 渠道 ══
       chx 是**构建期**算好写进行数据的（页面不做映射），所以这里有两件事要验：
       ① 数据里真的有这个字段（否则筛选恒空、且看不出原因）；
       ② 选一档 ⇒ view 等于该档条数。 */
    o.chxPresent = rs.some(function (d) { return !!d.chx; });
    var chxVals = opts("#chx").map(function (x) { return x.value; }).filter(Boolean);
    o.chxOptions = chxVals;
    if (chxVals.length) {
      var x0 = chxVals[0];
      $("#chx").value = x0;
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
      o.chxFilterSample = { v: x0, rows: view.length, want: wantBy(function (d) { return d.chx === x0; }) };
      resetAll();
    }

    /* ══ 数值区间（流量 / 通话）══
       用「≤5GB」而不是「≥100GB」：后者在个别网可能真的是 0 条
       （数据使然，不是 bug），拿它做断言会把正常情况判成失败。 */
    $("#gf").value = "0,5";
    try { apply(); } catch (e) {}
    /* 2026-10-04 页面档位改半开 (lo,hi]（列表审查 F2）：g=0 归「无流量」独立档，
       「≤5GB」= (0,5]。oracle 是复现，跟页面同一语义。 */
    o.gfSample = { rows: view.length, want: wantBy(function (d) { return d.g != null && d.g > 0 && d.g <= 5; }) };
    resetAll();

    $("#cf").value = "none";
    try { apply(); } catch (e) {}
    o.cfSample = { rows: view.length, want: wantBy(function (d) {
      var c = parseFloat(d.c); return d.c == null || d.c === "" || isNaN(c) || c === 0;
    }) };
    resetAll();

    /* ══ 可点掉的筛选标签 ══
       不能只验「渲染出来了」—— 真正的功能是**点 × 能摘掉那一个条件**，
       所以点完之后必须看到结果变大、且标签数减一。 */
    if (chxVals.length) { $("#chx").value = chxVals[0]; }
    if (catVals.length) { $("#cat").value = catVals[0]; }
    try { syncTyOptions(); } catch (e) {}
    try { clearChips(); } catch (e) {}
    try { apply(); } catch (e) {}
    var fc = document.querySelectorAll("#stat .fchip");
    o.fchipCount = fc.length;
    o.fchipTexts = [].map.call(fc, function (b) { return txt(b); });
    /* 🔴 必须挑一个**真会收窄结果**的标签来点，不能无脑点第一个。
       第一个通常是「状态：在售」—— 而移动 / 电信的下架数据本来就是 0 条，
       摘掉它条数**根本不会变**，于是正常情况被判成「× 没生效」。
       实测踩过：move / telecom 报错而 unicom / cbn 通过，就是这个原因
       —— 报错的那两网才是数据正常的，断言反过来冤枉了它们。 */
    var pick = null;
    for (var i = 0; i < fc.length; i++) {
      var dk = fc[i].dataset.d;
      if (dk && dk !== "st" && dk !== "kw") { pick = fc[i]; break; }
    }
    if (pick) {
      var n0 = fc.length, rows0 = view.length, pk = pick.dataset.d;
      pick.click();
      o.fchipRemove = { removed: pk, before: n0,
                        after: document.querySelectorAll("#stat .fchip").length,
                        rowsBefore: rows0, rowsAfter: view.length,
                        rowGrew: view.length > rows0 };
    }
    resetAll();
    return o;
  }

  /* 每网的期望。`stopped` = 本网有没有下架数据（页签该不该可点）。
     ⚠️ 2026-10-03：原先这里还有一个 `city` 期望（本网该不该出地市下拉）。
        地市维度整块下线后它失去意义 —— 四个网的期望**一律**是「没有 #ct」，
        所以那条断言从「按网给期望」变成了 check() 里的无条件断言（见下）。 */
  var EXPECT = {
    move:    { stopped: false },
    telecom: { stopped: false },
    unicom:  { stopped: true  },
    cbn:     { stopped: true  }
  };

  function check(o, exp) {
    var bad = [];
    if (o.error) { return ["页面异常: " + o.error]; }
    if (!(o.rows > 0)) { bad.push("row=0"); }
    if (!o.scOptions.length) { bad.push("无地域档位"); }
    else if (!o.scChoosable.length) { bad.push("地域两档都不可选"); }

    /* ══ 地市维度（2026-10-04 改回正向）══
       · 有 #ct 的网：深链 ct=城市 必须筛准（恰好等于该市专属条数）；
       · 无 #ct 的网（cbn）：深链 ct 必须一字不变（幽灵断言，防「恢复一半」）。 */
    if (o.ctUsable) {
      if (o.cityProbeErr) { bad.push("地市深链操作异常: " + o.cityProbeErr); }
      else if (o.cityProbe) {
        var cp = o.cityProbe;
        if (cp.got !== cp.want) {
          bad.push("深链 ct=" + cp.city + " 条数不符：页面 " + cp.got
            + " ≠ 数据 " + cp.want + "（专属口径）");
        }
      }
    } else {
      if (o.ghostErr) { bad.push("深链幽灵值操作异常: " + o.ghostErr); }
      else if (o.ghostUnaffected === false) {
        bad.push("深链 #ct=… 竟然改变了结果（幽灵条件生效了，严重）："
          + o.ghostHashRows + " ≠ " + o.ghostRowsBefore);
      }
    }
    /* ★ 「已下架」页签：本网有下架数据时该页签可点、且能算出条数。 */
    if (exp.stopped) {
      if (o.stopTabErr) { bad.push("已下架页签操作异常: " + o.stopTabErr); }
      else if (!(o.rowsOnStop > 0)) {
        bad.push("「已下架」页签算出 0 条（本网有下架数据，页签却空）");
      }
    }

    // 下架页签
    var stop = (o.tabs || []).filter(function (t) { return /已下架/.test(t.t); })[0];
    if (!stop) { bad.push("找不到「已下架」页签"); }
    else if (exp.stopped && stop.disabled) { bad.push("期望已下架页签可用，实际置灰"); }
    else if (!exp.stopped && !stop.disabled) { bad.push("期望已下架页签置灰（本网取不到下架），实际可用"); }

    // 类型大类
    if (!(o.catOptions || []).length) { bad.push("无类型大类选项"); }
    else {
      if (o.catOrderOk === false) { bad.push("大类顺序与 CAT_ORDER 不一致"); }
      var cs = o.catFilterSample || {};
      if (!(cs.rows > 0)) { bad.push("大类「" + cs.cat + "」筛出 0 条"); }
      else if (cs.rows !== cs.want) {
        bad.push("大类「" + cs.cat + "」条数不符：页面 " + cs.rows + " ≠ 数据 " + cs.want);
      }
      if ((o.tyLinkBad || []).length) {
        bad.push("大类→细分的联动置灰有漏：" + o.tyLinkBad.join(" / "));
      }
    }

    // 渠道
    if (!o.chxPresent) { bad.push("数据里没有渠道档 chx"); }
    if (!(o.chxOptions || []).length) { bad.push("无渠道选项"); }
    else {
      var xs = o.chxFilterSample || {};
      if (xs.rows !== xs.want) {
        bad.push("渠道「" + xs.v + "」条数不符：页面 " + xs.rows + " ≠ 数据 " + xs.want);
      }
    }

    // 数值区间
    var g = o.gfSample || {}, c = o.cfSample || {};
    if (g.rows !== g.want) { bad.push("流量「≤5GB」条数不符：" + g.rows + " ≠ " + g.want); }
    if (c.rows !== c.want) { bad.push("通话「无通话」条数不符：" + c.rows + " ≠ " + c.want); }

    // 可点掉的筛选标签
    if (!(o.fchipCount > 0)) { bad.push("未渲染出筛选标签"); }
    else if (o.fchipRemove) {
      if (!(o.fchipRemove.after < o.fchipRemove.before)) { bad.push("点掉标签后标签数没减少"); }
      if (!o.fchipRemove.rowGrew) { bad.push("点掉标签后结果没变多（× 没生效）"); }
    }
    return bad;
  }

  var report = {};
  var allOk = true;
  ["move", "unicom", "telecom", "cbn"].forEach(function (code) {
    var o;
    try { o = snap(code); } catch (e) { o = { net: code, error: String(e) }; }
    var bad = check(o, EXPECT[code]);
    o.ok = bad.length === 0;
    o.problems = bad;
    if (!o.ok) { allOk = false; }
    report[code] = o;
  });
  report.__allOk = allOk;
  return JSON.stringify(report);
})()
