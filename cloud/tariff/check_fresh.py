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


def newest_history():
    """history.json 里最新一条记录 → (datetime, 描述)。

    ``items`` 是逐 (日期, 网) 追加的，取 ts 最大的一条即可；
    顺序不保证（回填的 src=backfill 记录可能后插），所以按 ts 排序取最大，
    而不是盲信最后一条。
    """
    d = _load(HIST)
    if d is None:
        return None, "history.json 缺失或不可解析"
    items = d.get("items") if isinstance(d, dict) else d
    if not isinstance(items, list) or not items:
        return None, "history.json 里还没有任何记录"
    best, best_ts = None, None
    for it in items:
        if not isinstance(it, dict):
            continue
        ts = parse_ts(it.get("ts"))
        if ts and (best_ts is None or ts > best_ts):
            best, best_ts = it, ts
    if best_ts is None:
        return None, "history.json 里没有可解析的时间戳"
    net = NET_CN.get(best.get("code"), best.get("net") or best.get("code") or "?")
    return best_ts, f"history.json 最新一条（{net} {best_ts:%Y-%m-%d %H:%M}）"


def newest_snapshot():
    """snapshots/ 里最新的快照日期 → (datetime, 描述)。文件名尾部是 YYYYMMDD。"""
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
            best, best_p = d, os.path.basename(p)
    if best is None:
        return None, "snapshots/ 下没有任何快照"
    return best, f"最新快照（{best_p}）"


def check(max_hours):
    now = datetime.datetime.now(CST)
    out = {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "max_hours": max_hours,
           "items": [], "stale": [], "missing": []}

    for label, fn in (("history", newest_history), ("snapshot", newest_snapshot)):
        ts, why = fn()
        if ts is None:
            out["missing"].append(why)
            continue
        age = (now - ts).total_seconds() / 3600.0
        rec = {"source": label, "why": why,
               "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
               "age_hours": round(age, 2)}
        out["items"].append(rec)
        if age > max_hours:
            out["stale"].append(rec)

    if not out["items"]:                    # 两条都读不到：连基线都没了
        return 2, out
    if out["stale"]:
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
        print("检查时间：%s（阈值 %d 小时）" % (out["now"], out["max_hours"]))
        for it in out["items"]:
            flag = "⚠ 过期" if it["age_hours"] > out["max_hours"] else "✅"
            print("  %s %s：%s 小时前（%s）"
                  % (flag, it["why"], it["age_hours"], it["ts"]))
        for m in out["missing"]:
            print("  ❌ 读不到：%s" % m)
        if code == 1:
            print("\n::warning::资费数据已过期 —— 巡检可能没在跑，请看 Actions 与 cron")
        elif code == 2:
            print("\n::error::连数据基线都读不到（history.json / snapshots 都不可用）")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
