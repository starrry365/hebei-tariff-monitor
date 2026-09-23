#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对重构后的页面做分镜截图：四个页签 + 深色主题。
用法: python _shots.py <outdir>
"""
import base64
import json
import os
import sys
import time
import urllib.request

import websocket

PORT = 9223
PH = urllib.request.ProxyHandler({})   # 🔴 必须显式空 dict，否则偷读残留代理


def http_json(path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 headers={"Host": "localhost"})
    opener = urllib.request.build_opener(PH)
    with opener.open(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pick_page():
    ps = [p for p in http_json("/json/list") if p.get("type") == "page"]
    for p in ps:
        if not p.get("url", "").startswith("about:"):
            return p
    return ps[0]


class Cdp:
    def __init__(self, page):
        url = page["webSocketDebuggerUrl"].replace("localhost", f"127.0.0.1:{PORT}", 1)
        self.ws = websocket.create_connection(url, timeout=120, suppress_origin=True)
        self.i = 0

    def call(self, method, params=None, timeout=120):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg
        raise SystemExit("timeout " + method)

    def ev(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True})
        res = r.get("result", {}).get("result", {})
        if "exceptionDetails" in r.get("result", {}):
            print("  !! exc:", json.dumps(r["result"]["exceptionDetails"], ensure_ascii=False)[:300])
        return res.get("value")

    def shot(self, path, tries=2):
        for k in range(tries):
            r = self.call("Page.captureScreenshot", {"format": "png",
                                                     "captureBeyondViewport": False})
            b64 = r.get("result", {}).get("data")
            if b64:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
                print("  shot ->", path, os.path.getsize(path) // 1024, "KB")
                return True
            print("  retry shot", k + 1, json.dumps(r, ensure_ascii=False)[:200])
            time.sleep(1.5)
        return False


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    c = Cdp(pick_page())

    # 视口放大到 1600x1000（dpr 1 → 截图更清晰，且能看全布局）
    c.call("Emulation.setDeviceMetricsOverride",
           {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(0.6)

    # 浅色主题
    c.ev("applyTheme('light'); document.documentElement.setAttribute('data-theme','light'); 1")

    plan = [("ov", "01-ov"), ("list", "02-list"), ("hist", "03-hist"), ("about", "04-about")]
    for view, name in plan:
        c.ev(f"setView('{view}'); 1")
        time.sleep(0.8)
        c.shot(os.path.join(outdir, f"{name}.png"))

    # 深色主题 + 总览
    c.ev("applyTheme('dark'); document.documentElement.setAttribute('data-theme','dark'); setView('ov'); 1")
    time.sleep(0.8)
    c.shot(os.path.join(outdir, "05-ov-dark.png"))

    # 深色 + 明细
    c.ev("setView('list'); 1")
    time.sleep(0.8)
    c.shot(os.path.join(outdir, "06-list-dark.png"))

    # 换网看主题色（联通 → 明细）
    c.ev("applyTheme('light'); document.documentElement.setAttribute('data-theme','light'); "
         "if(typeof switchNet==='function'){switchNet('unicom');} setView('list'); 1")
    time.sleep(1.2)
    c.shot(os.path.join(outdir, "07-list-unicom.png"))

    # 回到默认，恢复视口
    c.ev("setView('ov'); if(typeof switchNet==='function'){switchNet('move');} 1")
    c.call("Emulation.clearDeviceMetricsOverride")
    print("done")


if __name__ == "__main__":
    main()
