# -*- coding: utf-8 -*-
"""四网「地市粒度可达性」矩阵：直接读已归档快照，看条目里到底有没有地市级信息。

产物：**移动 ✅ / 电信 ✅ / 联通 ✅ / 广电 ❌** 的可复现证据。
（2026-09-22 那版写的是「联通 ❌」——**已被推翻**：联通的城市归属不写在条目字段里，
  而是采集侧按「该资费的三级目录出现在哪些城市」逐条记的 `_cityNames` / `_allCity`，
  所以「扫 4 位地市码」这套判据扫不到它 ⇒ 当年判成了「没有」。详见
  `docs/联通河北资费-地市维度纠错-20260924.md`。
  本探针现在**两种来源都认**：字段里的 4 位地市码（移动/电信）+ 采集侧的 `_cityNames`（联通）。）

用途：结论已同时钉进 CI 断言（.github/workflows/tariff-daily.yml），本探针用于
「上游语义变了」时人工复核 —— 例如广电突然冒出地市、或移动的 486 条地市专属掉到 0。

用法：
    python probes/probe_city_matrix.py              # 用最新一份快照
    python probes/probe_city_matrix.py 20260922     # 指定日期（文件名里的 YYYYMMDD）

⚠️ 快照是**当时那版采集器**的产物：2026-09-24 及更早的联通快照是「单城采集」，
   条目上**没有** `_cityNames` ⇒ 对它跑会报「联通 无」。那不是上游变了，是快照比采集器老。

判据（只看上游/采集侧给出的东西，不猜）：
    移动/电信 = applicableArea / _areaCodes / city / province 里出现河北 12 地市**码**（4 位数字）
    联通      = 条目上的 `_cityNames`（城市名列表）非空
                 · `_allCity=1` ⇒ 覆盖全部城市 ⇒ 全省通用（采集侧刻意不落列表）
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
                "_areaNames", "areaStat", "county", "district",
                # 联通侧的城市归属由采集器写在条目上（不是上游字段，见 he_unicom_tariff）
                "_cityNames", "_allCity")


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
            # ★ 联通侧：城市归属由**采集侧**按「这条资费的三级目录出现在哪些城市」逐条写在
            #   条目上（`_cityNames`，见 he_unicom_tariff.collect）—— 它不带 4 位地市码，
            #   上面那套「扫码表」扫不到，必须单独认。`_allCity=1` = 全部城市都有 ⇒ 全省通用。
            for nm in (e.get("_cityNames") or []):
                city_hits[nm] += 1
                row_hit = True
            if row_hit:
                city_rows += 1
        n = len(es)
        matrix[net] = {"n": n, "city_rows": city_rows, "cities": sorted(city_hits)}
        # ★ 口径必须与下面那个矩阵**同一个** `city_rows`：
        #   早先这里另算了一个「只数 `_cityNames`/`cty`」的 n_cty，于是电信那行印出
        #   「带地市归属 0 条」——电信的归属是 `_areaCodes` 里的 4 位码，不算 `cty`，
        #   明明有 31 条却印 0。两个数在同一段输出里互相矛盾，最容易把人带沟里。
        # ★ 移动 / 电信 这里只数**上游码**：构建期还有一层「文案兜底」（雄安新区 /
        #   华北油田 这类没有码的区域从名称里认），那层发生在 build，快照里看不到 ⇒
        #   页面上移动是 486 条而不是这里的 481。
        tail = "（仅上游字段；构建期文案兜底另算，见 rows_of）" if net in ("移动", "电信") else ""
        print("== %s  %d 条  (带地市归属 %d 条%s)" % (net, n, city_rows, tail))
        print("   非空地域字段: %s" % (dict(hits) or "（无任何地域字段）"))
        if city_hits:
            print("   地市命中 %d 条（%d 市）: %s"
                  % (city_rows, len(city_hits), dict(city_hits.most_common())))
        else:
            if net == "联通":
                print("   地市命中: 无 —— ⚠️ 若本快照早于 2026-09-24，那是**旧采集器（单城）**"
                      "的产物：条目上没有 `_cityNames`，不是上游没有。")
            else:
                print("   地市命中: 无 ⇒ 该网没有地市级维度（上游/采集都没给）")
        print()

    print("=== 矩阵（条目级地市归属）===")
    expect = {"移动": "有", "电信": "有", "联通": "有", "广电": "无"}
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
        print("  %-4s  %5d 条 · 带地市归属 %4d 条（%2d 市）  期望=%s 实际=%s  [%s]"
              % (net, m["n"], m["city_rows"], len(m["cities"]), expect[net], got, flag))
        if net == "联通" and got == "无":
            print("         ⚠️ 若本快照早于 2026-09-24，那是**旧采集器（单城）**的产物、"
                  "不是上游变了：")
            print("            旧快照条目上没有 `_cityNames`。先采一轮再看（python probes/he_unicom_tariff.py dump）。")
    print()
    if ok:
        print("✅ 与现行结论一致：移动 / 电信 / 联通 有条目级地市归属，广电没有。")
    else:
        print("⚠️ 与基线结论不一致 —— 上游语义或采集器可能已变，请人工复核（并同步 CI 断言）。")
        sys.exit(1)


if __name__ == "__main__":
    main()
