#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地域归属（hb/cn + 地市码 cty）与下架标（st）的**离线回归探针**。

为什么要有它：这两套判据写在 `cloud/tariff/tariff_monitor.py` 的
``WHERE_OF`` / ``state_of`` 里，**判错了不会报错** ——
  · 地域判松了 → 其他省份的资费混进页面（用户看到不该有的条目）
  · 地域判严了 → 整批条目被静默丢弃（页面上「少了什么」没有任何提示）
  · 地市码判错 → 那个市的筛选恒为空，页面同样毫无提示
  · 下架判错 → 「已下架」页签里出现在售资费，或反之
所以每次改动判据后，用它对着**已归档快照**跑一遍，看四网的分布有没有突变。

它**不联网**、不写任何文件 —— 只读 `cloud/tariff/snapshots/` 下的快照，
与 `tariff_monitor` 共用同一份判据代码（import 进来的，不是抄一遍），
所以测的就是线上真正跑的那份逻辑。

```bash
python probes/probe_scope_state.py
```

参考基线（2026-09-22，河北四网；「地市码」＝**条目数**，非码命中次数）：
    move     3887 条  河北 2438 / 全国 1449   地市码 481 条（12 市）   已下架 0
    unicom   7899 条  河北 7899               无地市码                 已下架 3092
    cbn       324 条  河北 131 / 全国 193     无地市码                 已下架 116
    telecom   884 条  河北 884                地市码  31 条（12 市）   已下架 0
若某个数字突然为 0 或翻倍，先怀疑判据而不是上游。
"""
import collections
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "cloud", "tariff"))

import tariff_monitor as T                                    # noqa: E402

SNAP = os.path.join(REPO, "cloud", "tariff", "snapshots")
PREFIX = T.SNAP_PREFIX


def latest(prefix):
    fs = sorted(glob.glob(os.path.join(SNAP, prefix + "*.json.gz")))
    if not fs:
        return None, None
    with gzip.open(fs[-1], "rt", encoding="utf-8") as f:
        return json.load(f), os.path.basename(fs[-1])


def entries_of(o):
    """统一取条目：移动是 {groups:[{entries}]}，其余是扁平 {entries}。"""
    if o.get("entries") is not None:
        return o["entries"]
    return [e for g in (o.get("groups") or []) for e in (g.get("entries") or [])]


def groups_of(o):
    """统一取「组」：判下架时联通要看组的一级分类号，所以不能只拿扁平 entries。"""
    if o.get("groups") is not None:
        return o["groups"]
    return [{"type2": "", "tariffAttr": "", "entries": o.get("entries") or []}]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bad = []
    for code in ("move", "unicom", "cbn", "telecom"):
        o, fn = latest(PREFIX[code])
        if not o:
            print("%-8s 快照缺失（snapshots/%s*.json.gz）" % (code, PREFIX[code]))
            bad.append(code)
            continue
        base = T.data_day(o)
        sc, st, n = collections.Counter(), 0, 0
        cty_rows, cty_hit = 0, collections.Counter()
        for g in groups_of(o):
            for e in g["entries"]:
                n += 1
                s, cs = T.where_of(code, e)
                sc[s or "（无关·丢弃）"] += 1
                if cs:
                    cty_rows += 1
                    for c in cs:
                        cty_hit[T.HB_CITY.get(c, c)] += 1
                if T.state_of(code, e, g, base):
                    st += 1
        print("%-8s %5d 条 · 基线 %s · %s · 地市码 %d 条%s · 已下架 %d  ← %s"
              % (code, n, base,
                 " / ".join("%s %d" % (T.SCOPE_CN.get(k, k), v)
                            for k, v in sorted(sc.items())),
                 cty_rows,
                 ("（%d 市）" % len(cty_hit)) if cty_hit else "（无）",
                 st, fn))
        # 与判据设计相冲突的形态：宁可这里红，也不要页面上静默少条目
        if code == "move" and st:
            bad.append("move 出现了下架数据（上游语义变了？）")
        # ★ 联通/广电**上游没有地市级数据**（2026-09-22 实测）：
        #   联通 12 城 indexData 骨架逐条一致 + 明细零地域字段 + 旁路接口族不路由；
        #   广电 qryAreaList 只有省级粒度，传地市码回 BASE102。
        #   这里把「它们不该有地市码」钉住：哪天冒出来说明上游变了，得人工看。
        if code in ("unicom", "cbn") and cty_rows:
            bad.append("%s 上游本无地市级数据，却出现 %d 条带地市码" % (code, cty_rows))
        # 移动/电信则**必须**有：为 0 说明 applicableArea / _areaCodes 在归一化时丢了
        if code in ("move", "telecom") and not cty_rows:
            bad.append("%s 的地市码全丢 —— applicableArea / _areaCodes 没进行数据？" % code)
        # ★ 只在**快照自称采了下架**时才要求有下架数据。
        #   旧快照（改造前生成的）本来就没有那批，对着它断言只会天天报假警，
        #   而假警报多了真警就没人看了。includestopped 缺失 = 改造前的旧快照。
        if code in ("unicom", "cbn") and o.get("includeStopped") and not st:
            bad.append("%s 标了 includeStopped 却没有下架条目" % code)
        if code in ("unicom", "cbn") and not o.get("includeStopped") and not st:
            print("         └ 该快照是「未含下架」的旧版（改造前生成）；"
                  "下次真实巡检重采后才会带 includeStopped")
        if not sc.get("hb") and not sc.get("cn"):
            bad.append("%s 既无河北也无全国条目 —— 地域判据大概率错了" % code)
    if bad:
        print("\n!! 异常：")
        for b in bad:
            print("   -", b)
        return 1
    print("\n✅ 分布与预期形态一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
