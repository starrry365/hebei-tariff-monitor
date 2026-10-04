# -*- coding: utf-8 -*-
"""CDP 首屏实测：加载本地页面，读取导航计时 + JSON.parse 耗时。"""
import json
import sys
import time
import urllib.request

import websocket

PROBE = open(__file__.replace("cdp_startup_run.py", "cdp_startup_probe.js"),
             encoding="utf-8").read()

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


cmd("Page.enable")
cmd("Runtime.enable")
cmd("Page.navigate",
    url="file:///D:/Work/WorkBuddy/hebei-tariff-monitor/cloud/tariff/docs/index.html")
time.sleep(6)
r = cmd("Runtime.evaluate", expression=PROBE, returnByValue=True)
res = r.get("result", {}).get("result", {})
if res.get("subtype") == "error":
    print("EVAL ERROR:", res.get("description", "")[:300])
    sys.exit(1)
print(res.get("value"))
