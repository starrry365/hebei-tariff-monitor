# -*- coding: utf-8 -*-
"""首屏性能仪表（2026-10-04 工程审查 P1「先有仪表再谈优化」）。

**它测什么**：导航到本地重建页（docs/index.html），读 Navigation Timing 的
DCL / load，并实测「当前网 rowsOf 首调」的懒解码耗时（cols→rows，交互前
最重的一次同步计算）。内联体积与 sidecar 字节数由 Python 侧直接从磁盘统计
（file:// 下页面里 fetch 不到 sidecar，页面侧测不了，也没必要）。

2026-10-15 四网数据拆分懒加载落地后，体积指标改为「内联 meta（随模板走，
不再随数据量涨）+ sidecar（不可变 URL，SW 缓存后零往返）」，DCL 预期回落到
600ms 档并与四网总数据量脱钩；阈值语义不变，锚的是「数据量再翻倍也不恶化」。

判据（拆分前基线 2026-10-15：DCL 1103~4220ms · 内联 6.24MB；拆分前基线
2026-10-04：DCL 604~642ms · parse 47ms · blob 6.2MB）：
  exit 0  正常（≤1500ms）
  exit 0  软超线（1500~5000ms）—— 打印警示，不挡回归
  exit 1  硬超线（>5000ms 或解码 >2000ms）—— 数据量已到悬崖，必须优化

2026-10-17 又一轮（HIST 拆 sidecar + 详情行懒生成 + toDate 记忆化）：
  内联 679KB → 375KB · DCL 671ms → ~460ms · DOM 节点 6739 → 2613 ·
  render 57ms → 19ms · 翻页 32ms → 7ms。每轮 apply（敲键/改筛选）约
  110ms → 47ms。下次再优化时请以这组数为新基线。

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
DATA_DIR = os.path.abspath(os.path.join(BASE, "..", "..", "cloud", "tariff",
                                        "docs", "data"))
URL = "file:///" + HTML.replace("\\", "/")

DCL_WARN = 1500
DCL_FAIL = 5000
DEC_FAIL = 2000

# 在页面里执行：读导航计时 + 实测当前网懒解码首调。返回 JSON 字符串。
# 调用前 Python 侧已轮询等 sidecar 就绪（netReady），这里只管计时。
PROBE_JS = r"""
(() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const t0 = performance.now();
  const rows = rowsOf(NET);
  const t1 = performance.now();
  return JSON.stringify({
    dcl: Math.round(nav.domContentLoadedEventEnd),
    load: Math.round(nav.loadEventEnd),
    decMs: Math.round(t1 - t0),
    net: NET,
    rows: rows.length,
    view: (typeof view !== 'undefined') ? view.length : -1
  });
})()
"""


def _sidecar_bytes():
    """docs/data/ 下四网 sidecar 的总字节数（目录不存在＝还没拆分，计 0）。

    2026-10-17 起 ``hist.*.js``（变更历史，懒加载）也在同目录 —— 它**不属于
    首屏预算**（点开时间线才拉），混进来会把「首屏成本」这本账算错，故单独统计。
    """
    try:
        return sum(os.path.getsize(os.path.join(DATA_DIR, f))
                   for f in os.listdir(DATA_DIR)
                   if f.endswith(".js") and not f.startswith("hist."))
    except OSError:
        return 0


def _hist_bytes():
    """变更历史 sidecar 字节数（不进首屏，仅作参考打印）。"""
    try:
        return sum(os.path.getsize(os.path.join(DATA_DIR, f))
                   for f in os.listdir(DATA_DIR)
                   if f.startswith("hist.") and f.endswith(".js"))
    except OSError:
        return 0


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
    # 等 sidecar 异步加载完成（file:// 下是毫秒级；给 10s 余量）。
    # 等不到也继续 —— rows=0 本身就是要记录的异常信号。
    for _ in range(40):
        if ev("(typeof netReady==='function')&&netReady(NET)").get("value"):
            break
        time.sleep(0.25)
    time.sleep(0.5)                          # load 事件收尾

    res = ev(PROBE_JS)
    if res.get("subtype") == "error" or res.get("value") is None:
        print("!! 探针执行失败：%s" % str(res.get("description"))[:200])
        return 1
    d = json.loads(res["value"])

    # 内联体积（页面文件）与 sidecar 体积（数据文件）由 Python 侧从磁盘统计。
    inline_kb = os.path.getsize(HTML) / 1024
    sc_kb = _sidecar_bytes() / 1024

    # 判定与输出（最后一行是机器可读摘要，run_checks 解析它写进检查项注记）
    warn = DCL_WARN < d["dcl"] <= DCL_FAIL
    hard = d["dcl"] > DCL_FAIL or d["decMs"] > DEC_FAIL
    print("首屏仪表：DCL=%dms · load=%dms · 当前网(%s)懒解码=%dms · "
          "内联=%.0fKB · sidecar=%.0fKB · 历史(懒)=%.0fKB · rows=%d · view=%d"
          % (d["dcl"], d["load"], d["net"], d["decMs"], inline_kb, sc_kb,
             _hist_bytes() / 1024, d["rows"], d["view"]))
    print(json.dumps(d, ensure_ascii=False))
    if hard:
        print("!! 超过硬阈值（DCL>%d 或解码>%d）—— 首屏已到悬崖，必须优化"
              % (DCL_FAIL, DEC_FAIL))
        return 1
    if warn:
        print("⚠ DCL 超软阈值 %dms —— 仪表记录在案；持续上涨再查内联体积" % DCL_WARN)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
