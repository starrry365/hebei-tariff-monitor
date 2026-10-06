# -*- coding: utf-8 -*-
"""一条命令跑全套**本机**检查：四网体检 + 地域口径 + 交互遍历 + e2e + 筛选口径对账。

为什么需要它
------------
这些检查**都不在 CI 里**（CI 只跑 `audit_data` 与 `check_area_scope` —— 浏览器类要真
Chrome + 虚拟显示，云端成本高）。于是有一个非常现实的失效模式：**检查写了但没人跑**。
把「先起 CDP Chrome → nav → eval 两次遍历 → eval e2e → 再跑三网对账」这一串
（还得记住输出路径、记得连跑两次比对）压成 1 条命令，人真的会跑的概率差一个数量级。
**「有检查脚本」≠「检查真的会跑」** —— 本脚本就是把后者补上。

用法：
    python probes/tools/run_checks.py                  # 全套
    python probes/tools/run_checks.py --no-browser     # 只跑不需要浏览器的（秒级）
    python probes/tools/run_checks.py --net unicom     # 体检 / 对账只跑联通

⚠️ 前置：页面产物必须是**刚生成的** —— 先跑 `python cloud/tariff/rebuild_offline.py`
   （它 `archive=False`，不碰入库的 `page/index.html.gz`）。
   🔴 本脚本**不会**替你重建页面：拿旧页面跑出一片绿，比不跑更糟。
⚠️ 需要：`.tools/venv_gui` 那个解释器（有 websocket-client）；
   调试 Chrome 由 `cdp_launch.py` 自动拉起（幂等，已在跑就直接复用）。

退出码：全部通过 0；任一项失败 1。逐项打印，最后给汇总表。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))                 # probes/tools
REPO = os.path.dirname(os.path.dirname(BASE))
CT = os.path.join(REPO, "cloud", "tariff")
STEP = os.path.join(BASE, "ct_browser", "cdp_desktop_step.py")
HTML = os.path.join(CT, "docs", "index.html")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

argv = sys.argv[1:]
NO_BROWSER = "--no-browser" in argv
NETS_ALL = ("move", "telecom", "unicom", "cbn")      # 体检四网
# 对账网别（与 cloud/tariff/conformance.py 的 NETS_OK 保持一致）。
# ⚠️ 2026-10-03：广电此前被排除，理由只有一条「上游没有地市维度 ⇒ 城市用例必然对不上」。
#    地市维度整块下线后那条理由失效，实测 `--net cbn` 83/83 与 oracle 一致，
#    于是把它补进来 —— 这一网此前**完全没有 oracle 覆盖**。
#    ⚠️ 代价是这一步会比原来慢一点（4 网 × 启动一次 Chrome）。要再加网，
#       先按同样方式单独跑通再放行，别顺手加（对账是唯一能抓「页面系统性算错」的那层）。
NETS_CONF = ("move", "telecom", "unicom", "cbn")


def _net_arg(default=None):
    if "--net" in argv:
        i = argv.index("--net")
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


ONLY = _net_arg()
if ONLY:
    if ONLY not in NETS_ALL:
        # 🔴 无效 --net 静默回退跑全量曾是默认行为（2026-10-04 修）：
        #    `--net telecom` 手滑打成 `--net ct` 时，你以为只查了电信，
        #    实际把四网全部跑了一遍 —— 结论里「telecom 通过」那行查的根本不是你想要的。
        #    宁可报错让人重敲，也不给一个看似正常、实则答非所问的运行。
        sys.exit("!! 未知网别 %r（可选：%s）" % (ONLY, ", ".join(NETS_ALL)))
    NETS_ALL = (ONLY,) if ONLY in NETS_ALL else NETS_ALL
    NETS_CONF = (ONLY,) if ONLY in NETS_CONF else NETS_CONF

results = []          # [(名称, ok, 摘要)]
TMP = tempfile.mkdtemp(prefix="hbchecks_")


def run(name, cmd, quiet_tail=0, note_ok=None):
    """跑一条命令；quiet_tail>0 时只在失败时回显末尾若干行。"""
    p = subprocess.run(cmd, cwd=REPO, capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    ok = p.returncode == 0
    if not ok and quiet_tail:
        print("   ---- %s 输出末尾 ----" % name)
        for l in out.strip().split("\n")[-quiet_tail:]:
            print("   " + l)
    results.append((name, ok, note_ok if ok and note_ok else ("exit=%d" % p.returncode)))
    print("  %s %-42s %s" % ("OK  " if ok else "!!  ", name, results[-1][2]))
    return p, out


def js(name, js_file, out_json):
    """在页面里跑一个 JS 探针，落盘 JSON。"""
    subprocess.run([sys.executable, STEP, "eval", js_file, out_json],
                   cwd=REPO, capture_output=True)
    if not os.path.exists(out_json):
        results.append((name, False, "无输出（eval 失败）"))
        print("  !!   %-42s 无输出（eval 失败）" % name)
        return None
    with io.open(out_json, encoding="utf-8") as f:
        return json.load(f)


def main():
    if not os.path.exists(HTML):
        sys.exit("找不到 %s —— 先跑 python cloud/tariff/rebuild_offline.py" % HTML)
    print("页面：%s（%.1f MB）\n" % (HTML, os.path.getsize(HTML) / 1048576.0))
    print("修改时间：%s  ← 确认它是**刚重建**的，不是上一次的\n"
          % __import__("time").strftime("%Y-%m-%d %H:%M:%S",
                                        __import__("time").localtime(os.path.getmtime(HTML))))

    # ── 1. 四网数据体检（无需浏览器）──────────────────────────────────
    print("[1] 数据体检 audit_data.py")
    for n in NETS_ALL:
        run("audit_data --net %s" % n,
            [sys.executable, os.path.join(CT, "audit_data.py"), "--net", n, "--quiet"])

    # ── 2. 地域口径 ──────────────────────────────────────────────────
    print("\n[2] 地域口径（只保留 河北 + 全国）")
    run("check_area_scope.py",
        [sys.executable, os.path.join(REPO, "probes", "check_area_scope.py"),
         "--json", os.path.join(TMP, "area.json")])

    # ── 2b. 采集维度语义（★ 2026-09-24 新增，抓的是「该采的是不是都去采了」）──
    #   前两步问的都是「已采到的那批对不对」；这两条问的是**维度本身有没有漏**。
    #   两者失效方式完全不同：前者算错能对出来，后者是整栏/整档**从未被请求过**，
    #   接口全 200、日志无异常，唯一症状是「少了」。本轮真错（3121 码名 +
    #   全省的第二种写法）就是这一层，且**前两步全都照不出来**。
    print("\n[2b] 采集维度语义（地市码名 ↔ 条目文案 · 全省的第二种写法）")
    run("probe_city_code_semantics.py",
        [sys.executable, os.path.join(REPO, "probes", "probe_city_code_semantics.py")],
        quiet_tail=20)
    print("\n[2c] 采集维度穷尽性（★ 打真接口：上游目录/地市集合/板块取值）")
    run("probe_coverage_axes.py --net unicom",
        [sys.executable, os.path.join(REPO, "probes", "probe_coverage_axes.py"),
         "--net", "unicom"], quiet_tail=20)

    # ── 2d. 吸顶层不透明（★ 2026-09-24 新增）─────────────────────────
    #   抓的是「吸顶层半透明 ⇒ 滚动内容从它下面透上来叠成乱码」这一类。
    #   为什么必须单独一条：rect / elementFromPoint / 手工点按钮**全都测不出来**
    #   —— 透明元素的几何完全正常、命中测试也正常。唯一能表达它的量是背景 alpha。
    print("\n[2d] 吸顶层不透明（每条 position:sticky 的规则都要自带实色背景）")
    run("probe_sticky_layers.py",
        [sys.executable, os.path.join(REPO, "probes", "probe_sticky_layers.py")],
        quiet_tail=14)

    # ── 2e. 联通栏目覆盖（★ 2026-09-24 新增）────────────────────────
    #   2c 问的是**骨架**（cityList / 栏目签名 / 板块取值），它**不问**「每个组合
    #   到底有没有数据」。而「整栏从未被请求过」失效时接口全 200、日志无异常，
    #   唯一症状就是「少了」。这一步逐组合打真接口，把「三级目录 id 数」与
    #   「快照落在该组合的条目数」并排对：id > 0 而条目 == 0 ⇒ 整栏漏采。
    #   ⚠️ 判据只能是「一边有、一边零」，不能是「两个数相等」—— 同一条资费可以挂
    #      多个栏目，快照 entries 是按 reportNo 去重后的（去重前后差 ~1079）。
    print("\n[2e] 联通栏目覆盖（44 组合 × 12 城逐格：上游有目录 / 快照有没有条目）")
    run("probe_unicom_axes.py",
        [sys.executable, os.path.join(REPO, "probes", "probe_unicom_axes.py")],
        quiet_tail=16)

    # ── 2f. 筛选维度口径（★ 2026-09-24 新增）────────────────────────
    #   上面几条问的是**采集**（该采的是不是都去采了）；这一条问的是**构建**
    #   （采回来的有没有被正确归类到筛选维度上）。它失效时接口全 200、采集零失败，
    #   唯一症状是「页面上某一类永远筛不出来」。
    #   三条：联通「停售套餐」按二级栏目还原 / ty 取值域 ⊆ 上游栏目 /
    #         sect 只在本网真有两档时落盘。
    print("\n[2f] 筛选维度口径（停售还原 / 细分域 / 板块显隐）")
    run("probe_filter_dims.py",
        [sys.executable, os.path.join(REPO, "probes", "probe_filter_dims.py")],
        quiet_tail=18)

    if NO_BROWSER:
        return finish()

    # ── 3. 起 Chrome + 导航 ─────────────────────────────────────────
    print("\n[3] 调试 Chrome（9223）")
    try:
        import websocket                                        # noqa: F401
    except ImportError:
        results.append(("Chrome CDP 探针", False, "缺 websocket-client（用 .tools/venv_gui 的解释器）"))
        print("  !!   缺 websocket-client —— 请用 .tools/venv_gui/Scripts/python.exe 跑本脚本")
        return finish()
    run("cdp_launch.py", [sys.executable, os.path.join(BASE, "cdp_launch.py")])
    # ★ 先去 about:blank 再进页面（2026-10-06 第 5 轮体检加固）：tab 里若残留
    #   同文件带 hash 的旧状态（人工调试 / 上一轮探针），直接 nav 会变成
    #   **同文档导航**——脚本不重跑、控件不重置，walk/e2e 全部在旧状态上跑，
    #   断言对着上一轮的 view 报错（本次实测：e2e 报「页面 5280 ≠ 数据 81」，
    #   5280 是上一轮 move 的全量行数；unicom 轮看到的甚至是 move 的页面）。
    #   手动复测页面本身深链/hot-switch 全对 —— 纯测试环境残留，非代码回归。
    run("nav 清场（about:blank）", [sys.executable, STEP, "nav", "about:blank"])
    run("nav 到本地页面", [sys.executable, STEP, "nav",
                           "file:///" + HTML.replace("\\", "/")])

    # ── 4. 交互遍历（连跑两次必须逐字节一致）──────────────────────────
    print("\n[4] 交互遍历 page_walk_check.js（异常 0 + 连跑一致）")
    w1 = os.path.join(TMP, "walk1.json")
    w2 = os.path.join(TMP, "walk2.json")
    d1 = js("page_walk_check.js（第 1 遍）", os.path.join(BASE, "page_walk_check.js"), w1)
    d2 = js("page_walk_check.js（第 2 遍）", os.path.join(BASE, "page_walk_check.js"), w2)
    if d1 is not None and d2 is not None:
        b1 = io.open(w1, "rb").read()
        b2 = io.open(w2, "rb").read()
        errs = d1.get("errs") or []
        bad = {k: v for k, v in (d1.get("bind") or {}).items()
               if v != "function" and not isinstance(v, (int, dict))}
        geom = (d1.get("misc") or {}).get("geom") or {}
        geom_ok = all(geom.get(k) for k in ("vtabOk", "barOk", "thOk", "hitOk", "opaqueOk"))
        ok = (not errs) and (not bad) and (b1 == b2) and geom_ok
        why = []
        if errs:
            why.append("异常 %d" % len(errs))
        if bad:
            why.append("绑定缺失 %s" % list(bad))
        if b1 != b2:
            why.append("连跑不一致")
        if not geom_ok:
            why.append("几何自检未过")
            st = (d1.get("misc") or {}).get("sticky") or {}
            if st.get("bad"):
                why.append("吸顶层半透明 %s" % st["bad"])
            if not st.get("n"):
                why.append("一个吸顶根都没扫到（判据空转）")
        results.append(("walk：异常 0 + 连跑一致 + 几何命中",
                        ok, "异常 0 · 连跑一致 · 几何 OK" if ok else "；".join(why)))
        print("  %s %-42s %s" % ("OK  " if ok else "!!  ", results[-1][0], results[-1][2]))
        if errs:
            for e in errs[:5]:
                print("       ! %s" % e)

    # ── 5. e2e 语义断言 ─────────────────────────────────────────────
    print("\n[5] 页面级断言 page_e2e_check.js")
    # ★ e2e 前再清场一次（2026-10-06）。walk 与 e2e 是两个 eval，walk 会留下
    #   状态（页签 STA / 视图 view / hash）；e2e 的 resetAll 名义上能恢复，但
    #   低频竞态实测仍会漂：cityProbe 按**错误的网**对账（got 恒为另一网的
    #   全量行数，如四网全见 1303=telecom 全量），而同一脚本单独跑全绿、
    #   连跑两遍也全绿 —— 纯跨 eval 状态耦合，不是页面回归。
    #   与 [3] 的清场同理：换一次全新页面加载从根上断开，一次 nav ≈1 秒，
    #   比一次假红（人肉排查半小时）便宜得多。
    run("nav 清场（e2e 前，about:blank）", [sys.executable, STEP, "nav", "about:blank"])
    run("nav 到本地页面（e2e 用全新加载）", [sys.executable, STEP, "nav",
                           "file:///" + HTML.replace("\\", "/")])
    e = js("page_e2e_check.js", os.path.join(BASE, "page_e2e_check.js"),
           os.path.join(TMP, "e2e.json"))
    if e is not None:
        ok = bool(e.get("__allOk"))
        probs = []
        for net in NETS_ALL:
            v = e.get(net) or {}
            for p in (v.get("problems") or []):
                probs.append("%s: %s" % (net, p))
        results.append(("e2e：四网语义断言", ok, "__allOk=true" if ok else "；".join(probs) or "ok=false"))
        print("  %s %-42s %s" % ("OK  " if ok else "!!  ", results[-1][0], results[-1][2]))
        for p in probs[:6]:
            print("       ! %s" % p)

    # ── 5b. 首屏性能仪表（★ 2026-10-04 工程审查 P1）─────────────────
    #   首屏是唯一随数据量线性恶化的环节（交互层实测 9ms/8096 行）。
    #   这里不挡回归（软阈值只警示），职责是**每次回归都留一个数**：
    #   DCL 持续上涨到 1500ms 以上，就该按 eng-review P1 预案拆分了。
    print("\n[5b] 首屏性能仪表 page_startup_check.py")
    p, out = run("startup：DCL / JSON.parse 基线",
                 [sys.executable, os.path.join(BASE, "page_startup_check.py")],
                 quiet_tail=8)
    if not p.returncode:
        import re
        m = re.search(r'\{"dcl".*\}', out)
        if m:
            d = json.loads(m.group(0))
            tag = "" if d["dcl"] <= 1500 else " ⚠ 超软阈值"
            results[-1] = (results[-1][0], True,
                           "DCL %dms · parse %dms · blob %.1fMB%s"
                           % (d["dcl"], d["parseMs"], d["blobMB"], tag))

    # ── 5c. 数据不变量全量审计（★ 2026-10-06 第 5 轮体检）─────────────
    #   机器穷举四网全部行 × 30+ 条不变量 + 六源跨层对账（快照/页面/gz 归档/
    #   history/feed/模板）。与抽查式检查的本质区别：**全量**，任何一行破坏
    #   不变量都会被抓到。观察项（R3 联通无细分 / R6 广电下架无日期）是上游
    #   形态，只输出数量不 fail——数量突变才是信号。
    print("\n[5c] 数据不变量审计 deep_audit.py")
    p, out = run("audit：行不变量 + 六源对账",
                 [sys.executable, os.path.join(BASE, "deep_audit.py")],
                 quiet_tail=10)
    if p.returncode:
        results[-1] = (results[-1][0], False, "存在不变量违规，详见上方输出")

    # ── 6. 筛选口径对账（三网）───────────────────────────────────────
    print("\n[6] 筛选口径对账 run_conformance.py")
    for n in NETS_CONF:
        p, out = run("conformance --net %s" % n,
                     [sys.executable, os.path.join(BASE, "run_conformance.py"),
                      "--net", n, "--html", HTML], quiet_tail=12)
        if not p.returncode:
            results[-1] = (results[-1][0], True, "与 oracle 逐条一致")
    return finish()


def finish():
    print("\n" + "=" * 66)
    nfail = sum(1 for _n, ok, _s in results if not ok)
    for n, ok, s in results:
        print("  %s %-42s %s" % ("✅" if ok else "❌", n, s))
    print("=" * 66)
    print("共 %d 项，%s" % (len(results), "全部通过 ✅" if not nfail else "**%d 项失败 ❌**" % nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
