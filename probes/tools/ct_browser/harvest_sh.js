// 上海电信「资费专区」采集（provCode=600102，2026-10-06 实测 508 条）。
// 与 harvest_hb.js 同一接口族、同一 WAF、同一前置条件（真实 Chrome 过瑞数挑战，
// 见 ci_grab.sh / harvest_hb.js 文件头），差别只有省份代码 —— 上海=600102
// （省码表在页面组件 Index-1f2bc0ae.js 的 provineList 里，按字母分组）。
// 不采集团块（jt）：上海小页只看本省目录，集团那 500+ 条与河北共用即可。
// 用法：cdp_desktop_step.py eval 本文件 [输出路径]（默认 cloud/tariff/.shct_raw.json）
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
  const PC = '600102';
  const tree = await post('tariffSectionHome', {ticket: '', sessionid: '', provCode: PC});
  const sid = (tree.responseContent || {}).sessionid;
  const l1 = (tree.responseContent || {}).lableOneList || [];
  const out = {
    fetchedAt: new Date().toISOString(),
    provCode: PC, provName: '上海',
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
