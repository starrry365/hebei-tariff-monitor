#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：联通「三级目录逐城取并集」这一步，**失败格有没有被当成空目录**。

════════════════════════════════════════════════════════════════════
为什么这是高危
════════════════════════════════════════════════════════════════════
联通的「不限地市 / 城市专属」判定=**该资费的三级目录在几个城市出现过**：

    len(出现的城市) >= 12  →  _allCity=1（不限地市，全省通用）
    否则                   →  _cityNames=[少数几个城市]（城市专属）

所以「某城有没有这个目录」是**判定全省/专属的唯一依据**。而
`he_unicom_tariff.get_level3()` 对「请求失败」与「真的没这个目录」**返回同一个东西**：

    code == "0001"          → []   # 空目录（正常）
    code != "0000"          → []   # ⚠️ 网络抖动 / 限流 / 超时 —— 也被当成空目录！

后果是**静默且方向固定**的：一个格子没取到 ⇒ 那条资费「覆盖城市数」少 1 ⇒
**本该「不限地市」的被改判成「N 城专属」**，于是用户在其他 11 个城市按地市筛时
**看不到它**——而页面上没有任何异常，那条资费只是「不在这个市」。

这与「漏 130 个三级目录」是同一类错，只是触发条件更隐蔽（要网络抖动）。

本探针：对全部 (attr × 一级 × 二级) × 12 城 逐一请求 threeLevelName，
记录 **code 分布** 与每个格子的目录数，算出：
  · 失败格数量（code 不属于 0000/0001）
  · 与「同格重复请求」的不一致（同格连取两次，结果不同 ⇒ 抖动实锤）
"""
import collections
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import he_unicom_tariff as U          # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def one(p):
    """(attr, 一级, 二级, 城名, 城码) → 该格的 code 与目录 id 集合。"""
    a, f, s, nm, code = p
    r = U.post("/queryTariffNew/threeLevelName",
               {"tariffAttributes": a, "firstLevel": f, "secondLevel": s,
                "provinceId": U.PROV, "cityId": code})
    rc = r.get("code") if isinstance(r, dict) else "?"
    ids = set()
    if rc == "0000":
        for x in (r.get("data") or {}).get("dataList") or []:
            if x.get("id"):
                ids.add(x["id"])
    return (a, f, s, nm, code), rc, ids, str(r.get("msg") or "")[:60]


def main():
    levels, _ = U.get_menu(U.CITY)
    combos = []
    for lv in levels:
        for sub in lv.get("secondLevels") or []:
            combos.append((str(lv.get("firstLevel")), str(sub.get("secondLevel")),
                           lv.get("firstLevelName"), sub.get("secondLevelName")))
    cities = list(U.CITY_CODES)
    tasks = [(a, f, s, nm, code)
             for (f, s, _fn, _sn) in combos for a in U.ATTRS for nm, code in cities]
    print("(attr×一级×二级) %d 组合 × 2 attr × 12 城 = %d 格" % (len(combos), len(tasks)))

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(one, tasks))

    codes = collections.Counter(rc for _k, rc, _i, _m in res)
    print("\n[A] code 分布：", dict(codes))
    bad = [(k, rc, m) for k, rc, _i, m in res if rc not in ("0000", "0001")]
    if bad:
        print("   ⚠️⚠️ **%d 个格子的请求失败**（会被 get_level3 当成空目录）：" % len(bad))
        for k, rc, m in bad[:15]:
            print("      attr=%s %s/%s 城=%s code=%s %s" % (k[0], k[1], k[2], k[3], rc, m))
    else:
        print("   ✅ 全部格子的 code 都是 0000/0001（无失败格）")

    empty = sum(1 for _k, rc, _i, _m in res if rc == "0001")
    print("   空目录(code=0001) %d 格 · 有目录 %d 格"
          % (empty, sum(1 for _k, rc, _i, _m in res if rc == "0000")))

    # 每格的目录数分布（看有没有「同城同组合、数量差异巨大」的怪格）
    n3 = collections.Counter(len(i) for _k, _rc, i, _m in res)
    print("\n[B] 每格三级目录数分布（前 12）：", dict(sorted(n3.items())[:12]))

    # 同格重复：抽 60 格连取两次，比较 id 集合 —— 抖动会让两次不同
    print("\n[C] 同格连取两次（抽 %d 格）验证稳定性" % 60)
    sample = tasks[::max(1, len(tasks) // 60)][:60]
    with ThreadPoolExecutor(max_workers=8) as ex:
        r1 = list(ex.map(one, sample))
        r2 = list(ex.map(one, sample))
    diff = [(a, b) for a, b in zip(r1, r2) if a[2] != b[2]]
    print("   两次结果不同的格：%d / %d" % (len(diff), len(sample)))
    for a, b in diff[:10]:
        print("      attr=%s %s/%s 城=%s : %d vs %d 个目录"
              % (a[0][0], a[0][1], a[0][2], a[0][3], len(a[2]), len(b[2])))
    if not diff:
        print("   ✅ 抽样格两次逐一致")

    out = {"codes": dict(codes), "bad_cells": len(bad), "empty_cells": empty,
           "sampled_unstable": len(diff), "total_cells": len(tasks)}
    dst = os.path.join(os.path.dirname(BASE), "evidence", "unicom-harvest-health.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写出 %s" % dst)
    ok = not bad and not diff
    print(">>> 结论：%s" % ("逐城取并集这一步是干净的 ✅" if ok
                          else "**存在失败格 / 抖动** ❌ —— 「不限地市」判定可能已被污染"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
