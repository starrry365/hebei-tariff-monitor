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
  · 不进 tariff_monitor.NETS 注册表 —— 页签、总览、推送全部不感知；
  · 不写 state.json / history.json 等任何**共享**状态；
    变化跟踪（2026-10-07 加）用自己的三件套，与主链路物理隔离：
      snapshots/shct_YYYYMMDD.json.gz   每日快照归档（diff 的比对基准）
      changes/shct-YYYY-MM-DD.md        变更报告（与四网的 {tag}-日期.md 同目录不重名）
      shct_history.json                 变更历史（sh.html 时间线的唯一数据源）
    ★ 为什么不直接写主 history.json：主页时间线按 NET_LS 排序渲染，上海电信
      混进去要么挤在末尾、要么得改主页 ——「隐秘自用」就不该在主页露脸。
  · 采不到就抛错，由 build_sh.py 退回**本目录快照** snapshots/shct_latest.json；
  · 归一化直接 import ct_monitor._normalize（prov_name="上海"），
    费用/流量/日期的口径规则与河北电信**字面上同源**，不会漂。

变化跟踪（2026-10-07，参考河北四网管线）：
  · diff / 快照 / 样本 / 时间线裁剪**直接复用** tariff_monitor 的同名函数
    （index_rows / diff_rows / build_smp / save_snapshot / load_prev /
     hist_for_page）—— 身份键、字段口径、样本结构字面上同源，页面渲染
    与河北时间线长一个样。各写一份迟早漂移，这是本仓库吃过的亏。
  · 报告**自写**不复用 write_report：主链路的 write_report 内部会
    hist_append 进 history.json（共享状态，见上）。上海的报告段落后自足。
  · 护栏接 change_guard.audit_diff（与四网同一条），失败**放行原始 diff** ——
    「判不出来就保留」的同一条原则；但 audit_diff 本身抛异常时降级为无护栏。
════════════════════════════════════════════════════════════════════
"""
import gzip
import json
import os
import re
import sys
import time

import ct_monitor
import tariff_monitor as TM

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


# ══ 变化跟踪（2026-10-07 加，参考河北四网管线）══════════════════════
# 三件套（见模块头）：每日快照归档 / changes 报告 / shct_history.json。
# 全部是上海**私有**文件 —— 主链路的 state.json / history.json 一个字节都不碰。

SNAP_PREFIX = "shct_"
NET_NAME = "上海电信"
TAG = "shct"
HIST_FILE = os.path.join(BASE, "shct_history.json")
HIST_KEEP = 400        # 与主链路同一量级：约一年的日级记录
CHG = os.path.join(BASE, "changes")


def load_history():
    """读 shct_history.json。缺失/损坏一律回空表 —— 展示用旁路数据，
    不能因为一份坏文件把小页构建打断（与 TM.load_history 同一条规矩）。"""
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"schema": 1, "items": []}


def hist_append(rec):
    """往 shct_history.json 写一条巡检记录 —— 同一天只留最新一条。

    去重键是 **日期**（单网不需要 (日期,网) 二元组，但保留同款语义），
    覆盖时**原位替换**，尾部截到 HIST_KEEP。indent=1 与显式 newline 的
    理由见 TM.hist_append —— 那两条注释一字不改地适用这里。"""
    h = load_history()
    items = list(h["items"])
    key = rec.get("d")
    for i, x in enumerate(items):
        if x.get("d") == key:
            items[i] = rec
            break
    else:
        items.append(rec)
    h["schema"] = 1
    h["items"] = items[-HIST_KEEP:]
    try:
        import change_guard as _G
        _G.write_json(HIST_FILE, h)
    except Exception:
        with open(HIST_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(h, f, ensure_ascii=False, indent=1)
    return h


def last_hist(day=""):
    """shct 历史里该日期**之前**最近的一条记录（护栏回弹判据要用）。

    🔴 rec 的 ``d`` 必须与这里的比较口径一致（都是带横线的 YYYY-MM-DD）：
       字符串比较只在同一种格式下才是时间序 —— 8 位与横线混用会乱序。"""
    """shct 历史里该日期**之前**最近的一条记录（护栏回弹判据要用）。"""
    items = load_history().get("items") or []
    day = (day or "")[:10]
    best = None
    for x in items:
        xd = str(x.get("d") or "")
        if day and xd >= day:
            continue
        if best is None or xd > str(best.get("d") or ""):
            best = x
    return best


def prune_snapshots(keep=60):
    """shct_ 前缀的过期快照清理（与主链路 KEEP_SNAPSHOTS=60 同一量级）。"""
    for p in TM.snap_paths(SNAP_PREFIX)[:-keep] if len(TM.snap_paths(SNAP_PREFIX)) > keep else []:
        os.remove(p)
        log("清理过期上海快照 %s" % os.path.basename(p))


def _write_report(prev, d, idx, oidx, added, removed, changed, guard, day):
    """变更报告 changes/shct-YYYY-MM-DD.md。

    ★ 自写而不复用 TM.write_report：那一份内部会 hist_append 进**主链路**
      history.json（共享状态）。段落数意下收敛：头部三数字 + 新增/下线/
      字段变更明细 + 护栏摘要，够自用回溯即可。
    """
    L = [f"# {NET_NAME}资费变更报告 · {day[:4]}-{day[4:6]}-{day[6:]}", "",
         f"- 本次抓取：{d.get('fetchedAt')}",
         f"- 上次抓取：{prev.get('fetchedAt', '（无）')}",
         f"- 条目数：{len(oidx)} → **{len(idx)}**",
         f"- 新增 **{len(added)}** · 下线 **{len(removed)}** · 字段变更 **{len(changed)}**", ""]
    if guard:
        for n in (guard.get("notes") or []):
            L.append(f"> 🛡 {n}")
        if guard.get("notes"):
            L.append("")
        if guard.get("rebound"):
            L.append("> 🛡 **判定为基线回弹：本轮不计任何变化。** " +
                     "；".join(guard.get("suspected") or []))
            L.append("")
    if added:
        L.append(f"## 新增资费（{len(added)}）")
        L.append("")
        for k in added[:120]:
            r = idx.get(k) or {}
            L.append(f"- **{r.get('_name') or r.get('_tname') or k}** 〔{r.get('_ty') or ''}〕 {TM.brief(r)}")
        if len(added) > 120:
            L.append(f"- …（其余 {len(added) - 120} 条见当日快照）")
        L.append("")
    if removed:
        L.append(f"## 下线/下架资费（{len(removed)}）")
        L.append("")
        for k in removed[:120]:
            r = oidx.get(k) or {}
            L.append(f"- **{r.get('_name') or r.get('_tname') or k}** 〔{r.get('_ty') or ''}〕 {TM.brief(r)}")
        if len(removed) > 120:
            L.append(f"- …（其余 {len(removed) - 120} 条见上一轮快照）")
        L.append("")
    if changed:
        L.append(f"## 字段变更（{len(changed)}）")
        L.append("")
        for k, dd in changed[:120]:
            r = idx.get(k) or {}
            det = "、".join(f"{TM.FIELD_CN.get(f, f)}「{str(o)[:24]}」→「{str(n)[:24]}」"
                            for f, (o, n) in dd.items())
            L.append(f"- **{r.get('_name') or r.get('_tname') or k}**：{det}")
        if len(changed) > 120:
            L.append(f"- …（其余 {len(changed) - 120} 条见当日快照）")
        L.append("")
    if not (added or removed or changed):
        L.append("本次未检测到任何变化。")
        L.append("")
    txt = "\n".join(L)
    os.makedirs(CHG, exist_ok=True)
    # 文件名与主链路同款 {tag}-YYYY-MM-DD.md（四网都是带横线，别另立门户）
    p = os.path.join(CHG, f"{TAG}-{day[:4]}-{day[4:6]}-{day[6:]}.md")
    try:
        import change_guard as _G
        _G.atomic_write_text(p, txt, newline="\n")
    except Exception:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
    return p


def run_change_tracking(d, log=log):
    """一轮完整的变化跟踪：**归档 → diff → 护栏 → 报告 → history**。

    由 build_sh.main() 在拿到本轮数据后调用；返回 summary dict（含给页面
    注入用的 history items）。🔴 任何内部失败都**不向上抛**（自用源，跟踪
    挂了不能拖死小页构建）—— 但每一处失败都 log 出来，静默降级最不可饶恕。
    """
    out = {"n": len(d.get("entries") or []), "a": 0, "r": 0, "c": 0,
           "note": "", "report": "", "skip": ""}
    try:
        fetched = str(d.get("fetchedAt") or "")
        today = ct_monitor._local_ts(fetched)[:10].replace("-", "")
        if not re.fullmatch(r"\d{8}", today):
            log("!! fetchedAt 无法解析出日期，本轮跳过变化跟踪")
            out["skip"] = "bad-day"
            return out

        # ① 对比基准先取（load_prev 排除「今天」；今天已有一份时退回它，
        #   与主链路同一口径 —— 当天重复跑不会把真变化洗成「首版基线」）
        prev, prev_path = TM.load_prev(today, prefix=SNAP_PREFIX)

        # ② 归档当日快照（mtime=0，内容不变则字节不变，幂等）
        TM.save_snapshot(d, today, prefix=SNAP_PREFIX)
        prune_snapshots()

        # ③ 同一轮重复跑（快照 fetchedAt 一致）⇒ 只是重渲染，不记变化
        if prev and str(prev.get("fetchedAt") or "") == fetched:
            out["skip"] = "same-round"
            return out

        idx = TM.index_rows(d)
        if prev is None:
            # 真·首版：只建基线。报告写明，时间线记一条 baseline（a/r/c 全 0）
            p = _write_report({}, d, idx, {}, [], [], [], None, today)
            hist_append({"ts": fetched[:19], "d": ct_monitor._local_ts(fetched)[:10],
                         "code": TAG, "net": NET_NAME,
                         "n": len(idx), "a": 0, "r": 0, "c": 0, "note": "baseline", "smp": []})
            log(f"上海电信首版基线：{len(idx)} 条 → {os.path.basename(p)}")
            out.update({"report": p, "note": "baseline"})
            return out

        oidx = TM.index_rows(prev)
        a, r, c = TM.diff_rows(oidx, idx)

        # ④ 护栏核验（与四网同一条 audit_diff）。模块/判据抛异常 ⇒
        #   降级为无护栏放行原始 diff —— 判不出来就保留，少报比多报严重。
        guard = None
        try:
            import change_guard as G2
            guard = G2.audit_diff(a, r, c, oidx, idx, today=today,
                                  base_day=str(prev.get("fetchedAt") or "")[:10],
                                  old2_rows=None,
                                  prev_rec=last_hist(fetched[:10]), log=log)
            a, r, c = guard["added"], guard["removed"], guard["changed"]
        except Exception as e:
            log("!! 护栏异常，按原始 diff 放行：%r" % (e,))
            guard = None

        note = ""
        if guard and guard.get("rebound"):
            note = "rebound"
            a, r, c = [], [], []

        # ⑤ 报告 + history（样本结构与四网同源，页面渲染共用同一套形态）
        p = _write_report(prev, d, idx, oidx, a, r, c, guard, today)
        smp = TM.build_smp(idx, oidx, a, r, c)
        rec = {"ts": fetched[:19], "d": fetched[:10], "code": TAG, "net": NET_NAME,
               "n": len(idx), "a": len(a), "r": len(r), "c": len(c), "smp": smp}
        if guard:
            for k, n in (("restored", "gs"), ("fake_removed", "gf"),
                         ("relocated", "gl"), ("state_moved", "gm")):
                if guard.get(k):
                    rec[n] = len(guard[k])
        if note:
            rec["note"] = note
        hist_append(rec)
        out.update({"a": len(a), "r": len(r), "c": len(c), "note": note,
                    "report": p})
        log("上海电信变更跟踪：新增 %d / 下线 %d / 字段变更 %d%s → %s"
            % (len(a), len(r), len(c),
               ("（" + note + "）") if note else "", os.path.basename(p)))
        return out
    except Exception as e:
        import traceback
        log("!! 上海变化跟踪失败（不影响小页构建）：\n" + traceback.format_exc())
        out["skip"] = "error"
        return out


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
