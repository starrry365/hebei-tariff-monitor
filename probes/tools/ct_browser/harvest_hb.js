// 电信「资费专区」真实浏览器采集（河北 provCode=609906 + 集团 1000000037）。
// 前置：真实 Chrome --remote-debugging-port=9223（不带 --enable-automation，
// navigator.webdriver 必须为 false，否则被瑞数 WAF 400 拒），CDP 导航到
// https://www.189.cn/wapportalweb/rateZone/index.html?provCode=609906 过挑战后，
// 用 cdp_desktop_step.py eval 本文件 → 结果落 .ct_raw.json（cloud/tariff/）。
// 详见 probes/he_ct_tariff.py 与 cloud/tariff/ct_monitor.py 模块头。
//
// ① 省级（provCode=609906）：POST tariffSection.do，AES 包体，结构化字段。
// ② 集团「资费公示」页签（provCode=1000000037，2026-10-03 补采）：另一族
//    **无加密 GET** 接口 —— /bss/tariffZone/newTarifZone12List.do（目录，
//    dataObject[].lable1Id/lable1Name/lable2List）+ newTarifZone3Title.do
//    （明细，dataObject[] 直出，**无 count 字段**）。条目是 HTML 详情族
//    （name/jbxx/ffnr/other_content/report_no），归一化见 ct_monitor._normalize_jt。
//    细分（lable2）不在条目字段里 ⇒ 对每个 lable2Id 再发一轮 3Title，
//    用 report_no 反查建 l2map（国际 280 条里只有 46 条、加装包 239 里只有 32
//    条有二级归属，其余细分留空是诚实的）。整块 try/catch：集团挂了不许连累省级。
(async () => {
  const EP = 'https://www.189.cn/wapportalweb/wapportalweb/tariffSection.do';
  const enc = (plain) => CryptoJS.AES.encrypt(
    CryptoJS.enc.Utf8.parse(plain),
    CryptoJS.enc.Utf8.parse('telecom_wap_2018'),
    {mode: CryptoJS.mode.ECB, padding: CryptoJS.pad.Pkcs7}).toString();
  const tmpl = (fc, rc) => '{"headerInfo": { "functionCode": "' + fc + '"},"requestContent":' + rc + '}';
  const post = async (fc, rc) => {
    const r = await fetch(EP, {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: enc(tmpl(fc, JSON.stringify(rc))), credentials: 'include'
    });
    return r.json();
  };
  const PC = '609906';
  const tree = await post('tariffSectionHome', {ticket: '', sessionid: '', provCode: PC});
  const sid = tree.responseContent.sessionid;
  const l1 = tree.responseContent.lableOneList;
  const out = {
    fetchedAt: new Date().toISOString(),
    provCode: PC, provName: '河北',
    sessionid: sid,
    lableOneList: l1.map(x => ({id: x.id, name: x.name, lableTow: x.lableTow})),
    sections: {}, codes: {}
  };
  for (const l of l1) {
    const q = await post('tariffSectionQuery', {sessionid: sid, type: 1, provCode: PC, lable1Id: l.id});
    out.codes[l.name] = q.headerInfo && q.headerInfo.code;
    const rc = q.responseContent || {};
    out.sections[l.name] = {
      count: rc.zoneTitleListCount,
      len: (rc.zoneTitleList || []).length,
      zoneTitleList: rc.zoneTitleList || []
    };
  }
  // ══ 集团资费公示（另族 GET 接口，见文件头注释 ②）═════════════════════
  try {
    const JT_PC = '1000000037';
    const jt = {provCode: JT_PC, tree: [], sections: {}, codes: {}, l2map: {}};
    const g = (u) => fetch(u, {credentials: 'include'}).then(r => r.json());
    const tt = await g('/bss/tariffZone/newTarifZone12List.do?provCode=' + JT_PC);
    jt.tree = (tt.dataObject || []).map(x => ({
      lable1Id: x.lable1Id, lable1Name: x.lable1Name,
      lable2List: (x.lable2List || []).map(y => ({lable2Id: y.lable2Id, lable2Name: y.lable2Name}))
    }));
    for (const x of jt.tree) {
      const r = await g('/bss/tariffZone/newTarifZone3Title.do?lable1Id=' + x.lable1Id + '&provCode=' + JT_PC);
      jt.codes[x.lable1Name] = r.code;
      const arr = r.dataObject || [];
      jt.sections[x.lable1Name] = {len: arr.length, zoneTitleList: arr};
      // 二级归属反查：lable2Id 过滤拉小份，report_no → 二级名
      for (const y of x.lable2List) {
        const r2 = await g('/bss/tariffZone/newTarifZone3Title.do?lable1Id=' + x.lable1Id
                           + '&lable2Id=' + y.lable2Id + '&provCode=' + JT_PC);
        (r2.dataObject || []).forEach(e => { jt.l2map[e.report_no || e.id] = y.lable2Name; });
      }
    }
    out.jt = jt;
  } catch (e) { out.jt = {err: String(e)}; }
  return JSON.stringify(out);
})()
