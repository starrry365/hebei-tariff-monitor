# -*- coding: utf-8 -*-
"""首屏性能仪表（2026-10-04 工程审查 P1「先有仪表再谈优化」）。

**它测什么**：导航到本地重建页（docs/index.html），读 Navigation Timing 的
DCL / load，并在页面里实测 NETS 数据 blob 的 JSON.parse 耗时与字节数。
这是**唯一随数据量线性恶化**的环节（交互层 filter+sort 实测 9ms，
即使 10 倍数据也只有 ~90ms），所以阈值锚在首屏。

**为什么做成本地仪表而不是 CI 闸门**：post-checks job 无浏览器（纯 Python
探针），为一条性能曲线装 Chrome+Xvfb 不划算。仪表的职责是**记录趋势**——
每次本机回归都打印当前 DCL，超软阈值（1500ms，≈3 倍数据）时提示按
eng-review P1 预案（四网拆分懒加载）动手；超硬阈值（5000ms）才 fail。

判据（实测基线 2026-10-04：DCL 604~642ms · parse 47ms · blob 6.2MB）：
  exit 0  正常（≤1500ms）
  exit 0  软超线（1500~5000ms）—— 打印警示，不挡回归
  exit 1  硬超线（>5000ms 或 parse >2000ms）—— 数据量已到悬崖，必须优化

用法：python probes/tools/page_startup_check.py
（调试 Chrome 由 cdp_launch.py 幂等拉起；run_checks 第 [5b] 步调用本脚本。）
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.abspath(os.path.join(BASE, "..", "..", "cloud", "tariff",
                                    "docs", "index.html"))
URL = "file:///" + HTML.replace("\\", "/")

DCL_WARN = 1500
DCL_FAIL = 5000
PARSE_FAIL = 2000

# 在页面里执行：读导航计时 + 实测 JSON.parse。返回 JSON 字符串。
PROBE_JS = r"""
(() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const html = document.documentElement.innerHTML;
  const start = html.indexOf('const NETS=') + 11;
  const end = html.indexOf(';\n', start);
  const blob = html.slice(start, end);
  const t0 = performance.now();
  JSON.parse(blob);
  const t1 = performance.now();
  return JSON.stringify({
    dcl: Math.round(nav.domContentLoadedEventEnd),
    load: Math.round(nav.loadEventEnd),
    parseMs: Math.round(t1 - t0),
    blobMB: +(blob.length / 1048576).toFixed(1),
    view: (typeof view !== 'undefined') ? view.length : -1
  });
})()
"""


def main():
    if not os.path.exists(HTML):
        print("!! 找不到 %s —— 先跑 python cloud/tariff/rebuild_offline.py" % HTML)
        return 1

    # Chrome 幂等拉起（已在跑则直接复用）—— 与 run_checks 的 [3] 步同一入口。
    subprocess.run([sys.executable, os.path.join(BASE, "cdp_launch.py")],
                   capture_output=True)
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9223/json"))
    tab = [t for t in tabs if t.get("type") == "page"][0]
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60,
                                     suppress_origin=True)
    _n = [0]

    def cmd(method, **params):
        _n[0] += 1
        ws.send(json.dumps({"id": _n[0], "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _n[0]:
                return m

    def ev(expr):
        r = cmd("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("result", {})

    cmd("Page.enable")
    cmd("Runtime.enable")
    # 先 about:blank 再导航：保证读到的是**这一次**的导航计时，不是上一次的残留。
    cmd("Page.navigate", url="about:blank")
    time.sleep(0.4)
    cmd("Page.navigate", url=URL)
    for _ in range(60):                      # 最多等 15s（首次启动可能要装 profile）
        time.sleep(0.25)
        if ev("document.readyState==='complete'").get("value"):
            break
    else:
        print("!! 页面 15s 内未加载完成（Chrome 是否正常？）")
        return 1
    time.sleep(0.5)                          # load 事件收尾

    res = ev(PROBE_JS)
    if res.get("subtype") == "error" or res.get("value") is None:
        print("!! 探针执行失败：%s" % str(res.get("description"))[:200])
        return 1
    d = json.loads(res["value"])

    # 判定与输出（最后一行是机器可读摘要，run_checks 解析它写进检查项注记）
    warn = DCL_WARN < d["dcl"] <= DCL_FAIL
    hard = d["dcl"] > DCL_FAIL or d["parseMs"] > PARSE_FAIL
    print("首屏仪表：DCL=%dms · JSON.parse=%dms · blob=%.1fMB · view=%d"
          % (d["dcl"], d["parseMs"], d["blobMB"], d["view"]))
    print(json.dumps(d, ensure_ascii=False))
    if hard:
        print("!! 超过硬阈值（DCL>%d 或 parse>%d）—— 数据量已到悬崖，按 eng-review"
              " P1 预案（四网拆分懒加载）动手" % (DCL_FAIL, PARSE_FAIL))
        return 1
    if warn:
        print("⚠ DCL 超软阈值 %dms —— 仪表记录在案；持续上涨再按预案拆分" % DCL_WARN)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
