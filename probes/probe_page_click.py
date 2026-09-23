# -*- coding: utf-8 -*-
"""模拟点击真实页面的每个栏目，抓它自己发请求拿到多少条

页面是**按需加载**：首次只取默认栏目（1 次 getTariffListInfo），
切栏目才发新请求。所以要逐个点过去，才能知道每个栏目页面实际取到多少。

判据价值：这是**独立于采集逻辑**的验证 —— 页面是上游前端，它取数的参数
（fistLimit / 容器 / 分页）代表「用户实际看到什么」，而不是我们猜的。

用法（需先 python probes/tools/cdp_launch.py）：
    python probes/probe_page_click.py
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
URL = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}
ATTR_CN = {"1": "全网资费", "2": "河北资费", "3": "attr3"}


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
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True}, sid)
        res = (r.get("result") or {})
        if "exceptionDetails" in res:
            return {"__err": str(res["exceptionDetails"])[:200]}
        return ((res.get("result") or {}).get("value"))

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
c.send("Network.clearBrowserCache", {}, sid)
c.send("Storage.clearDataForOrigin",
       {"origin": "https://h.app.coc.10086.cn", "storageTypes": "all"}, sid)
c.send("Page.navigate", {"url": URL}, sid)
print("已清缓存导航，等 25s 首屏…")
time.sleep(25)

# 收集请求（后台持续收，避免漏）
pending = {}


def pump(seconds):
    """收 seconds 秒的事件，把新的 getTariffListInfo 请求塞进 pending"""
    t_end = time.time() + seconds
    while time.time() < t_end:
        m = c.recv(max(0.3, t_end - time.time()))
        if not m or m.get("sessionId") not in (None, sid):
            continue
        if m.get("method") == "Network.requestWillBeSent":
            p = m["params"]
            r = p.get("request") or {}
            if "getTariffListInfo" in (r.get("url") or ""):
                pending[p["requestId"]] = r.get("postData") or ""


pump(2)
print("首屏已发明细请求 %d 个\n" % len(pending))

# 找可点的栏目元素（叶子节点、文本短、命中栏目关键词）
find = r"""
(function(){
  var KW = ['套餐','加装包','营销活动','港澳台','国际','标准资费','全网','河北','个人','政企'];
  var out = [];
  document.querySelectorAll('*').forEach(function(el){
    if (el.children.length) return;
    var t = (el.innerText||'').trim();
    if (!t || t.length > 10) return;
    if (!KW.some(function(k){ return t.indexOf(k) >= 0; })) return;
    var p = el.parentElement;
    out.push({t: t, cls: (el.className||'').toString().slice(0,40),
              pcls: p ? (p.className||'').toString().slice(0,40) : '',
              path: (function(){ var s=[],n=el; while(n&&s.length<5){ s.unshift(n.tagName); n=n.parentElement;} return s.join('>'); })()});
  });
  return JSON.stringify(out.slice(0, 40));
})()
"""
els = c.ev(find, sid)
print("候选可点元素：")
try:
    for e in json.loads(els):
        print("   %-12s cls=%-30s parent=%s" % (e["t"], e["cls"][:28], e["pcls"][:28]))
except Exception:
    print("  ", els)

# 逐个点击：先点「板块」类（全网/河北/个人/政企），再点栏目类
click_js = r"""
(function(kw){
  var hit = null;
  document.querySelectorAll('*').forEach(function(el){
    if (hit || el.children.length) return;
    var t = (el.innerText||'').trim();
    if (t === kw || t.indexOf(kw) === 0) hit = el;
  });
  if (!hit) return 'notfound:' + kw;
  var node = hit;
  for (var i = 0; i < 4 && node; i++) {
    try { node.click(); return 'clicked:' + kw; } catch (e) {}
    node = node.parentElement;
  }
  return 'fail:' + kw;
})(%s)
"""

targets = ["全网资费", "河北资费", "套餐", "加装包", "营销活动", "港澳台", "标准资费"]
print("\n=== 逐栏目点击 ===")
clicks = {}
for kw in targets:
    before = set(pending)
    r = c.ev(click_js % json.dumps(kw, ensure_ascii=False), sid)
    pump(6)
    new = set(pending) - before
    clicks[kw] = (r, list(new))
    print("  点击 %-8s → %-22s 新请求 %d" % (kw, str(r)[:22], len(new)))

# ★ 取响应体前必须再等：请求刚发出时 getResponseBody 拿不到内容（返回空），
#   表现就是「明明发了请求，统计却是 0 条」—— 容易被误读成「页面没取数」。
print("\n等待在途请求完成…")
pump(12)
print("\n=== 页面自己取到的明细（按请求参数）===")
print("%-10s %-14s %8s %8s %8s %8s %6s %5s"
      % ("板块", "栏目", "系列", "nonMod", "module", "合计", "fist", "page"))
print("-" * 78)
agg = {}
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
        print("  解密失败:", e)
        continue
    a, t2 = str(b.get("tariffAttr")), str(b.get("type2"))
    d = j.get("data") if isinstance(j, dict) else None
    beans = (d or {}).get("beans") or [] if isinstance(d, dict) else []
    nm = sum(len(x.get("nonModuleList") or []) for x in beans)
    md = sum(len(mm.get("tariffList") or [])
             for x in beans for mm in (x.get("moduleList") or []))
    k = (a, t2)
    s = agg.setdefault(k, {"nm": 0, "md": 0, "series": 0, "fist": set(), "pages": 0})
    s["nm"] += nm
    s["md"] += md
    s["series"] += len(beans)
    s["fist"].add(str(b.get("fistLimit")))
    s["pages"] += 1
    print("%-10s %-14s %8d %8d %8d %8d %6s p=%s"
          % (ATTR_CN.get(a, a), ZFLX.get(t2, t2), len(beans), nm, md, nm + md,
             b.get("fistLimit"), b.get("page")))
print("-" * 78)
for k, s in sorted(agg.items()):
    print("汇总 %-10s %-14s 合计 %d 条（请求 %d 次，fistLimit=%s）"
          % (ATTR_CN.get(k[0], k[0]), ZFLX.get(k[1], k[1]),
             s["nm"] + s["md"], s["pages"], "/".join(sorted(s["fist"]))))
c.send("Target.closeTarget", {"targetId": tid})
