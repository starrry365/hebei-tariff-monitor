# -*- coding: utf-8 -*-
"""未知容器扫描：上游响应里还有没有**第三种**明细容器？

「声明对账 0 缺口」的盲区：对账用的是上游给的 nonModuleListTotal + moduleListTotal。
若上游还有**第三种**容器，它不会被算进这两个计数 ⇒ 两边照样相等、照样漏。
（moduleList 就是这样静默丢了一整栏的。）

所以扫一遍 bean 的**全部键**，找出「值是 list、元素是 dict、且带资费特征字段
（name / tariffName / fees / reportNo）」的键，看有没有我们没消费的。

用法：python probes/probe_unknown_container.py
"""
import json
import os
import ssl
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import mz_crypto  # noqa: E402

ROOT = "https://h.app.coc.10086.cn/website/nrapigate/"
REF = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
PROV = "311"
HEADERS = {"Content-Type": "application/json; charset=UTF-8", "User-Agent": UA,
           "Origin": "https://h.app.coc.10086.cn", "Referer": REF,
           "x-requested-with": "com.greenpoint.android.mc10086.activity",
           "x-qen": "1", "x-app-version": "1.0.2", "channelid": "CHINA_APP"}
_ctx = ssl.create_default_context()
_ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx),
                                     urllib.request.ProxyHandler({}))
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}
ATTR_CN = {"1": "全网资费", "2": "河北资费", "3": "attr3"}
# 已消费的容器字段
KNOWN = {"nonModuleList", "moduleList"}
# 资费条目的特征字段（命中 2 个以上才算「像是条目」）
SIG = {"name", "tariffName", "fees", "reportNo", "applicablePeople",
       "onlineDay", "offineDay", "data", "channel"}


def call(path, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ROOT + path, data=data, headers=HEADERS, method="POST")
    with OPENER.open(req, timeout=30) as r:
        j = json.loads(r.read().decode("utf-8", "replace").strip())
    if isinstance(j, dict) and set(j.keys()) == {"body"}:
        j = json.loads(mz_crypto.decrypt(j["body"]))
    return j


def scan_bean(b):
    """返回 {键名: (元素数, 命中特征数)} —— 疑似未消费的条目容器"""
    out = {}
    for k, v in b.items():
        if k in KNOWN or not isinstance(v, list) or not v:
            continue
        if not isinstance(v[0], dict):
            continue
        hit = len(SIG & set(v[0].keys()))
        if hit >= 2:
            out[k] = (len(v), hit)
    return out


def main():
    t2 = call("nrtariff/new/Tariff/getType2List", {"province": PROV, "isPublic": "1"})
    combos = (t2.get("data") if isinstance(t2, dict) else None) or []
    print("分类组合 %d 个\n" % len(combos))
    keys_all = set()
    unknown = {}
    beans_n = 0
    for c in combos:
        a, t1, t2v = str(c.get("tariffAttr")), str(c.get("type1")), str(c.get("type2"))
        r = call("nrtariff/new/Tariff/getTariffListInfo",
                 {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
                  "tariffAttr": a, "type1": t1, "type2": t2v,
                  "page": 1, "limit": 100, "fistLimit": 5000})
        d = r.get("data") if isinstance(r, dict) else None
        for b in (d or {}).get("beans") or []:
            beans_n += 1
            keys_all |= set(b.keys())
            for k, v in scan_bean(b).items():
                u = unknown.setdefault(k, {"n": 0, "hit": v[1], "eg": []})
                u["n"] += v[0]
                if len(u["eg"]) < 3:
                    u["eg"].append("%s/%s" % (ATTR_CN.get(a, a), ZFLX.get(t2v, t2v)))
    print("扫描系列 %d 个" % beans_n)
    print("bean 的全部键：%s\n" % sorted(keys_all))
    if not unknown:
        print("✅ 除 nonModuleList / moduleList 外，**没有**别的条目容器")
        return
    print("⚠️ 发现疑似未消费的容器：")
    for k, v in unknown.items():
        print("   %-20s 元素 %d 个 · 特征命中 %d · 出现于 %s"
              % (k, v["n"], v["hit"], "/".join(v["eg"])))


if __name__ == "__main__":
    main()
