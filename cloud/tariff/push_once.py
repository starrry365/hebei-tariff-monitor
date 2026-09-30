#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补推一次 —— 从 history.json 出变化，走 notify 的多通道发出去

与巡检的关系：
  巡检自己的推送是 ``notify.notify_change()``（**当场**推，数据在内存里）。
  本脚本用于**巡检之外**的场合：
    · 通道刚修好 / 刚配上，想把最近一轮补发一次；
    · 本地验证「数据 → 通道」这条链路（先 --dry 看内容，再真发）；
    · 当周报用（``--days 7``）。

为什么不能直接用 ``notify.py --test``：
  ``--test`` 发的是**写死的自检文案**，验的是「通道通不通」；
  本脚本发的是**真实数据**，验的是「history.json → 渲染 → 通道」整条链路。
  通道通但渲染炸掉（字段改名、空数据、编码问题）时，只有本脚本能验出来。

用法：
    python cloud/tariff/push_once.py                 # 最近 1 个自然日，真发（按指纹去重）
    python cloud/tariff/push_once.py --dry           # 只打印，不发
    python cloud/tariff/push_once.py --days 3        # 最近 3 天
    python cloud/tariff/push_once.py --date 2026-09-29
    python cloud/tariff/push_once.py --force         # 无视「无变化不推」与指纹去重

退出码：0 = 已推 / 无需推；1 = 有通道但全失败；2 = 读不到数据
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import notify    # noqa: E402
import summary   # noqa: E402

REPO_DEFAULT = os.getenv("GITHUB_REPOSITORY", "")


def main():
    ap = argparse.ArgumentParser(description="补推一次资费变更（读 history.json）")
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=1, help="最近 N 个自然日（含今天）")
    ap.add_argument("--repo", default=REPO_DEFAULT,
                    help="owner/name，给了就在正文末尾附站点链接")
    ap.add_argument("--dry", action="store_true", help="只打印，不发送")
    ap.add_argument("--force", action="store_true",
                    help="无视「无变化不推」与内容指纹去重")
    ap.add_argument("--title", help="自定义标题")
    args = ap.parse_args()

    # 1) 取数据（读不到就是 2 —— 比「没变化」严重得多，不能混为一谈）
    try:
        items = summary.load_history()
    except SystemExit as e:
        print("::error::%s" % e)
        return 2
    picked = summary.pick(items, date=args.date, days=args.days)
    rounds = [summary._to_round(it) for it in picked]

    if not picked:
        print("汇总范围内没有任何记录（--date %s / --days %d），不发。"
              % (args.date or "-", args.days))
        return 0 if not args.force else 2

    day = str(args.date) if args.date else str(picked[-1].get("d") or "")
    body = notify.build_body(day, rounds, repo=args.repo)
    title = args.title or ("资费巡检 %s：" % day) + notify._headline(rounds)

    # 2) 零变化闸门（与巡检同一条判据 —— 免得手动补推反而破了「一次变化只推一条」）
    if not args.force and not notify.has_change(rounds):
        print("汇总范围内没有值得推送的变化（想强推加 --force），不发。")
        return 0

    print("=" * 60)
    print("标题：%s" % title)
    print("-" * 60)
    print(body)
    print("=" * 60)

    if args.dry:
        print("[dry] 只打印，未发送。可用通道：%s"
              % ("、".join(notify.available()) or "（无）"))
        return 0

    chans = notify.available()
    if not chans:
        print("没有任何通道配置了凭据，跳过推送。")
        print("  钉钉 DINGTALK_WEBHOOK / 飞书 FEISHU_WEBHOOK / 企微 WECOM_WEBHOOK /")
        print("  PushPlus PUSHPLUS_TOKEN / 邮件 SMTP_HOST+MAIL_TO")
        return 0

    print("将推送至：%s" % "、".join(chans))
    res = notify.send_all(title, body, dedup=not args.force)
    for k, v in (res or {}).items():
        print("  %s: %s" % (k, v))

    if not res:
        return 0
    if any(str(v) == "ok" for v in res.values()):
        return 0
    if res.get("_dedup") == "skipped":
        return 0
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
