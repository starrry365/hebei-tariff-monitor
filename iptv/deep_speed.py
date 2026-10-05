# -*- coding: utf-8 -*-
"""深测96台: 每台3轮×6秒窗口真实拉流, 统计中位/最低/波动。串行避免压垮源。"""
import re, time, json, socket, urllib.request, urllib.parse, statistics

socket.setdefaulttimeout(8)
UA = {"User-Agent": "PotPlayer/250604"}
SRC = r"D:\Work\WorkBuddy\_aptv_probe\河北移动IPTV-终版96台.m3u"
OUT = r"D:\Work\WorkBuddy\_aptv_probe\deep_speed_result.json"
ROUNDS, WINDOW = 3, 6  # 轮数, 每轮秒数


def parse():
    items, name = [], None
    for l in open(SRC, encoding="utf-8").read().splitlines():
        l = l.strip()
        if l.startswith("#EXTINF"):
            name = re.search(r",(.*)$", l).group(1).strip()
        elif l.startswith("http"):
            items.append({"name": name, "url": l})
    return items


def one_round(url):
    """6秒窗口内尽量拉, 返回 kbps; 失败返回 None"""
    seg = url
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=8) as r:
            head = r.read(65536)
        text = head.decode("utf-8", "ignore")
        if "#EXTM3U" in text[:4096] or ".m3u8" in url:
            seg = None
            for ln in text.splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    seg = ln if ln.startswith("http") else urllib.parse.urljoin(url, ln)
                    break
            if not seg:
                return None
            req = urllib.request.Request(seg, headers=UA)
            stream = urllib.request.urlopen(req, timeout=8)
        else:
            stream = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8)
    except Exception:
        return None
    t0 = time.time(); got = 0
    try:
        while time.time() - t0 < WINDOW:
            c = stream.read(262144)
            if not c: break
            got += len(c)
    except Exception:
        pass
    finally:
        try: stream.close()
        except Exception: pass
    dt = time.time() - t0
    if got < 100000:  # <100KB 视为失败
        return None
    return int(got * 8 / 1024 / max(dt, 0.3))


def main():
    items = parse()
    results = []
    t0 = time.time()
    for i, it in enumerate(items, 1):
        rounds = []
        for r in range(ROUNDS):
            k = one_round(it["url"])
            rounds.append(k)
            if k is None:
                time.sleep(1)
        ok = [k for k in rounds if k is not None]
        rec = {"name": it["name"], "url": it["url"], "rounds": rounds,
               "ok_n": len(ok), "median": int(statistics.median(ok)) if ok else None,
               "min": min(ok) if ok else None, "max": max(ok) if ok else None}
        # 波动率: (max-min)/median
        if ok and len(ok) >= 2:
            rec["jitter"] = round((rec["max"] - rec["min"]) / rec["median"], 2)
        else:
            rec["jitter"] = 0.0 if ok else None
        results.append(rec)
        med = rec["median"]
        bar = "FAIL" if med is None else "%5dM%-3s 抖动%.0f%% (%s)" % (
            med // 1000, "", rec["jitter"] * 100 if rec["jitter"] is not None else 0,
            "/".join("✗" if k is None else "%d" % (k // 1000) for k in rounds))
        print("[%02d/%d] %-12s %s" % (i, len(items), bar, it["name"]), flush=True)
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok_cnt = sum(1 for r in results if r["median"])
    full = sum(1 for r in results if r["ok_n"] == ROUNDS)
    print("\n完成: %d/%d 至少1轮通, %d 台3轮全通, 耗时 %.1f 分钟"
          % (ok_cnt, len(results), full, (time.time() - t0) / 60))


if __name__ == "__main__":
    main()
