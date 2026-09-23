# -*- coding: utf-8 -*-
"""公告接口的省份问题：河北到底有没有自己的公告？

上一轮结论「河北没有省级公告」的依据是 provinceCode=311 返回 0 条。
但那个结论有个没验证的前提：**100 = 集团/全国**。若 100 其实是**北京**，
那 264 条就是北京（或总部）的公告，河北的得另外找。

本脚本让页面自己发请求（公告接口有签名头，不能离线重放），逐个省份入口打开
公告页，抓它发的请求体全文 + 响应，看：
  · 请求体里 provinceCode / cityCode 到底变成了什么（确认 URL 参数有没有被页面采纳）
  · 返回多少条、这些条的 contactProvince 是什么分布

用法（需先 python probes/tools/cdp_launch.py）：
    python probes/probe_annc_prov.py
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


def url_of(prov, city):
    return ("%s?sourcePageType=tariffZonePers&provC=%s&cityC=%s&fontType=1&isCareMode=1"
            % (PAGE, prov, city))


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

    def recv(self, t):
        self.ws.settimeout(t)
        try:
            return json.loads(self.ws.recv())
        except Exception:
            return None


CASES = [
    ("100", "0000"),     # 上一轮抓的那批
    ("311", "0000"),     # 河北（省级，不指定地市）
    ("311", "3110"),     # 河北·石家庄
    ("311", "3190"),     # 河北·邢台
    ("351", "0000"),     # 山西（对照：别的省有吗）
    ("531", "0000"),     # 山东（对照）
]
c = CDP()
print("%-14s %-40s %6s %s" % ("入口", "页面实际发出的请求体", "条数", "contactProvince 分布"))
print("-" * 110)
for prov, city in CASES:
    tid, sid = c.new_tab()
    c.send("Network.enable", {"maxPostDataSize": 65536}, sid)
    c.send("Page.enable", {}, sid)
    c.send("Network.setUserAgentOverride", {"userAgent": UA}, sid)
    c.send("Network.clearBrowserCache", {}, sid)
    c.send("Storage.clearDataForOrigin",
           {"origin": "https://h.app.coc.10086.cn", "storageTypes": "all"}, sid)
    c.send("Page.navigate", {"url": url_of(prov, city)}, sid)
    pend = {}
    t_end = time.time() + 18
    while time.time() < t_end:
        m = c.recv(max(0.4, t_end - time.time()))
        if not m or m.get("sessionId") not in (None, sid):
            continue
        if m.get("method") == "Network.requestWillBeSent":
            p = m["params"]
            r = p.get("request") or {}
            if "announcement" in (r.get("url") or ""):
                pend[p["requestId"]] = r.get("postData") or ""
    time.sleep(6)     # 等请求落地，否则 getResponseBody 取到空
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
        items = ((j.get("data") or {}).get("list")
                 if isinstance(j.get("data"), dict) else None)
        if items is None:
            items = (j.get("data") or []) if isinstance(j.get("data"), list) else []
        provs = {}
        for it in items:
            k = str(it.get("contactProvince") or it.get("provinceCode") or "?")
            provs[k] = provs.get(k, 0) + 1
        got.append((body, len(items), provs, j.get("returnCode")))
    if not got:
        print("%-14s %-40s %6s %s" % ("%s/%s" % (prov, city), "(没抓到公告请求)", "-", ""))
    for body, n, provs, rc in got:
        try:
            b = json.dumps(json.loads(body), ensure_ascii=False)
        except Exception:
            b = body
        b = b.replace('"cellNum":"99999999999",', "")
        print("%-14s %-40s %6d %s  rc=%s"
              % ("%s/%s" % (prov, city), b[:40], n, provs, rc))
    c.send("Target.closeTarget", {"targetId": tid})
