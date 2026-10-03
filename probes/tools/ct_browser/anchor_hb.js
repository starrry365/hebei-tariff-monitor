// 电信采集完整性锚定：type=0 全量枚举 vs 分类轮询并集，reportNo 级对账。
// 前置同 harvest_hb.js（真实 Chrome 9223，页面已过挑战）。
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
  // type=0：忽略 lable1Id，返回全量
  const full = await post('tariffSectionQuery', {sessionid: sid, type: 0, provCode: PC, lable1Id: ''});
  const fl = (full.responseContent || {}).zoneTitleList || [];
  // 分类轮询并集（与 harvest 相同方式再取一遍，同会话）
  const perCat = {};
  const l1 = tree.responseContent.lableOneList;
  for (const l of l1) {
    const q = await post('tariffSectionQuery', {sessionid: sid, type: 1, provCode: PC, lable1Id: l.id});
    perCat[l.name] = ((q.responseContent || {}).zoneTitleList || []);
  }
  const byNo = (list) => {
    const m = {};
    for (const e of list) m[e.reportNo] = e;
    return m;
  };
  const fullM = byNo(fl);
  const unionM = {};
  for (const name of Object.keys(perCat))
    for (const e of perCat[name]) unionM[e.reportNo] = e;
  const onlyFull = Object.keys(fullM).filter(k => !unionM[k]);
  const onlyUnion = Object.keys(unionM).filter(k => !fullM[k]);
  return JSON.stringify({
    fetchedAt: new Date().toISOString(),
    fullCount: (full.responseContent || {}).zoneTitleListCount,
    fullLen: fl.length,
    unionLen: Object.keys(unionM).length,
    sumPerCat: Object.values(perCat).reduce((a, b) => a + b.length, 0),
    perCatCount: Object.fromEntries(Object.entries(perCat).map(([k, v]) => [k, v.length])),
    onlyFull_count: onlyFull.length,
    onlyFull_sample: onlyFull.slice(0, 10).map(k => fullM[k].name),
    onlyUnion_count: onlyUnion.length,
    onlyUnion_sample: onlyUnion.slice(0, 10).map(k => unionM[k].name),
    fullDuplicateReportNo: fl.length - Object.keys(fullM).length
  });
})()
