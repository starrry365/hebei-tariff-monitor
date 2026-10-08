#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🕒 数据新鲜度检查 —— 巡检到底有没有真的在跑

为什么单独一个脚本（而不是看 CI 红绿）：
  巡检失效有两条**完全不同**的路子 ——
    ① 任务跑了但报错：Actions 页面变红，总有人能看到；
    ② 任务**根本没跑**（cron 被高峰排队吃掉 / 分支改了 / workflow 被禁用 /
       额度或计费把 job 挡在启动之前）：页面绿着、站点还在，只有数据在悄悄变老。
  本脚本专治 ②：只看**产物**（history.json 的时间戳 + 最新快照日期），
  不看 CI 状态 —— 产物新鲜才是唯一可信的证据。

  🔴 真实教训：本仓库另一个站点就遇到过「job 根本没启动」（GitHub 计费闸门），
     Actions 里连 step 都没有、日志是空的；如果只盯着「有没有红色 ❌」，
     这种失效会**永远静默**。

判定：最新数据时间距今 > FRESH_MAX_HOURS（默认 30）⇒ 判为过期。
  为什么是 30 小时：巡检每日两轮（北京 09:30 / 15:30，2026-10-05 改），
  正常最大数据年龄 18 小时，留 12 小时给 GitHub 排队。
  真连着两轮没跑，30 小时必然报警。

退出码：0 = 新鲜 / 1 = 过期 / 2 = 拿不到任何数据（比过期更严重：连基线都读不到）

用法：
    python cloud/tariff/check_fresh.py
    python cloud/tariff/check_fresh.py --max-hours 30
    python cloud/tariff/check_fresh.py --json      # 机器可读，供 workflow 消费
"""
import argparse
import datetime
import glob
import gzip
import json
import os
import re
import sys

CST = datetime.timezone(datetime.timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, "history.json")
SNAP_DIR = os.path.join(BASE, "snapshots")

NET_CN = {"move": "河北移动", "unicom": "河北联通",
          "telecom": "河北电信", "cbn": "中国广电"}

# 分源清单（2026-10-08 深查）：快照前缀 ↔ history code。
# 🔴 只看「全局最新」有盲区：若广电单独静默失效而其它源正常，全局最新
#   永远新鲜，报警永远不响 —— 而这恰是本脚本要防的场景②（任务根本没跑）。
#   分源判龄后，任一源缺新鲜证据都会响。shct 是独立链路（shct_history.json），
#   不在主巡检范围。
SOURCES = [("移动", "move", "hebei_tariff"),
           ("联通", "unicom", "unicom_tariff"),
           ("电信", "telecom", "ct_tariff"),
           ("广电", "cbn", "cbn_tariff")]

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
               "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_ts(s):
    """把 history.json 里的时间串解析成带时区的 datetime（本地一律按北京时间读）。

    兼容三种历史格式：``2026-09-30 09:06:44`` / ``2026-09-30T09:06:44+08:00`` /
    ``2026-09-30``。解析不出来就返回 None —— **不猜**，宁可报「读不到」。
    """
    s = str(s or "").strip()
    if not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.replace(tzinfo=CST) if d.tzinfo is None else d
    except Exception:
        pass
    for fmt in _TS_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _hist_items():
    """读 history.json 的 items（不可解析/为空都返回 None/[] 由调用方区分）。"""
    d = _load(HIST)
    if d is None:
        return None
    items = d.get("items") if isinstance(d, dict) else d
    return items if isinstance(items, list) else None


def _newest_ts_in(items, code=None):
    """items 里 ts 最大的一条 → (item, ts)；code 给了就只看该源。"""
    best, best_ts = None, None
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if code is not None and it.get("code") != code:
            continue
        ts = parse_ts(it.get("ts"))
        if ts and (best_ts is None or ts > best_ts):
            best, best_ts = it, ts
    return best, best_ts


def newest_history():
    """history.json 里最新一条记录 → (datetime, 描述)。

    ``items`` 是逐 (日期, 网) 追加的，取 ts 最大的一条即可；
    顺序不保证（回填的 src=backfill 记录可能后插），所以按 ts 排序取最大，
    而不是盲信最后一条。
    """
    items = _hist_items()
    if items is None:
        return None, "history.json 缺失或不可解析"
    if not items:
        return None, "history.json 里还没有任何记录"
    best, best_ts = _newest_ts_in(items)
    if best_ts is None:
        return None, "history.json 里没有可解析的时间戳"
    net = NET_CN.get(best.get("code"), best.get("net") or best.get("code") or "?")
    return best_ts, f"history.json 最新一条（{net} {best_ts:%Y-%m-%d %H:%M}）"


def _read_fetched_at(path):
    """读 gzip JSON 头部 fetchedAt（真实采集时刻）。读不到返回 None。

    只读头部 64KB：fetchedAt 写在 JSON 开头附近，整文件解压浪费
    （最大快照几 MB，几十个文件逐个全读就慢了）。头截断导致 json 不完整
    也无所谓 —— 用正则抠字段，不整包解析。
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
        m = re.search(r'"fetchedAt"\s*:\s*"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"', head)
        if m:
            return parse_ts(m.group(1))
    except (OSError, EOFError, ValueError):
        pass
    return None


def newest_snapshot_for(prefix):
    """某源（按文件名前缀）最新快照 → (datetime, 描述)。

    时间戳口径同 newest_snapshot：优先 fetchedAt，文件名日期只作兑底。
    """
    best, best_p = None, None
    for p in glob.glob(os.path.join(SNAP_DIR, prefix + "_*.json.gz")):
        m = re.search(r"_(\d{8})\.json\.gz$", os.path.basename(p))
        if not m:
            continue
        try:
            d = datetime.datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=CST)
        except ValueError:
            continue
        if best is None or d > best:
            best, best_p = d, p
    if best is None:
        return None, f"{prefix} 无快照"
    fa = _read_fetched_at(best_p)
    if fa is not None:
        best = fa
    return best, os.path.basename(best_p)


def newest_snapshot():
    """snapshots/ 里最新的快照 → (datetime, 描述)。文件名尾部是 YYYYMMDD。

    🔴 时间戳必须优先用 JSON 内的 fetchedAt（真实采集时刻），文件名日期
    （午夜零点）只作兜底 —— 2026-10-08 教训：20:17 采集的
    cbn_tariff_20261007.json.gz 按文件名算成 00:00，平白虚增 ~20 小时年龄，
    晨间窗口（当天班车落地前）的 push CI 全部误红（实际数据龄仅 12.6h）。
    """
    best, best_p = None, None
    for p in glob.glob(os.path.join(SNAP_DIR, "*.json.gz")):
        m = re.search(r"_(\d{8})\.json\.gz$", os.path.basename(p))
        if not m:
            continue
        try:
            d = datetime.datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=CST)
        except ValueError:
            continue
        if best is None or d > best:
            best, best_p = d, p
    if best is None:
        return None, "snapshots/ 下没有任何快照"
    fa = _read_fetched_at(best_p)
    if fa is not None:
        best = fa
    return best, f"最新快照（{os.path.basename(best_p)}）"


def check(max_hours):
    """分源判龄（2026-10-08 重构）。

    每源两个证据：快照 fetchedAt、history 里该 code 的最新一条。
    源龄 = 两者中**较新**的那个 —— 任一证物新鲜就说明该源最近确实跑过
    （巡检链路里快照与 history 几乎同刻落盘，正常时两者只差分钟级；
    取较新可避免「写入成功但另一方偶发失败」时的误报）。
    任一源超阈值 / 某源两个证据都没有 ⇒ 告警（exit 1）；
    四源全部一个证据都拿不到 ⇒ 连基线都没了（exit 2）。
    """
    now = datetime.datetime.now(CST)
    out = {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "max_hours": max_hours,
           "items": [], "stale": [], "missing": []}
    hist = _hist_items() or []

    for cn, code, prefix in SOURCES:
        s_ts, s_why = newest_snapshot_for(prefix)
        _, h_ts = _newest_ts_in(hist, code)
        ev = [(t, w) for t, w in ((s_ts, "快照 " + s_why),
                                  (h_ts, "history %s" % code)) if t]
        if not ev:
            out["missing"].append(f"{cn}：快照与 history 都没有记录")
            continue
        latest, latest_why = max(ev, key=lambda x: x[0])
        age = (now - latest).total_seconds() / 3600.0
        rec = {"source": cn, "code": code,
               "snapshot": s_ts.strftime("%Y-%m-%d %H:%M:%S") if s_ts else None,
               "history": h_ts.strftime("%Y-%m-%d %H:%M:%S") if h_ts else None,
               "why": latest_why,
               "ts": latest.strftime("%Y-%m-%d %H:%M:%S"),
               "age_hours": round(age, 2)}
        out["items"].append(rec)
        if age > max_hours:
            out["stale"].append(rec)

    if not out["items"]:                # 四源全部无任何数据：连基线都没了
        return 2, out
    if out["stale"] or out["missing"]:  # 任一源静默消失/过期都算告警
        return 1, out
    return 0, out


def main():
    ap = argparse.ArgumentParser(description="河北四网资费数据新鲜度检查")
    ap.add_argument("--max-hours", type=int,
                    default=int(os.getenv("FRESH_MAX_HOURS", "30") or 30))
    ap.add_argument("--json", action="store_true", help="输出 JSON（供 workflow 消费）")
    args = ap.parse_args()

    code, out = check(args.max_hours)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("检查时间：%s（阈值 %d 小时，分源判定）" % (out["now"], out["max_hours"]))
        for it in out["items"]:
            flag = "⚠ 过期" if it["age_hours"] > out["max_hours"] else "✅"
            print("  %s %s：源龄 %s 小时（最新证据 %s，%s）"
                  % (flag, it["source"], it["age_hours"], it["ts"], it["why"]))
            if it["snapshot"] is None:
                print("      （该源无快照，依据 history）")
        for m in out["missing"]:
            print("  ❌ 读不到：%s" % m)
        if code == 1:
            print("\n::warning::资费数据已过期（分源判定）—— 巡检可能没在跑，请看 Actions 与 cron")
        elif code == 2:
            print("\n::error::连数据基线都读不到（history.json / snapshots 都不可用）")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
