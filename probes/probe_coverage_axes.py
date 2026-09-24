#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：**采集维度本身**是否穷尽（栏目骨架 / 板块 attr / 地市集合）。

════════════════════════════════════════════════════════════════════
为什么需要这条判据
════════════════════════════════════════════════════════════════════
现有检查都在问「**已经采到的那批**对不对」（口径 / 展示 / 变更），
没有一条在问「**该采的是不是都去采了**」。两者的失效方式完全不同：

  前者失效 → 数字算错，能对出来；
  后者失效 → 整栏/整城**根本没被请求过**，接口全 200、日志无异常、
             唯一的症状是「少了」。而且少得很隐蔽：少的那部分从不出现。

历史两次真事故都属后者：
  · 只采邢台一城 ⇒ 漏 130 个三级目录（2026-09-24 修）
  · 一级/二级骨架只从单城取 ⇒ 若某城有它城没有的栏目，整栏漏（**本探针要证伪/证实**）

════════════════════════════════════════════════════════════════════
本探针做什么
════════════════════════════════════════════════════════════════════
【联通】
  A. 上游 cityList 是否 == 硬编码 CITY_CODES（少一个城就少一整城的资费）
  B. 12 城的一级/二级骨架是否一致（单城取骨架是否安全）+ 三轮取同城验证稳定性
  C. tariffAttributes 只有 1/2 吗（有没有第三个板块被漏掉）
【移动】
  D. getType2List 返回多少 (attr, type1, type2) 组合；带不带 cityId 是否不同
  E. getTariffListInfo 带 cityId 是否返回更多（移动要不要按地市采）

用法：
  python probes/probe_coverage_axes.py                # 联通 A/B/C
  python probes/probe_coverage_axes.py --net unicom --json out.json
  python probes/probe_coverage_axes.py --net move     # 移动 D/E（需 pycryptodome）
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
sys.path.insert(0, BASE)

import he_unicom_tariff as U          # noqa: E402


def log(m):
    print(m, flush=True)


# ═══════════════════════════ 联通 ═══════════════════════════
def _menu_sig(code):
    """一城的 (一级 -> {二级}) 骨架 + 原始 code。"""
    levels, meta = U.get_menu(code)
    tree = {}
    for lv in levels:
        tree[str(lv.get("firstLevel"))] = {
            "name": lv.get("firstLevelName"),
            "second": {str(s.get("secondLevel")): s.get("secondLevelName")
                       for s in (lv.get("secondLevels") or [])},
        }
    return tree, meta.get("code")


def uni_check_cities(workers=8):
    log("\n[A] 上游 cityList vs 硬编码 CITY_CODES")
    try:
        up = U.get_cities()
    except Exception as e:
        log("   拉取失败：%s" % e)
        return {"ok": False, "err": str(e)}
    up_codes = {str(c.get("cityCode")) for c in up}
    hard = {code for _nm, code in U.CITY_CODES}
    log("   上游 %d 个：%s" % (len(up),
        "、".join("%s(%s)" % (c.get("cityName"), c.get("cityCode")) for c in up)))
    log("   硬编码 %d 个：%s" % (len(hard), "、".join(sorted(hard))))
    miss = up_codes - hard          # 上游有、我们没采 → **少采集**
    extra = hard - up_codes         # 我们采了上游没有的 → 可能是笔误/过期码
    if miss:
        log("   ⚠️⚠️ **上游有 %d 个地市没被采集**：%s" % (len(miss), sorted(miss)))
    if extra:
        log("   ⚠️ 硬编码里有 %d 个不在上游 cityList：%s（查是否笔误）"
            % (len(extra), sorted(extra)))
    if not miss and not extra:
        log("   ✅ 完全一致（%d 城）" % len(up_codes))
    return {"ok": not miss and not extra,
            "upstream": [{"name": c.get("cityName"), "code": str(c.get("cityCode"))}
                         for c in up],
            "hardcoded": sorted(hard),
            "missing_from_harvest": sorted(miss),
            "not_in_upstream": sorted(extra)}


def uni_check_skeleton(workers=8):
    log("\n[B] 12 城一级/二级骨架是否一致（单城取骨架是否安全）")
    cities = list(U.CITY_CODES)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(lambda c: (c, *_menu_sig(c[1])), cities))
    per, bad = {}, []
    for (nm, code), tree, c in res:
        per[code] = tree
        if c != "0000":
            bad.append("%s(%s) code=%s" % (nm, code, c))
    if bad:
        log("   ⚠️ 有城市取骨架非 0000：%s" % "、".join(bad))

    def sig(t):
        return "|".join("%s:%s" % (f, ",".join(sorted(v["second"])))
                        for f, v in sorted(t.items()))

    sigs = {c: sig(t) for c, t in per.items()}
    uniq = set(sigs.values())
    log("   12 城签名去重后 **%d 种**" % len(uniq))
    for code, s in sorted(sigs.items()):
        nm = dict((c, n) for n, c in U.CITY_CODES)[code]
        n1 = len(per[code])
        n2 = sum(len(v["second"]) for v in per[code].values())
        log("      %-6s%-5s 一级=%d 二级=%d %s"
            % (nm, code, n1, n2, "" if len(uniq) == 1 else "← " + s[:60]))

    # 并集 vs 单城
    base = U.CITY
    all_f, all_s = set(), set()
    for t in per.values():
        for f, v in t.items():
            all_f.add(f)
            all_s.update((f, s) for s in v["second"])
    bt = per.get(base) or {}
    bf = set(bt)
    bs = set((f, s) for f, v in bt.items() for s in v["second"])
    miss_f, miss_s = all_f - bf, all_s - bs
    if miss_f:
        log("   ⚠️⚠️ **单城(%s)漏采一级栏目 %d 个**：%s" % (base, len(miss_f), sorted(miss_f)))
    if miss_s:
        log("   ⚠️⚠️ **单城(%s)漏采二级栏目 %d 个**：%s"
            % (base, len(miss_s), sorted(miss_s)))
    if not miss_f and not miss_s:
        log("   ✅ 单城骨架已覆盖 12 城并集（一级 %d / 二级 %d）—— 单城取骨架是安全的"
            % (len(all_f), len(all_s)))

    # 三轮取同城：证明「一致」不是抖动造成的假象
    log("   稳定性：同城(%s)连取 3 次" % base)
    t3 = [sig(_menu_sig(base)[0]) for _ in range(3)]
    log("      %s" % ("✅ 三次逐字节一致" if len(set(t3)) == 1 else "⚠️ 三次不一致！"))

    return {"ok": not miss_f and not miss_s and len(uniq) == 1,
            "distinct_sigs": len(uniq), "first_union": sorted(all_f),
            "second_union": sorted(map(list, all_s)),
            "missing_first": sorted(miss_f), "missing_second": sorted(map(list, miss_s)),
            "stable_3x": len(set(t3)) == 1,
            "per_city": {c: {"first": sorted(t), "n_second":
                             sum(len(v["second"]) for v in t.values())}
                         for c, t in per.items()}}


def uni_check_attrs():
    """tariffAttributes 取值域：只有 1/2 吗（有没有第三个板块被整栏漏采）。

    🔴🔴 这里探的是**上游没声明**的取值（3/4/5/''/0），而 get_level3 的返回值
    有**三种**必须分开的情况（见 he_unicom_tariff.get_level3 的文档）：
        list（非空）→ 这个板块有数据
        []          → 上游明说「暂无资费信息」（code=0001），是**真的没有**
        None        → **未知**：请求失败（限流 / 超时 / 非 0000/0001 的业务码）
    把 None 当 0 会把「第三个板块存在、只是这次没取到」判成「不存在」——
    方向固定、症状静默，正是本探针存在的理由（2026-09-24 CI 挂过一次：
    attr=5 撞上限流返回 None，旧代码直接 len(None) 崩 TypeError）。
    ⇒ 失败重试一次；仍失败就记为 unknown 并**判失败**，绝不冒充空目录。
    """
    log("\n[C] tariffAttributes 取值域（有没有第三个板块被漏掉）")
    base = U.CITY
    levels, _ = U.get_menu(base)

    def l3(a, first, second):
        lst = U.get_level3(a, first, second, base)
        if lst is None:                 # 抖动很常见，重试一次（代价极低）
            time.sleep(0.8)
            lst = U.get_level3(a, first, second, base)
        return lst

    hits, unknown = {}, {}
    for a in ("1", "2", "3", "4", "5", "", "0"):
        n = bad = 0
        for lv in levels:
            for sub in lv.get("secondLevels") or []:
                v = l3(a, lv.get("firstLevel"), sub.get("secondLevel"))
                if v is None:
                    bad += 1
                else:
                    n += len(v)
            if n:                        # 命中即止：省请求，也少给上游限流的理由
                break
        hits[a], unknown[a] = n, bad
        log("   tariffAttributes=%-4s → 三级目录 %d%s"
            % (repr(a), n, "" if not bad else "，另有 %d 格**请求失败（未知）**" % bad))

    nonzero = sorted(k for k, v in hits.items() if v)
    failed = sorted(k for k, v in unknown.items() if v)
    log("   有效取值：%s" % nonzero)
    ok = True
    if not (set(nonzero) <= {"1", "2"}):
        log("   ⚠️⚠️ 出现 1/2 之外的取值：%s ⇒ 采集侧 ATTRS 必须扩"
            % [k for k in nonzero if k not in ("1", "2")])
        ok = False
    else:
        log("   ✅ 只有 1/2（与其他证据一致）—— 采集侧 ATTRS=('1','2') 是完备的")
    if failed:
        # 「未知」不能当「没有」：这一格没查清，结论就不成立（重跑可自愈）
        log("   🔴 有取值没能查清（重试后仍失败）：%s ⇒ 结论不成立，需重跑" % failed)
        ok = False
    return {"ok": ok, "hits": hits, "unknown_failed": unknown}


# ═══════════════════════════ 移动 ═══════════════════════════
def move_probe():
    sys.path.insert(0, os.path.join(REPO, "cloud", "tariff"))
    import tariff_monitor as T

    log("\n[D1] 移动 getType2List **声明**的 (attr, type1, type2) 组合")
    combos = T.call("nrtariff/new/Tariff/getType2List",
                    {"province": T.PROV, "isPublic": "1"})
    arr = (combos.get("data") if isinstance(combos, dict) else None) or []
    declared = {(str(c.get("tariffAttr")), str(c.get("type1")), str(c.get("type2")))
                for c in arr}
    log("   声明 %d 个组合" % len(declared))
    ATTRS, T1S, T2S = ("1", "2", "3"), ("1", "2"), ("1", "2", "3", "4", "5")
    allc = [(a, t1, t2) for a in ATTRS for t1 in T1S for t2 in T2S]
    log("   笛卡尔积 %d（attr 3 × type1 2 × type2 5）" % len(allc))
    miss = [k for k in allc if k not in declared]
    log("   目录未声明的 %d 个" % len(miss))

    # ★★ 本探针的核心判据 —— 「上游目录没列」**不等于**「没有数据」。
    #   采集侧现在**完全信任** getType2List：它不列的，我们就不去请求。
    #   若某个未声明组合其实有数据，那就是**整栏静默漏采**（接口恒 200、无异常）。
    #   与联通那次「栏目骨架只从单城取」是同一类错误：拿一个**间接信号**当**完备性依据**。
    log("\n[D2] 直接请求**全部** %d 个组合 —— 目录之外还有没有数据？" % len(allc))
    extra, zero = [], []
    for (a, t1, t2) in allc:
        n, series = move_count(T, a, t1, t2)
        tag = "声明" if (a, t1, t2) in declared else "**未声明**"
        if n or series:
            log("   attr=%s t1=%s t2=%s  %-10s 条目 %-6d 系列 %d" % (a, t1, t2, tag, n, series))
        if (a, t1, t2) not in declared and (n or series):
            extra.append((a, t1, t2, n, series))
        if (a, t1, t2) in declared and not n and not series:
            zero.append((a, t1, t2))
    log("\n   → 声明了却零数据：%d 个 %s" % (len(zero), zero))
    if extra:
        log("   ⚠️⚠️ **目录未声明但实有数据** %d 个（采集侧正在整栏漏掉）：" % len(extra))
        for e in extra:
            log("        attr=%s type1=%s type2=%s → 条目 %d / 系列 %d" % e)
    else:
        log("   ✅ 目录未声明的组合确实都没有数据 —— 信任 getType2List 是成立的")

    log("\n[E] 移动 getTariffListInfo 是否随 cityId 变（移动要不要按地市采）")
    base_body = {"cellNum": "", "province": T.PROV, "isPublic": "1", "linkScn": "2",
                 "tariffAttr": "1", "type1": "1", "type2": "1",
                 "page": 1, "limit": 100, "fistLimit": 5000}
    r0 = T.call("nrtariff/new/Tariff/getTariffListInfo", dict(base_body))
    n0 = _count(r0)
    log("   不带 cityId            → %s" % n0)
    same = True
    for city in ("185", "180", "782"):
        for pname in ("cityId", "city", "cityCode", "areaCode"):
            b = dict(base_body)
            b[pname] = city
            c = _count(T.call("nrtariff/new/Tariff/getTariffListInfo", b))
            if c != n0:
                same = False
            log("   %s=%-10s → %s" % (pname, city, c))
    log("   → 传任何 cityId 参数%s"
        % ("对结果无影响 ✅（移动上游不按地市分数据，省级一次采全是对的）"
           if same else "**改变结果** ❌ ⇒ 必须按地市采集"))
    return {"declared": sorted(map(list, declared)),
            "cartesian_missing": [list(k) for k in miss],
            "unexpected_with_data": [list(e) for e in extra],
            "declared_zero": [list(k) for k in zero],
            "city_invariant": same,
            "ok": not extra}


def move_count(T, a, t1, t2):
    """直接请求一个 (attr, type1, type2) 组合，返回 (条目数, 系列数)（只看第 1 页，够判空）。"""
    body = {"cellNum": "", "province": T.PROV, "isPublic": "1", "linkScn": "2",
            "tariffAttr": a, "type1": t1, "type2": t2,
            "page": 1, "limit": 100, "fistLimit": 5000}
    r = T.call("nrtariff/new/Tariff/getTariffListInfo", body)
    d = r.get("data") if isinstance(r, dict) else None
    if not isinstance(d, dict):
        return 0, 0
    n, bs = 0, (d.get("beans") or [])
    for b in bs:
        n += len(b.get("nonModuleList") or [])
        for m in b.get("moduleList") or []:
            n += len(m.get("tariffList") or [])
    return n, len(bs)


def _count(r):
    d = r.get("data") if isinstance(r, dict) else None
    if not isinstance(d, dict):
        return "ERR:" + str(r)[:80]
    n = 0
    for b in d.get("beans") or []:
        n += len(b.get("nonModuleList") or [])
        for m in b.get("moduleList") or []:
            n += len(m.get("tariffList") or [])
    return "%d（系列 %d）" % (n, len(d.get("beans") or []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", default="unicom", choices=["unicom", "move"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    out = {}
    if a.net == "unicom":
        out["cities"] = uni_check_cities(a.workers)
        out["skeleton"] = uni_check_skeleton(a.workers)
        out["attrs"] = uni_check_attrs()
        ok = all(v.get("ok") for v in out.values())
    else:
        out["move"] = move_probe()
        ok = out["move"].get("ok")
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        log("已写出 " + a.json)
    log("\n>>> 结论：%s" % ("采集维度穷尽 ✅" if ok else "**存在未覆盖的采集维度** ❌"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
