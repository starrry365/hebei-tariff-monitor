/* 页面级端到端断言（在真实 Chrome 里跑，不是 jsdom）。
 *
 * 用法：
 *   python probes/tools/cdp_launch.py
 *   python probes/tools/ct_browser/cdp_desktop_step.py nav "file:///<...>/cloud/tariff/docs/index.html"
 *   python probes/tools/ct_browser/cdp_desktop_step.py eval probes/tools/page_e2e_check.js out.json
 *
 * 断言四网各自的关键 UI 行为：
 *   · 地域筛选 #sc 的档位与置灰（只有一档地域的网，另一档必须置灰）
 *   · 地市筛选 #ct 的**显隐**（本网一条地市归属都没有的必须隐藏，而不是置灰）
 *     —— 判据是**数据**（有没有 d.cty），不是任何「本网不分城市」的声明：联通那网
 *        声明过 allProvince 而它与事实相反，地市维度因此被整层藏掉，无人察觉。
 *   · 地市筛选**真的筛对了**：每个可点地市档的条数必须与数据**精确相符**，
 *     口径 = 「不限地市(pw) ＋ 该市专属(cty)」，样本取**当前页签下真有条目**的那个。
 *     🔴 判据**不是**「结果必须收窄」—— 本网常常绝大部分都是不限地市，选一个市
 *        不收窄是正确行为，旧判据会把正确结果判成失败（2026-09-24 改口径时更正）。
 *   · 幽灵塞值：隐藏维度手工塞值必须**不生效**（否则等于拿不存在的维度筛）
 *   · 已下架页签的可用性与条数；且该页签下**地市维度必须整体禁用**、
 *     塞进去的地市值也不得生效（已下架条目没有地市归属）
 *   · 类型**两级**：#cat 大类的顺序与条数（必须与数据逐条对齐）、
 *     以及大类 → 细分的**联动置灰**（否则能选出「加装包 + 5G套餐」这种不存在的组合）
 *   · 渠道 #chx、流量 #gf、通话 #cf 三档筛选真的生效
 *   · 生效筛选被渲染成**可点掉的标签**，且点 × 真的摘掉那一个条件
 *
 * 🔴 每次测量前**必须自己重置全部筛选维度**（resetAll）：
 *   页面在多次 eval 之间**保留状态**（我把 #ct 留成 _none，下一次 eval 的 view 就是 3401 而不是 3887）。
 *   不重置的话，同一份脚本「刷新后跑」和「连跑两次」结果不同 —— 自动化检查最忌讳这个。
 *   实测踩过：连跑时 move 的「石家庄」被算成 0 条，误判成地市筛选坏了；重载后即 141 条。
 *
 * 返回：JSON 字符串（直接给 python 解析），不抛异常 —— 断言失败只体现在 ok=false。
 */
(function () {
  function num(x) { return (x || []).length; }
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
      ids = ["#kw", "#sc", "#cat", "#ty", "#ct", "#pf", "#gf", "#cf",
             "#bw", "#chx", "#on", "#off", "#chg"];
    }
    for (var i = 0; i < ids.length; i++) {
      var e = $(ids[i]);
      if (e) { e.value = ""; }
    }
    try { if (typeof syncTyOptions === "function") { syncTyOptions(); } } catch (e) {}
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

    var ct = $("#ct");
    o.ctVisible = getComputedStyle(ct).display !== "none";
    o.ctOptionCount = ct.options.length;
    o.ctValues = opts("#ct").map(function (x) { return x.value; });
    o.ctChoosable = opts("#ct").filter(function (x) {
      return !x.disabled && x.value && x.value !== "_none";
    }).map(function (x) { return x.value; });
    /* 选项文本带条数（「石家庄（19）」）—— 顺带把「有值但条数写 0」这种自相矛盾捞出来：
       它说明建选项时用的口径与置灰时用的口径不是同一个。 */
    o.ctZeroText = opts("#ct").filter(function (x) {
      return !x.disabled && /（0）$/.test(String(x.textContent || ""));
    }).map(function (x) { return x.value; });

    var rs = rowsOf(NET);
    o.rows = rs.length;
    o.ctyRows = rs.filter(function (d) { return num(d.cty); }).length;

    o.tabs = [].slice.call(document.querySelectorAll("#tabs button")).map(function (b) {
      return { t: b.textContent, disabled: !!b.disabled };
    });

    // 幽灵塞值：维度隐藏时手工塞一个地市值，view 必须**不受影响**
    if (!o.ctVisible) {
      var before = view.length;
      $("#ct").value = "邢台";
      if ($("#kw")) { $("#kw").value = ""; }
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
      o.ghostCityFilterRows = view.length;
      o.ghostUnaffected = (view.length === before);
      resetAll();
    }

    // 地市筛选真实生效性（仅 ctVisible 时）
    // 🔴 必须挑一个**非空、且没被置灰**的地市值：
    //    · 选项 0 是「全部城市」('')、选项 1 是 _none —— 拿它们测等于没测（都是「不筛」）；
    //    · 置灰的档在 apply() 里有保险丝（当没筛）⇒ 拿它测会得到「未收窄」的假失败，
    //      而那不是筛选坏了，是本网真的没有这个地市（实测踩过：差点误判成地市筛选失效）。
    // 🔴🔴 但「可点」还不够 —— 样本必须**在当前页签下真有条目**（本页签默认是「在售」）。
    //    联通整批地市全是已下架（石家庄 19 / 邢台 9 / 承德 7 在「在售」下恒为 0），
    //    而它们的下拉档按**全量**建、不算置灰 ⇒ 拿第一个可点档当样本会得到
    //    「地市 石家庄 筛出 0 条」的**假失败**，看着像地市筛选坏了（2026-09-24 实测踩到）。
    //    ⇒ 逐个试，取第一个筛出 > 0 的；一个都没有才判失败（那才是真筛不动）。
    /* 🔴🔴 与数据的对账口径（2026-09-24 改）：**不再用「结果必须收窄」当判据**。
       口径已改成「选某个市 = 不限地市 ＋ 该市专属」，而本网常常绝大部分都是不限地市
       （联通在售 4820 条里 4775 条不限地市）⇒ 选一个市**完全可以不收窄**，
       「未收窄」不再是失效的证据，反而是正确行为。旧判据会把正确结果判成失败。
       现在改成与数据**精确对账**：页面条数 == 数据里「pw 或 cty 含该市」的条数。
       这比「收窄了」强得多 —— 收窄只证明筛掉了点东西，证明不了筛对了。 */
    if (o.ctVisible && o.ctValues.length) {
      var real = o.ctChoosable;
      if (real.length) {
        var picked = null, tried = [], badCity = [];
        for (var ci = 0; ci < real.length; ci++) {
          $("#ct").value = real[ci];
          try { clearChips(); } catch (e) {}
          try { apply(); } catch (e) {}
          var cw = wantBy(function (d) {
            return !!d.pw || (d.cty || []).indexOf(real[ci]) >= 0;
          });
          var rec = { city: real[ci], rows: view.length, want: cw };
          tried.push(rec);
          if (rec.rows !== rec.want) { badCity.push(rec); }
          if (view.length > 0 && !picked) { picked = rec; }
        }
        o.cityFilterTried = tried;
        o.cityFilterBad = badCity;
        o.cityFilterSample = picked || (tried.length
          ? { error: "所有可选地市在本页签下都筛出 0 条（试了 " + tried.length + " 个）" }
          : null);
      } else {
        o.cityFilterSample = { error: "没有可用的非空地市值" };
      }
      resetAll();
    }

    /* ★★ 「已下架」页签下地市维度必须**整体禁用** —— 已下架条目没有地市归属
       （上游「停售目录」是各城各自的遗留清单，同一条停售资费被哪些城市收录不固定，
       构建期刻意不写 cty）。不禁用的话，用户选中某个市会得到 0 条，
       而那会被读成「这个市没有下架资费」—— 真相是「这个维度对该批数据不存在」。
       顺带记下：此刻按地市筛不该生效（无效筛选 = 不筛）。 */
    if ((o.tabs || []).some(function (t) { return /已下架/.test(t.t) && !t.disabled; })) {
      try {
        STA = "1"; syncTabs();
        if ($("#ct")) { $("#ct").value = ""; }
        try { clearChips(); } catch (e) {}
        apply();
        o.rowsOnStop = view.length;
        var ctEl = $("#ct");
        o.ctDisabledOnStop = !!(ctEl && ctEl.disabled);
        /* 再塞一个具体地市值：禁用态下它必须**不生效**（否则又会给出 0 条的假答案） */
        if (ctEl && o.ctChoosable.length) {
          ctEl.value = o.ctChoosable[0];
          try { clearChips(); } catch (e) {}
          apply();
          o.stopTabCityRows = view.length;
        }
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
    o.gfSample = { rows: view.length, want: wantBy(function (d) { return d.g != null && d.g >= 0 && d.g <= 5; }) };
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

  /* 每网的期望。★ `city` 的判据是「本网数据里有没有条目级地市归属」，与页面同源。
     🔴 unicom 从 false 改 true（2026-09-24）：此前它被归到「没有地市维度」是**错的** ——
       那是「采集侧只采了邢台一城」造成的假阴性，而页面又信了数据源的 allProvince 声明，
       于是整个维度被藏起来。现在联通按 12 城并集采集、逐条带回城市归属。
       注意这条期望对**旧快照**会误报（老快照里没有城市字段，页面正确地隐藏了下拉）——
       所以本地跑之前要保证页面是用 12 城并集数据重建的。 */
  var EXPECT = {
    move:    { city: true,  stopped: false },
    telecom: { city: true,  stopped: false },
    unicom:  { city: true,  stopped: true  },
    cbn:     { city: false, stopped: true  }
  };

  function check(o, exp) {
    var bad = [];
    if (o.error) { return ["页面异常: " + o.error]; }
    if (!(o.rows > 0)) { bad.push("row=0"); }
    if (!o.scOptions.length) { bad.push("无地域档位"); }
    else if (!o.scChoosable.length) { bad.push("地域两档都不可选"); }

    // 期望：有地市归属 ⇒ #ct 显示 + 能筛；无 ⇒ 隐藏 + 塞值无效
    if (exp.city && !o.ctVisible) { bad.push("期望有地市筛选，实际隐藏"); }
    if (!exp.city && o.ctVisible) { bad.push("期望隐藏地市筛选，实际显示"); }
    if (!exp.city && o.ghostUnaffected === false) { bad.push("幽灵地市值生效了(严重)"); }
    if (exp.city) {
      var s = o.cityFilterSample || {};
      if (!s.city) { bad.push("地市筛选未取到非空样本（可点的地市档全是 0 条？）"); }
      else if (!(s.rows > 0)) { bad.push("地市 " + s.city + " 筛出 0 条"); }
      /* ★ 每个可点地市档都要与数据精确对上（口径 = 不限地市 ＋ 该市专属）。
         ⚠️ 不能用「结果必须收窄」当判据：本网常常绝大部分都是不限地市，
            选一个市**不收窄是正确行为**（见 snap() 里的注释）。 */
      if ((o.cityFilterBad || []).length) {
        bad.push("地市档条数与数据不符（口径应为「不限地市 ＋ 该市专属」）："
          + o.cityFilterBad.slice(0, 4).map(function (r) {
              return r.city + " 页面" + r.rows + "≠数据" + r.want;
            }).join(" / "));
      }
      // 选项上写着「（N）」而有值的没被置灰的项，N 必须 > 0 —— 否则建选项与置灰两处口径不一致
      if ((o.ctZeroText || []).length) {
        bad.push("地市选项写着 0 条却没置灰：" + o.ctZeroText.join("/"));
      }
    }
    /* ★ 「已下架」页签下：地市维度必须禁用，且塞进去的地市值必须**不生效**
       （已下架条目没有地市归属，见 snap() 的注释）。 */
    if (exp.stopped) {
      if (o.stopTabErr) { bad.push("已下架页签操作异常: " + o.stopTabErr); }
      else {
        if (o.ctDisabledOnStop === false) {
          bad.push("「已下架」页签下地市维度没被禁用（选地市会给出 0 条这种假答案）");
        }
        if (o.stopTabCityRows != null && o.stopTabCityRows !== o.rowsOnStop) {
          bad.push("「已下架」页签下地市值竟然生效了："
            + o.stopTabCityRows + " ≠ " + o.rowsOnStop);
        }
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
