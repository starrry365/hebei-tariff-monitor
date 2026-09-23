# -*- coding: utf-8 -*-
"""启动一个**独立真实 Chrome**（CDP 端口 9223），用于：
  · 过瑞数类 WAF 看运营商的 JS 挑战页（电信必须走这条路）
  · 加载生成的查询页，跑真实浏览器端到端断言（见 page_e2e_check.js）

🔴 绝不带 --enable-automation：navigator.webdriver=true 会被运营商 WAF 直接 400 空响应，
   等于自己把难度调高。
🔴 用 DETACHED_PROCESS 让它脱离本进程 —— 脚本退出后 Chrome 继续活着，
   后续用 probes/tools/ct_browser/cdp_desktop_step.py nav/eval/shot 驱动它。

用法：
    python probes/tools/cdp_launch.py            # 启动（幂等：已在跑就直接返回）
    python probes/tools/cdp_launch.py --kill     # 关掉 CDP 实例（按端口找到的进程）
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 9223
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROF = os.path.join(ROOT, "probes", "tools", "cdp_prof")   # 已 gitignore

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
]

# 真直连：显式传空 ProxyHandler，否则 urllib 会偷读环境变量/注册表里的残留代理
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def cdp_alive(timeout=2):
    try:
        with OP.open("http://127.0.0.1:%d/json/version" % PORT, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    raise SystemExit("找不到 Chrome，请改 CHROME_CANDIDATES")


def kill():
    # 通过 CDP 自带接口让实例自己退出，比按 pid 杀干净（不会误伤用户日常的 Chrome）
    try:
        with OP.open("http://127.0.0.1:%d/json/version" % PORT, timeout=3) as r:
            info = json.loads(r.read().decode())
        ws = info.get("webSocketDebuggerUrl", "")
        print("CDP 仍在线：%s" % ws)
        print("（Chrome 不提供远程退出接口，请手动关闭那个窗口，或用任务管理器结束 chrome.exe）")
    except Exception:
        print("CDP 端口 %d 上没有实例在跑。" % PORT)
    return 0


def main():
    if "--kill" in sys.argv:
        return kill()

    if cdp_alive():
        print("CDP %d 已在运行，无需重复启动。" % PORT)
        return 0

    chrome = find_chrome()
    os.makedirs(PROF, exist_ok=True)

    args = [chrome,
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=" + PROF,
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,OptimizationHints",
            # 🔴 这是个「后台跑、没有人在看」的调试实例，必须关掉节流：
            #    窗口不在前台时 rAF / 定时器会被节流甚至完全不执行，
            #    于是依赖 requestAnimationFrame 的布局逻辑实测不到效果 ——
            #    2026-09-23 就因此误判过（--barh 恒为 0，其实是 rAF 没跑）。
            #    这三个开关也只影响调度，不改 UA、不设 webdriver，不影响过 WAF。
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--window-size=1280,900",
            "about:blank"]

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000

    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    try:
        p = subprocess.Popen(args, creationflags=flags, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
    except OSError:
        # CREATE_BREAKAWAY_FROM_JOB 在「本进程不在 job 里」时会失败，退化重试
        p = subprocess.Popen(args, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                             close_fds=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    print("chrome pid = %d" % p.pid)

    for i in range(20):
        time.sleep(1)
        info = cdp_alive()
        if info:
            print("CDP ready after %ds: %s" % (i + 1, info.get("Browser", "")[:80]))
            return 0
    print("CDP 未就绪（Chrome 可能被沙箱挡了，或端口被占用）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
