# -*- coding: utf-8 -*-
"""四网「地市粒度可达性」矩阵：直接读已归档快照，看条目里到底有没有地市级信息。

这是 2026-09-22 深挖结论（**移动 ✅ / 电信 ✅ / 联通 ❌ / 广电 ❌**）的可复现证据生成器。
结论已同时钉进 CI 断言（.github/workflows/tariff-daily.yml），本探针用于「上游语义变了」
时人工复核 —— 例如联通突然冒出 cty、或移动的 481 条地市专属掉到 0。

用法：
    python probes/probe_city_matrix.py              # 用最新一份快照
    python probes/probe_city_matrix.py 20260922     # 指定日期（文件名里的 YYYYMMDD）

判据（只看上游给的字段，不猜）：
    地域字段 = applicableArea / _areaCodes / city / province / _areaNames …
    命中 = 上述字段里出现河北 12 地市码（4 位数字）
"""
import collections
import glob
import gzip
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "cloud", "tariff", "snapshots")

# 河北 12 地市（联通行政代码前 4 位；3121 = 省直辖县级市 定州/辛集）
CITY = {"3100": "邯郸", "3110": "石家庄", "3120": "保定", "3121": "省直辖(定州/辛集)",
        "3130": "张家口", "3140": "承德", "3150": "唐山", "3160": "廊坊",
        "3170": "沧州", "3180": "衡水", "3190": "邢台", "3350": "秦皇岛"}

# 网 → 快照文件名模板（移动 = hebei_，其余各一张）
NET_FILE = [("移动", "hebei_tariff_%s.json.gz"),
            ("联通", "unicom_tariff_%s.json.gz"),
            ("广电", "cbn_tariff_%s.json.gz"),
            ("电信", "ct_tariff_%s.json.gz")]

PROBE_FIELDS = ("applicableArea", "_areaCodes", "applicableAreaLabel", "city", "province",
                "_areaNames", "areaStat", "allProvince", "county", "district")


def newest_day():
    ds = []
    for p in glob.glob(os.path.join(SNAP, "*_tariff_*.json.gz")):
        m = re.search(r"_(\d{8})\.json\.gz$", p)
        if m:
            ds.append(m.group(1))
    if not ds:
        raise SystemExit("快照目录里没有任何 *_tariff_YYYYMMDD.json.gz：%s" % SNAP)
    return max(ds)


def entries_of(o):
    """兼容两种形态：分组（groups[].entries）与平铺（entries）。"""
    out = []
    for g in o.get("groups") or []:
        out.extend(g.get("entries") or [])
    if not out:
        out = o.get("entries") or []
    return out


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else newest_day()
    print("快照目录：%s" % SNAP)
    print("基线日期：%s\n" % day)
    matrix = {}
    for net, tpl in NET_FILE:
        p = os.path.join(SNAP, tpl % day)
        if not os.path.exists(p):
            print("== %s：快照缺失 %s ==" % (net, os.path.basename(tpl % day)))
            matrix[net] = None
            continue
        o = json.load(gzip.open(p, "rt", encoding="utf-8"))
        es = entries_of(o)
        hits = collections.Counter()
        city_hits = collections.Counter()
        city_rows = 0
        for e in es:
            row_hit = False
            for f in PROBE_FIELDS:
                v = e.get(f)
                if v not in (None, "", [], {}):
                    hits[f] += 1
            blob = " ".join(str(e.get(f) or "") for f in
                            ("applicableArea", "_areaCodes", "city", "province"))
            for t in re.findall(r"\d{4}", blob):
                if t in CITY:
                    city_hits[CITY[t]] += 1
                    row_hit = True
            if row_hit:
                city_rows += 1
        n = len(es)
        matrix[net] = {"n": n, "city_rows": city_rows, "cities": sorted(city_hits)}
        print("== %s  %d 条  (allProvince=%s)" % (net, n, o.get("allProvince")))
        print("   非空地域字段: %s" % (dict(hits) or "（无任何地域字段）"))
        if city_hits:
            print("   地市码命中 %d 条（%d 市）: %s"
                  % (city_rows, len(city_hits), dict(city_hits.most_common())))
        else:
            print("   地市码命中: 无 ⇒ 该网上游**没有地市级维度**")
        print()

    print("=== 矩阵（条目级地市码）===")
    expect = {"移动": "有", "电信": "有", "联通": "无", "广电": "无"}
    ok = True
    for net, _ in NET_FILE:
        m = matrix.get(net)
        if m is None:
            print("  %-4s  快照缺失" % net)
            ok = False
            continue
        got = "有" if m["city_rows"] else "无"
        flag = "OK " if got == expect[net] else "！变"
        if got != expect[net]:
            ok = False
        print("  %-4s  %5d 条 · 地市码 %4d 条（%2d 市）  期望=%s 实际=%s  [%s]"
              % (net, m["n"], m["city_rows"], len(m["cities"]), expect[net], got, flag))
    print()
    if ok:
        print("✅ 与 2026-09-22 结论一致：移动/电信有地市级，联通/广电没有。")
    else:
        print("⚠️ 与基线结论不一致 —— 上游语义可能已变，请人工复核（并同步 CI 断言）。")
        sys.exit(1)


if __name__ == "__main__":
    main()
