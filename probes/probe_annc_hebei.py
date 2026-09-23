# -*- coding: utf-8 -*-
"""河北公告到底有没有：逐个试公告页的参数开关，看有没有河北的数据

已知（2026-09-23 实测）：
  · 公告端点**只有一个**：nrapigate/nrtariff/new/personal/announcement/list
  · 请求体省份来自页面 URL 的 provC / cityC（页面读 cityInfo.provinceCode）
  · scopePageCode 由 fontType 决定：fontType=2 → 21106，否则 → 22958
  · provinceCode=100 → 264 条；311（河北）/351/531/210/200/250/371/571/731 **全为 0**

本脚本把还没试的开关都试一遍：fontType=2（另一个 scopePageCode）、带 cityC、
不同 cellNum 占位，看河北能不能出数据。同时打印页面可见文本，
确认「空数组」在界面上表现为「加载中」还是「暂无」。

用法（需先 python probes/tools/cdp_launch.py）：
    python probes/probe_annc_hebei.py
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
PAGE = "https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZoneAnncList.html"


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


CASES = [
    ("311", "0000", "1", "河北 + fontType=1 (scopePageCode=22958)"),
    ("311", "0000", "2", "河北 + fontType=2 (scopePageCode=21106)"),
    ("311", "3110", "2", "河北石家庄 + fontType=2"),
    ("100", "0000", "2", "北京 + fontType=2（对照）"),
    ("100", "0000", "1", "北京 + fontType=1（对照，已知 264）"),
]
c = CDP()
for prov, city, ft, desc in CASES:
    url = ("%s?sourcePageType=tariffZonePers&provC=%s&cityC=%s&fontType=%s&isCareMode=1"
           % (PAGE, prov, city, ft))
    tid, sid = c.new_tab()
    c.send("Network.enable", {"maxPostDataSize": 65536}, sid)
    c.send("Page.enable", {}, sid)
    c.send("Runtime.enable", {}, sid)
    c.send("Network.setUserAgentOverride", {"userAgent": UA}, sid)
    c.send("Network.clearBrowserCache", {}, sid)
    c.send("Storage.clearDataForOrigin",
           {"origin": "https://h.app.coc.10086.cn", "storageTypes": "all"}, sid)
    c.send("Page.navigate", {"url": url}, sid)
    pend = {}
    t_end = time.time() + 20
    while time.time() < t_end:
        m = c.recv(max(0.4, t_end - time.time()))
        if not m or m.get("sessionId") not in (None, sid):
            continue
        if m.get("method") == "Network.requestWillBeSent":
            p = m["params"]
            r = p.get("request") or {}
            if "announcement" in (r.get("url") or ""):
                pend[p["requestId"]] = r.get("postData") or ""
    time.sleep(6)
    got = []
    for rid, body in pend.items():
        m = c.send("Network.getResponseBody", {"requestId": rid}, sid)
        raw = ((m.get("result") or {}).get("body")) or ""
        if not raw:
            continue
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and set(j.keys()) == {"body"}:
                j = json.loads(mz_crypto.decrypt(j["body"]))
        except Exception:
            continue
        d = j.get("data")
        items = (d.get("list") if isinstance(d, dict) else d) or []
        provs = {}
        for it in items:
            k = str(it.get("contactProvince") or "?")
            provs[k] = provs.get(k, 0) + 1
        got.append((len(items), provs, j.get("returnCode")))
    summary = ", ".join("%d 条 %s rc=%s" % g for g in got) or "(没抓到公告请求)"
    txt = c.ev("JSON.stringify((document.body?document.body.innerText:' ').slice(0,120))", sid)
    try:
        txt = json.loads(txt)
    except Exception:
        pass
    print("%-34s → %-28s 页面文本: %s" % (desc, summary, str(txt)[:60].replace("\n", " ")))
    c.send("Target.closeTarget", {"targetId": tid})
