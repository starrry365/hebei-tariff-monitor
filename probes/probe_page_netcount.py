# -*- coding: utf-8 -*-
"""独立验证：让**真实页面**自己取数，统计它每个栏目拿到多少条

为什么需要它：采集侧的「声明对账 0 缺口」是**上游自证** —— 拿上游给的计数
去对上游给的明细，若上游计数本身就不含某一部分，两边照样相等、照样漏。
真正独立的判据是：**页面自己拿到多少条**。页面是上游前端，它取数的口径
（哪些组合、哪些容器、什么分页参数）才决定「用户实际看到什么」。

做法：新开一个隔离 tab（browser 级 Target.createTarget + attach，避免连错/串台），
Network.enable 后导航到 tariffZonePers.html，抓它自己发的 getTariffListInfo，
取响应体本地解密，按 (tariffAttr, type2) 汇总条目数，再与采集侧快照逐栏对照。

用法（需先 python probes/tools/cdp_launch.py）：
    python probes/probe_page_netcount.py           # 默认 50 秒等待
    python probes/probe_page_netcount.py 70        # 指定等待秒数
"""
import json
import gzip
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
URL = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}
ATTR_CN = {"1": "全网资费", "2": "河北资费", "3": "attr3"}
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 50


def http_json(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP:
    def __init__(self):
        ver = http_json("/json/version")
        self.ws = websocket.create_connection(ver["webSocketDebuggerUrl"],
                                              timeout=90, suppress_origin=True)
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
        r = self.send("Target.createTarget", {"url": "about:blank"})
        tid = (r.get("result") or {}).get("targetId")
        r2 = self.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        return tid, (r2.get("result") or {}).get("sessionId")

    def recv(self, timeout):
        self.ws.settimeout(timeout)
        try:
            return json.loads(self.ws.recv())
        except Exception:
            return None


def main():
    c = CDP()
    tid, sid = c.new_tab()
    print("新 tab %s / session %s" % (tid[:8], (sid or "")[:8]))
    c.send("Network.enable", {}, sid)
    c.send("Page.enable", {}, sid)
    c.send("Runtime.enable", {}, sid)
    # ★ UA 必须与采集一致：服务端按 UA 里的 leadeon/…/CMCCIT 认客户端。
    c.send("Network.setUserAgentOverride", {"userAgent": UA}, sid)
    c.send("Page.navigate", {"url": URL}, sid)
    print("已导航，等待 %ds 让页面自己取数…\n" % WAIT)

    pending = {}      # requestId -> 请求体明文
    t_end = time.time() + WAIT
    while time.time() < t_end:
        m = c.recv(max(0.5, t_end - time.time()))
        if not m:
            break
        if m.get("sessionId") not in (None, sid):
            continue
        ev = m.get("method")
        if ev == "Network.requestWillBeSent":
            p = m["params"]
            u = (p.get("request") or {}).get("url") or ""
            if "getTariffListInfo" in u:
                pending[p["requestId"]] = (p.get("request") or {}).get("postData") or ""
    print("页面发出的 getTariffListInfo 请求 %d 个" % len(pending))

    stats = {}
    for rid, body in pending.items():
        try:
            b = json.loads(body)
        except Exception:
            continue
        m = c.send("Network.getResponseBody", {"requestId": rid}, sid)
        raw = ((m.get("result") or {}).get("body")) or ""
        if not raw:
            continue
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and set(j.keys()) == {"body"}:
                j = json.loads(mz_crypto.decrypt(j["body"]))
        except Exception as e:
            print("  解密失败 %s: %s" % (rid[:8], e))
            continue
        a, t2 = str(b.get("tariffAttr")), str(b.get("type2"))
        d = j.get("data") if isinstance(j, dict) else None
        beans = (d or {}).get("beans") or [] if isinstance(d, dict) else []
        s = stats.setdefault((a, t2), {"nm": 0, "md": 0, "series": 0, "total": None,
                                       "fist": b.get("fistLimit"), "page": b.get("page")})
        s["nm"] += sum(len(x.get("nonModuleList") or []) for x in beans)
        s["md"] += sum(len(mm.get("tariffList") or [])
                       for x in beans for mm in (x.get("moduleList") or []))
        s["series"] += len(beans)
        if s["total"] is None and isinstance(d, dict):
            s["total"] = (d.get("page") or {}).get("total")

    print("\n%-10s %-14s %7s %8s %8s %8s %8s %6s"
          % ("板块", "栏目", "系列", "nonMod", "module", "合计", "上游total", "fist"))
    print("-" * 78)
    tot = 0
    for k in sorted(stats):
        s = stats[k]
        n = s["nm"] + s["md"]
        tot += n
        print("%-10s %-14s %7d %8d %8d %8d %8s %6s"
              % (ATTR_CN.get(k[0], k[0]), ZFLX.get(k[1], k[1]),
                 s["series"], s["nm"], s["md"], n, s["total"], s["fist"]))
    print("-" * 78)
    print("页面合计取到 %d 条" % tot)

    snap = os.path.join(BASE, "cloud", "tariff", "snapshots",
                        "hebei_tariff_%s.json.gz" % time.strftime("%Y%m%d"))
    if os.path.exists(snap):
        o = json.loads(gzip.open(snap, "rb").read().decode("utf-8"))
        mine = {}
        for g in o["groups"]:
            k = (str(g.get("tariffAttr")), str(g.get("type2")))
            mine[k] = mine.get(k, 0) + len(g.get("entries") or [])
        print("\n=== 与采集侧快照逐栏对照 ===")
        bad = 0
        for k in sorted(set(list(stats) + list(mine))):
            s = stats.get(k, {})
            pn = s.get("nm", 0) + s.get("md", 0)
            mn = mine.get(k, 0)
            if pn != mn:
                bad += 1
            print("  %-10s %-14s 页面=%-6d 采集=%-6d %s"
                  % (ATTR_CN.get(k[0], k[0]), ZFLX.get(k[1], k[1]), pn, mn,
                     "" if pn == mn else "◀◀ 不一致"))
        print("\n不一致栏目数：%d" % bad)
    try:
        c.send("Target.closeTarget", {"targetId": tid})
    except Exception:
        pass


if __name__ == "__main__":
    main()
