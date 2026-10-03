(async function () {
  var P = 'provCode=1000000037';
  function g(u) { return fetch('/bss/tariffZone/' + u, { credentials: 'include' }).then(function (r) { return r.json(); }); }
  var tree = (await g('newTarifZone12List.do?' + P)).dataObject || [];
  var out = [];
  for (var i = 0; i < tree.length; i++) {
    var x = tree[i];
    var r1 = await g('newTarifZone3Title.do?lable1Id=' + x.lable1Id + '&' + P);
    var d1 = r1.dataObject;
    var row = { l1: x.lable1Name, l1id: x.lable1Id, direct: Array.isArray(d1) ? d1.length : d1, directKeys: (Array.isArray(d1) && d1[0]) ? Object.keys(d1[0]) : null };
    out.push(row);
    for (var k = 0; k < (x.lable2List || []).length; k++) {
      var y = x.lable2List[k];
      var r2 = await g('newTarifZone3Title.do?lable1Id=' + x.lable1Id + '&lable2Id=' + y.lable2Id + '&' + P);
      var d2 = r2.dataObject;
      row.has2 = row.has2 || {};
      row.has2[y.lable2Name] = Array.isArray(d2) ? d2.length : JSON.stringify(d2).slice(0, 80);
      if (Array.isArray(d2) && d2[0] && !row.entrySample) row.entrySample = JSON.stringify(d2[0]).slice(0, 1200);
    }
  }
  return JSON.stringify(out, null, 1).slice(0, 9000);
})()
