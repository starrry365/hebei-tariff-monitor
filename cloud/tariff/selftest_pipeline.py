# -*- coding: utf-8 -*-
"""集成缝自测 —— 专治「函数级自测全绿、主流程照样炸」那一类。

★ 为什么必须有它（2026-09-26 的真实事故，代价是 CI 整轮中断）：

    change_guard 自测过了、notify 自测过了、py_compile 过了、本机沙箱也「全绿」，
    推上去 CI 第 8 步 ``python cloud/tariff/tariff_monitor.py`` 直接抛
    ``NameError: name 'summaries' is not defined`` —— ``other_nets()`` 里
    ``summaries`` 的初始化漏了，而函数体里三处 append 它。

    **本机为什么没发现**：那些自测清一色是**函数级**的 —— 单独调 ``net_round`` /
    ``diff_round`` / ``write_page`` / ``build_html``，**没有一个走到 ``other_nets``
    这个集成缝**。它需要真联网采集，所以被所有人（包括我）默认跳过了。

    ⇒ 教训：**函数级全过 ≠ 集成路径通**。集成缝是「没人测 + 一炸就中断整轮」的
      最高危位置，必须专门盯。

本文件因此**不打真接口**：把 ``net_round`` / ``snap_net_ready`` / ``load_latest``
换成桩，只验证**函数之间的接线** —— 返回值个数、字段齐不齐、各分支有没有被走到。
纯 stdlib、毫秒级，可以放进 CI 当闸门。

覆盖：
  · ``other_nets`` 四个分支（全采到 / 兜底 / 采集失败 / 混跑）与返回契约
  · ``_NOTE_CN`` 覆盖全部 note 取值（漏一项 ⇒ 英文码印到中文界面上）
  · 四网注册表自洽（tag / snap_prefix 不撞车、NET_RUN 与注册表一致、未知网不炸）

用法:
    python cloud/tariff/selftest_pipeline.py      # 退出码 0 = 通过
    # 另：改了四网元信息（NetSource 子类）后，用 check_nets_refactor.py 做逐项对拍
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tariff_monitor as T                                        # noqa: E402

FAILS = []


def ck(name, got, want):
    if got != want:
        FAILS.append(f"{name}: 得到 {got!r}，期望 {want!r}")


def _dups(seq):
    """挑出重复出现的元素（每个只报一次）—— 查 tag / snap_prefix 撞车用。"""
    seen, out = set(), []
    for x in seq:
        if x in seen and x not in out:
            out.append(x)
        seen.add(x)
    return out


def _sm(code, cn, note=None):
    """与 net_round 产出的 summary 同构（字段齐是契约的一部分）。"""
    return {"code": code, "net": cn, "n": 100, "added": 0, "removed": 0, "changed": 0,
            "restored": 0, "fake_removed": 0, "relocated": 0, "state_moved": 0,
            "samples": [], "report": "", "note": note, "guard": None}


def _stub_net_round(seen, ok=True):
    def f(code, today, fallback=None):
        seen.append(code)
        if not ok:
            # 采集失败的样子：没有 diff，只有带 note 的 summary
            return {"entries": []}, None, _sm(code, f"N-{code}", "collect-error")
        return ({"groups": [{"entries": [{"name": "X"}]}]},
                {"added": [], "removed": [], "changed": []},
                _sm(code, f"N-{code}"))
    return f


def _stub_load_latest(today, prefix):
    return {"entries": [1, 2, 3]}, f"snapshots/{prefix}{today}.json.gz"


def main():
    orig = (T.net_round, T.snap_net_ready, T.load_latest)
    try:
        # ── 分支 A：全部真采到 ─────────────────────────────────────────
        seen = []
        T.net_round = _stub_net_round(seen)
        T.snap_net_ready = lambda code, today: True
        T.load_latest = _stub_load_latest
        out = T.other_nets("20260926")
        ck("★ other_nets 返回 4 项（曾漏 summaries 导致 NameError）", len(out), 4)
        src, dif, tails, sums = out
        ck("summaries 是 list", isinstance(sums, list), True)
        ck("采到的网各有一条 summary", len(sums), len(seen))
        ck("sources / diffs 是 dict", (isinstance(src, dict), isinstance(dif, dict)),
           (True, True))
        ck("tails 是 list", isinstance(tails, list), True)
        ck("summary 字段齐（契约）",
           all(set(s) >= {"code", "net", "n", "added", "removed", "changed", "note"}
               for s in sums), True)
        ck("真采到的网没有 fallback 标记",
           any(s.get("note") == "snapshot-fallback" for s in sums), False)

        # ── 分支 B：兜底网没采到 ⇒ 走快照（曾漏 append 的那条路）───────
        seen2 = []
        T.net_round = _stub_net_round(seen2)
        T.snap_net_ready = lambda code, today: False          # 强制走兜底
        out2 = T.other_nets("20260926")
        ck("兜底分支也返回 4 项", len(out2), 4)
        _, _, tails2, sums2 = out2
        ck("兜底网进了 summaries", any(s.get("note") == "snapshot-fallback" for s in sums2), True)
        ck("兜底网不该被 net_round 采", [c for c in seen2 if c in T.NET_SNAP], [])
        ck("兜底提示带快照日期（时效声明）", any("2026-09-26" in t for t in tails2), True)
        ck("兜底 summary 的 n 等于快照条数",
           [s["n"] for s in sums2 if s.get("note") == "snapshot-fallback"], [3])

        # ── 分支 C：采集失败 ⇒ 有 note、无 diff、且提示语不是「无变化」──
        seen3 = []
        T.net_round = _stub_net_round(seen3, ok=False)
        T.snap_net_ready = lambda code, today: True
        _, _, tails3, sums3 = T.other_nets("20260926")
        ck("采集失败也产出 summary", len(sums3), len(seen3))
        ck("采集失败带 note", any(s.get("note") for s in sums3), True)
        # 🔴 「没看到」绝不能被显示成「看到了，没变」—— 两者对读者的含义完全不同
        ck("采集失败不显示「无变化」", any("无变化" in t for t in tails3), False)

        # ── note 覆盖：能产出的 note 必须都有中文对照 ────────────────
        need = {"baseline", "resync", "collect-error", "snapshot-fallback",
                "degraded", "rebound", "schema"}
        ck("_NOTE_CN 覆盖全部 note 取值", sorted(need - set(T._NOTE_CN)), [])

        # ── _verify_notes：只把**需要人看一眼**的 note 挑出来 ─────────
        vn = T._verify_notes([_sm("move", "移动", "degraded"),
                              _sm("unicom", "联通", "baseline"),
                              _sm("cbn", "广电")])
        ck("_verify_notes 挑出 degraded、放过 baseline 与空", len(vn), 1)

        # ── 四网注册表自洽（2026-09-26 模块化后加）─────────────────────
        # ★ 这里只查**内部自洽**，不查具体值（值由 check_nets_refactor.py 现场对拍）。
        #   区别很重要：「加一网」不该让 CI 变红（那是正常演进），
        #   而「加一网时漏填一个字段」必须变红 —— 会让闸门一直红的东西会被绕过。
        nets = list(T.NETS.values())
        ck("注册表覆盖 NETS_META 的每一网",
           sorted(T.NETS), sorted(c for c, _, _ in T.NETS_META))
        ck("每网的 code/sh/cn/vendor/src/snap_prefix 都非空",
           [n.code for n in nets
            if not all((n.code, n.sh, n.cn, n.vendor, n.src, n.snap_prefix))], [])
        ck("tag 两两不重复（重复 ⇒ 两网写同一份变更报告，后写的盖掉先写的）",
           _dups([n.tag for n in nets]), [])
        ck("snap_prefix 两两不重复（重复 ⇒ 两网快照混排，拿别网数据当自己的基准）",
           _dups([n.snap_prefix for n in nets]), [])
        ck("只有「采集实现就在本文件」的网可以没有适配器模块",
           [n.code for n in nets if not n.mod], ["move"])
        ck("NET_RUN 恰好 = 有适配器模块的网",
           sorted(T.NET_RUN), sorted(n.code for n in nets if n.mod))
        ck("NET_RUN 的值与注册表一致",
           [c for c in T.NET_RUN
            if T.NET_RUN[c] != (T.NETS[c].mod, T.NETS[c].cn, T.NETS[c].tag)], [])
        ck("NET_SNAP / NET_STOPPED / NET_NOCACHE 都是注册表的子集",
           [c for c in list(T.NET_SNAP) + sorted(T.NET_STOPPED) + sorted(T.NET_NOCACHE)
            if c not in T.NETS], [])
        ck("未知网的地域判据不炸且留数据（宁可多留，不可静默丢）",
           T.where_of("没有这个网", {}), ("hb", []))
        ck("未知网的下架判据不炸且判在售",
           T.state_of("没有这个网", {}, {}, "2026-09-26"), False)
    finally:
        T.net_round, T.snap_net_ready, T.load_latest = orig     # 不污染同进程后续用例

    if FAILS:
        print("集成缝自测失败 %d 项：" % len(FAILS))
        for x in FAILS:
            print("  ✗", x)
        return 1
    print("集成缝自测通过（other_nets 四分支 / 返回契约 / note 覆盖 / 不误报「无变化」）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
