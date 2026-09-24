#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联通河北「地市维度」审计探针 —— 四层判据一次跑完，用于回答
「河北联通资费到底分不分城市、要不要按城市采集」。

━━ 为什么会有这个探针（一次真实的误判） ━━
2026-09-24 之前，本项目一直写着「河北联通资费与 cityId 无关，只采一城即可」，
依据是 `he_unicom_tariff.city_drift()` 的抽查结果。用户指出「每个市都有，还有个雄安」
后复测，证明**旧结论是错的**，且错法是典型的**抽样假阴性**：
  · 旧 `city_drift()` 只抽 (套餐/移网) 与 (加装包/权益包) 两个组合 × 3 城；
  · 而这两个组合**恰好全省一致** —— 抽样点全落在「无差异」的那几个维度上；
  · 真正有城市差异的组合（宽带、流量包、短信包、促销、停售套餐…）一个都没抽到。
后果不是报错，是**静默漏数据**：主链路「只采邢台」漏掉 130 条资费，日志里毫无异常。

━━ 四层判据（缺一即可能得出错误结论） ━━
  ① 同城重复性：同参数跑两遍须完全一致 —— 排除「非确定性/分页截断」这个混淆项。
     没有这一层，「城市间差异」可能只是服务端返回顺序抖动，会得出相反结论。
  ② 跨省区分度：河北 188 vs 北京 110 须零交集 —— 证明 provinceId/cityId 确实被使用。
     没有这一层，可能整体就是个不认参数的接口，那两城差异也就无从谈起。
  ③ 城市差异语义：差异条目名须带城市特征词（雄安/沧州/保定…）——
     证明差异是「城市专属资费」，而不是「排序不同」或「数量截断」。
  ④ 全组合覆盖：必须遍历 indexData 返回的**全部** (一级×二级) 组合 × **全部** 12 地市。
     这一层就是当年漏掉的那一层 —— 少抽一个组合就可能整个结论反过来。

另附一个关键结论（决定采集成本）：
  ⑤ `operateData` 按 id 解析、cityId 不设门槛 —— 用邢台 cityId 能取到雄安专属条目
     ⇒ 正确策略是「菜单取 12 城并集 → 明细按并集 id 只拉一遍」，请求量约 2 倍，
        而不是「12 城各采一遍」的 12 倍。

用法：
  python probe_unicom_city_scope.py            # 四层判据 + 并集量化（约 1 分钟）
  python probe_unicom_city_scope.py --json out.json
"""
import argparse
import hashlib
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
    urllib.request.ProxyHandler({}),          # ★ 真直连；不显式传就会被注册表残留代理劫持
    urllib.request.HTTPSHandler(context=CTX),
)

H5_UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
         "Mobile Safari/537.36")
BLANK = {"duanlianjieabc": "", "channelCode": "", "serviceType": "", "saleChannel": "",
         "externalSources": "", "contactCode": ""}

# 两条链路。★ 本探针两者都测：网页版(img/zifeizhuanquwt)发的是 m + version=WT，
#   与 APP 版(mxx)不同域名。若只测一条，可能把「链路差异」误当成「无城市维度」。
LINKS = {
    "网页版(m/WT)": {"base": "https://m.client.10010.com/servicequerybusiness",
                     "referer": "https://img.client.10010.com/", "wt": True},
    "APP域(mxx)":  {"base": "https://mxx.client.10010.com/servicequerybusiness",
                    "referer": "https://imgxx.client.10010.com/", "wt": False},
}
PROV = "018"
CITIES = [("石家庄", "188"), ("唐山", "181"), ("秦皇岛", "182"), ("邯郸", "186"),
          ("邢台", "185"), ("保定", "187"), ("张家口", "184"), ("承德", "189"),
          ("沧州", "180"), ("廊坊", "183"), ("衡水", "720"), ("雄安", "782")]
ATTRS = ("1", "2")


def post(link, path, data, timeout=30):
    d = dict(BLANK)
    d.update(data)
    if link["wt"]:
        d["version"] = "WT"
    d.setdefault("behaviorId", "LTB4GKFRRYV13TJY20260924000000000")
    req = urllib.request.Request(link["base"] + path, data=urllib.parse.urlencode(d).encode(),
                                 method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": H5_UA,
        "Origin": link["referer"].rstrip("/"),
        "Referer": link["referer"],
        "x-requested-with": "com.sinovatech.unicom.ui",
    })
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def md5(o):
    return hashlib.md5(json.dumps(o, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]


def menu_city(link, code, fl, sl, prov=PROV):
    """该城在该 (一级×二级) 下 attr=1/2 合并的三级目录 {id: name}。

    ★ prov 必须与 code 配套 —— 拿 provinceId=018 去查北京 cityId=110 会**静默回空**，
      于是「跨省交集为 0」看着成立，其实是参数自造的空集。这种假通过比不测更糟：
      它会让人以为「参数确实生效」，从而把结论建立在错误的对照上。
    """
    out = {}
    for a in ATTRS:
        r = post(link, "/queryTariffNew/threeLevelName",
                 {"tariffAttributes": a, "firstLevel": str(fl), "secondLevel": str(sl),
                  "provinceId": prov, "cityId": code})
        for x in (((r.get("data") or {}).get("dataList")) or []):
            if x.get("id"):
                out[x["id"]] = x.get("name")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    print("=" * 76)
    print("判据① 同城重复性（同参数连跑两遍，须完全一致）")
    link = LINKS["网页版(m/WT)"]
    det_ok = True
    for (fl, sl) in [("1", "1002"), ("2", "2001"), ("99", "2")]:
        x = set(menu_city(link, "188", fl, sl))
        y = set(menu_city(link, "188", fl, sl))
        ok = x == y
        det_ok &= ok
        print(f"   [{fl}-{sl}] 第1次={len(x)} 第2次={len(y)} 一致={ok}")

    print("\n判据② 跨省区分度（河北 188/prov=018 vs 北京 110/prov=011）")
    # ⚠️ 不能拿「交集为 0」当判据 —— 两省在**全国/跨省目录**（attr=1）上本来就共享条目
    #    （移网交集 57 条）。正确的对照是：两侧各有对方没有的条目，且北京独有条目名带「北京」。
    for (fl, sl) in [("1", "1002"), ("1", "1001")]:
        hb = menu_city(link, "188", fl, sl, prov="018")
        bj = menu_city(link, "110", fl, sl, prov="011")
        only_hb = [hb[i] for i in set(hb) - set(bj)]
        only_bj = [bj[i] for i in set(bj) - set(hb)]
        bj_tagged = sum(1 for n in only_bj if n and ("北京" in n))
        print(f"   [{fl}-{sl}] 河北188={len(hb)} 北京110={len(bj)} 交集={len(set(hb)&set(bj))} "
              f"仅河北={len(only_hb)} 仅北京={len(only_bj)}（其中带「北京」字样 {bj_tagged} 条）")
        if only_bj:
            print(f"        北京独有样例：{str(only_bj[0])[:48]}")

    print("\n判据⑤ 明细是否受 cityId 门控（决定采集成本）")
    # 取一个只存在于雄安的 id，用邢台 cityId 去拉
    xa = menu_city(link, "782", "2", "2001")
    xt = menu_city(link, "185", "2", "2001")
    only = [k for k in xa if k not in xt][:2]
    if only:
        r = post(link, "/queryTariffNew/operateData/" + "_".join(only),
                 {"page": "1", "size": "10", "provinceId": PROV, "cityId": "185"})
        det = ((r.get("data") or {}).get("detailList")) or []
        print(f"   用邢台 cityId 拉 {len(only)} 个雄安专属 id → 返回明细 {len(det)} 条 "
              f"⇒ {'明细按 id 解析、不受 cityId 门控' if det else '⚠️ 受门控'}")

    print("\n判据③④ 全组合 × 全地市：城市差异 + 并集量化")
    allres = {}
    for name, lk in LINKS.items():
        d0 = (post(lk, "/queryTariffNew/indexData", {"provinceId": PROV, "cityId": "188"})
              .get("data") or {})
        combos = [(str(L.get("firstLevel")), L.get("firstLevelName"),
                   str(s.get("secondLevel")), s.get("secondLevelName"))
                  for L in (d0.get("levelList") or [])
                  for s in (L.get("secondLevels") or [])]
        tasks = [((fl, sl), nm, code) for (fl, fln, sl, sln) in combos for nm, code in CITIES]

        def cell(t):
            (fl, sl), nm, code = t
            try:
                return (fl, sl), code, menu_city(lk, code, fl, sl)
            except Exception as e:
                return (fl, sl), code, {"__ERR__": str(e)[:40]}

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            cells = list(ex.map(cell, tasks))
        per = defaultdict(dict)
        for (fl, sl), code, m in cells:
            per[(fl, sl)][code] = m

        single, union, diff_combos = set(), set(), []
        exclusive = defaultdict(list)
        print(f"\n--- 链路 {name}（{len(combos)} 组合 × {len(CITIES)} 地市）")
        for (fl, fln, sl, sln) in combos:
            d = per[(fl, sl)]
            u = set().union(*[set(d.get(c, {})) for _n, c in CITIES]) if d else set()
            s0 = set(d.get("185", {}))
            single |= s0
            union |= u
            if any(set(d.get(c, {})) != set(d.get("185", {})) for _n, c in CITIES):
                diff_combos.append(f"{fln}/{sln}")
            if len(u) > len(s0):
                print(f"   [{fl}-{sl}] {fln}/{sln:<16} 单城={len(s0):<5} 并集={len(u):<5} "
                      f"净增={len(u)-len(s0)}")
            for nm, code in CITIES:
                mine = set(d.get(code, {}))
                others = set().union(*[set(d.get(c2, {})) for nm2, c2 in CITIES if c2 != code])
                for i in (mine - others):
                    exclusive[nm].append((d[code].get(i), f"{fln}/{sln}"))

        print(f"   存在城市差异的组合 {len(diff_combos)}/{len(combos)} 个："
              f"{'、'.join(diff_combos) if diff_combos else '（无）'}")
        print(f"   合计：单城(邢台)三级目录={len(single)}  12城并集={len(union)}  "
              f"净增={len(union)-len(single)}")
        print("   各城专属条目（其他城市都没有）：")
        for nm, _c in CITIES:
            it = exclusive.get(nm) or []
            if it:
                print(f"      {nm:<4} {len(it):<4} 条  例：{str(it[0][0])[:42]}")
        allres[name] = {"combos": len(combos), "diff_combos": diff_combos,
                        "single_city_menu": len(single), "union_menu": len(union),
                        "net_gain": len(union) - len(single),
                        "exclusive": {k: [n for n, _ in v] for k, v in exclusive.items()}}

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"province": PROV, "cities": [{"name": n, "code": c} for n, c in CITIES],
                       "links": allres}, f, ensure_ascii=False, indent=1)
        print("\nsaved ->", a.json)

    print("\n" + "=" * 76)
    print("结论：河北联通资费**按地市不同**（见上「各城专属条目」）。"
          "采集须取 12 城并集；明细因按 id 解析可只拉一遍。")


if __name__ == "__main__":
    main()
