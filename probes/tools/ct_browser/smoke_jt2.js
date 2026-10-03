(async () => {
  const out = {};
  // ① 移动网：集团公示档应存在但置灰（0 条）
  const sb0 = document.getElementById('sb');
  out.moveJTDisabled = [...sb0.options].find(o => o.value === '集团资费').disabled;
  // ② 切到电信
  location.hash = '#net=telecom&st=0&s=on&d=-1';
  await new Promise(r => setTimeout(r, 1200));
  const sb = document.getElementById('sb');
  out.ctSbOptions = [...sb.options].map(o => o.textContent);
  const jt = [...sb.options].find(o => o.value === '集团资费');
  jt.selected = true;
  jt.dispatchEvent(new Event('change', {bubbles: true}));
  await new Promise(r => setTimeout(r, 900));
  out.statJT = document.getElementById('stat').textContent.slice(0, 70);
  out.chips = document.getElementById('chips').textContent.slice(0, 60);
  // ③ 切河北档对照
  const hb = [...sb.options].find(o => o.value === '本省资费');
  hb.selected = true;
  hb.dispatchEvent(new Event('change', {bubbles: true}));
  await new Promise(r => setTimeout(r, 900));
  out.statHB = document.getElementById('stat').textContent.slice(0, 50);
  // 清筛选
  sb.value = '';
  sb.dispatchEvent(new Event('change', {bubbles: true}));
  await new Promise(r => setTimeout(r, 700));
  out.statAll = document.getElementById('stat').textContent.slice(0, 46);
  return JSON.stringify(out, null, 1);
})()
