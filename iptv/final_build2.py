#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终版v2：限流3并发诚实复测（晚高峰），每频道测前3候选，选真实最快"""
import json, re, os, time, threading, urllib.request, urllib.parse, socket

BASE = r"D:\Work\WorkBuddy\_aptv_probe"
socket.setdefaulttimeout(6)
UA = {"User-Agent": "PotPlayer/250604"}
sem = threading.Semaphore(3)

res = json.load(open(os.path.join(BASE, "multi_round_result.json"), encoding="utf-8"))

def norm(name):
    n = re.split(r"\s*·", name)[0].strip()
    n = re.sub(r"[（(].*?[)）]", "", n).replace("★", "").strip()
    n = n.replace("高清4M", "").replace("高清", "")
    return n

def cat(name):
    if re.match(r"^CCTV", name, re.I): return "央视"
    if name.endswith("卫视"): return "卫视"
    return "专题"

def cctv_order(name):
    n = name.upper().replace("CCTV", "").strip()
    if "5+" in n: return 5.5
    m = re.sub(r"[^0-9]", "", n)
    return float(m) if m else 99

for r in res:
    r["host"] = re.match(r"https?://([^/]+)", r["url"]).group(1)
    r["ch"] = norm(r["name"])
    r["cat"] = cat(r["ch"])

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

# 每频道取三轮中位前3的候选（去重 host）
jobs = []
for ch in sorted(set(r["ch"] for r in res)):
    cands = sorted([r for r in res if r["ch"] == ch and r["ok_n"] == 3 and r["med"] >= 1000],
                   key=lambda x: -x["med"])
    if not cands:
        cands = sorted([r for r in res if r["ch"] == ch and r["ok_n"] >= 2 and r["med"] >= 1500],
                       key=lambda x: -x["med"])
    seen = set(); n = 0
    for c in cands:
        if c["host"] in seen: continue
        seen.add(c["host"])
        jobs.append((ch, n, c))
        n += 1
        if n >= 3: break

print("待测 %d 条（限3并发）" % len(jobs), flush=True)
fresh = {}
def job(j):
    ch, rank, c = j
    with sem:
        k = measure(c["url"])
    fresh[(ch, rank)] = k
ts = [threading.Thread(target=job, args=(j,)) for j in jobs]
t0 = time.time()
for t in ts: t.start()
for t in ts: t.join(120)
print("测完 %.0fs" % (time.time() - t0), flush=True)

final = []; flags = []
for ch in sorted(set(r["ch"] for r in res)):
    cands = [(rank, c, fresh.get((ch, rank))) for rank, c in [(j[1], j[2]) for j in jobs if j[0] == ch]]
    if not cands: continue
    ranked = sorted(cands, key=lambda x: -(x[2] or 0))
    rank, c, k = ranked[0]
    if k is None or k < 1000:
        flags.append((ch, k))
    final.append({"ch": ch, "cat": c["cat"], "url": c["url"], "med": c["med"],
                  "fresh": k, "host": c["host"]})

out = ["#EXTM3U",
       "# 河北移动 IPTV · 终版93台 | 2026-10-04 23:40 晚高峰实测",
       "# 每频道仅1条主源（备用源已移除）；主源=限流3并发现场复测最快",
       "# 括号内为 23:40 晚高峰现场速度", ""]
for g, title in [("央视", "央视"), ("卫视", "卫视"), ("专题", "影视·专题")]:
    items = [f for f in final if f["cat"] == g]
    if g == "央视":
        items.sort(key=lambda x: cctv_order(x["ch"]))
    else:
        items.sort(key=lambda x: -(x["fresh"] or 0))
    out.append("# --- %s（%d 台）---" % (title, len(items)))
    for f in items:
        fr = "现场%.1fM" % (f["fresh"] / 1000.0) if f["fresh"] else "现场FAIL"
        out.append('#EXTINF:-1 tvg-name="%s" group-title="%s",%s ·%s' % (f["ch"], title, f["ch"], fr))
        out.append(f["url"])
    out.append("")
open(os.path.join(BASE, "河北移动IPTV-终版93台.m3u"), "w", encoding="utf-8").write("\n".join(out) + "\n")

print("终版: %d 频道" % len(final))
ok3 = sum(1 for f in final if f["fresh"] and f["fresh"] >= 3000)
ok2 = sum(1 for f in final if f["fresh"] and 1500 <= f["fresh"] < 3000)
print("现场 ≥3M: %d | 1.5~3M: %d | <1.5M或FAIL: %d" % (ok3, ok2, len(flags)))
print("\n偏慢频道:", ", ".join("%s(%s)" % (c, k or "FAIL") for c, k in flags) or "无")
print("\n现场速度榜 Top15:")
for f in sorted(final, key=lambda x: -(x["fresh"] or 0))[:15]:
    print("  %-14s %.1fMbps  %s" % (f["ch"], (f["fresh"] or 0) / 1000.0, f["host"]))
