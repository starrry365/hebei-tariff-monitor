#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拉取最新的「河北移动资费查询页」并打开。

页面自 2026-09-20 起不再有公网入口（GitHub 免费版 Pages 只支持公开仓库），
改为按 gzip 归档存在本仓库（hebei-mobile-h5-reverse）里：
    cloud/tariff/page/index.html.gz
用本脚本拉到本机解压查看，本机不对外托管。

页面上的日期是「数据基线日期」（数据自己的版本，没变就不前进）；
巡检有没有在跑看「最近巡检」（本脚本会从 state.json 读出来一起打印）。

用法：
    python view_page.py              拉取最新页面并打开浏览器
    python view_page.py --no-open    只拉取，不打开
    python view_page.py --print      打印本地页面摘要（数据基线 / 条数）后退出

依赖：标准库即可；需要 git（用来取仓库地址与本机已保存的 GitHub 凭据）。
"""
import argparse
import gzip
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

# 默认仓库；一般会用 git remote 自动推断（换 clone 也不再写死）
DEFAULT_REPO = "starrry365/hebei-mobile-h5-reverse"
ARCHIVE = "cloud/tariff/page/index.html.gz"   # 仓库内 gzip 归档路径
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "index.html")  # 本机解压落点（已 gitignore）
# 本机代理可能在的端口（V2Ray / 其他）；探测不到就直连
PROXY_PORTS = (10808, 10809, 10811, 7890, 7897)
UA = "tariff-viewer/1.0"


def log(msg):
    print(msg, flush=True)


def detect_repo():
    """从本目录的 git remote origin 推断 owner/repo；失败则用默认值。"""
    try:
        r = subprocess.run(["git", "-C", HERE, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=15)
        url = (r.stdout or "").strip()
        m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?$", url)
        if m:
            return "%s/%s" % (m.group(1), m.group(2))
    except Exception:
        pass
    return DEFAULT_REPO


def detect_proxy():
    """返回 http://127.0.0.1:<port> 或 None。

    ★ 不硬编码端口：V2Ray 会在 TUN / PAC / 清除系统代理之间切换，
      硬编码的后果是「静默走错通道」。
    """
    for p in PROXY_PORTS:
        s = socket.socket()
        s.settimeout(0.25)
        try:
            s.connect(("127.0.0.1", p))
            return "http://127.0.0.1:%d" % p
        except OSError:
            continue
        finally:
            s.close()
    return None


def github_token():
    """从本机 git 凭据管理器取 GitHub token（不落盘、不回显）。"""
    try:
        r = subprocess.run(
            "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill",
            shell=True, capture_output=True, text=True, timeout=30)
    except Exception as e:
        raise SystemExit("调用 git 取凭据失败：%s" % e)
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise SystemExit(
        "本机没有存 GitHub 凭据，无法拉取私密仓库。\n"
        "  先随便 clone 一次该仓库，或执行：\n"
        "    git ls-remote https://github.com/%s.git" % DEFAULT_REPO)


def fetch(token, proxy, repo):
    """取仓库内 gzip 归档，返回解压后的 HTML bytes。"""
    url = "https://api.github.com/repos/%s/contents/%s" % (repo, ARCHIVE)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github.raw",
        "User-Agent": UA,
    })
    # ★ 显式传 ProxyHandler：urllib 不传时会自动读环境变量/注册表里的残留代理，
    #   把请求静默改道（历史踩过）。
    handlers = [urllib.request.HTTPSHandler(),
                urllib.request.ProxyHandler({"https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=90) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SystemExit("远端还没有归档页面（仓库里没有 %s）" % ARCHIVE)
        if e.code in (401, 403):
            raise SystemExit("凭据无效或无权访问私密仓库（HTTP %d）" % e.code)
        raise
    # 归档是裸 gzip 流（魔数 1f8b）；若是被当文本取回，这里会报错提示
    if not blob[:2] == b"\x1f\x8b":
        raise SystemExit("取回内容不是 gzip（前 4 字节 %r），可能 Accept 头没生效"
                         % blob[:4])
    return gzip.decompress(blob)


def fetch_state(token, proxy, repo):
    """取 state.json（每次巡检都会覆盖写入，只有几百字节）。

    为什么要单独取它：页面归档只在**数据真变了**的时候才更新，
    所以页面上的日期回答不了「每日巡检还活着吗」这个问题。
    这个文件每次跑都更新，正好用来回答它。取不到（首版基线阶段）返回 None。
    """
    url = "https://api.github.com/repos/%s/contents/cloud/tariff/state.json" % repo
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github.raw",
        "User-Agent": UA,
    })
    handlers = [urllib.request.HTTPSHandler(),
                urllib.request.ProxyHandler({"https": proxy} if proxy else {})]
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def summarize(raw):
    t = raw.decode("utf-8", "replace")
    mt = re.search(r"数据基线 ([0-9-]+)", t)
    # 四网容器：取「移动」那网的条数（当前唯一接了数据的网）。
    # 用 raw_decode 而不是正则圈 [...];：行内文本里带 HTML，出现 "};" 之类的
    # 组合并不稀奇，非贪婪正则会在那里提前截断。
    mi = re.search(r"const NETS=", t)
    n = -1
    if mi:
        try:
            nets, _ = json.JSONDecoder().raw_decode(t[mi.end():])
            n = len(((nets.get("move") or {}).get("rows")) or [])
        except Exception:
            n = -1
    return (mt.group(1) if mt else "?"), n, len(raw)


def open_in_browser(p):
    """用系统默认程序打开。优先 os.startfile，失败再退回 explorer.exe。"""
    try:
        os.startfile(p)          # noqa: S606  (Windows 专用)
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(["explorer.exe", p])
        return True
    except Exception as e:
        log("  ⚠ 自动打开失败（%s），请手动打开：%s" % (e, p))
        return False


def main():
    ap = argparse.ArgumentParser(description="拉取最新资费查询页")
    ap.add_argument("--no-open", action="store_true", help="只拉取，不打开浏览器")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="打印本地页面摘要后退出（不下载）")
    ap.add_argument("-o", "--out", default=OUT, help="输出路径（默认 docs/index.html）")
    a = ap.parse_args()

    if a.print_only:
        if not os.path.exists(a.out):
            raise SystemExit("本地还没有页面：%s（先跑一次不带 --print 的）" % a.out)
        ts, n, size = summarize(open(a.out, "rb").read())
        log("数据基线: %s" % ts)
        log("条目数  : %d" % n)
        log("文件大小: %.2f MB" % (size / 1048576))
        return 0

    repo = detect_repo()
    proxy = detect_proxy()
    log("仓库    : %s" % repo)
    log("归档    : %s" % ARCHIVE)
    log("代理    : %s" % (proxy or "无（直连）"))
    token = github_token()
    raw = fetch(token, proxy, repo)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(raw)

    ts, n, size = summarize(raw)
    log("已保存  : %s" % a.out)
    log("数据基线: %s" % ts)
    log("条目数  : %d" % n)
    log("文件大小: %.2f MB" % (size / 1048576))

    # 「数据基线」是数据自己的版本日期（数据没变就不前进），
    # 「最近巡检」才是监控的存活证明 —— 两者都报，避免把前者误读成后者。
    st = fetch_state(token, proxy, repo)
    if st:
        log("最近巡检: %s（新增 %s / 下线 %s / 变更 %s）"
            % (st.get("ts", "?"), st.get("added", "?"),
               st.get("removed", "?"), st.get("changed", "?")))
    else:
        log("最近巡检: （仓库里还没有 state.json，首版基线阶段属正常）")

    if not a.no_open:
        if open_in_browser(a.out):
            log("已用默认浏览器打开")
    return 0


if __name__ == "__main__":
    sys.exit(main())
