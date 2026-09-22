/* 页面级端到端断言（在真实 Chrome 里跑，不是 jsdom）。
 *
 * 用 probes/tools/cdp_launch.py 起 Chrome，再用
 * probes/tools/ct_browser/cdp_desktop_step.py eval 本文件，
 * 目标页 = 本地的 cloud/tariff/docs/index.html（CI 产物的未压缩版）。
 *
 * 断言四网各自的关键 UI 行为：
 *   · 地域筛选 #sc 的档位与置灰（只有一档地域的网，另一档必须置灰）
 *   · 地市筛选 #ct 的**显隐**（上游没有地市级数据的网必须隐藏，而不是置灰）
 *   · 幽灵塞值：隐藏维度手工塞值必须**不生效**（否则等于拿不存在的维度筛）
 *   · 已下架页签的可用性与条数
 *
 * 返回：JSON 字符串（直接给 python 解析），不抛异常 —— 断言失败只体现在 ok=false。
 */
(function () {
  var OUT = {};

  function num(x) { return (x || []).length; }
  function txt(el) { return String((el && el.textContent) || ""); }

  function snap(netCode) {
    var o = { net: netCode };
    if (typeof NET === "undefined" || typeof net !== "function") {
      o.error = "页面未加载完（NET/net 不存在）";
      return o;
    }
    NET = netCode;
    try { clearChips(); } catch (e) {}
    try { renderNav(); } catch (e) {}
    try { renderNet(); } catch (e) {}
    try { apply(); } catch (e) {}

    var n = net() || {};
    o.netname = n.nm || "";
    o.allProvince = !!n.allProvince;
    o.sub = txt($("#sub")).slice(0, 170);

    // 地域档位
    var sc = $("#sc");
    o.scOptions = [].map.call(sc.options, function (x) {
      return { v: x.value, t: x.text, disabled: !!x.disabled };
    });
    o.scChoosable = o.scOptions.filter(function (x) { return !x.disabled; }).map(function (x) { return x.v; });

    // 地市维度
    var ct = $("#ct");
    o.ctVisible = getComputedStyle(ct).display !== "none";
    o.ctOptionCount = ct.options.length;
    o.ctValues = [].map.call(ct.options, function (x) { return x.value; });
    o.ctValueNow = ct.value;

    var rs = rowsOf(NET);
    o.rows = rs.length;
    o.ctyRows = rs.filter(function (d) { return num(d.cty); }).length;

    // 下架页签
    var tabs = [].slice.call(document.querySelectorAll("#tabs button"));
    o.tabs = tabs.map(function (b) {
      return { t: b.textContent, disabled: !!b.disabled };
    });

    // 幽灵塞值：维度隐藏时手工塞一个地市值，view 必须**不受影响**
    if (!o.ctVisible) {
      var before = view.length;
      $("#ct").value = "邢台";
      if ($("#kw")) $("#kw").value = "";
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
      o.ghostCityFilterRows = view.length;
      o.ghostUnaffected = (view.length === before);
      $("#ct").value = "";
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
    }

    // 地市筛选真实生效性（仅 ctVisible 时）
    if (o.ctVisible && o.ctValues.length) {
      var first = o.ctValues[0];
      $("#ct").value = first;
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
      o.cityFilterSample = { city: first, rows: view.length };
      $("#ct").value = "";
      try { clearChips(); } catch (e) {}
      try { apply(); } catch (e) {}
    }
    return o;
  }

  function check(o, exp) {
    var bad = [];
    if (o.error) { return ["页面异常: " + o.error]; }
    if (o.rows <= 0) { bad.push("row=0"); }
    if (!o.scOptions.length) { bad.push("无地域档位"); }
    // 期望：有地市级 ⇒ #ct 显示；无 ⇒ 隐藏
    if (exp.city && !o.ctVisible) { bad.push("期望有地市筛选，实际隐藏"); }
    if (!exp.city && o.ctVisible) { bad.push("期望隐藏地市筛选，实际显示"); }
    // 隐藏维度塞值必须不生效
    if (!exp.city && o.ghostUnaffected === false) { bad.push("幽灵地市值生效了(严重)"); }
    // 下架页签
    if (exp.stopped) {
      var anyStop = o.tabs.some(function (t) { return /已下架/.test(t.t) && !t.disabled; });
      if (!anyStop) { bad.push("已下架页签不可用"); }
    }
    // 单档地域必须置灰另一档
    if (o.scChoosable.length === 1) {
      // 正常：另一档 disabled
    } else if (o.scChoosable.length === 0) {
      bad.push("地域两档都不可选");
    }
    return bad;
  }

  var EXPECT = {
    move:    { city: true,  stopped: false },
    telecom: { city: true,  stopped: false },
    unicom:  { city: false, stopped: true  },
    cbn:     { city: false, stopped: true  }
  };

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
