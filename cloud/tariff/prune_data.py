#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧹 过期快照归档裁剪 —— snapshots/ 目录只保留最近 N 天（默认 90 天）

为什么要有这个脚本：
  每轮巡检给每源落一份 snapshots/<前缀>_<YYYYMMDD>.json.gz，仓库里只进不出。
  实测 18 天攒了 74 个文件 37MB —— 照这个速度一年就是 ~1500 个文件 700MB+，
  git clone 会越来越慢，而 diff 的比对基线只需要**上一轮**一份；
  长期记录另有 history.json（自带 HIST_KEEP=400 条上限）与 changes/*.md 兜底，
  快照老档没有第二消费者。

  🔴 只认 `*_YYYYMMDD.json.gz` 命名：shct_latest.json 这类指针文件、
     非快照命名的一律不碰。

  🔴 保险丝：删完之后全目录剩 < 4 份（四网至少各 1 份）就**整个放弃删除**，
     恢复原状并报错 —— 防的是参数写错（--days 0）把目录清空。
     与 workflow 提交前的产物完整性闸门同一条原则，但闸门在删除之后才跑，
     这里的保险丝必须在删除**之前**就拦住。

用法：
    python cloud/tariff/prune_data.py              # 试运行：只列出将删的文件
    python cloud/tariff/prune_data.py --days 90 --delete

退出码：0 = 正常（含 dry-run）/ 1 = 触发保险丝，没有删除任何文件
"""
import argparse
import datetime
import glob
import os
import re
import sys

CST = datetime.timezone(datetime.timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(BASE, "snapshots")
MIN_KEEP = 4          # 四网至少各 1 份 —— 低于这个数说明参数/逻辑出了问题
SNAP_RE = re.compile(r"_(\d{8})\.json\.gz$")


def scan(days):
    """返回 (to_delete, keep)：按文件名日期与阈值分成两组。

    🔴 每源（文件名前缀）**最新的一份永不删**：某源若停更 90+ 天，
       按年龄会把它仅剩的快照删光 —— diff 比对基线就断了。
       年龄口径按文件名日期（午夜零点），这里只做粗粒度裁剪，够用。
    """
    now = datetime.datetime.now(CST)
    to_delete, keep = [], []
    newest_of_prefix = {}
    for p in glob.glob(os.path.join(SNAP_DIR, "*.json.gz")):
        m = SNAP_RE.search(os.path.basename(p))
        if not m:                       # 不认识的名字一律不碰
            keep.append(p)
            continue
        prefix = os.path.basename(p)[:m.start()]
        try:
            d = datetime.datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=CST)
        except ValueError:
            keep.append(p)
            continue
        if prefix not in newest_of_prefix or d > newest_of_prefix[prefix][0]:
            newest_of_prefix[prefix] = (d, p)
        if (now - d).days > days:
            to_delete.append((p, prefix, d))
        else:
            keep.append(p)
    # 每源最新的那份从删除名单里捞回来
    protected = {p for _, p in newest_of_prefix.values()}
    keep_all = [p for p, _, _ in to_delete if p in protected] + keep
    to_delete = [t for t in to_delete if t[0] not in protected]
    return [p for p, _, _ in to_delete], keep_all


def main():
    ap = argparse.ArgumentParser(description="过期快照归档裁剪")
    ap.add_argument("--days", type=int,
                    default=int(os.getenv("SNAP_KEEP_DAYS", "90") or 90),
                    help="保留最近 N 天（默认 90）")
    ap.add_argument("--delete", action="store_true",
                    help="真删；不带这个参数只做试运行（列出将删的文件）")
    args = ap.parse_args()

    to_delete, keep = scan(args.days)
    print("快照目录：%s（阈值 %d 天，%s；每源最新一份受保护不删）"
          % (SNAP_DIR, args.days, "删除模式" if args.delete else "试运行"))
    if not to_delete:
        print("  没有过期归档（保留 %d 份）" % len(keep))
        return 0
    for p in sorted(to_delete):
        print("  将删 %s" % os.path.basename(p))
    if len(keep) < MIN_KEEP:
        print("::error::删除后将只剩 %d 份（低于四网底线 %d）—— 放弃删除，检查 --days 参数"
              % (len(keep), MIN_KEEP))
        return 1
    if not args.delete:
        print("  共 %d 份过期，试运行未删除（加 --delete 才真删）" % len(to_delete))
        return 0
    for p in to_delete:
        os.remove(p)
    print("  已删除 %d 份，保留 %d 份" % (len(to_delete), len(keep)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
