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
  const probe = await post('tariffSectionQuery', {sessionid: sid, type: 1, provCode: PC, lable1Id: l1[0].id});
  const rc = probe.responseContent || {};
  const info = {
    treeOk: probe.headerInfo && probe.headerInfo.code,
    rcKeys: Object.keys(rc),
    listLen: (rc.list || rc.tariffList || rc.zoneTitleList || []).length,
    sample: JSON.stringify(rc).slice(0, 1200),
    l1: l1.map(x => ({id: x.id, name: x.name, tow: x.lableTow.map(t => t.name)}))
  };
  return JSON.stringify(info);
})()
