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
NETS_CONF = ("move", "telecom", "unicom")            # 对账三网（广电无地市维度）


def _net_arg(default=None):
    if "--net" in argv:
        i = argv.index("--net")
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


ONLY = _net_arg()
if ONLY:
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
        geom_ok = all(geom.get(k) for k in ("vtabOk", "barOk", "thOk", "hitOk"))
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
        results.append(("walk：异常 0 + 连跑一致 + 几何命中",
                        ok, "异常 0 · 连跑一致 · 几何 OK" if ok else "；".join(why)))
        print("  %s %-42s %s" % ("OK  " if ok else "!!  ", results[-1][0], results[-1][2]))
        if errs:
            for e in errs[:5]:
                print("       ! %s" % e)

    # ── 5. e2e 语义断言 ─────────────────────────────────────────────
    print("\n[5] 页面级断言 page_e2e_check.js")
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
