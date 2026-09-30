#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧪 hebei-tariff-monitor 测试套件骨架

把仓库里已有的 4 个自测脚本统一串成一个入口，让本地和 CI 都能一键跑：

    python tests/run_all.py                  # 跑所有测试
    python tests/run_all.py --quick          # 只跑不依赖采集快照的快速测试
    python tests/run_all.py --only notify    # 只跑推送相关
    python tests/run_all.py --list           # 列出所有测试名

设计要点：
- **零新增依赖**：直接调用现有脚本的 main()，不重新实现逻辑
- **失败语义保留**：每个子测试的退出码原样上报，不让一个失败拖死其他
- **本地 / CI 同源**：本地跑的和 GitHub Actions 跑的是同一份代码

已有的 4 个自测脚本（位置都在 cloud/tariff/）：
    · selftest_pipeline.py  —— other_nets 四分支 / 返回契约 / note 覆盖
    · change_guard.py       —— 护栏：假下架 / 假新增 / 身份漂移 / 回弹 / 降级
    · notify.py --selfcheck —— 推送：零变化闸门 / 异常态必推 / 转义
    · notify.py --check     —— 配置：列已配置通道（不发请求）

audit_data.py 不在这套里 —— 它需要快照文件、检查的是「采回来的数据」对不对，
属于巡检时间点产物，不是算法本身的单元测试。
"""
import argparse
import os
import subprocess
import sys
import time

# ────────────────────────────────────────────────────────────────
# 仓库根
# ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ────────────────────────────────────────────────────────────────
# 测试清单（顺序很重要：快的先跑、容易暴露问题的先跑）
# ────────────────────────────────────────────────────────────────
# 每条：(name, command, description, quick)
#   quick = True  → 不需要任何外部数据 / 不联网；适合本地预提交快检
#   quick = False → 依赖仓库现有快照；适合 CI 完整跑
TESTS = [
    ("notify-config",
     [sys.executable, "cloud/tariff/notify.py", "--check"],
     "推送通道配置自检（列出已配置通道，不发请求）",
     True),

    ("notify-selfcheck",
     [sys.executable, "cloud/tariff/notify.py", "--selfcheck"],
     "推送逻辑自测（零变化闸门 / 异常态必推 / 转义）",
     True),

    ("change-guard",
     [sys.executable, "cloud/tariff/change_guard.py"],
     "护栏自测（假下架 / 假新增 / 身份漂移 / 回弹 / 降级状态机）",
     True),

    ("pipeline-selftest",
     [sys.executable, "cloud/tariff/selftest_pipeline.py"],
     "集成缝自测（other_nets 四分支 / 返回契约 / note 覆盖）",
     False),    # 会读现有快照文件
]


def run_one(name, cmd, cwd=ROOT, timeout=60):
    """跑一个子测试；返回 (ok, elapsed_seconds, output_tail)"""
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        elapsed = time.time() - t0
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        return r.returncode == 0, elapsed, tail
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, ["⏱ 超时（%ds）" % timeout]
    except Exception as e:
        return False, time.time() - t0, ["💥 执行异常：%s" % e]


def main():
    parser = argparse.ArgumentParser(description="跑 hebei-tariff-monitor 测试套件")
    parser.add_argument("--quick", action="store_true",
                        help="只跑不依赖快照的快速测试（本地预提交用）")
    parser.add_argument("--only", metavar="NAME",
                        help="只跑指定名称的测试（多个用逗号分隔）")
    parser.add_argument("--list", action="store_true",
                        help="列出所有测试名后退出")
    parser.add_argument("--timeout", type=int, default=60,
                        help="单测试超时秒数（默认 60）")
    args = parser.parse_args()

    if args.list:
        for name, _, desc, quick in TESTS:
            tag = " (quick)" if quick else ""
            print(f"  {name:24s}{tag:8s}{desc}")
        return 0

    selected = TESTS
    if args.only:
        names = set(n.strip() for n in args.only.split(","))
        selected = [t for t in TESTS if t[0] in names]
        if not selected:
            print(f"❌ 没有匹配的测试名：{args.only}")
            print("可用：", ", ".join(t[0] for t in TESTS))
            return 2
    if args.quick:
        selected = [t for t in selected if t[3]]

    print("=" * 72)
    print(f"🧪 hebei-tariff-monitor 测试套件 ({len(selected)} 个)")
    print(f"   根目录：{ROOT}")
    print(f"   模式：{'quick' if args.quick else 'full'}"
          + (f" / only={args.only}" if args.only else ""))
    print("=" * 72)

    results = []
    for name, cmd, desc, _ in selected:
        print(f"\n▶ {name} —— {desc}")
        ok, elapsed, tail = run_one(name, cmd, timeout=args.timeout)
        results.append((name, ok, elapsed))
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name} ({elapsed:.1f}s)")
        for line in tail:
            print(f"    {line}")

    # ── 汇总 ──
    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    for name, ok, elapsed in results:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name:24s} ({elapsed:.1f}s)")
    print(f"\n  {passed} 通过 · {failed} 失败 · 总耗时 "
          f"{sum(e for _, _, e in results):.1f}s")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
