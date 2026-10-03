# -*- coding: utf-8 -*-
"""联通采集独立复核：从上游实时重采一遍，与最新快照逐层对比。

  A. 目录层：12 城 × (attr×一级×二级) 组合的三级 id 并集 → 每城目录规模、组合覆盖
  B. 明细层：按「城市组」分批拉全部明细 → reportNo 集合对比（漏采 / 多采）
  C. 归属层：live 重算每条的城市归属 vs 快照 _allCity/_cityNames
  D. 字段层：抽样对比关键字段（费用/流量/通话/有效期）

用途：怀疑「联通采少了/采错了」时的人工深查工具。全量重采约 850 个请求、4~5 分钟，
**不进每日巡检**（巡检里的 probe_coverage_axes / probe_unicom_axes 是轻量版：
前者查骨架、后者查「目录有而快照无」的组合级缺口；本工具是逐条级的终审）。

用法：python probes/audit_unicom_live.py [快照文件]（缺省 = 最新一份 unicom_tariff_*.gz）
2026-10-03 首跑结论：漏采 0 / 多采 0 / 组合计数漂移 0 / 城市归属不一致 0 / 字段抽样无差异
—— 四层全对，采集链路无恙。
"""
import sys, os, json, gzip, time, random, collections, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import he_unicom_tariff as U

args = sys.argv[1:]
SNAP = args[0] if args else sorted(glob.glob(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "cloud", "tariff", "snapshots",
    "unicom_tariff_*.json.gz")))[-1]
snap = json.load(gzip.open(SNAP, "rt", encoding="utf-8"))
NM_OF = dict((c, nm) for nm, c in U.CITY_CODES)   # 城市码 → 中文名
snap_es = snap["entries"]
snap_by_rn = {str(e["reportNo"]).strip(): e for e in snap_es if e.get("reportNo")}
print("== 快照：%s · fetchedAt %s · %d 条" % (SNAP, snap.get("fetchedAt"), len(snap_es)))

t0 = time.time()
# ── A. 目录层 ──────────────────────────────────────────────
levels, meta = U.get_menu()
if not levels:
    print("!! 菜单获取失败:", str(meta)[:200]); sys.exit(2)
pairs, skipped = [], []
# ⚠️ 快照 includeStopped=True（停售套餐 3355 条在快照里），live 侧**必须同样包含**
#    停售栏目（firstLevel=99），否则会出现 3355 条假「快照多出」告警。
for lv in levels:
    if str(lv.get("firstLevel")) == U.STOPPED_FIRST:
        skipped.append(lv.get("firstLevelName"))   # 记录但**不跳过**，照样采
    for sub in lv.get("secondLevels") or []:
        for a in U.ATTRS:
            pairs.append((a, lv.get("firstLevel"), sub.get("secondLevel"),
                          lv.get("firstLevelName"), sub.get("secondLevelName")))
print("== live 菜单：一级 %d（含停售 %s）× attr2 = %d 组合"
      % (len(levels), "/".join(skipped) or "无", len(pairs)))

live = {}          # (a,f,s) -> {id: set(citycode)}
city_ids = {}      # code -> set(id)
grid_fails = []
for nm, code in U.CITY_CODES:
    city_ids[code] = set()
    for (a, f, s, fn, sn) in pairs:
        lst = U.get_level3(a, f, s, code)
        if lst is None:
            time.sleep(1.0)
            lst = U.get_level3(a, f, s, code)
        if lst is None:
            grid_fails.append((nm, a, f, s))
            continue
        for x in lst:
            i = x.get("id")
            if not i:
                continue
            live.setdefault((a, f, s), {}).setdefault(i, set()).add(code)
            city_ids[code].add(i)
print("== 目录网格取数失败格 %d 个 %s" % (len(grid_fails), grid_fails[:6] if grid_fails else ""))
for nm, code in U.CITY_CODES:
    print("   %s live 三级目录 %d 个" % (nm, len(city_ids[code])))
union = set()
for m in live.values():
    union |= set(m)
print("   12 城并集 %d 个" % len(union))

# ── B. 明细层（与采集器同法：按城市组分批，reportNo 并集去重） ──
jobs = []
for key, ids_map in live.items():
    by_city = collections.defaultdict(list)
    for i, cs in ids_map.items():
        by_city[tuple(sorted(cs))].append(i)
    for cs, ids in by_city.items():
        for k in range(0, len(ids), U.BATCH):
            jobs.append((key, cs, ids[k:k + U.BATCH]))
print("== 明细请求 %d 批（≤%d id/批）" % (len(jobs), U.BATCH))

live_rn = {}       # reportNo -> {"cities": set(code), "combo": (a,f,s), "e": dict}
det_fails = []
def job(j):
    key, cs, ids = j
    det, raw = U.get_detail(ids, U.CITY)
    if det is None:
        time.sleep(1.0)
        det, raw = U.get_detail(ids, U.CITY)
    return key, cs, det

from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=4) as ex:
    for key, cs, det in ex.map(job, jobs):
        if det is None:
            det_fails.append((key, cs, len(jobs)))
            continue
        for e in det:
            rn = str(e.get("reportNo") or "").strip()
            if not rn:
                continue
            rec = live_rn.setdefault(rn, {"cities": set(), "combo": key, "e": e})
            rec["cities"] |= set(cs)
print("== 明细批次失败 %d 个 %s" % (len(det_fails), det_fails[:4] if det_fails else ""))
print("== live 去重后 reportNo：%d 个（耗时 %.0fs）" % (len(live_rn), time.time() - t0))

# 集合对比
live_set, snap_set = set(live_rn), set(snap_by_rn)
missing = sorted(live_set - snap_set)     # 上游有、快照没有 ⇒ 漏采
extra = sorted(snap_set - live_set)       # 快照有、上游没有 ⇒ 上游已撤（或快照多余）
print("\n== B. 集合对比")
print("   漏采（live 有快照无）：%d %s" % (len(missing), missing[:8]))
print("   快照多出（上游已无）：%d %s" % (len(extra), extra[:8]))

# 组合级计数对比
snap_combo = collections.Counter((e.get("_attr"), e.get("_firstLevel"), e.get("_secondLevel"))
                                 for e in snap_es)
live_combo = collections.Counter(rec["combo"] for rec in live_rn.values())
allk = set(snap_combo) | set(live_combo)
drift = []
for k in sorted(allk, key=str):
    a, b = snap_combo.get(k, 0), live_combo.get(k, 0)
    if a != b:
        drift.append((k, a, b))
print("   组合级计数漂移 %d 个：" % len(drift))
for k, a, b in drift[:14]:
    print("     attr=%s %s/%s 快照 %d vs live %d" % (k[0], k[1], k[2], a, b))

# ── C. 归属层 ──────────────────────────────────────────────
n_all = n_city = n_bad = 0
bad_attr = []
for rn, rec in live_rn.items():
    se = snap_by_rn.get(rn)
    if not se:
        continue
    if len(rec["cities"]) >= 12:
        exp_all, exp_names = True, []
    else:
        exp_all, exp_names = False, sorted(NM_OF[c] for c in rec["cities"] if c in NM_OF)
    got_all = bool(se.get("_allCity"))
    got_names = sorted(se.get("_cityNames") or [])
    if got_all != exp_all or (not exp_all and got_names != exp_names):
        n_bad += 1
        if len(bad_attr) < 8:
            bad_attr.append((rn, exp_all, exp_names, got_all, got_names))
    n_all += 1 if exp_all else 0
    n_city += 0 if exp_all else 1
print("\n== C. 归属复核：live 全省通用 %d · 城市专属 %d；与快照不一致 %d 条" % (n_all, n_city, n_bad))
for b in bad_attr:
    print("   %s live(all=%s,%s) snap(all=%s,%s)"
          % (b[0], b[1], ",".join(b[2])[:30], b[3], ",".join(b[4])[:30]))

# ── D. 字段层抽样 ──────────────────────────────────────────
FIELDS = ["name", "feesStandard", "minute", "commonData", "sms", "validPeriod",
          "startDate", "endDate", "serviceContent", "broadBand"]
random.seed(7)
sample = random.sample(sorted(live_set & snap_set), min(30, len(live_set & snap_set)))
fdiff = collections.Counter()
fex = []
for rn in sample:
    a, b = live_rn[rn]["e"], snap_by_rn[rn]
    for f in FIELDS:
        if str(a.get(f) or "").strip() != str(b.get(f) or "").strip():
            fdiff[f] += 1
            if len(fex) < 5:
                fex.append((rn, f, str(a.get(f))[:40], str(b.get(f))[:40]))
print("\n== D. 字段抽样 30 条：差异字段 %s" % (dict(fdiff) or "无"))
for x in fex:
    print("   %s %s live=%r snap=%r" % x)
print("\n== 完成，总耗时 %.0fs" % (time.time() - t0))
