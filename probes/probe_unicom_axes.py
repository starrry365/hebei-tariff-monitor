#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：联通「全部 (板块 × 一级 × 二级) 组合」是否都有被真正请求过。

【为什么需要单独一条】
`probe_coverage_axes.py --net unicom` 问的是**骨架**：上游 cityList 是不是 12 城、
12 城的一级/二级栏目签名是否一致、tariffAttributes 有几个取值。
它**不问**「每个组合到底有没有数据、数据有没有进快照」——
而后者正是失效时唯一能看见的东西：整栏**从未被请求过**时接口全 200、
日志无异常，症状只有「少了」，少的那部分却从不出现。

本探针逐个组合**打真接口**，把「该组合在 12 城并集下的三级目录 id 数」
与「快照里落在该组合的条目数」并排放在一起：

    三级 id > 0  而  快照条目 == 0   ⇒ 🔴 漏采（整栏没进快照）
    三级 id == 0（全部 code=0001）   ⇒ 上游确实没有这一栏（正常）
    有 None（请求失败）              ⇒ ⚠️ 结论不可信，需重跑

⚠️ **三级 id 数 ≫ 快照条目数是正常的、不是漏**：同一条资费可以挂在多个栏目下，
   快照 entries 是**按 reportNo 去重**后的结果（去重前后差 ~1079）。
   所以判据只能是「id > 0 而条目 == 0」，不能是「两个数相等」。

【2026-09-24 实测结论】
44 组合 × 12 城 = 528 格，请求 0 失败。13 个组合为空，全部是上游确实没有：
    · 河北(attr=2) 的「港澳台/国际资费」「标准资费」两个一级分类 —— 全部二级都空
      （河北本省不发这两类；页面里河北就只剩 套餐/加装包/营销活动/停售套餐 四类）
    · 全国(attr=1) 的「套餐/固话」「套餐/融合」「营销活动/合约」
其余 31 个组合的三级目录数与快照逐一对上。

用法：
  python probes/probe_unicom_axes.py            # 全量核查（528 次请求，约 20s）
  python probes/probe_unicom_axes.py --quiet    # 只打结论与异常
证据落盘：evidence/unicom-axes-coverage.json
"""
import gzip
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import he_unicom_tariff as U  # noqa: E402

SNAP = os.path.join(REPO, "cloud", "tariff", "snapshots")
OUT = os.path.join(REPO, "evidence", "unicom-axes-coverage.json")


def _latest_snapshot(prefix="unicom_tariff_"):
    """该网**最新**的快照路径。

    🔴 绝不硬编码日期：CI 每天采的快照名带当天日期，写死一天就等于这条判据
       只在写它的那天有效，之后永远读不到数据 ⇒ **静默不检查**。
    """
    cand = []
    for nm in os.listdir(SNAP):
        if nm.startswith(prefix) and nm.endswith(".json.gz"):
            cand.append((nm[len(prefix):-len(".json.gz")], os.path.join(SNAP, nm)))
    return max(cand)[1] if cand else None


def main():
    quiet = "--quiet" in sys.argv
    t0 = time.time()
    levels, meta = U.get_menu()
    if not levels:
        print("!! 菜单骨架获取失败：%s" % str(meta)[:200])
        return 2

    combos = []
    for lv in levels:
        for sub in (lv.get("secondLevels") or []):
            for a in U.ATTRS:
                combos.append((a, str(lv.get("firstLevel")), str(sub.get("secondLevel")),
                               lv.get("firstLevelName"), sub.get("secondLevelName")))
    cities = list(U.CITY_CODES)
    if not quiet:
        print("一级 %d 个 · (板块 × 一级 × 二级) 组合 %d 个 · 地市 %d 个 · 请求 %d 次"
              % (len(levels), len(combos), len(cities), len(combos) * len(cities)))

    def cell(p):
        (a, fl, sl, _f, _s), (cn, cc) = p
        lst = U.get_level3(a, fl, sl, cc)
        if lst is None:                    # 抖动很常见，重试一次（代价极低）
            time.sleep(0.8)
            lst = U.get_level3(a, fl, sl, cc)
        if lst is None:
            return (a, fl, sl), cn, None
        return (a, fl, sl), cn, sorted(x.get("id") for x in lst if x.get("id"))

    res, fails = {}, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for key, cn, ids in ex.map(cell, [(c, ct) for c in combos for ct in cities]):
            d = res.setdefault(key, {"ids": set(), "cities": {}, "unknown": []})
            if ids is None:
                d["unknown"].append(cn)
                fails.append((key, cn))
                continue
            if ids:
                d["ids"].update(ids)
                d["cities"][cn] = len(ids)

    # ── 与最新快照对照 ────────────────────────────────────────────────
    f = _latest_snapshot()
    have = {}
    if f:
        d = json.load(gzip.open(f, "rt", encoding="utf-8"))
        for e in d.get("entries") or []:
            k = (str(e.get("_attr")), str(e.get("_firstLevel")), str(e.get("_secondLevel")))
            have[k] = have.get(k, 0) + 1
    if not quiet:
        print("对照快照：%s（条目落在 %d 个组合里）"
              % (os.path.basename(f or "(无)"), len(have)))
        print()
        print("%-5s %-5s %-6s %-16s %-20s %8s %8s %6s  %s"
              % ("板块", "一级", "二级", "一级名", "二级名", "三级目录", "快照条", "城数", "判定"))

    report, missed, unknown = [], [], []
    for (a, fl, sl, fln, sln) in combos:
        d = res.get((a, fl, sl), {"ids": set(), "cities": {}, "unknown": []})
        nid, nsn, ncity = len(d["ids"]), have.get((a, fl, sl), 0), len(d["cities"])
        if d["unknown"]:
            verdict = "⚠️ 请求失败 %d 城" % len(d["unknown"])
            unknown.append((a, fl, sl))
        elif nid > 0 and nsn == 0:
            verdict = "🔴 有三级目录但快照 0 条 —— 整栏漏采"
            missed.append((a, fl, sl))
        elif nid == 0:
            verdict = "空（上游无此栏目）"
        else:
            verdict = "ok"
        if not quiet:
            print("%-5s %-5s %-6s %-16s %-20s %8d %8d %6d  %s"
                  % (a, fl, sl, fln, sln, nid, nsn, ncity, verdict))
        report.append({"attr": a, "firstLevel": fl, "secondLevel": sl,
                       "firstLevelName": fln, "secondLevelName": sln,
                       "level3Ids": nid, "snapshotRows": nsn, "cities": ncity,
                       "unknownCities": d["unknown"], "verdict": verdict})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"snapshot": os.path.basename(f or ""), "fetchedAt": time.strftime("%F %T"),
                   "combos": report, "fails": [list(x) for x in fails]},
                  fh, ensure_ascii=False, indent=1)

    print()
    tot_id = sum(c["level3Ids"] for c in report)
    tot_sn = sum(c["snapshotRows"] for c in report)
    empty = sum(1 for c in report if c["level3Ids"] == 0)
    print("耗时 %.1fs · 组合 %d（空 %d / 有数据 %d）· 请求失败格 %d"
          % (time.time() - t0, len(combos), empty, len(combos) - empty, len(fails)))
    print("三级目录合计 %d · 快照条目合计 %d（差值＝同一资费挂多栏目的去重，正常）"
          % (tot_id, tot_sn))
    if fails:
        print("⚠️ 有 %d 个格子请求失败 —— **结论不可信**（失败格会让「有数据」误判成「空」），请重跑："
              % len(fails))
        for k, cn in fails[:10]:
            print("     attr=%s %s/%s @ %s" % (k[0], k[1], k[2], cn))
    if missed:
        print("🔴 **整栏漏采 %d 个组合**（上游有目录，快照里一条都没有）：" % len(missed))
        for a, fl, sl in missed:
            print("     attr=%s firstLevel=%s secondLevel=%s" % (a, fl, sl))
    if not fails and not missed:
        print("✅ 全部组合与上游一致：无整栏漏采、无失败格")
    print("证据落盘：evidence/%s" % os.path.basename(OUT))
    return 2 if (missed or fails) else 0


if __name__ == "__main__":
    sys.exit(main())
