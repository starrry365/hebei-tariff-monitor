#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上海电信「资费专区」适配器 —— 隐秘第五源（自用，不进四网主链路）。

════════════════════════════════════════════════════════════════════
★ 为什么能做这么小：与河北电信走的是**同一个接口族**（www.189.cn
  wapportalweb / tariffSection.do，瑞数 WAF + AES 包体，见 ct_monitor.py
  模块头的完整逆向记录），唯一差别是省份代码：
      河北 = 609906（主链路，ct_monitor）
      上海 = 600102（本模块，2026-10-06 实测 508 条：套餐 87 / 加装包 332 /
             营销活动 89，字段与河北同族，含 reportNo/onlineDay/tariffAttr）
  省码表藏在页面组件 Index-1f2bc0ae.js（按字母分组的 provineList），
  600102 之外全表见 harvest_sh.js 同目录的探针产物。

★ 边界（与主链路四网刻意不同）：
  · 不进 tariff_monitor.NETS 注册表 —— 页签、总览、变更历史、推送全部不感知；
  · 不写 .shct_raw.json 以外的任何共享状态（state.json / history.json 不碰）；
  · 采不到就抛错，由 build_sh.py 退回**本目录快照** snapshots/shct_latest.json；
  · 归一化直接 import ct_monitor._normalize（prov_name="上海"），
    费用/流量/日期的口径规则与河北电信**字面上同源**，不会漂。
════════════════════════════════════════════════════════════════════
"""
import gzip
import json
import os
import re
import sys
import time

import ct_monitor

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, ".shct_raw.json")
RAW_GZ = RAW + ".gz"
SNAP = os.path.join(BASE, "snapshots", "shct_latest.json")

PROV = "600102"
PROV_NAME = "上海"
PROV_URL = "https://www.189.cn/wapportalweb/rateZone/index.html?provCode=" + PROV


def log(m):
    print(time.strftime("[%H:%M:%S] ") + m, flush=True)


def raw_path():
    """原始采集产物的实际路径（.shct_raw.json 优先，退回 .gz），都没有则 None。"""
    return RAW if os.path.exists(RAW) else (RAW_GZ if os.path.exists(RAW_GZ) else None)


def raw_day():
    """原始产物里的**采集日期**（YYYYMMDD）；缺失 / 损坏返回空串。

    与 ct_monitor.raw_day 同一条规矩：走 _local_ts 转北京时间，否则 UTC 时区
    会把「今天采的」判成昨天，闸门悄悄退回快照渲染（见 ct_monitor._local_ts
    的 🔴 注释，那里踩过）。"""
    p = raw_path()
    if not p:
        return ""
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
    except Exception:
        return ""
    return ct_monitor._local_ts(raw.get("fetchedAt"))[:10].replace("-", "")


def fetch_all():
    """读 .shct_raw.json（真实浏览器采集产物，见 harvest_sh.js）→ 中间格式。

    🔴 这里**不发任何网络请求** —— 与 ct_monitor.fetch_all 同一原则：
    本函数只做「原始数据 → 移动字段名契约」的纯转换。"""
    path = raw_path()
    if not path:
        raise SystemExit(
            "!! 缺少上海电信原始采集数据 %s（或 .gz）\n"
            "   采集方法（必须真实 Chrome，脚本直连会被瑞数 WAF 拦）：\n"
            "   · bash probes/tools/ct_browser/ci_grab.sh（采河北时顺手采上海）\n"
            "   · 本机单采：浏览器过挑战后 eval probes/tools/ct_browser/harvest_sh.js" % RAW)
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    if not str(raw.get("provCode")) == PROV:
        raise SystemExit("!! .shct_raw.json 的 provCode=%s ≠ %s（换省了？先改 PROV）"
                         % (raw.get("provCode"), PROV))

    groups, entries, seen = [], [], set()
    l1map = {x.get("name"): x.get("id") for x in (raw.get("lableOneList") or [])}
    for name, sec in (raw.get("sections") or {}).items():
        arr = sec.get("zoneTitleList") or []
        lable1_id = l1map.get(name) or ""
        # 🔴 服务端 count 是分类内真实条数 —— len 对不上说明被截断，宁可报错
        #   （与 ct_monitor 同一条判据，残页绝不进快照）
        if sec.get("count") is not None and int(sec["count"]) != len(arr):
            raise SystemExit("!! 分类「%s」count=%s ≠ 实收 %d（疑似截断，拒绝采空）"
                             % (name, sec["count"], len(arr)))
        g = {"tariffAttr": "2", "type2": lable1_id, "type2Name": name, "entries": []}
        for e in arr:
            n = ct_monitor._normalize(e, name, lable1_id, prov_name=PROV_NAME)
            # 上海条目自带 lable2Name（河北省级接口没有）—— 有就用，细分维度更准
            n["type3Name"] = str(e.get("lable2Name") or "")
            k = n["reportNo"] or ("name:" + n["name"])
            if k in seen:
                continue
            seen.add(k)
            g["entries"].append(n)
            entries.append(n)
        groups.append(g)
    groups.sort(key=lambda g: ({"套餐": 0, "加装包": 1, "营销活动": 2}.get(g["type2Name"], 9),
                               g["type2Name"]))
    return {"province": PROV, "provinceName": PROV_NAME,
            "endpoint": ct_monitor.EP, "fetchedAt": ct_monitor._local_ts(raw.get("fetchedAt")),
            "sourceUrl": PROV_URL,
            "groups": groups, "entries": entries}


def save_snapshot():
    """采集成功后把**归一化产物**覆盖写进 snapshots/shct_latest.json（幂等，后写覆盖）。

    ★ 为什么存归一化后的而不是原始包：快照的唯一消费者是 build_sh.py 的
      「采不到就退快照」路径，存中间格式让快照不依赖 AES/接口细节的存活；
    ★ 覆盖写 + fetchedAt 在文件里：重复跑无害（幂等），新旧一眼可辨。"""
    d = fetch_all()
    os.makedirs(os.path.dirname(SNAP), exist_ok=True)
    with open(SNAP, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "--save":
        d = save_snapshot()
        log("上海电信快照已存 %s" % SNAP)
    else:
        d = fetch_all()
    stat = {"%s/%s" % (g["tariffAttr"], g["type2Name"]): len(g["entries"]) for g in d["groups"]}
    log("上海电信 %d 条 %s（fetchedAt=%s）" % (len(d["entries"]), stat, d["fetchedAt"]))
    no_fee = sum(1 for e in d["entries"] if not e["fees"])
    log("  非月费口径（f 留空，费用原文在 otherContent）：%d 条" % no_fee)
