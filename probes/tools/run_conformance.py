# -*- coding: utf-8 -*-
"""筛选口径对账 · 端到端驱动。

把 conformance.py 的 oracle 期望值送进真实浏览器，用页面自己的 apply() 跑一遍，
逐条比对 —— 也就是把**断掉的那一环**补上（此前 cases.json 无人消费）。

流程：
  1) 跑 conformance.py 生成用例（oracle 侧，独立重写的筛选语义）
  2) 起/复用调试 Chrome，导航到本地 docs/index.html（带 #net=<网>）
  3) 把用例内联进页面消费者脚本（probes/tools/page_conformance_check.js），CDP eval
  4) 比对期望 / 实际，打印差异；差异非空则退出码 1

用法:
    python probes/tools/run_conformance.py                # move（conformance 只覆盖它）
    python probes/tools/run_conformance.py --net move
    python probes/tools/run_conformance.py --html <路径>  # 指定页面产物（默认本地 docs/index.html）

⚠️ 需要：调试 Chrome 已在 9223 端口（python probes/tools/cdp_launch.py）、
   python 侧有 websocket-client（本机用 .tools/venv_gui 的解释器）。
"""
import io
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))          # probes/tools
REPO = os.path.dirname(os.path.dirname(BASE))
CT = os.path.join(REPO, "cloud", "tariff")
STEP = os.path.join(BASE, "ct_browser", "cdp_desktop_step.py")
CONSUMER = os.path.join(BASE, "page_conformance_check.js")
TMP = os.path.join(REPO, ".conformance")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    argv = sys.argv[1:]
    net = "move"
    if "--net" in argv:
        i = argv.index("--net")
        net = argv[i + 1] if i + 1 < len(argv) else "move"
    html = os.path.join(CT, "docs", "index.html")
    if "--html" in argv:
        i = argv.index("--html")
        html = argv[i + 1] if i + 1 < len(argv) else html

    os.makedirs(TMP, exist_ok=True)
    cases_p = os.path.join(TMP, "cases.json")

    # 1) oracle 侧
    r = subprocess.run([sys.executable, os.path.join(CT, "conformance.py"), cases_p, "--net", net],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip())
        return 2
    cases = json.loads(io.open(cases_p, encoding="utf-8").read())
    print("\n用例 %d 条 · 页面 %s · 网 %s" % (len(cases), html, net))

    # 2) 组页面消费者脚本（用例内联）
    body = io.open(CONSUMER, encoding="utf-8").read()
    runner = ("var CONF_CASES=%s;\nvar CONF_NET=%s;\n"
              % (json.dumps(cases, ensure_ascii=False), json.dumps(net))) + body
    runner_p = os.path.join(TMP, "runner.js")
    io.open(runner_p, "w", encoding="utf-8").write(runner)

    # 3) 导航 + 执行
    url = "file:///" + html.replace("\\", "/") + "#net=" + net
    subprocess.run([sys.executable, STEP, "nav", url], check=False)
    out_p = os.path.join(TMP, "result.json")
    subprocess.run([sys.executable, STEP, "eval", runner_p, out_p], check=False)
    if not os.path.exists(out_p):
        print("!! 浏览器没有产出结果（调试 Chrome 在 9223 上吗？）")
        return 2
    rep = json.loads(io.open(out_p, encoding="utf-8").read())

    # 4) 比对
    print("跑完 %d/%d 用例，零结果 %d 条" % (rep["ran"], rep["cases"], rep["zero"]))
    if rep["errors"]:
        print("\n!! 页面上跑不起来（用例被跳过，不等于通过）：")
        for e in rep["errors"]:
            print("   · %-28s %s" % (e["tag"], e["error"]))
    if rep["mismatches"]:
        print("\n❌ 对不上 %d 条：" % len(rep["mismatches"]))
        for m in rep["mismatches"][:40]:
            print("   %-30s 期望 %-6s 实际 %-6s" % (m["tag"], m["want"], m["got"]))
        if len(rep["mismatches"]) > 40:
            print("   … 其余 %d 条见 %s" % (len(rep["mismatches"]) - 40, out_p))
    ok = not rep["mismatches"] and not rep["errors"]
    print("\n%s" % ("✅ 筛选口径与 oracle 完全一致" if ok else "⚠️ 存在差异，见上"))
    print("用例明细 → %s" % out_p)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
