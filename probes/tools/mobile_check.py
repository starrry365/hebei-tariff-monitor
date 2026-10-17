# -*- coding: utf-8 -*-
"""窄屏（手机）渲染体检：横向溢出 + 卡片视图 + 关键区块高度。

为什么单独一个探针：桌面端与手机的失败模式完全不同。桌面看的是「表格能不能
排下」，手机看的是「会不会横向溢出（一旦溢出，Chrome 会把整页缩放，
表现为全站字都变小）」和「10 列表格有没有折成卡片」。

判据（2026-10-17 基线：320/360/390/414/600/641/768/900/1024/1440 全部 over=0）：
  exit 0  各宽度均无横向溢出，且 ≤640 为卡片视图、>640 为表格视图
  exit 1  有宽度横向溢出（documentElement.scrollWidth > clientWidth）

用法：
    python probes/tools/mobile_check.py            # 检查（带截图）
    python probes/tools/mobile_check.py --no-shot  # 只量不截图
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 9223
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(ROOT, "cloud", "tariff", "docs", "index.html")
WIDTHS = (320, 360, 390, 414, 600, 641, 768, 900, 1024, 1440)

MEASURE = """JSON.stringify({
  inner: innerWidth,
  client: document.documentElement.clientWidth,
  over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  card: getComputedStyle(document.querySelector('#tb tr.row')).display,
  topbar: Math.round((document.querySelector('.topbar')||{getBoundingClientRect:()=>({height:0})})
                     .getBoundingClientRect().height),
  rows: document.querySelectorAll('#tb tr.row').length,
  nodes: document.getElementsByTagName('*').length
})"""


def connect():
    subprocess.run([sys.executable, os.path.join(BASE, "cdp_launch.py")],
                   capture_output=True)
    # 🔴 显式跳过系统代理：本地 CDP 端口不该走代理（否则会卡在 CONNECT）
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            tabs = json.load(opener.open("http://127.0.0.1:%d/json" % PORT))
            pages = [t for t in tabs if t.get("type") == "page"]
            if pages:
                return websocket.create_connection(
                    pages[0]["webSocketDebuggerUrl"], timeout=60,
                    suppress_origin=True)
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("CDP 连接失败（先跑一次 probes/tools/cdp_launch.py）")


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    def cmd(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                return r

    def ev(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True,
                     awaitPromise=True)
        return r.get("result", {}).get("result", {}).get("value")

    def shot(self, path):
        r = self.cmd("Page.captureScreenshot", format="png")
        data = r.get("result", {}).get("data")
        if data:
            open(path, "wb").write(base64.b64decode(data))
        return bool(data)


def main():
    do_shot = "--no-shot" not in sys.argv
    if not os.path.exists(PAGE):
        raise SystemExit("找不到 %s —— 先跑一次 render_only()/构建" % PAGE)
    url = "file:///" + PAGE.replace("\\", "/")
    c = CDP(connect())
    c.cmd("Page.enable")
    c.cmd("Runtime.enable")

    bad = []
    print("%-6s %-7s %-7s %-8s %-9s %s" %
          ("宽", "overflow", "卡片", "顶栏", "rows", "结论"))
    for w in WIDTHS:
        c.cmd("Emulation.setDeviceMetricsOverride", width=w, height=844,
              deviceScaleFactor=2, mobile=w < 900)
        c.cmd("Page.navigate", url="about:blank")
        time.sleep(0.25)
        c.cmd("Page.navigate", url=url)
        for _ in range(150):
            time.sleep(0.18)
            if c.ev("document.readyState==='complete'"):
                break
        time.sleep(0.9)
        d = json.loads(c.ev(MEASURE) or "{}")
        card = d.get("card") == "flex"
        want_card = w <= 640
        ok = d.get("over", 1) <= 0 and card == want_card
        if not ok:
            bad.append((w, d.get("over"), card, want_card))
        print("%-6d %-7s %-7s %-8s %-9s %s" %
              (w, d.get("over"), "卡片" if card else "表格",
               d.get("topbar"), d.get("rows"), "OK" if ok else "✗"))
        if do_shot and w in (390, 768):
            out = os.path.join(ROOT, "_mobile_%d.png" % w)
            if c.shot(out):
                print("       截图 %s" % os.path.basename(out))

    if bad:
        print("\n✗ %d 个宽度不合格：%s" % (len(bad), bad))
        print("  横向溢出会让 Chrome 缩放整页（全站字变小），"
              "常见来源：不折行的 flex 行、固定 min-width 的表格、nowrap 长文本。")
        return 1
    print("\n✓ 全部宽度无横向溢出，视图切换正常")
    return 0


sys.exit(main())
