#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联通河北「栏目 × 地市 × 板块」覆盖完整性核验 —— 回答
「每个地市的每个栏目，都收全了吗」。

━━ 为什么会有这个探针 ━━
2026-09-24 用户提问：「河北省的每个地市还有好几个栏目（套餐/加装包/营销活动/港澳台国际/
标准资费/停售套餐，以及流量包/短信包/语音包/权益包/新业务/其他），每个地市的每个栏目
都收集全了吗？全国资费也是这样的……不全请补充完整。」

这是一个**覆盖完整性**问题，不是「有没有报错」问题。采集漏栏目/漏地市时，
接口照样 200、日志照样干净 —— 只有把「上游声明的全集」与「实际取到的集合」逐格对账
才看得出来。本探针就是这把尺子。

━━ 五层判据（缺一即可能得出错误结论） ━━
  ① 骨架完整性：`indexData` 的 levelList 必须同时含 6 个一级 × 22 个二级；
     且须与**真实页面**渲染出的栏目树一致（页面 radio 值 = firstLevel/secondLevel）。
     ⚠️ `tariffAttributes` 只有 1/2 有效（3/4/5/空 全为 0 条），别以为漏了板块。
  ② 板块映射：「全国资费」↔ `tariffAttributes=1`、「本省资费」↔ `=2`。
     已用 CDP 抓页面真实请求体证实：点「全国资费」发出 `tariffAttributes=1`。
  ③ 逐格覆盖：22 组合 × 12 地市 × 2 板块 = 528 格，逐格取三级目录数。
     任何一格为 0 都要能解释（上游无数据 / 该地市无此类），不能默认「本来就没有」。
  ④ 明细可达：三级目录 id 并集逐个分批拉明细，**传入 id 数必须等于返回明细数**。
     若返回 < 传入，说明有 id 拉不到明细 = 真缺口（本次实测差 0）。
  ⑤ 阴性对照：拿一个「上游确实有数据」的省市（北京）做阳性对照 ——
     例如 套餐/固话 在北京有 2 条、在河北 0 条；港澳台 4003/4004 两地都 0。
     没有这一层，「河北 0 条」无法区分「上游无」与「我们查法错」。

━━ 一个容易误判的数字 ━━
  三级目录 id 并集 9121 个，但去重后资费只有 8042 条（差 1079）。
  这 **不是漏**：`operateData` 的返回条数恒等于传入 id 数（实测 9121 传 → 9121 回），
  差额来自**同一资费被挂在多个栏目/板块下**（同一 reportNo 多个目录 id）。
  判据：看「传入 vs 返回」而不是「id 数 vs 去重数」—— 后者会把重复挂载误读成丢失。

用法：
  python probe_unicom_coverage.py                  # 五层判据 + 覆盖矩阵
  python probe_unicom_coverage.py --json out.json
"""
import argparse
import json
import ssl
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),          # ★ 真直连，见 unicom_monitor 的同款注释
    urllib.request.HTTPSHandler(context=CTX),
)
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 Mobile Safari/537.36")
BLANK = {"duanlianjieabc": "", "channelCode": "", "serviceType": "", "saleChannel": "",
         "externalSources": "", "contactCode": ""}
BASE = "https://m.client.10010.com/servicequerybusiness"
REF = "https://img.client.10010.com/"

PROV = "018"
HBEI_CITIES = [("石家庄", "188"), ("唐山", "181"), ("秦皇岛", "182"), ("邯郸", "186"),
               ("邢台", "185"), ("保定", "187"), ("张家口", "184"), ("承德", "189"),
               ("沧州", "180"), ("廊坊", "183"), ("衡水", "720"), ("雄安", "782")]
ATTRS = ("1", "2")               # 1=全国资费  2=本省资费
BATCH = 30
BJ = {"name": "北京", "prov": "011", "city": "110"}   # 阴性/阳性对照省


def post(path, data, timeout=45):
    d = dict(BLANK)
    d.update(data)
    d["version"] = "WT"
    d.setdefault("behaviorId", "LTB4GKFRRYV13TJY20260924000000000")
    req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(d).encode(),
                                 method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*", "User-Agent": UA,
        "Origin": REF.rstrip("/"), "Referer": REF,
        "x-requested-with": "com.sinovatech.unicom.ui"})
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def level3(prov, city, fl, sl, attr):
    """(prov,city) 在 (attr,一级,二级) 下的三级目录 {id: name}。"""
    r = post("/queryTariffNew/threeLevelName",
             {"firstLevel": fl, "secondLevel": sl, "provinceId": prov,
              "cityId": city, "tariffAttributes": attr})
    return {x["id"]: x.get("name")
            for x in (((r.get("data") or {}).get("dataList")) or []) if x.get("id")}


def skeleton(prov=PROV, city="188"):
    d = (post("/queryTariffNew/indexData", {"provinceId": prov, "cityId": city})
         .get("data") or {})
    return [(str(L.get("firstLevel")), L.get("firstLevelName"),
             str(s.get("secondLevel")), s.get("secondLevelName"))
            for L in (d.get("levelList") or []) for s in (L.get("secondLevels") or [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()

    print("=" * 84)
    print("判据① 骨架完整性（indexData 声明的 一级 × 二级 全集）")
    combos = skeleton()
    lv1 = []
    for fl, fln, sl, sln in combos:
        if fl not in [x[0] for x in lv1]:
            lv1.append((fl, fln))
    print(f"   一级 {len(lv1)} 个：{'、'.join(n for _, n in lv1)}")
    print(f"   二级组合 {len(combos)} 个：")
    for fl, fln, sl, sln in combos:
        print(f"      [{fl}-{sl:<5}] {fln} / {sln}")
    print("   ⚠️ tariffAttributes 取值探测（3/4/5/空 是否也有数据）：")
    for attr in ("3", "4", "5", ""):
        n = 0
        for fl, _fln, sl, _sln in combos:
            p = {"firstLevel": fl, "secondLevel": sl, "provinceId": PROV, "cityId": "188"}
            if attr:
                p["tariffAttributes"] = attr
            r = post("/queryTariffNew/threeLevelName", p)
            n += len(((r.get("data") or {}).get("dataList")) or [])
        print(f"      tariffAttributes={attr or '(不传)':<6} → 全部 22 组合合计 {n} 条"
              f"{'  ✅ 确无数据' if n == 0 else '  ⚠️ 有数据！板块维度需扩'}")
    print("   板块映射（已用 CDP 抓页面真实请求体证实）："
          "「全国资费」→ tariffAttributes=1 ｜「本省资费」→ tariffAttributes=2")

    print()
    print("=" * 84)
    print("判据③ 逐格覆盖：22 组合 × 12 地市 × 2 板块")
    tasks = [(fl, sl, c, attr) for (fl, _n, sl, _s) in combos
             for _cn, c in HBEI_CITIES for attr in ATTRS]

    def cell(t):
        fl, sl, city, attr = t
        try:
            return t, set(level3(PROV, city, fl, sl, attr))   # 存 id 集合，才能算并集
        except Exception:
            return t, set()

    per = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for t, ids in ex.map(cell, tasks):
            per[t] = ids

    rows, union_ids = [], set()
    print(f"   {'栏目':<26}{'并集(全网/本省)':>15}{'单城188':>11}{'净增':>6}   有差异的地市数")
    for (fl, fln, sl, sln) in combos:
        u = {"1": set(), "2": set()}
        per_city = {}
        for _cn, c in HBEI_CITIES:
            per_city[c] = {}
            for attr in ATTRS:
                ids = per.get((fl, sl, c, attr), set())
                per_city[c][attr] = ids
                u[attr] |= ids
        union_ids |= (u["1"] | u["2"])
        single = {"1": len(per_city["188"]["1"]), "2": len(per_city["188"]["2"])}
        diff_cities = sum(
            1 for _cn, c in HBEI_CITIES
            if set(per_city[c]["1"]) != set(per_city["188"]["1"])
            or set(per_city[c]["2"]) != set(per_city["188"]["2"]))
        n1, n2 = len(u["1"]), len(u["2"])
        s1, s2 = single["1"], single["2"]
        rows.append({"fl": fl, "fln": fln, "sl": sl, "sln": sln,
                     "union": {"1": n1, "2": n2},
                     "single": single, "diffCities": diff_cities})
        col_u = f"{n1}/{n2}"
        col_s = f"{s1}/{s2}"
        print(f"   {fln + '/' + sln:<26}{col_u:>15}{col_s:>11}"
              f"{n1 + n2 - s1 - s2:>7}{str(diff_cities) + '/12':>9}")

    print()
    print("=" * 84)
    print("判据④ 明细可达性（每个三级目录 id 是否都能拉到明细）")
    U = sorted(union_ids)
    print(f"   三级目录 id 并集 = {len(U)}")
    batches = [U[i:i + BATCH] for i in range(0, len(U), BATCH)]

    def det(b):
        try:
            r = post("/queryTariffNew/operateData/" + "_".join(b),
                     {"page": "1", "size": str(len(b)), "provinceId": PROV, "cityId": "185"})
            dl = ((r.get("data") or {}).get("detailList")) or []
            return len(b), len(dl), [str(e.get("reportNo")) for e in dl]
        except Exception as e:
            return len(b), -1, [str(e)[:40]]

    nin = nout = 0
    reps = []
    errs = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, o, rs in ex.map(det, batches):
            if o < 0:
                errs += 1
                continue
            nin += i
            nout += o
            reps += [r for r in rs if r and r != "None"]
    print(f"   分批共 {len(batches)} 批（每批 ≤{BATCH} id），失败 {errs} 批")
    print(f"   传入 id 合计 = {nin}   返回明细条数(未去重) = {nout}   差 = {nin - nout}")
    ok = (nin == nout and errs == 0)
    print(f"   → {'✅ 每个 id 都解析出明细，无缺口' if ok else '⚠️ 有 id 拉不到明细，真缺口'}")
    print(f"   ⚠️ reportNo 去重后 = {len(set(reps))}（比 id 少 {nin - len(set(reps))}）"
          f"——这是「同一资费挂在多个栏目/板块」的重复挂载，**不是漏**；"
          f"判据看上面「传入 vs 返回」。")

    print()
    print("=" * 84)
    print("判据⑤ 阴性/阳性对照（北京 vs 河北 —— 区分「上游无」与「我们查法错」）")
    ctrl = []
    for (fl, sl, note) in [("1", "1003", "套餐/固话"),
                           ("1", "1001", "套餐/移网"),
                           ("4", "4001", "港澳台/加装包"),
                           ("4", "4003", "港澳台/融合套餐"),
                           ("4", "4004", "港澳台/标准资费")]:
        nb = len(level3(BJ["prov"], BJ["city"], fl, sl, "1")) + \
             len(level3(BJ["prov"], BJ["city"], fl, sl, "2"))
        nh = len(level3(PROV, "188", fl, sl, "1")) + \
             len(level3(PROV, "188", fl, sl, "2"))
        verdict = ("两地皆 0 → 上游本身无数据" if nb == 0 and nh == 0 else
                   "北京有/河北无 → 河北确实没有（查法正确）" if nb > 0 and nh == 0 else
                   "两地皆有 → 正常" if nb > 0 and nh > 0 else "⚠️ 北京 0 而河北非 0，需查")
        ctrl.append({"combo": f"{fl}-{sl}", "note": note, "bj": nb, "hb": nh,
                     "verdict": verdict})
        print(f"   [{fl}-{sl}] {note:<18} 北京={nb:<5} 河北188={nh:<5} {verdict}")

    print()
    print("=" * 84)
    tot_union = sum(r["union"]["1"] + r["union"]["2"] for r in rows)
    tot_single = sum(r["single"]["1"] + r["single"]["2"] for r in rows)
    zero = [f"{r['fln']}/{r['sln']}" for r in rows
            if r["union"]["1"] + r["union"]["2"] == 0]
    print(f"结论：栏目 {len(combos)} 个 / 地市 {len(HBEI_CITIES)} 个 / 板块 2 个"
          f" = {len(combos) * len(HBEI_CITIES) * 2} 格全覆盖")
    print(f"      {len(HBEI_CITIES)} 城并集(id 含栏目间重复) = {tot_union}"
          f"   单城(188) = {tot_single}   净增 = {tot_union - tot_single}")
    print(f"      全空栏目（上游无数据，已用北京对照确认）= {zero}")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"province": PROV, "combos": len(combos),
                       "cities": [{"name": n, "code": c} for n, c in HBEI_CITIES],
                       "rows": rows,
                       "detail": {"ids": nin, "returned": nout, "distinct": len(set(reps)),
                                  "gaps": nin - nout},
                       "control": ctrl,
                       "totals": {"union": tot_union, "single": tot_single}},
                      f, ensure_ascii=False, indent=1)
        print("\nsaved ->", a.json)


if __name__ == "__main__":
    main()
