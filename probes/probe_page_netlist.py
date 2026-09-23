# -*- coding: utf-8 -*-
"""看真实页面到底怎么取数：清空缓存后列出它发出的**全部**请求与响应大小

页面第一次访问时会把数据存进 localStorage，之后再打开就直接渲染缓存 ——
那样抓不到请求（表现为「有数据、零请求」）。所以必须先清缓存再导航。

用法（需先 python probes/tools/cdp_launch.py）：
    python probes/probe_page_netlist.py [等待秒数]
"""
import json
import os
import sys
import time
import urllib.request

import websocket

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 9223
URL = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def http_json(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP:
    def __init__(self):
        ver = http_json("/json/version")
        self.ws = websocket.create_connection(ver["webSocketDebuggerUrl"],
                                              timeout=120, suppress_origin=True)
        self.i = 0

    def send(self, method, params=None, sid=None):
        self.i += 1
        msg = {"id": self.i, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                return m

    def new_tab(self):
        tid = (self.send("Target.createTarget",
                         {"url": "about:blank"}).get("result") or {}).get("targetId")
        sid = (self.send("Target.attachToTarget",
                         {"targetId": tid, "flatten": True}).get("result") or {}).get("sessionId")
        return tid, sid

    def recv(self, t):
        self.ws.settimeout(t)
        try:
            return json.loads(self.ws.recv())
        except Exception:
            return None


c = CDP()
tid, sid = c.new_tab()
c.send("Network.enable", {"maxPostDataSize": 65536}, sid)
c.send("Page.enable", {}, sid)
c.send("Network.setUserAgentOverride", {"userAgent": UA}, sid)
# ★ 清干净：否则页面读 localStorage 里上次的数据渲染，一个请求都不发
c.send("Network.clearBrowserCache", {}, sid)
c.send("Storage.clearDataForOrigin",
       {"origin": "https://h.app.coc.10086.cn", "storageTypes": "all"}, sid)
c.send("Page.navigate", {"url": URL}, sid)
print("已清缓存并导航，等待 %ds\n" % WAIT)

reqs, resp = {}, {}
t_end = time.time() + WAIT
while time.time() < t_end:
    m = c.recv(max(0.5, t_end - time.time()))
    if not m or m.get("sessionId") not in (None, sid):
        continue
    ev = m.get("method")
    p = m.get("params") or {}
    if ev == "Network.requestWillBeSent":
        r = p.get("request") or {}
        u = r.get("url") or ""
        if "h.app.coc.10086.cn" in u:
            reqs[p["requestId"]] = (u, r.get("postData") or "", r.get("method"))
    elif ev == "Network.responseReceived":
        u = (p.get("response") or {}).get("url") or ""
        if "h.app.coc.10086.cn" in u:
            resp[p["requestId"]] = (p.get("response") or {})

print("捕获 %d 个请求（仅本站）\n" % len(reqs))
print("%-6s %-64s %s" % ("方法", "路径", "响应大小"))
print("-" * 100)
for rid, (u, body, meth) in reqs.items():
    path = u.replace("https://h.app.coc.10086.cn", "")
    r = resp.get(rid) or {}
    sz = r.get("encodedDataLength")
    tail = ""
    if body:
        try:
            b = json.loads(body)
            tail = "  <= " + json.dumps(b, ensure_ascii=False)[:90]
        except Exception:
            tail = "  <= " + body[:90]
    print("%-6s %-64s %s%s" % (meth or "?", path[:64], sz, tail))

n_doc = [k for k, v in reqs.items() if "tariffZonePers.html" in v[0]]
if n_doc:
    m = c.send("Network.getResponseBody", {"requestId": n_doc[0]}, sid)
    raw = ((m.get("result") or {}).get("body")) or ""
    print("\n主文档 HTML %d 字节；含 'reportNo' %d 次、'25JT' %d 次、'tariffName' %d 次"
          % (len(raw), raw.count("reportNo"), raw.count("25JT"), raw.count("tariffName")))
c.send("Target.closeTarget", {"targetId": tid})
