#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APTV 源全面实测：解析全部 m3u -> 筛选目标频道 -> 并发真实拉流测速"""
import os, re, json, time, socket, threading, queue, urllib.request, urllib.error

BASE = r"D:\Work\WorkBuddy\_aptv_probe"
FILES = ["aptv_iptv2.m3u", "yangg.m3u", "itvlist.m3u", "suxuang.m3u", "kulao.m3u"]
SRC_PRIORITY = {"aptv_iptv2": 0, "itvlist": 1, "suxuang": 2, "yangg": 3, "kulao": 4}

TARGETS = [
    "CCTV-1", "CCTV1", "CCTV-2", "CCTV2", "CCTV-3", "CCTV3", "CCTV-4", "CCTV4",
    "CCTV-5", "CCTV5", "CCTV-5+", "CCTV5+", "CCTV-6", "CCTV6", "CCTV-7", "CCTV7",
    "CCTV-8", "CCTV8", "CCTV-9", "CCTV9", "CCTV-10", "CCTV10", "CCTV-11", "CCTV11",
    "CCTV-12", "CCTV12", "CCTV-13", "CCTV13", "CCTV-14", "CCTV14", "CCTV-15", "CCTV15",
    "CCTV-16", "CCTV16", "CCTV-17", "CCTV17",
    "河北卫视", "湖南卫视", "浙江卫视", "江苏卫视", "东方卫视", "北京卫视", "山东卫视",
    "天津卫视", "安徽卫视", "深圳卫视", "广东卫视", "湖北卫视", "黑龙江卫视", "辽宁卫视",
    "凤凰中文", "凤凰资讯", "CHC", "第一剧场", "风云剧场", "怀旧剧场", "世界地理",
]
NORM = {}
for t in TARGETS:
    NORM.setdefault(t.replace("-", "").replace("+", "").upper(), t)

MAX_PER_TARGET = 5
TIMEOUT = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PotPlayer/250604"

def parse_m3u(path, src):
    chs = []
    name = None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF"):
                m = re.search(r",(.*)$", line)
                name = m.group(1).strip() if m else "?"
            elif line and not line.startswith("#"):
                if name:
                    chs.append((name, line, src))
                name = None
    return chs

def norm_name(n):
    n = re.sub(r"\s+", "", n)
    n = re.sub(r"[（(].*?[)）]", "", n)
    n = n.replace("-", "").replace("+", "").replace(" ", "")
    return n.upper()

all_ch = []
for fn in FILES:
    p = os.path.join(BASE, fn)
    if os.path.exists(p):
        src = fn.split(".")[0]
        all_ch += parse_m3u(p, src)

# 分桶：按目标频道归集
buckets = {}
for name, url, src in all_ch:
    key = norm_name(name)
    if key in NORM:
        b = buckets.setdefault(NORM[key], [])
        if len(b) < 40:
            b.append((src, name, url))

# 每个目标选前 MAX_PER_TARGET 个（按源优先级 + URL 多样性）
cands = []
for tgt, lst in buckets.items():
    lst.sort(key=lambda x: SRC_PRIORITY.get(x[0], 9))
    seen = set()
    n = 0
    for src, name, url in lst:
        host = re.match(r"https?://([^/]+)", url)
        hk = host.group(1) if host else url
        if hk in seen:
            continue
        seen.add(hk)
        cands.append((tgt, src, name, url))
        n += 1
        if n >= MAX_PER_TARGET:
            break

print("目标频道 %d 个，候选 %d 条" % (len(buckets), len(cands)))

def probe_one(tgt, src, name, url):
    r = {"tgt": tgt, "src": src, "name": name, "url": url, "ok": False,
         "code": 0, "kbps": 0, "err": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            r["code"] = resp.getcode()
            ct = resp.headers.get("Content-Type", "")
            body = resp.read(65536)
            # HLS: 抓 playlist 后再拉一个分片
            if "mpegurl" in ct.lower() or url.endswith(".m3u8") or b"#EXTM3U" in body[:4096]:
                txt = body.decode("utf-8", "ignore")
                seg = None
                for ln in txt.splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        seg = ln if ln.startswith("http") else urllib.parse.urljoin(url, ln)
                        break
                if not seg:
                    r["err"] = "empty-playlist"
                    return r
                req2 = urllib.request.Request(seg, headers={"User-Agent": UA})
                t1 = time.time()
                with urllib.request.urlopen(req2, timeout=TIMEOUT) as r2:
                    data = r2.read(262144)
                dt = time.time() - t1
                r["kbps"] = int(len(data) * 8 / 1024 / dt) if dt > 0 else 0
                r["ok"] = len(data) > 16384 and (data[:1] == b"\x47" or b"ftyp" in data[:64] or len(data) > 100000)
            else:
                dt = time.time() - t0
                r["kbps"] = int(len(body) * 8 / 1024 / dt) if dt > 0 else 0
                r["ok"] = len(body) > 16384 and (body[:1] == b"\x47" or b"ftyp" in body[:64])
    except Exception as e:
        r["err"] = str(e)[:80]
    return r

results = []
q = queue.Queue()
lock = threading.Lock()
def worker():
    while True:
        item = q.get()
        if item is None:
            break
        r = probe_one(*item)
        with lock:
            results.append(r)
            mark = "OK " if r["ok"] else "FAIL"
            print("[%s] %-10s %-22s %-5s %5dkbps %s" % (mark, r["tgt"], r["src"], r["code"], r["kbps"], r["err"] or r["name"][:30]))
        q.task_done()

NW = 12
threads = [threading.Thread(target=worker, daemon=True) for _ in range(NW)]
for t in threads: t.start()
for c in cands: q.put(c)
q.join()
for _ in range(NW): q.put(None)

with open(os.path.join(BASE, "probe_result.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)

ok = [r for r in results if r["ok"]]
print("\n=== 汇总: %d/%d 可用 ===" % (len(ok), len(results)))
for tgt in sorted(buckets):
    hits = [r for r in ok if r["tgt"] == tgt]
    hits.sort(key=lambda x: -x["kbps"])
    if hits:
        b = hits[0]
        print("%-10s ✓ %4dkbps  %s  %s" % (tgt, b["kbps"], b["src"], b["url"][:90]))
    else:
        print("%-10s ✗ 无可用" % tgt)
