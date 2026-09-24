#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 网络请求抓取（页面级 WS）：导航 + 记录指定关键字的请求 URL / 请求体 / 响应摘要。

用法:
  python cdp_net_dump.py <url> <关键字(逗号分隔)> [等待秒数] [输出json]

为什么需要它：网页版资费专区把接口调用拆进了懒加载 chunk，
静态读 JS 只能拿到「接口名」，拿不到「真实 payload」。这里直接听网络层，
是「到底发了什么参数」的唯一可靠真值来源。
"""
import json
import sys
import time
import urllib.request

import websocket

PORT = 9223
PH = urllib.request.ProxyHandler({})


def http_json(path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 headers={"Host": "localhost"})
    with urllib.request.build_opener(PH).open(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pick_page():
    ps = [p for p in http_json("/json/list") if p.get("type") == "page"]
    if not ps:
        raise SystemExit("no page target")
    for p in ps:
        if not p.get("url", "").startswith("about:"):
            return p
    return ps[0]


def main():
    url = sys.argv[1]
    keys = [k for k in sys.argv[2].split(",") if k]
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0
    outfile = sys.argv[4] if len(sys.argv) > 4 else None

    page = pick_page()
    ws_url = page["webSocketDebuggerUrl"].replace("localhost", f"127.0.0.1:{PORT}", 1)
    ws = websocket.create_connection(ws_url, timeout=90, suppress_origin=True)

    mid = [0]

    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        return mid[0]

    send("Network.enable", {"maxPostDataSize": 65536})
    send("Page.enable")
    send("Page.navigate", {"url": url})

    reqs = []
    by_id = {}
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            msg = json.loads(ws.recv())
        except Exception:
            break
        m = msg.get("method")
        if m == "Network.requestWillBeSent":
            p = msg["params"]
            rq = p.get("request", {})
            u = rq.get("url", "")
            if any(k in u for k in keys):
                rec = {"id": p.get("requestId"), "url": u,
                       "method": rq.get("method"),
                       "postData": rq.get("postData"),
                       "headers": {k: v for k, v in (rq.get("headers") or {}).items()
                                   if k.lower() in ("content-type", "referer", "origin",
                                                    "x-requested-with")}}
                reqs.append(rec)
                by_id[p.get("requestId")] = rec
        elif m == "Network.responseReceived":
            p = msg["params"]
            r = by_id.get(p.get("requestId"))
            if r is not None:
                r["status"] = p.get("response", {}).get("status")
                r["ctype"] = p.get("response", {}).get("mimeType")

    # 收尾：把响应体也抓一份（接口是纯 JSON，便于直接比对）
    for rec in reqs:
        if rec.get("status") == 200:
            rid = send("Network.getResponseBody", {"requestId": rec["id"]})
            # 简易等待响应
            t0 = time.time()
            while time.time() - t0 < 15:
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    break
                if msg.get("id") == rid:
                    body = msg.get("result", {}).get("body")
                    if body:
                        rec["bodyLen"] = len(body)
                        rec["bodyHead"] = body[:1200]
                    break
    try:
        ws.close()
    except Exception:
        pass

    print(f"命中 {len(reqs)} 个请求：")
    for r in reqs:
        print(f"\n--- [{r.get('status')}] {r.get('method')} {r['url']}")
        if r.get("postData"):
            print("    postData:", r["postData"][:900])
        if r.get("bodyHead"):
            print("    bodyHead:", r["bodyHead"][:700])
    if outfile:
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(reqs, f, ensure_ascii=False, indent=1)
        print("\nsaved ->", outfile)


if __name__ == "__main__":
    main()
