#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三轮全量复测 v2：每条 URL 硬性 20s 总时限，防 socket 拖死；进度实时落盘"""
import os, re, json, time, threading, urllib.request, urllib.parse, socket

BASE = r"D:\Work\WorkBuddy\_aptv_probe"
M3U = os.path.join(BASE, "河北移动-APTV实测合并版.m3u")
UA = {"User-Agent": "PotPlayer/250604"}
socket.setdefaulttimeout(6)

def parse():
    out = []
    name = None
    for line in open(M3U, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#EXTINF"):
            n = re.search(r",(.*)$", line)
            name = n.group(1).strip() if n else "?"
        elif line.startswith("http"):
            out.append({"name": name, "url": line})
    return out

def _measure(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=6) as r:
        body = r.read(65536)
        seg = None
        if b"#EXTM3U" in body[:4096] or ".m3u8" in url:
            txt = body.decode("utf-8", "ignore")
            for ln in txt.splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    seg = ln if ln.startswith("http") else urllib.parse.urljoin(url, ln)
                    break
            if not seg:
                return None
            req2 = urllib.request.Request(seg, headers=UA)
            t0 = time.time()
            buf = b""
            with urllib.request.urlopen(req2, timeout=6) as r2:
                while len(buf) < 786432:
                    chunk = r2.read(262144)
                    if not chunk:
                        break
                    buf += chunk
                    if time.time() - t0 > 10:
                        break
            dt = time.time() - t0
            data = buf
        else:
            dt = 0.05
            data = body
    if len(data) < 32768:
        return None
    return int(len(data) * 8 / 1024 / max(dt, 0.05))

def measure_guarded(url, hard=20):
    box = {}
    def run():
        try:
            box["k"] = _measure(url)
        except Exception:
            box["k"] = None
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(hard)
    return box.get("k")  # 超时返回 None

items = parse()
print("待测 %d 条" % len(items), flush=True)
results = [dict(it, rounds=[None, None, None]) for it in items]
sem = threading.Semaphore(6)

def round_job(rnd, idx):
    with sem:
        k = measure_guarded(results[idx]["url"])
    results[idx]["rounds"][rnd] = k
    if (idx + 1) % 20 == 0:
        print("  轮%d: %d/%d" % (rnd + 1, idx + 1, len(items)), flush=True)

for rnd in range(3):
    if rnd > 0:
        time.sleep(2)
    idxs = [i for i, r in enumerate(results)
            if rnd == 0 or (r["rounds"][0] is not None and (rnd == 1 or r["rounds"][1] is not None))]
    ts = [threading.Thread(target=round_job, args=(rnd, i)) for i in idxs]
    for t in ts: t.start()
    for t in ts: t.join(45)  # 每线程最长45s兜底
    alive = sum(1 for r in results if r["rounds"][rnd] is not None)
    print("轮%d 完成: %d/%d 存活" % (rnd + 1, alive, len(items)), flush=True)
    json.dump(results, open(os.path.join(BASE, "multi_round_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

for r in results:
    ks = sorted(k for k in r["rounds"] if k is not None)
    r["ok_n"] = len(ks)
    r["med"] = ks[len(ks)//2] if ks else 0
    r["max"] = ks[-1] if ks else 0

json.dump(results, open(os.path.join(BASE, "multi_round_result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
stable = [r for r in results if r["ok_n"] == 3]
flaky = [r for r in results if 0 < r["ok_n"] < 3]
dead = [r for r in results if r["ok_n"] == 0]
print("\n=== 稳定%d / 抖动%d / 死%d ===" % (len(stable), len(flaky), len(dead)))
print("\n--- 抖动 ---")
for r in sorted(flaky, key=lambda x: -x["max"]):
    print("%6d kbps(%d/3) %s" % (r["max"], r["ok_n"], r["name"]))
print("\n--- 稳定榜 ---")
for r in sorted(stable, key=lambda x: -x["med"]):
    print("%6d kbps  %s" % (r["med"], r["name"]))
print("DONE")
