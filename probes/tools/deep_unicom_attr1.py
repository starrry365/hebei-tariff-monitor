#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联通全国板块（attr=1）深度采集 + 完整性核查（v2，修正 v1 两处比对瑕疵）。

v1 的问题（为什么重跑）：
  ① Phase 2 忘了给 id_cities 填城市码 → 每批 cset=[] → 全部条目 _cityNames=[]，
     L3 地市归属比对 6252 条「假差异」；
  ② 明细没过 U.normalize() → 字段名停留在上游原名（feesStandard/startDate/...），
     L4 与已归一化的上一轮数据比，fees/onlineDay/offineDay/data 全量「假差异」。
  ③ 上一轮的目录并集没有落盘（refetch 存的是 regroup 后结构，无 level3），
     目录层改为双向锚定：本轮 attr=1 并集 + attr=2 并集 应等于已核实基准 9310。

核查设计（针对 collect() 的三个「够用但不深」的点）：
  ① 菜单骨架 12 城全查（主链路只看邢台一城）；
  ② 目录格子硬重试（最多 6 次、退避）—— 上轮 city_scope 有 2 格失败
     （2/2003@秦皇岛、99/4@邯郸，均 a=1），必须补齐；
  ③ attr=1 明细全量重拉，与上一轮四层比对：reportNo 集合、分栏目条数、
     地市归属、8 字段全量比对。

用法：
  python probes/tools/deep_unicom_attr1.py            # 全国板块（attr=1）
  python probes/tools/deep_unicom_attr1.py --attr 2   # 河北板块（attr=2）
"""
import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "probes"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import he_unicom_tariff as U  # noqa: E402

PREV = os.path.join(REPO, "evidence", "unicom-refetch-20261003.json.gz")
_ap = argparse.ArgumentParser()
_ap.add_argument("--attr", default="1", choices=["1", "2"], help="1=全国板块 2=河北板块")
ARGS = _ap.parse_args()
ATTR_DEEP = ARGS.attr
BNAME = {"1": "全国", "2": "河北"}
OUT = os.path.join(REPO, "evidence", "unicom-deep-attr%s-20261003.json.gz" % ATTR_DEEP)
ANCHOR_UNION = 9310              # 已核实基准：12 城×22 组合×2 attr 目录并集（audit_unicom_live）
CITIES = list(U.CITY_CODES)
BASE_CITY = U.CITY
W = 6


def log(m):
    print(m, flush=True)


def hard_level3(attr, f, s, code, tries=6):
    for i in range(tries):
        lst = U.get_level3(attr, f, s, code)
        if lst is not None:
            return lst, i
        time.sleep(min(0.8 * (i + 1), 5.0))
    return None, tries


def hard_detail(ids, tries=5):
    for i in range(tries):
        det, _raw = U.get_detail(ids, BASE_CITY)
        if det is not None:
            return det, i
        time.sleep(min(0.8 * (i + 1), 5.0))
    return None, tries


t0 = time.time()

# ── Phase 1：12 城菜单骨架全查 ────────────────────────────────────────────
log("== Phase 1：12 城菜单骨架全查 ==")
levels_base, _m = U.get_menu(BASE_CITY)
base_combos = set()
for lv in levels_base or []:
    for sub in lv.get("secondLevels") or []:
        base_combos.add((str(lv.get("firstLevel")), str(sub.get("secondLevel")),
                         lv.get("firstLevelName"), sub.get("secondLevelName")))


def m_cell(nm_code):
    nm, code = nm_code
    lvs, _m2 = U.get_menu(code)
    if not lvs:
        return nm, None
    return nm, set((str(lv.get("firstLevel")), str(sub.get("secondLevel")),
                    lv.get("firstLevelName"), sub.get("secondLevelName"))
                   for lv in lvs for sub in lv.get("secondLevels") or [])


with ThreadPoolExecutor(max_workers=W) as ex:
    menu = dict(ex.map(m_cell, CITIES))
menu_fails = [nm for nm, s in menu.items() if s is None]
all_combos = set(base_combos)
extra_by_city = {}
for nm, s in menu.items():
    if not s:
        continue
    all_combos |= s
    ex_ = s - base_combos
    if ex_:
        extra_by_city[nm] = ex_
log("菜单：基础城组合 %d · 12 城并集 %d · 基础城之外多出 %d %s · 菜单失败城 %s"
    % (len(base_combos), len(all_combos), sum(len(v) for v in extra_by_city.values()),
       ("：" + "; ".join("%s %s/%s" % (nm, c[0], c[1])
                         for nm, ss in extra_by_city.items() for c in sorted(ss)))
       if extra_by_city else "", menu_fails or "无"))

# ── Phase 2：目录深采（两个 attr 都扫：attr=1 是主角，attr=2 当并集锚）────
log("== Phase 2：目录深采（22 组合 × 12 城 × 2 attr，格子硬重试）==")
combos = sorted(all_combos, key=lambda x: (x[0], x[1]))
combo_name = {(f, s_): (fn, sn) for (f, s_, fn, sn) in combos}
tasks = [(a, c, nm_code) for a in U.ATTRS for c in combos for nm_code in CITIES]
combo_ids = defaultdict(dict)          # (attr, fl, sl) -> {id: name}
id_cities = defaultdict(set)           # (attr, id) -> {城市码}
cell_fail = []


def d_cell(t):
    a, (f, s_, _fn, _sn), (nm, code) = t
    lst, tries = hard_level3(a, f, s_, code)
    # ★ 必须回传 code（城市码）：出口归一化用 nm_of（码→名）反查，存名字会
    #   全部查不到 → 城市专属条目 _cityNames 变空集（v1 已踩，L3 假差异 139 条）
    return a, (f, s_), code, lst, tries


with ThreadPoolExecutor(max_workers=W) as ex:
    for a, key, code, lst, _tries in ex.map(d_cell, tasks):
        if lst is None:
            cell_fail.append((a, key[0], key[1], code))
            continue
        for x in lst:
            i = x.get("id")
            if i:
                combo_ids[(a,) + key][i] = x.get("name")
                id_cities[(a, i)].add(code)
union = {a: set(i for (aa, i) in id_cities if aa == a) for a in U.ATTRS}
log("目录：attr=1 并集 %d · attr=2 并集 %d · 两attr总和 %d（基准 %d，%s）· 格子 %d、失败 %s"
    % (len(union["1"]), len(union["2"]), len(union["1"]) + len(union["2"]), ANCHOR_UNION,
       "✅对上" if len(union["1"]) + len(union["2"]) == ANCHOR_UNION else "❌对不上",
       len(tasks),
       ("%d → " % len(cell_fail)) + "; ".join("a=%s %s/%s@%s" % t for t in cell_fail[:8])
       if cell_fail else "0"))

# ── Phase 3：%s板块明细全量重拉（按 (组合, 城市集) 分批，镜像 collect）────
log("== Phase 3：%s板块（attr=%s）明细全量重拉 ==" % (BNAME[ATTR_DEEP], ATTR_DEEP))
jobs = []
for (a, f, s_), m in combo_ids.items():
    if a != ATTR_DEEP:
        continue
    by_city = defaultdict(list)
    for i in m:
        by_city[tuple(sorted(id_cities[(a, i)]))].append(i)
    for cset, ids in by_city.items():
        for k in range(0, len(ids), U.BATCH):
            jobs.append(((f, s_), ids[k:k + U.BATCH], list(cset)))
log("明细批次 %d 个" % len(jobs))


def j_run(j):
    key, ids, cset = j
    det, tries = hard_detail(ids)
    return key, cset, det


det_fail, entries, rep_cities = [], [], {}
with ThreadPoolExecutor(max_workers=W) as ex:
    for n_done, (key, cset, det) in enumerate(ex.map(j_run, jobs), 1):
        if det is None:
            det_fail.append((key, cset[:3]))
            continue
        fn, sn = combo_name[key]
        for e in det:
            e["_attr"] = ATTR_DEEP
            e["_firstLevel"] = key[0]
            e["_secondLevel"] = key[1]
            e["_firstLevelName"] = fn
            e["_secondLevelName"] = sn
            e["_cities"] = cset
            rn = str(e.get("reportNo") or "").strip()
            if rn:
                rep_cities.setdefault(rn, set()).update(cset)
            entries.append(e)
        if n_done % 100 == 0:
            log("   进度 %d/%d 批" % (n_done, len(jobs)))
log("明细：批次 %d · 失败 %s · 原始条目 %d"
    % (len(jobs), ("%d" % len(det_fail)) + (" → " + "; ".join(
        "%s/%s" % (k[0][0], k[0][1]) for k in det_fail[:6]) if det_fail else ""),
      len(entries)))

# 去重 + normalize + 地市归一（镜像 collect 的出口语义，v1 漏了 normalize）
seen, uniq = set(), []
for e in entries:
    k = str(e.get("reportNo") or "").strip() or json.dumps(e, ensure_ascii=False, sort_keys=True)
    if k in seen:
        continue
    seen.add(k)
    if k in rep_cities:
        e["_cities"] = sorted(rep_cities[k])
    U.normalize(e)
    uniq.append(e)
nm_of = dict((c, nm) for nm, c in CITIES)
for e in uniq:
    cs = e.pop("_cities", None) or []
    if len(cs) >= len(CITIES):
        e["_allCity"] = 1
    else:
        e["_cityNames"] = [nm_of[c] for c in cs if c in nm_of]
log("去重：%d → %d 条（重复 %d）" % (len(entries), len(uniq), len(entries) - len(uniq)))

with gzip.open(OUT, "wt", encoding="utf-8") as fh:
    json.dump({"attr": ATTR_DEEP, "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
               "entries": uniq,
               "unionAttr1": sorted(union["1"]), "unionAttr2": sorted(union["2"]),
               "menuExtra": {nm: sorted("%s/%s" % (c[0], c[1])) for nm, c in extra_by_city.items()},
               "fails": {"menu": menu_fails, "cell": cell_fail, "detail": len(det_fail)}},
              fh, ensure_ascii=False)
log("落盘：%s（%.0f KB）" % (OUT, os.path.getsize(OUT) / 1024))

# ── Phase 4：与上一轮四层比对 ────────────────────────────────────────────
log("== Phase 4：与上一轮四层比对 ==")
prev = json.load(gzip.open(PREV, "rt", encoding="utf-8"))
prev_ent = [e for e in prev["entries"] if str(e.get("_attr")) == ATTR_DEEP]
kf = lambda e: str(e.get("reportNo") or "").strip()
prev_by_rn = {kf(e): e for e in prev_ent if kf(e)}
now_by_rn = {kf(e): e for e in uniq if kf(e)}
rn_new = set(now_by_rn) - set(prev_by_rn)
rn_lost = set(prev_by_rn) - set(now_by_rn)
log("L1 reportNo：上轮 %d · 本轮 %d · 新增 %d · 消失 %d"
    % (len(prev_by_rn), len(now_by_rn), len(rn_new), len(rn_lost)))
for r in list(rn_new)[:8]:
    log("   + %s %s" % (r, (now_by_rn[r].get("name") or "")[:40]))
for r in list(rn_lost)[:8]:
    log("   - %s %s" % (r, (prev_by_rn[r].get("name") or "")[:40]))

cnt_prev, cnt_now = Counter(), Counter()
for e in prev_ent:
    cnt_prev[(str(e.get("_firstLevel")), str(e.get("_secondLevel")))] += 1
for e in uniq:
    cnt_now[(str(e.get("_firstLevel")), str(e.get("_secondLevel")))] += 1
drift = [(k, cnt_prev.get(k, 0), cnt_now.get(k, 0))
         for k in sorted(set(cnt_prev) | set(cnt_now))
         if cnt_prev.get(k, 0) != cnt_now.get(k, 0)]
log("L2 分栏目条数：栏目 %d 个，有出入 %d 个%s"
    % (len(set(cnt_prev) | set(cnt_now)), len(drift),
       "；" + "; ".join("%s/%s %d→%d" % (k[0], k[1], a, b) for k, a, b in drift[:8]) if drift else ""))

city_diff = []
for rn in set(prev_by_rn) & set(now_by_rn):
    def cset(e):
        if e.get("_cityNames"):
            return set(e["_cityNames"])
        return "ALL" if e.get("_allCity") else set()
    ps, qs = cset(prev_by_rn[rn]), cset(now_by_rn[rn])
    if ps != qs:
        city_diff.append((rn, sorted(ps) if isinstance(ps, set) else ps,
                          sorted(qs) if isinstance(qs, set) else qs))
log("L3 地市归属：不一致 %d 条%s"
    % (len(city_diff), "；例：" + "; ".join("%s %s→%s" % t for t in city_diff[:5]) if city_diff else ""))

fld_diff = Counter()
ex_fld = defaultdict(list)
for rn in set(prev_by_rn) & set(now_by_rn):
    p, q = prev_by_rn[rn], now_by_rn[rn]
    for fld in ("fees", "data", "minute", "sms", "broadBand", "validPeriod",
                "onlineDay", "offineDay"):
        if str(p.get(fld) or "") != str(q.get(fld) or ""):
            fld_diff[fld] += 1
            if len(ex_fld[fld]) < 2:
                ex_fld[fld].append("%s: %r→%r" % (rn[:14], str(p.get(fld))[:24], str(q.get(fld))[:24]))
log("L4 字段比对（8 字段 × %d 条）：%s"
    % (len(set(prev_by_rn) & set(now_by_rn)),
       ("；".join("%s %d（%s）" % (f2, n, "; ".join(ex_fld[f2])) for f2, n in sorted(fld_diff.items()))
        if fld_diff else "零差异")))

ok = not (menu_fails or cell_fail or det_fail or rn_new or rn_lost or drift
          or city_diff or fld_diff)
log("== 完成 / %.0fs · 结论：%s ==" % (
    time.time() - t0,
    "✅ %s板块采集完整（菜单无漏、格子无失败、批次无失败、四层比对零差异）" % BNAME[ATTR_DEEP] if ok
    else "⚠️ 存在差异/失败，见上方各行"))
sys.exit(0 if ok else 4)
