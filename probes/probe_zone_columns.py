# -*- coding: utf-8 -*-
"""探针：移动「资费专区」栏目覆盖度核对

用户报称页面（tariffZonePers.html）上是两级栏目：
    全网资费：套餐 / 加装包 / 营销活动 / 港澳台国际专区
    河北资费：套餐 / 加装包 / 营销活动 / 港澳台国际资费 / 标准资费专区
本脚本回答三件事：
  1. 上游「分类树」接口到底给了哪些 (tariffAttr, type1, type2) 组合 —— 有没有漏枚举
  2. 每个组合的「系列数」与「条目数」—— 哪个栏目是空壳（有系列名、无明细）
  3. 空壳栏目是上游真没数据，还是我们参数/接口用错了（换接口、换参数试）
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import mz_crypto  # noqa: E402
import ssl  # noqa: E402

ROOT = "https://h.app.coc.10086.cn/website/nrapigate/"
REF = ("https://h.app.coc.10086.cn/cmcc-app/uni-pages/tariffZonePers.html"
       "?pageId=1834149966764851200&channelId=P00000132579&yx=1390478183")
UA = ("Mozilla/5.0 (Linux; Android 16; 23113RKC6C Build/BP2A.250605.031.A3; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36 leadeon/12.5.4/CMCCIT")
PROV = "311"
HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": UA,
    "Origin": "https://h.app.coc.10086.cn",
    "Referer": REF,
    "x-requested-with": "com.greenpoint.android.mc10086.activity",
    "x-qen": "1",
    "x-app-version": "1.0.2",
    "channelid": "CHINA_APP",
}
_ctx = ssl.create_default_context()
_ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx),
                                     urllib.request.ProxyHandler({}))
ZFLX = {"1": "套餐", "2": "加装包", "3": "营销活动", "4": "港澳台/国际资费",
        "5": "标准资费", "6": "国际及港澳台标准资费", "7": "其他"}
ATTR_CN = {"1": "全网资费", "2": "河北资费", "3": "?（attr=3）"}


def call(path, body, raw=False):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ROOT + path, data=data, headers=HEADERS, method="POST")
    with OPENER.open(req, timeout=30) as r:
        txt = r.read().decode("utf-8", "replace").strip()
    j = json.loads(txt)
    if isinstance(j, dict) and set(j.keys()) == {"body"}:
        j = json.loads(mz_crypto.decrypt(j["body"]))
    return j if not raw else (j, txt)


def main():
    print("=" * 78)
    print("① 分类树 getType2List —— 上游到底给了哪些组合")
    print("=" * 78)
    t2 = call("nrtariff/new/Tariff/getType2List", {"province": PROV, "isPublic": "1"})
    combos = (t2.get("data") if isinstance(t2, dict) else None) or []
    print("returnCode=%s  组合数=%d" % (t2.get("returnCode"), len(combos)))
    if combos:
        print("样例原始字段：%s" % json.dumps(combos[0], ensure_ascii=False))
    seen = set()
    for c in combos:
        a, t1, t2v = str(c.get("tariffAttr")), str(c.get("type1")), str(c.get("type2"))
        seen.add((a, t2v))
        print("  attr=%s(%s) type1=%s type2=%s(%s)  %s"
              % (a, ATTR_CN.get(a, "?"), t1, t2v, ZFLX.get(t2v, "?"),
                 json.dumps({k: v for k, v in c.items()
                             if k not in ("tariffAttr", "type1", "type2")},
                            ensure_ascii=False)))
    print("\n※ 缺失的 (attr,type2)：")
    for a in ("1", "2", "3"):
        miss = [t for t in ("1", "2", "3", "4", "5", "6", "7") if (a, t) not in seen]
        print("   attr=%s 缺 %s" % (a, ",".join(miss) or "（无）"))

    print("\n" + "=" * 78)
    print("② 每个组合的系列数 / 条目数（含分类树没给的 type2 也一并试）")
    print("=" * 78)
    probe = [(a, t) for a in ("1", "2", "3")
             for t in ("1", "2", "3", "4", "5", "6", "7")]
    for a, t in probe:
        for t1 in ("1", "2"):
            if (a, t1, t) in {(str(c.get("tariffAttr")), str(c.get("type1")),
                               str(c.get("type2"))) for c in combos}:
                pass
        r = call("nrtariff/new/Tariff/getTariffListInfo",
                 {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
                  "tariffAttr": a, "type1": "1", "type2": t,
                  "page": 1, "limit": 100, "fistLimit": 100})
        d = r.get("data") if isinstance(r, dict) else None
        if not isinstance(d, dict):
            print("  attr=%s type2=%s(%s)  →  异常 %s"
                  % (a, t, ZFLX.get(t, "?"), str(r)[:120]))
            continue
        beans = d.get("beans") or []
        n = sum(len(b.get("nonModuleList") or []) for b in beans)
        pg = d.get("page") or {}
        flag = ""
        if n == 0 and beans:
            flag = "  ◀◀ 有系列无明细"
        print("  attr=%s type2=%s(%-10s) total=%-5s 系列=%-4d 条目=%-5d%s"
              % (a, t, ZFLX.get(t, "?"), pg.get("total"), len(beans), n, flag))
        time.sleep(0.2)

    print("\n" + "=" * 78)
    print("③ 空壳栏目深挖：attr=1 type2=4（全网·港澳台国际专区）原始响应")
    print("=" * 78)
    r = call("nrtariff/new/Tariff/getTariffListInfo",
             {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
              "tariffAttr": "1", "type1": "1", "type2": "4",
              "page": 1, "limit": 100, "fistLimit": 100})
    d = r.get("data") or {}
    beans = d.get("beans") or []
    print("returnCode=%s total=%s 系列数=%d" % (r.get("returnCode"),
                                              (d.get("page") or {}).get("total"),
                                              len(beans)))
    if beans:
        b0 = beans[0]
        print("首个系列的键：%s" % list(b0.keys()))
        print("首个系列样例：%s" % json.dumps(
            {k: v for k, v in b0.items() if k != "nonModuleList"},
            ensure_ascii=False)[:400])
        print("nonModuleList 长度：%d" % len(b0.get("nonModuleList") or []))
        print("前 8 个系列名：%s" % [b.get("tariffName") for b in beans[:8]])

    print("\n  -- 变体试探（看是否参数问题）--")
    variants = [
        ("linkScn=1", {"linkScn": "1"}),
        ("isPublic=空", {"isPublic": ""}),
        ("limit=500", {"limit": 500, "fistLimit": 500}),
        ("不带 linkScn", {"_drop": "linkScn"}),
    ]
    for name, patch in variants:
        body = {"cellNum": "", "province": PROV, "isPublic": "1", "linkScn": "2",
                "tariffAttr": "1", "type1": "1", "type2": "4",
                "page": 1, "limit": 100, "fistLimit": 100}
        if "_drop" in patch:
            body.pop(patch["_drop"], None)
        else:
            body.update(patch)
        try:
            rr = call("nrtariff/new/Tariff/getTariffListInfo", body)
            dd = rr.get("data") or {}
            bb = dd.get("beans") or []
            nn = sum(len(b.get("nonModuleList") or []) for b in bb)
            print("   %-12s → 系列=%-4d 条目=%-5d rc=%s"
                  % (name, len(bb), nn, rr.get("returnCode")))
        except Exception as e:
            print("   %-12s → %s: %s" % (name, type(e).__name__, e))

    print("\n" + "=" * 78)
    print("④ 标准资费专区：getStandardlist（独立于 getTariffListInfo 的接口）")
    print("=" * 78)
    for path in ("nrtariff/new/Tariff/getStandardlist",
                 "nrtariff/new/Tariff/getStandardList"):
        for body in ({"province": PROV, "isPublic": "1"},
                     {"province": PROV, "isPublic": "1", "type1": "1"},
                     {"province": PROV, "isPublic": "1", "tariffAttr": "2"},
                     {"province": PROV, "isPublic": "1", "tariffAttr": "2", "type1": "1"}):
            try:
                rr = call(path, body)
                s = json.dumps(rr, ensure_ascii=False)
                print("  %s %s → rc=%s len=%d %s"
                      % (path.split("/")[-1], json.dumps(body, ensure_ascii=False),
                         rr.get("returnCode"), len(s), s[:200]))
            except Exception as e:
                print("  %s %s → %s: %s"
                      % (path.split("/")[-1], json.dumps(body, ensure_ascii=False),
                         type(e).__name__, str(e)[:100]))

    print("\n" + "=" * 78)
    print("⑤ attr=3 是什么？（4 个组合全空，页面上是否有这一块）")
    print("=" * 78)
    r3 = call("nrtariff/new/Tariff/getTariffSeriesNameList",
              {"province": PROV, "isPublic": "1", "tariffAttr": "3",
               "type1": "1", "type2": "1"})
    print("getTariffSeriesNameList attr=3 → rc=%s data=%s"
          % (r3.get("returnCode"),
             json.dumps(r3.get("data"), ensure_ascii=False)[:200]))
    for a in ("1", "2", "3"):
        rr = call("nrtariff/new/Tariff/getTariffSeriesNameList",
                  {"province": PROV, "isPublic": "1", "tariffAttr": a,
                   "type1": "1", "type2": "4"})
        dd = rr.get("data") or []
        print("  attr=%s type2=4 系列名数=%d rc=%s"
              % (a, len(dd) if isinstance(dd, list) else -1, rr.get("returnCode")))


if __name__ == "__main__":
    main()
