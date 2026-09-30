#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变化汇总 —— 把 history.json 的逐轮记录翻成一条可读的 Markdown

和 notify.py 的关系（重要）：
  **notify.build_body() 才是正文格式的唯一权威**（标题层级、emoji、备注区、
  四网排序）。本模块只干一件事：把 history.json 的记录**翻译**成 build_body
  认得的 ``rounds`` 结构，然后交给它渲染 —— 绝不自己再拼一份格式。
  两处各拼一份，改了一处忘了另一处，用户就会看到两种风味。

为什么需要它：
  ``notify.notify_change()`` 是巡检**当场**用的（数据来自内存里的本轮结果）。
  但「补推一条」「回看昨天到底变了什么」「本地单独发一次」这些场景没有内存结果，
  只剩落盘的 history.json 可读。本模块补的就是这一段。

用法：
    python cloud/tariff/summary.py                     # 最近 1 个自然日
    python cloud/tariff/summary.py --days 3            # 最近 3 天（含今天）
    python cloud/tariff/summary.py --date 2026-09-30   # 指定某天
    python cloud/tariff/summary.py --json              # 输出 rounds 结构（调试用）
"""
import argparse
import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import notify  # noqa: E402  （同目录；只为了复用 build_body / _headline）

HIST = os.path.join(BASE, "history.json")
CST = datetime.timezone(datetime.timedelta(hours=8))

#: 四网的稳定展示顺序 —— 固定下来，读者才能一眼扫到想看的那一网
ORDER = ("move", "unicom", "telecom", "cbn")
NET_CN = {"move": "河北移动", "unicom": "河北联通",
          "telecom": "河北电信", "cbn": "中国广电"}


def load_history(path=HIST):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        raise SystemExit("读不到 history.json：%s（%s）" % (path, e))
    items = d.get("items") if isinstance(d, dict) else d
    return items if isinstance(items, list) else []


def _to_round(it):
    """history 的一条记录 → build_body 认的 round。"""
    code = it.get("code") or ""
    return {
        "code": code,
        "net": it.get("net") or NET_CN.get(code) or code or "?",
        "n": it.get("n") or 0,
        "added": it.get("a") or 0,
        "removed": it.get("r") or 0,
        "changed": it.get("c") or 0,
        "samples": it.get("smp") or [],
        # history 里没有 note；src=backfill 说明这条是回填的（不是当场巡检产出）
        "src": it.get("src") or "",
    }


def pick(items, date=None, days=1):
    """挑出要汇总的记录 → [{d, code, ...}]，同 (日期, 网) 只留**最后一条**。

    为什么取最后一条而不是全部：同一天重跑多轮是常态（定时 + 手动 dispatch +
    重试），history 会为同一 (日期, 网) 追加多条；全拿会出现「移动新增 3 条」
    下一行又是「移动新增 3 条」—— 那是重复播报，不是两次变更。
    """
    if date:
        want = {str(date)}
    else:
        today = datetime.datetime.now(CST).date()
        want = {(today - datetime.timedelta(days=i)).isoformat()
                for i in range(max(1, days))}

    best = {}                                   # (d, code) → 记录
    for it in items:
        if not isinstance(it, dict):
            continue
        d = str(it.get("d") or "")
        if d not in want:
            continue
        key = (d, it.get("code") or "")
        cur = best.get(key)
        # ts 是字符串且格式统一（YYYY-MM-DD HH:MM:SS），字典序即时间序
        if cur is None or str(it.get("ts") or "") >= str(cur.get("ts") or ""):
            best[key] = it

    out = []
    for (d, code) in sorted(best, key=lambda k: (k[0], ORDER.index(k[1])
                                                 if k[1] in ORDER else 99)):
        out.append(best[(d, code)])
    return out


def build_summary(date=None, days=1, hist=None, repo="", title=None):
    """返回 (标题, Markdown 正文)。

    ``repo`` 形如 ``owner/name``，给了就在正文末尾附站点链接（与巡检一致）。
    """
    items = hist if hist is not None else load_history()
    picked = pick(items, date=date, days=days)
    rounds = [_to_round(it) for it in picked]

    if date:
        day = str(date)
    elif picked:
        day = str(picked[-1].get("d") or "")
    else:
        day = datetime.datetime.now(CST).strftime("%Y-%m-%d")

    body = notify.build_body(day, rounds, repo=repo)
    if not picked:
        body = "# 河北四网资费巡检 %s\n\n本次汇总范围内没有任何记录。\n" % day
    t = title or ("资费巡检 %s：" % day) + notify._headline(rounds)
    return t, body


def main():
    ap = argparse.ArgumentParser(description="把 history.json 汇总成 Markdown")
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD（默认最近 N 天）")
    ap.add_argument("--days", type=int, default=1, help="最近 N 个自然日（含今天）")
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""),
                    help="owner/name，给了就在正文末尾附站点链接")
    ap.add_argument("--title", help="自定义标题")
    ap.add_argument("--json", action="store_true", help="输出 rounds 结构")
    args = ap.parse_args()

    t, md = build_summary(date=args.date, days=args.days,
                          repo=args.repo, title=args.title)
    if args.json:
        items = load_history()
        print(json.dumps({"title": t,
                          "rounds": [_to_round(x) for x in
                                     pick(items, date=args.date, days=args.days)]},
                         ensure_ascii=False, indent=2))
    else:
        print("标题：%s\n" % t)
        print(md)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
