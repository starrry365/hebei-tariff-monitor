#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全网搜集的河北候选源 限流3并发实测"""
import os, re, time, threading, urllib.request, urllib.parse, socket

BASE = r"D:\Work\WorkBuddy\_aptv_probe\webfind"
socket.setdefaulttimeout(6)
UA = {"User-Agent": "PotPlayer/250604"}
sem = threading.Semaphore(3)

cands = []  # (name, url, src)

# 1) 河北移动双CDN（xisohi repo）
for line in open(os.path.join(BASE, "hb_mobile.txt"), encoding="utf-8", errors="ignore"):
    line = line.strip()
    if "," in line and "http" in line:
        name, url = line.split(",", 1)
        if url.startswith("http"):
            src = "111新CDN" if "111.63" in url else "OTT域名"
            cands.append((name.strip(), url.strip(), src))

# 2) bztv.tvbus.cc 卫视
bztv = {
    "东方卫视": "dfws", "东南卫视": "dnws", "广东卫视": "gdws", "甘肃卫视": "gsws",
    "广西卫视": "gxws", "贵州卫视": "gzws", "河南卫视": "haws", "湖北卫视": "hbws",
    "河北卫视": "hews", "河北卫视B": "hebs", "黑龙江卫视": "hljws", "湖南卫视": "hnws",
    "吉林卫视": "jlws", "江苏卫视": "jsws", "江西卫视": "jxws", "辽宁卫视": "lnws",
    "内蒙古卫视": "nmgws", "宁夏卫视": "nxws", "青海卫视": "qhws", "四川卫视": "scws",
    "山东卫视": "sdws", "山西卫视": "sxws", "陕西卫视": "snws", "深圳卫视": "szws",
    "天津卫视": "tjws", "新疆卫视": "xjws", "西藏卫视": "xzws", "云南卫视": "ynws",
    "浙江卫视": "zjws", "安徽卫视": "ahws", "北京卫视": "bjws", "重庆卫视": "cqws",
    "CCTV-1": "cctv1", "CCTV-5": "cctv5",
}
for n, p in bztv.items():
    cands.append((n, "http://bztv.tvbus.cc:8081/cdnlive/%s.m3u8" % p, "bztv"))

# 3) 百视通 8M 卫视
bestv = {
    "东方卫视": "dfwshd8m", "北京卫视": "bjwshd8m", "天津卫视": "tjwshd8m",
    "辽宁卫视": "lnwshd8m", "黑龙江卫视": "hljwshd8m", "吉林卫视": "jlwshd8m",
    "山东卫视": "sdws8m", "甘肃卫视": "gswshd8m", "重庆卫视": "cqws8m",
    "浙江卫视": "zjwshd8m", "江苏卫视": "jswshd8m", "湖南卫视": "hnwshd8m",
    "安徽卫视": "ahwshd8m", "湖北卫视": "hbwshd8m", "河北卫视": "hewshd8m",
    "广东卫视": "gdwshd8m", "深圳卫视": "szwshd8m", "江西卫视": "jxwshd8m",
    "山西卫视": "sxwshd8m", "河南卫视": "hnws8m",
}
for n, p in bestv.items():
    cands.append((n, "http://aliyun-qhbu-live.bestvcdn.com.cn/live/program/live/%s/8000000/mnf.m3u8" % p, "bestv8M"))

# 4) 河北广电官方
cands.append(("河北卫视官方", "http://weblive.hebtv.com/live/hbws_bq/index.m3u8", "河北广电官方"))

# 5) 黑龙江移动 PLTV 样本（全国移动常可通）
hlj = {
    "HLJ源-央视": "3221226474", "HLJ源-河北卫视": "3221226406", "HLJ源-黑龙江": "3221226327",
    "HLJ源-河南": "3221226480", "HLJ源-湖北": "3221225627", "HLJ源-湖南": "3221225610",
    "HLJ源-江苏": "3221225613", "HLJ源-江西": "3221226344",
}
for n, pid in hlj.items():
    cands.append((n, "http://ottrrs.hl.chinamobile.com/PLTV/88888888/224/%s/index.m3u8" % pid, "黑龙江移动"))

print("候选 %d 条" % len(cands))

def measure(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=6) as r:
            body = r.read(65536)
            seg = None
            if b"#EXTM3U" in body[:4096] or ".m3u8" in url:
                for ln in body.decode("utf-8", "ignore").splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        seg = ln if ln.startswith("http") else urllib.parse.urljoin(url, ln)
                        break
                if not seg: return None
                t0 = time.time(); buf = b""
                with urllib.request.urlopen(urllib.request.Request(seg, headers=UA), timeout=6) as r2:
                    while len(buf) < 786432:
                        c = r2.read(262144)
                        if not c: break
                        buf += c
                        if time.time() - t0 > 10: break
                dt = time.time() - t0; data = buf
            else:
                dt = 0.05; data = body
        return int(len(data) * 8 / 1024 / max(dt, 0.05)) if len(data) >= 32768 else None
    except Exception:
        return None

results = []
lock = threading.Lock()
def job(item):
    name, url, src = item
    with sem:
        k = measure(url)
    with lock:
        results.append((name, url, src, k))
        print("[%s] %-10s %-14s %s" % ("OK " if k else "FAIL", name, src, k or ""), flush=True)

ts = [threading.Thread(target=job, args=(c,)) for c in cands]
t0 = time.time()
for t in ts: t.start()
for t in ts: t.join(90)
print("\n测完 %.0fs, 存活 %d/%d" % (time.time() - t0, sum(1 for r in results if r[3]), len(cands)))

import json
json.dump([{"name": n, "url": u, "src": s, "kbps": k} for n, u, s, k in results],
          open(os.path.join(BASE, "webfind_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
