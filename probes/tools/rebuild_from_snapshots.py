#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用**现有快照**离线重建查询页（不联网、不归档），供分类重设计后的本地验证。

为什么不跑完整巡检：完整巡检要真连四网上游（慢且抖），而分类逻辑只吃快照 ——
直接把 snapshots/ 里四网最新快照喂给 build_html(archive=False)，产物 docs/index.html
与 CI 归档同构（只是没有 ca/ck 变更徽章，那些来自 diff，与分类无关）。
🔴 archive=False（铁律）：本机重建的页面绝不能盖掉入库官方归档，见 build_html 注释。

用法：
  python probes/tools/rebuild_from_snapshots.py
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tariff_monitor as T  # noqa: E402

SNAP_DIR = os.path.join(REPO, "cloud", "tariff", "snapshots")
PREFIX = {"move": "hebei_tariff_", "unicom": "unicom_tariff_",
          "telecom": "ct_tariff_", "cbn": "cbn_tariff_"}


def latest(prefix):
    cand = [(nm[len(prefix):-len(".json.gz")], os.path.join(SNAP_DIR, nm))
            for nm in os.listdir(SNAP_DIR)
            if nm.startswith(prefix) and nm.endswith(".json.gz")]
    return max(cand)[1] if cand else None


sources = {}
for code, pfx in PREFIX.items():
    p = latest(pfx)
    if not p:
        print("!! %s 无快照，留空壳" % code)
        continue
    with gzip.open(p, "rt", encoding="utf-8") as f:
        sources[code] = json.load(f)
    print("已载入 %s：%s" % (code, os.path.basename(p)))

T.build_html(sources, notice="", diffs=None, archive=False)
print("完成：docs/index.html（未归档、未入 git）")
