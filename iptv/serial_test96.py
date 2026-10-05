# -*- coding: utf-8 -*-
"""逐个串行实测 96 台：每条 URL 独立拉流测速，硬性总时限，输出 JSON。"""
import re, time, json, socket, urllib.request, urllib.parse, sys

socket.setdefaulttimeout(8)
UA = {"User-Agent": "PotPlayer/250604"}
SRC = r"D:\Work\WorkBuddy\_aptv_probe\河北移动IPTV-终版96台.m3u"
OUT = r"D:\Work\WorkBuddy\_aptv_probe\final96_result.json"
PER_URL_LIMIT = 20  # 秒，单条硬上限

def parse():
    items, name = [], None
    for l in open(SRC, encoding="utf-8").read().splitlines():
        l = l.strip()
        if l.startswith("#EXTINF"):
            name = re.search(r",(.*)$", l).group(1).strip()
        elif l.startswith("http"):
            items.append({"name": name, "url": l})
    return items

def measure(url):
    """返回 kbps 或 None。总时长硬限 PER_URL_LIMIT。"""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read(65536)
        if time.time() - t0 > PER_URL_LIMIT: return None
        # m3u8: 取分片再拉
        text = body.decode("utf-8", "ignore")
        seg = None
        if "#EXTM3U" in text[:4096] or ".m3u8" in url:
            for ln in text.splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    seg = ln if ln.startswith("http") else urllib.parse.urljoin(url, ln)
                    break
            if not seg:
                return None
            t1 = time.time(); got = 0
            with urllib.request.urlopen(urllib.request.Request(seg, headers=UA), timeout=8) as r2:
                while got < 1048576:
                    if time.time() - t1 > PER_URL_LIMIT - (time.time() - t0) - 1: break
                    c = r2.read(262144)
                    if not c: break
                    got += len(c)
            dt = time.time() - t1
            if got < 65536: return None
            return int(got * 8 / 1024 / max(dt, 0.05))
        else:
            # 直接 TS 流：续拉
            t1 = time.time(); got = len(body)
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8) as r2:
                while got < 1048576:
                    if time.time() - t1 > PER_URL_LIMIT - 1: break
                    c = r2.read(262144)
                    if not c: break
                    got += len(c)
            dt = time.time() - t1
            if got < 65536: return None
            return int(got * 8 / 1024 / max(dt, 0.05))
    except Exception:
        return None

def main():
    items = parse()
    results = []
    t_start = time.time()
    for i, it in enumerate(items, 1):
        kbps = measure(it["url"])
        it["kbps"] = kbps
        results.append(it)
        mark = "FAIL" if kbps is None else ("%5d kbps" % kbps)
        print("[%02d/%d] %-10s %s" % (i, len(items), mark, it["name"]), flush=True)
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["kbps"])
    print("\n完成: %d/%d 通, 总耗时 %.1f 分钟" % (ok, len(results), (time.time() - t_start) / 60))

if __name__ == "__main__":
    main()
