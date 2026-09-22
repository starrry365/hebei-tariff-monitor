#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面 Chrome(9223) CDP 驱动：nav / eval / cookies / state / shot
用法:
  python ct_step.py nav <url>
  python ct_step.py eval <js文件> [输出json文件]
  python ct_step.py cookies
  python ct_step.py state     # 标题/url/readyState/webdriver
  python ct_step.py shot <输出png>   # 截图（失败现场取证）
"""
import json
import sys
import time
import urllib.request

import websocket  # venv_gui 已有

PORT = 9223
PH = urllib.request.ProxyHandler({})


def http_json(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 headers={"Host": "localhost"})
    opener = urllib.request.build_opener(PH)
    with opener.open(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pick_page():
    ps = [p for p in http_json("/json/list") if p.get("type") == "page"]
    if not ps:
        raise SystemExit("no page")
    # 优先非 about:blank 的第一个页面
    for p in ps:
        if not p.get("url", "").startswith("about:"):
            return p
    return ps[0]


def ws_conn(page):
    url = page["webSocketDebuggerUrl"].replace("localhost", f"127.0.0.1:{PORT}", 1)
    return websocket.create_connection(url, timeout=60, suppress_origin=True)


def call(page, method, params=None, timeout=60):
    ws = ws_conn(page)
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg
        return {"error": "timeout"}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def evaluate(page, expr, timeout=60):
    return call(page, "Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": True,
    }, timeout)


def out(r):
    try:
        res = r["result"]["result"]
        v = res.get("value")
        if isinstance(v, str):
            print(v)
        else:
            print(json.dumps(v, ensure_ascii=False, indent=1)[:8000])
        if res.get("subtype") == "error" or "exceptionDetails" in r.get("result", {}):
            print("!! exception:", json.dumps(r, ensure_ascii=False)[:1500])
    except Exception:
        print(json.dumps(r, ensure_ascii=False)[:2000])


def main():
    cmd = sys.argv[1]
    if cmd == "nav":
        page = pick_page()
        call(page, "Page.navigate", {"url": sys.argv[2]})
        time.sleep(2)
        print("nav ->", sys.argv[2])
        return
    if cmd == "state":
        page = pick_page()
        r = evaluate(page, "JSON.stringify({t:document.title,u:location.href,"
                          "rs:document.readyState,wd:navigator.webdriver,"
                          "ck:document.cookie.length})")
        out(r)
        return
    if cmd == "cookies":
        page = pick_page()
        r = call(page, "Network.getCookies",
                 {"urls": ["https://www.189.cn/", "https://wap.189.cn/"]})
        for c in r.get("result", {}).get("cookies", []):
            print(f"{c['name']}={c['value'][:40]}... dom={c.get('domain')}")
        return
    if cmd == "shot":
        # 失败现场的唯一可靠证据：WAF 拦下时页面是挑战页/空白页，
        # 单看 eval 报的异常分不清「被拦」还是「页面没加载完」。
        page = pick_page()
        r = call(page, "Page.captureScreenshot", {"format": "png"})
        b64 = r.get("result", {}).get("data")
        if not b64:
            print("!! 截图失败:", json.dumps(r, ensure_ascii=False)[:400])
            return
        import base64
        p = sys.argv[2] if len(sys.argv) > 2 else "ct_shot.png"
        with open(p, "wb") as f:
            f.write(base64.b64decode(b64))
        print("shot ->", p)
        return
    if cmd == "eval":
        page = pick_page()
        expr = open(sys.argv[2], encoding="utf-8").read()
        r = evaluate(page, expr, timeout=120)
        if len(sys.argv) > 3:  # eval <jsfile> <outfile> : 完整结果落盘
            res = r.get("result", {}).get("result", {})
            v = res.get("value")
            with open(sys.argv[3], "w", encoding="utf-8") as f:
                if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                json.dump(v if not isinstance(v, str) else {"raw": v},
                          f, ensure_ascii=False, indent=1)
            print("saved ->", sys.argv[3], len(str(v)), "chars")
            return
        out(r)
        return


if __name__ == "__main__":
    main()
