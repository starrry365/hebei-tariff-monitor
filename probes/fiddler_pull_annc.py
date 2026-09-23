# -*- coding: utf-8 -*-
"""从 Fiddler MCP 拉公告会话：请求参数 + 解密响应 + 标题/省份分布

🔴 重建版 MCP 服务端的 session 状态有 bug：initialize 建新 session 后 tools/call
   一律 "Tool 'X' not found"（连 get_status 也一样），而**最早建的那个 session 一直可用**。
   所以这里固定复用最早成功的 session id；若失效再重建并重试。

🔴 本仓**公开**：MCP 的 ApiKey 一律不写进代码。取值顺序（两边都没有就报错，
   绝不回退到硬编码值）：
     1) 环境变量 ``FIDDLER_MCP_KEY``（形如 ``ApiKey <uuid>``）
     2) 同目录 ``.fiddler_mcp_key``（已 gitignore，本地开发用）

用法：python probes/fiddler_pull_annc.py [关键词=announcement]
"""
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "cloud", "tariff"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import mz_crypto  # noqa: E402

MCP = "http://localhost:8868/mcp"
_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fiddler_mcp_key")


def _load_key():
    """ApiKey 从环境变量或本地忽略文件取；取不到直接报错。"""
    v = (os.environ.get("FIDDLER_MCP_KEY") or "").strip()
    if not v and os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, encoding="utf-8") as f:
            v = f.read().strip()
    if not v:
        raise SystemExit(
            "未找到 Fiddler MCP ApiKey。请二选一：\n"
            "  · 设置环境变量 FIDDLER_MCP_KEY（形如 ApiKey xxxxxxxx-xxxx-…）\n"
            "  · 在 probes/ 下放置 .fiddler_mcp_key（内容就是那一行 ApiKey，已 gitignore）")
    return v if v.lower().startswith("apikey") else ("ApiKey " + v)


KEY = _load_key()
KW = sys.argv[1] if len(sys.argv) > 1 else "announcement"


def rpc(session, method, params=None, mid=[0]):
    mid[0] += 1
    body = json.dumps({"jsonrpc": "2.0", "id": mid[0], "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(MCP, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": KEY, "Mcp-Session-Id": session})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode("utf-8", "replace")
    raw = parse_sse(raw)
    j = json.loads(raw)
    if "error" in j:
        raise RuntimeError(json.dumps(j["error"], ensure_ascii=False))
    return j.get("result")


def parse_sse(raw):
    """SSE → JSON 文本。拼接全部 data: 行（大响应可能分多条事件/多行）。"""
    if "data:" not in raw:
        return raw
    datas = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            datas.append(line[5:].lstrip())
    if not datas:
        return raw
    joined = "\n".join(datas)
    try:
        json.loads(joined)
        return joined
    except Exception:
        pass
    # 拼起来还不是合法 JSON：可能混了多条事件，逐条找第一条能解析的
    for d in datas:
        try:
            json.loads(d)
            return d
        except Exception:
            continue
    return joined


def result_text(res):
    txt = ""
    for c in (res or {}).get("content") or []:
        if c.get("type") == "text":
            txt += c.get("text") or ""
    # 🔴 服务端会把多段 JSON 直接拼接（会话数组 + "Retrieved N sessions" 说明对象），
    #   json.loads 报 Extra data ⇒ 用 raw_decode 取第一个合法 JSON 值。
    try:
        val, _ = json.JSONDecoder().raw_decode(txt)
        return val
    except Exception:
        return txt


def get_sid():
    """先试已知的可用 session；不行就新建（并提示服务端 bug）。"""
    known = "xOiBx3Ocl4TRbFVO3mGYKg"
    try:
        rpc(known, "tools/call", {"name": "get_status", "arguments": {}})
        return known
    except Exception:
        pass
    body = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "probe", "version": "1"}}}).encode()
    req = urllib.request.Request(MCP, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "Authorization": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        sid = r.headers.get("Mcp-Session-Id")
        r.read()
    # 新 session 可能踩 "Tool not found" bug：多试几次 get_status
    for _ in range(3):
        try:
            rpc(sid, "tools/call", {"name": "get_status", "arguments": {}})
            return sid
        except Exception:
            pass
    return sid


def main():
    sid = get_sid()
    print("MCP session:", sid)
    ss = result_text(rpc(sid, "tools/call",
                         {"name": "get_sessions",
                          "arguments": {"sessionsSource": "LiveTraffic"}}))
    if isinstance(ss, str):
        print("get_sessions 返回文本：", ss[:200])
        return
    sessions = ss if isinstance(ss, list) else (ss.get("sessions") or [])
    print("LiveTraffic 总会话 %d，按 %r 筛…" % (len(sessions), KW))
    hits = [s for s in sessions if KW.lower() in json.dumps(s, ensure_ascii=False).lower()]
    print("命中 %d 条\n" % len(hits))
    for s in hits[:30]:
        print("  #%s %s %s" % (s.get("id"), s.get("method"), str(s.get("url"))[:100]))

    # 逐条拉详情（请求体明文 + 响应体）
    agg = {}
    for s in hits:
        sidn = s.get("id")
        try:
            d = result_text(rpc(sid, "tools/call",
                                {"name": "get_session_details",
                                 "arguments": {"sessionId": str(sidn),
                                               "sessionsSource": "LiveTraffic"}}))
        except Exception as e:
            print("  #%s 详情失败: %s" % (sidn, str(e)[:80]))
            continue
        txt = d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
        # 找请求体（postData）与响应体
        reqbody, respbody = "", ""
        if isinstance(d, dict):
            reqbody = (d.get("request") or {}).get("postData") or \
                      d.get("postData") or d.get("requestBody") or ""
            respbody = (d.get("response") or {}).get("body") or \
                       d.get("responseBody") or d.get("body") or ""
            if not reqbody:
                for k in ("requestBody", "postData", "body"):
                    if d.get(k):
                        reqbody = d[k]
                        break
        else:
            # 文本里抠 JSON
            import re
            m = re.search(r'"postData"\s*:\s*"([^"]*)"', txt)
            if m:
                reqbody = m.group(1).encode().decode("unicode_escape", "replace")
        if isinstance(reqbody, str) and reqbody.startswith("{"):
            try:
                rb = json.loads(reqbody)
                rb.pop("cellNum", None)
                key = json.dumps({k: rb.get(k) for k in
                                  ("provinceCode", "cityCode", "scopePageCode",
                                   "channelType", "filteredChildIds")},
                                 ensure_ascii=False)
            except Exception:
                key = reqbody[:80]
        else:
            key = str(reqbody)[:80]
        n = "?"
        provs = {}
        titles = []
        if respbody:
            try:
                j = json.loads(respbody)
                if isinstance(j, dict) and set(j.keys()) == {"body"}:
                    j = json.loads(mz_crypto.decrypt(j["body"]))
                d2 = j.get("data")
                items = (d2.get("list") if isinstance(d2, dict) else d2) or []
                n = len(items)
                for it in items:
                    p = str(it.get("contactProvince") or "?")
                    provs[p] = provs.get(p, 0) + 1
                    titles.append(str(it.get("noticeTitle") or it.get("title") or ""))
            except Exception:
                n = "解密失败"
        a = agg.setdefault(key, {"n": 0, "provs": {}, "titles": [], "resp": 0})
        a["n"] = n if n != "?" else a["n"]
        a["resp"] += 1
        for p, v in provs.items():
            a["provs"][p] = a["provs"].get(p, 0) + v
        a["titles"] = (a["titles"] + titles)[:5]

    print("\n=== 按请求参数汇总 ===")
    for k, v in agg.items():
        print("参数 %s" % k)
        print("  条数=%s contactProvince=%s" % (v["n"], v["provs"] or "-"))
        for t in v["titles"][:5]:
            print("   ·", t[:60])
        print()


if __name__ == "__main__":
    main()
