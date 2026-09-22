// 电信「资费专区」真实浏览器采集（河北 provCode=609906）。
// 前置：真实 Chrome --remote-debugging-port=9223（不带 --enable-automation，
// navigator.webdriver 必须为 false，否则被瑞数 WAF 400 拒），CDP 导航到
// https://www.189.cn/wapportalweb/rateZone/index.html?provCode=609906 过挑战后，
// 用 cdp_desktop_step.py eval 本文件 → 结果落 .ct_raw.json（cloud/tariff/）。
// 详见 probes/he_ct_tariff.py 与 cloud/tariff/ct_monitor.py 模块头。
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
  return JSON.stringify(out);
})()
