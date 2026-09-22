#!/usr/bin/env bash
# 电信「资费专区」云端采集（Linux / GitHub Runner）。
#
# 为什么需要它：`tariffSection.do` 被瑞数类 JS 挑战 WAF 保护 —— 纯 HTTP 必 412，
# 且 `navigator.webdriver=true` 的自动化浏览器会被 400 空响应硬拒。
# 唯一实测通路是**真实 Chrome + 远程调试口**（启动只给 --remote-debugging-port，
# 绝不给 --enable-automation）。
#
# 🟢 2026-09-22 实测（ubuntu-latest）：用 Xvfb 起**有头** Chrome 即可通过挑战，
#    页面标题「资费专区」、navigator.webdriver=false、884 条 5 个分类逐项采全。
#    ⇒ 「云端采不到电信」是错判，真正的前提只是「得有浏览器 + 得有屏幕」。
#    别改成 --headless：headless 的 UA 带 HeadlessChrome，等于自己把难度调高。
#
# ✅ 两处等待都是**轮询**而不是「固定 sleep + 单次判断」：
#    2026-09-22 第一次接入 CI 时就是栽在这上面 —— 固定 `sleep 6` 后单发 curl 查
#    DevTools 端口，判成「Chrome 没起来」直接放弃；而同一镜像上换个时机它 10 秒后才
#    开端口。冷启动 runner（新 profile + dbus 报错）本来就会慢，固定等待必然假失败。
#
# 用法：
#     bash probes/tools/ct_browser/ci_grab.sh [输出路径]
#     默认输出 cloud/tariff/.ct_raw.json（已 gitignore，是 ct_monitor.fetch_all 的输入）
#
# 🔴 **本脚本永不非零退出**：电信是「机会性采集」，采不到时调用方
#    （tariff_monitor.other_nets）会自动退回仓库快照渲染，页面照样有四网。
#    这里只负责把失败原因打成 ::warning:: 让人看见，不负责把整轮巡检搞挂。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
OUT="${1:-$ROOT/cloud/tariff/.ct_raw.json}"
STEP="$HERE/cdp_desktop_step.py"
URL="https://www.189.cn/wapportalweb/rateZone/index.html?provCode=609906"
PORT=9223
PROFILE="$(mktemp -d)"
TMP="${RUNNER_TEMP:-/tmp}"
CHROME_LOG="$TMP/ct-chrome.log"
SHOT="$TMP/ct_shot.png"
# 页面标题：挑战通过后业务页才会是这个。用它当「挑战过了没」的判据，
# 比盯 cookie 长度或 HTTP 码都直接。
TITLE="资费专区"

warn() { echo "::warning title=电信采集失败::$*"; }
info() { echo "[ct-grab] $*"; }

[ -f "$STEP" ] || { warn "找不到 CDP 驱动 $STEP"; exit 0; }
rm -f "$OUT"

# ── 1) 找 Chrome ────────────────────────────────────────────────────
CHROME="$(command -v google-chrome || command -v google-chrome-stable \
          || command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROME" ]; then
  info "runner 未自带 chrome，改用官方 deb 安装"
  curl -sSL -o "$TMP/ct-chrome.deb" \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && sudo apt-get install -y -qq "$TMP/ct-chrome.deb" >/dev/null \
    && CHROME="$(command -v google-chrome-stable || true)"
fi
[ -n "$CHROME" ] || { warn "装不上 Chrome，本轮跳过（退回快照渲染）"; exit 0; }
info "chrome = $CHROME（$("$CHROME" --version 2>/dev/null)）"

# ── 2) 起虚拟显示 + 有头 Chrome ──────────────────────────────────────
if ! command -v Xvfb >/dev/null; then
  info "缺 Xvfb，尝试安装"
  sudo apt-get update -qq && sudo apt-get install -y -qq xvfb >/dev/null || true
fi
if command -v Xvfb >/dev/null; then
  Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >"$TMP/ct-xvfb.log" 2>&1 &
  sleep 2
  export DISPLAY=:99
  info "Xvfb :99 已起"
else
  warn "没有 Xvfb，改用无显示启动（成功率下降，但值得一试）"
fi

nohup "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check \
  --no-sandbox --disable-dev-shm-usage --disable-gpu \
  about:blank >"$CHROME_LOG" 2>&1 &
CHROME_PID=$!

cleanup() { kill "$CHROME_PID" 2>/dev/null || true; rm -rf "$PROFILE"; }
trap cleanup EXIT

# ── 3) 轮询等 DevTools 端点（最多 45s）───────────────────────────────
READY=""
for i in $(seq 1 45); do
  if curl -s --max-time 3 "http://127.0.0.1:$PORT/json/version" >"$TMP/ct-devtools.json" 2>/dev/null; then
    READY=1; break
  fi
  if ! kill -0 "$CHROME_PID" 2>/dev/null; then
    warn "Chrome 进程在第 ${i}s 就退出了"
    break
  fi
  sleep 1
done
if [ -z "$READY" ]; then
  warn "等不到 DevTools 端点（45s）。chrome.log 末尾："
  tail -25 "$CHROME_LOG" 2>/dev/null || true
  exit 0
fi
info "DevTools 就绪（第 ${i}s）：$(head -c 100 "$TMP/ct-devtools.json")"

# ── 4) 导航 → 轮询等挑战通过（最多 60s）──────────────────────────────
info "导航 $URL"
python "$STEP" nav "$URL" || true
STATE=""
for i in $(seq 1 12); do
  sleep 5
  STATE="$(python "$STEP" state 2>&1 | tail -1)"
  case "$STATE" in
    *"\"t\":\"$TITLE\""*) info "挑战通过（第 $((i * 5))s）"; break ;;
  esac
  echo "  …等待挑战 $((i * 5))s  $STATE"
done
echo "[ct-grab] 页面状态：$STATE"

# 页面没起来就别往下走了 —— 硬发请求只会拿到挑战页的 HTML，报一堆看不懂的错。
case "$STATE" in
  *'"wd":false'*) : ;;
  *) warn "navigator.webdriver 不是 false（当前：$STATE），WAF 必然拒。看截图与 chrome.log"
     python "$STEP" shot "$SHOT" || true
     tail -25 "$CHROME_LOG" 2>/dev/null || true
     exit 0 ;;
esac
case "$STATE" in
  *"\"t\":\"$TITLE\""*) : ;;
  *) warn "60s 内没进到业务页（当前：$STATE）—— 挑战没过。截图已存 $SHOT"
     python "$STEP" shot "$SHOT" || true
     exit 0 ;;
esac

# ── 5) 采集 + 自检 ───────────────────────────────────────────────────
info "采集（页面自带 CryptoJS 造包体）"
if ! python "$STEP" eval "$HERE/harvest_hb.js" "$OUT"; then
  python "$STEP" shot "$SHOT" || true
  warn "采集 JS 执行失败，截图已存 $SHOT"
  exit 0
fi

python - "$OUT" <<'PY'
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p, encoding="utf-8"))
except Exception as e:
    print("::warning title=电信采集产物不可解析::%s: %s" % (type(e).__name__, e)); raise SystemExit(0)
if "raw" in d and len(d) == 1:
    print("::warning title=电信采集返回未解析字符串::前 300 字：%s" % d["raw"][:300]); raise SystemExit(0)
tot = 0
for k, v in (d.get("sections") or {}).items():
    n = len(v.get("zoneTitleList") or [])
    tot += n
    # 服务端自报 count 与实收 len 不等 ⇒ 被截断。宁可整轮判失败走快照，
    # 也别把一份残页当成「今日采集成功」写进快照（那会连带生成假的下线报告）。
    c = v.get("count")
    if c is not None and int(c) != n:
        print("::warning title=电信采集被截断::分类「%s」count=%s 但只收到 %s 条" % (k, c, n))
print("[ct-grab] provCode=%s codes=%s 合计 %d 条" % (d.get("provCode"), d.get("codes"), tot))
if tot < 500:
    print("::warning title=电信采集条数异常::只拿到 %d 条（正常 800+），本轮视为失败" % tot)
PY

echo "[ct-grab] 完成，产物 $OUT"
exit 0
