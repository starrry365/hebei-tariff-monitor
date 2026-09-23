# -*- coding: utf-8 -*-
"""河北用户实际看到的公告是哪批？—— 从「资费公示专区」首页看

公告页 tariffZoneAnncList.html 要带 provC，河北(311) 返回空。
但**资费专区首页** tariffZonePers.html 不带省份参数，它顶部就有一块公告区。
河北用户平时是**从这个首页**看公告的 —— 所以要看首页这块到底取的是哪批。

判据：首页发出的公告请求里 provinceCode 是多少、返回多少条、
有没有北京专属条目（"北京移动xxx"）。

用法（需先 python probes/tools/cdp_launch.py）：
    python probes/probe_annc_from_zone.py
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
import mz_crypto  # noqa: E402

PORT = 9223
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
URL = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")


def http_json(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP:
    def __init__(self):
        ver = http_json("/json/version")
        self.ws = websocket.create_connection(ver["webSocketDebuggerUrl"],
                                              timeout=180, suppress_origin=True)
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

    def ev(self, expr, sid):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True}, sid)
        res = r.get("result") or {}
        if "exceptionDetails" in res:
            return {"__err": str(res["exceptionDetails"])[:200]}
        return (res.get("result") or {}).get("value")

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
c.send("Runtime.enable", {}, sid)
c.send("Network.setUserAgentOverride", {"userAgent": UA}, sid)
c.send("Network.clearBrowserCache", {}, sid)
c.send("Storage.clearDataForOrigin",
       {"origin": "https://h.app.coc.10086.cn", "storageTypes": "all"}, sid)
c.send("Page.navigate", {"url": URL}, sid)
print("打开资费公示专区首页，等 30s\n")
pend = {}
t_end = time.time() + 30
while time.time() < t_end:
    m = c.recv(max(0.4, t_end - time.time()))
    if not m or m.get("sessionId") not in (None, sid):
        continue
    if m.get("method") == "Network.requestWillBeSent":
        p = m["params"]
        r = p.get("request") or {}
        u = r.get("url") or ""
        if "announcement" in u:
            pend[p["requestId"]] = r.get("postData") or ""
time.sleep(8)
print("首页发出的公告请求 %d 个" % len(pend))
for rid, body in pend.items():
    m = c.send("Network.getResponseBody", {"requestId": rid}, sid)
    raw = ((m.get("result") or {}).get("body")) or ""
    if not raw:
        print("  (响应体取不到)")
        continue
    j = json.loads(raw)
    if isinstance(j, dict) and set(j.keys()) == {"body"}:
        j = json.loads(mz_crypto.decrypt(j["body"]))
    d = j.get("data")
    items = (d.get("list") if isinstance(d, dict) else d) or []
    provs = {}
    for it in items:
        provs[str(it.get("contactProvince") or "?")] = \
            provs.get(str(it.get("contactProvince") or "?"), 0) + 1
    print("  请求体 %s" % body[:160])
    print("  → %d 条，contactProvince 分布 %s" % (len(items), provs))
    bj = [it.get("noticeTitle") or it.get("title") or "" for it in items]
    print("  含「北京」的：%s" % [t for t in bj if "北京" in str(t)][:4])
    print("  含河北地市的：%s" % [t for t in bj if any(
        k in str(t) for k in ("河北", "石家庄", "唐山", "保定", "邢台"))][:4])
    print("  前 3 条：%s" % bj[:3])
c.send("Target.closeTarget", {"targetId": tid})
