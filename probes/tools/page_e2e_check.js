/* 页面级端到端断言（在真实 Chrome 里跑，不是 jsdom）。
 *
 * 用法：
 *   python probes/tools/cdp_launch.py
 *   python probes/tools/ct_browser/cdp_desktop_step.py nav "file:///<...>/cloud/tariff/docs/index.html"
 *   python probes/tools/ct_browser/cdp_desktop_step.py eval probes/tools/page_e2e_check.js out.json
 *
 * 断言四网各自的关键 UI 行为：
 *   · 地域筛选 #sc 的档位与置灰（只有一档地域的网，另一档必须置灰）
 *   · 地市筛选 #ct 的**显隐**（上游没有地市级数据的网必须隐藏，而不是置灰）
 *   · 地市筛选**真的能筛**（选一个真实地市 ⇒ 结果收窄且 > 0）
 *   · 幽灵塞值：隐藏维度手工塞值必须**不生效**（否则等于拿不存在的维度筛）
 *   · 已下架页签的可用性与条数
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

  /* 把界面恢复成「刚打开」：所有筛选维度清空 + 回到「在售」页签。
     不假设调用方已经刷新页面 —— 这个脚本要能连跑多次、且与执行顺序无关。 */
  function resetAll() {
    var ids = ["#kw", "#ty", "#ct", "#pf", "#bw", "#chg", "#sc", "#on", "#off"];
    for (var i = 0; i < ids.length; i++) {
      var e = $(ids[i]);
      if (e) { e.value = ""; }
    }
    try { if (typeof STA !== "undefined") { STA = "0"; } } catch (e) {}
    try { if (typeof pg !== "undefined") { pg = 0; } } catch (e) {}
    try { clearChips(); } catch (e) {}
    try { syncTabs(); } catch (e) {}
    try { apply(); } catch (e) {}
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
    o.allProvince = !!n.allProvince;
    o.sub = txt($("#sub")).slice(0, 170);

    var sc = $("#sc");
    o.scOptions = [].map.call(sc.options, function (x) {
      return { v: x.value, t: x.text, disabled: !!x.disabled };
    });
    o.scChoosable = o.scOptions.filter(function (x) { return !x.disabled; }).map(function (x) { return x.v; });

    var ct = $("#ct");
    o.ctVisible = getComputedStyle(ct).display !== "none";
    o.ctOptionCount = ct.options.length;
    o.ctValues = [].map.call(ct.options, function (x) { return x.value; });

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
    // 🔴 必须挑一个**非空**的地市值：选项 0 是「全部城市」('')、选项 1 是 _none，
    //    拿它们测等于没测（都是「不筛」的语义）。
    if (o.ctVisible && o.ctValues.length) {
      var real = o.ctValues.filter(function (v) { return v && v !== "_none"; });
      if (real.length) {
        var first = real[0];
        $("#ct").value = first;
        try { clearChips(); } catch (e) {}
        try { apply(); } catch (e) {}
        o.cityFilterSample = { city: first, rows: view.length, narrowed: view.length < o.rows };
      } else {
        o.cityFilterSample = { error: "没有可用的非空地市值" };
      }
      resetAll();
    }
    return o;
  }

  var EXPECT = {
    move:    { city: true,  stopped: false },
    telecom: { city: true,  stopped: false },
    unicom:  { city: false, stopped: true  },
    cbn:     { city: false, stopped: true  }
  };

  function check(o, exp) {
    var bad = [];
    if (o.error) { return ["页面异常: " + o.error]; }
    if (!(o.rows > 0)) { bad.push("row=0"); }
    if (!o.scOptions.length) { bad.push("无地域档位"); }
    else if (!o.scChoosable.length) { bad.push("地域两档都不可选"); }

    // 期望：有地市级 ⇒ #ct 显示 + 能筛；无 ⇒ 隐藏 + 塞值无效
    if (exp.city && !o.ctVisible) { bad.push("期望有地市筛选，实际隐藏"); }
    if (!exp.city && o.ctVisible) { bad.push("期望隐藏地市筛选，实际显示"); }
    if (!exp.city && o.ghostUnaffected === false) { bad.push("幽灵地市值生效了(严重)"); }
    if (exp.city) {
      var s = o.cityFilterSample || {};
      if (!s.city) { bad.push("地市筛选未取到非空样本"); }
      else if (!(s.rows > 0)) { bad.push("地市 " + s.city + " 筛出 0 条"); }
      else if (s.narrowed === false) { bad.push("地市 " + s.city + " 未收窄结果（筛选失效？）"); }
    }

    // 下架页签
    var stop = (o.tabs || []).filter(function (t) { return /已下架/.test(t.t); })[0];
    if (!stop) { bad.push("找不到「已下架」页签"); }
    else if (exp.stopped && stop.disabled) { bad.push("期望已下架页签可用，实际置灰"); }
    else if (!exp.stopped && !stop.disabled) { bad.push("期望已下架页签置灰（本网取不到下架），实际可用"); }
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
