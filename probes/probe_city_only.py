# -*- coding: utf-8 -*-
"""「某个地市**独有**的三级目录」证据生成器 —— 回答「为什么按这个市筛只出来 N 条」。

为什么要它
----------
2026-09-24 用户报障：「联通邢台明明还有在售套餐，我怎么看一个也没有」。查下来是
**筛选口径**问题（页面当时只留 cty 含该市的条目），但要把话说圆，必须回答
「上游到底有没有邢台专属的在售资费」—— 猜不行，得直接问接口。

它做的事：对每个 (attr × 一级 × 二级) 组合，取 12 城各自的 threeLevelName 集合，
挑出**只有目标城市有**的三级目录 id，并打印它属于哪个组合、叫什么名字。

判读方式
--------
  · 若这些 id **全部**落在「一级=99（停售套餐）」⇒ 上游确实没有该市的在售专属资费；
  · 若在售组合里也有该市独有 id ⇒ 采集侧漏了，是我们这边的 bug。
两种情况要给出的修法完全不同，所以这一步不能省。

用法:
    python probes/probe_city_only.py                    # 默认邢台（185）
    python probes/probe_city_only.py --city 沧州
    python probes/probe_city_only.py --json evidence/unicom-city-only-xt.json

⚠️ 代价：22 个组合 × 12 城 ≈ 264 次接口调用，数分钟。结论不需要每天复核时**别**跑。

配套报告：docs/联通河北资费-地市口径与已下架归属-20260924.md
"""
import argparse
import collections
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import he_unicom_tariff as U                              # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="邢台",
                    help="地市中文名或 3 位码（见 he_unicom_tariff.CITY_CODES），默认邢台")
    ap.add_argument("--json", default="", help="把结果落盘成 JSON（证据留存用）")
    args = ap.parse_args()

    cities = list(U.CITY_CODES)                     # [(中文名, 3 位码), ...]
    want = args.city.strip()
    code = None
    for nm, c in cities:
        if want in (nm, c):
            code = c
            break
    if not code:
        sys.exit("认不出地市 %r —— 可选：%s" % (want, "、".join(nm for nm, _ in cities)))
    name = dict((c, nm) for nm, c in cities)[code]

    levels, meta = U.get_menu(code)
    if not levels:
        sys.exit("骨架获取失败: %s" % str(meta)[:200])
    pairs = []
    for lv in levels:
        for sub in lv.get("secondLevels") or []:
            for a in U.ATTRS:
                pairs.append((a, lv.get("firstLevel"), sub.get("secondLevel"),
                              lv.get("firstLevelName"), sub.get("secondLevelName")))
    print("%s（%s）· 组合 %d 个 · 地市 %d 个\n" % (name, code, len(pairs), len(cities)))

    mine_only, all_ids = [], set()
    for a, f, s, fn, sn in pairs:
        per = {}
        for nm, c in cities:
            ids = set()
            for x in U.get_level3(a, f, s, c) or []:
                if x.get("id"):
                    ids.add(x["id"])
                    all_ids.add(x["id"])
            per[c] = ids
        others = set()
        for nm, c in cities:
            if c != code:
                others |= per[c]
        got = per[code] - others
        if got:
            names = {x.get("id"): x.get("name")
                     for x in (U.get_level3(a, f, s, code) or []) if x.get("id")}
            for i in sorted(got):
                mine_only.append({"attr": a, "一级": f, "一级名": fn,
                                  "二级": s, "二级名": sn, "id": i, "名称": names.get(i)})

    print("=== 只有 %s 有的三级目录：%d 个 ===" % (name, len(mine_only)))
    byc = collections.Counter((r["一级"], r["一级名"]) for r in mine_only)
    for k, v in sorted(byc.items()):
        print("   一级 %s(%s)：%d 个" % (k[0], k[1], v))
    print()
    for r in mine_only:
        print("   attr=%s 一级=%s(%s) 二级=%s(%s)  id=%s  %s"
              % (r["attr"], r["一级"], r["一级名"], r["二级"], r["二级名"], r["id"], r["名称"]))
    print()
    print("%s 独有 id %d 个；12 城全部 id 合计 %d 个" % (name, len(mine_only), len(all_ids)))
    # ★ 判读提示：把「结论该怎么读」直接印出来，省得看的人自己推。
    stop = [r for r in mine_only if str(r["一级"]) == "99"]
    if not mine_only:
        print("→ 该市没有任何独有三级目录 ⇒ 它的资费全部来自各城共有的目录（含全省通用）。")
    elif len(stop) == len(mine_only):
        print("→ 该市的独有目录**全部**在「一级=99（停售套餐）」⇒ 上游确实没有该市的"
              "在售专属资费，按该市筛看到的在售条目来自「不限地市」那批。")
    else:
        print("→ 该市有 %d 个独有目录落在在售分类里（非 99）⇒ 属于该市的在售专属资费。"
              % (len(mine_only) - len(stop)))

    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(json.dumps(
            {"city": name, "code": code, "组合数": len(pairs),
             "独有三级目录": mine_only, "12城全部id数": len(all_ids)},
            ensure_ascii=False, indent=1))
        print("已落盘 → %s" % args.json)


if __name__ == "__main__":
    main()
