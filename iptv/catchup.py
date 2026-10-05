# -*- coding: utf-8 -*-
"""河北移动OTT源 回看生成器 v2 (栅格对齐修复版)
关键: 回看分片必须锚定到流的10秒栅格(以直播播放列表的 PROGRAM-DATE-TIME 为基准),
      用挂钟时间直接拼会 404。兼容两种分片命名(北京时间式/Unix时间戳式)。

用法:
  python catchup.py                         # 列出可回看频道
  python catchup.py CCTV-1 30               # 30分钟前开始, 默认回看10分钟
  python catchup.py CCTV-1 30 20            # 30分钟前开始, 回看20分钟
  python catchup.py 湖南卫视 12:00 30       # 今天12:00开始(超过深度会拒绝)
  python catchup.py CCTV-5 -30 -list        # 只生成不打开PotPlayer
"""
import re, sys, io, ssl, os, json, subprocess, urllib.request
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ssl._create_default_https_context = ssl._create_unverified_context
UA = "Mozilla/5.0"
HOST = "hbgslbserv.taipan.jda.bcs.ottcn.com:6060"
CST = timezone(timedelta(hours=8))
_HERE = os.path.dirname(os.path.abspath(__file__))
SUPER = os.path.join(_HERE, "catchup_channels.json")
OUT = os.path.join(_HERE, "_catchup.m3u8")
POT = os.environ.get("POTPLAYER", r"D:\PotPlayer\PotPlayerMini64.exe")


def get(u, t=15, rng=None):
    h = {"User-Agent": UA}
    if rng: h["Range"] = rng
    return urllib.request.build_opener().open(urllib.request.Request(u, headers=h), timeout=t).read()


def channels():
    """频道名 -> (dir, cid)，仅1001树（唯一有回看归档的树）"""
    out = {}
    for name, v in json.load(open(SUPER, encoding="utf-8")).items():
        out[name] = ("030000001001", v["cid"])
    return out


def depths():
    """深度直接来自 catchup_channels.json 的 depth_days 字段（单位: 天）"""
    out = {}
    for name, v in json.load(open(SUPER, encoding="utf-8")).items():
        out[name] = {"depth": float(v.get("depth_days", 0)) * 86400}
    return out


def live_tpl(d, c):
    txt = get(f"http://{HOST}/{d}/{c}/1.m3u8").decode("utf-8", "replace")
    segs = [l.strip() for l in txt.splitlines() if l.startswith("http")]
    m = re.search(r"#EXT-X-PROGRAM-DATE-TIME:([\dT:\-]+)Z", txt)
    if not segs or not m:
        raise RuntimeError("直播模板获取失败")
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return segs[0], base


def shift(tpl, base, t):
    """把模板时间替换到流栅格上的时刻 t (t 必须与 base 同相位10s对齐)"""
    b = t.replace(minute=(t.minute // 30) * 30, second=0, microsecond=0)
    cst = t.astimezone(CST); cst2 = cst + timedelta(seconds=10)
    u = re.sub(r"(/[^/?]+)_\d{8}_\d{6}_\d{6}\.ts",
               lambda m: "%s_%s_%s.ts" % (m.group(1), cst.strftime("%Y%m%d_%H%M%S"),
                                          cst2.strftime("%H%M%S")), tpl)
    u = re.sub(r"_\d{10}_\d{10}\.ts",
               lambda m: "_%d_%d.ts" % (int(t.timestamp()), int(t.timestamp()) + 10), u)
    u = re.sub(r"/\d{6}/\d{6}/", "/%s/%s/" % (b.strftime("%Y%m"), b.strftime("%d%H%M")), u)
    u = re.sub(r"prog_time=\d{14}", "prog_time=" + t.strftime("%Y%m%d%H%M%S"), u)
    return u


def build(d, c, start_utc, minutes):
    tpl, base = live_tpl(d, c)
    # 栅格对齐: 与 base 同相位
    t0 = start_utc - timedelta(seconds=(start_utc - base).total_seconds() % 10)
    if t0 > base - timedelta(seconds=30):
        t0 = base - timedelta(minutes=1)  # 至少回1分钟, 别贴着直播边
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:11",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    n, t = 0, t0
    while n < minutes * 6:  # 10s/片
        lines += ["#EXTINF:10.0,", shift(tpl, base, t)]
        t += timedelta(seconds=10); n += 1
    return "\n".join(lines) + "\n", t0


def main():
    chans, deps = channels(), depths()
    if len(sys.argv) == 1:
        print("可回看频道 %d 个（按归档深度排序, ★=7天）:" % len(chans))
        for name in sorted(chans, key=lambda x: -deps.get(x, {}).get("depth", 0)):
            dp = deps.get(name, {}).get("depth", 0) / 86400
            print("  %s %-14s 深度%.1f天" % ("★" if dp >= 6.9 else " ", name, dp))
        print('\n用法: python catchup.py 频道名 [分钟前|HH:MM] [时长分,默认10] [-list]')
        return
    q = sys.argv[1]
    name = next((c for c in chans if c == q), None) \
        or next((c for c in chans if c.startswith(q)), None) \
        or next((c for c in chans if q in c), None)
    if not name:
        print("没有这个频道:", sys.argv[1]); return
    when = sys.argv[2] if len(sys.argv) > 2 else "30"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else 10
    open_pot = "-list" not in sys.argv
    d, c = chans[name]
    now = datetime.now(timezone.utc)
    if ":" in when:
        hh, mm = map(int, when.split(":"))
        start = now.astimezone(CST).replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone(timezone.utc)
        if start > now: start -= timedelta(days=1)
    else:
        start = now - timedelta(minutes=int(when))
    # 深度校验
    dep = deps.get(name, {}).get("depth", 0)
    if dep and (now - start).total_seconds() > dep:
        print("✗ %s 归档深度只有 %.1f 天, 回看不了那么久" % (name, dep / 86400)); return
    m3u8, t0 = build(d, c, start, dur)
    open(OUT, "w", encoding="utf-8").write(m3u8)
    print("已生成: %s 从 %s 回看 %d 分钟 (%s/%s)" %
          (name, t0.astimezone(CST).strftime("%m-%d %H:%M"), dur, d, c))
    print("文件:", OUT)
    if open_pot and os.path.exists(POT):
        subprocess.Popen(["cmd", "/c", "start", "", POT, OUT])
    elif open_pot:
        print("PotPlayer 不在 %s，请手动打开上面的文件（或设环境变量 POTPLAYER 指向播放器）" % POT)


if __name__ == "__main__":
    main()
