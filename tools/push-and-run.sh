#!/usr/bin/env bash
# 推送 + 触发一轮巡检 + 盯到跑完（本机专用）
#
# 为什么要有这个脚本：
#   这台机器直连 github.com:443 会被重置（奇怪的是 api.github.com 却是通的），
#   所以 push 必须走本地 SOCKS5 代理；而触发 workflow 又要 PAT。
#   两件事每次手工拼，容易漏、也容易写错代理参数。固化在这里，一条命令到底。
#
# 依赖（= 这台机器的现状）：
#   - 代理：127.0.0.1:10808（v2rayN 的 SOCKS5 口）—— 可用 PROXY=... 覆盖
#   - 凭据：git-credential-manager 里存的 ghp_…（scopes 含 repo + workflow）
#     —— **只读取、不落盘、不打印**
#   - python（解析 API 的 JSON）
#
# 用法：
#   tools/push-and-run.sh                 # 推送当前 HEAD → 触发 → 盯到结束
#   tools/push-and-run.sh --no-watch      # 只推送 + 触发，不等待
#   PROXY=http://127.0.0.1:7890 tools/push-and-run.sh
#
# 退出码：0 = 成功 / 1 = 推送或触发失败 / 2 = workflow 跑失败

set -uo pipefail

REPO="starrry365/hebei-tariff-monitor"
WORKFLOW="tariff-daily.yml"
BRANCH="main"
PROXY="${PROXY:-socks5h://127.0.0.1:10808}"
PY="${PY:-python}"

WATCH=1
[ "${1:-}" = "--no-watch" ] && WATCH=0

cd "$(dirname "$0")/.." || { echo "✗ 找不到仓库根"; exit 1; }

# ── 1. 推送（带重试：代理线路会间歇性 TLS 握手失败）────────────────────
echo "── 1/4 推送 $BRANCH ──"
timeout 120 git -c http.proxy="$PROXY" fetch origin >/dev/null 2>&1

# 远端很可能**已经领先** —— 上一轮巡检刚把数据提交回仓库（snapshots/ changes/
# page/ history.json state.json 等）。先变基再推。
# 这个仓库里 CI 提交的全是数据文件、我们改的是代码/文档，实测零重叠；
# 但真冲突了必须停下让人看，**绝不 force push**。
BEHIND=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo "0")
if [ "$BEHIND" != "0" ]; then
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "✗ 远端领先 $BEHIND 个提交，但本地有未提交改动 —— 先提交再来"
    exit 1
  fi
  echo "   远端领先 $BEHIND 个提交（上一轮巡检的数据），先变基"
  if ! git rebase "origin/$BRANCH" >/dev/null 2>&1; then
    echo "✗ 变基冲突 —— 需人工处理（git rebase --abort 可退回）"
    exit 1
  fi
  echo "   变基完成，本地 $(git rev-list --count "origin/$BRANCH..HEAD") 个提交待推"
fi

AHEAD=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo "?")
if [ "$AHEAD" = "0" ]; then
  echo "   远端已是最新，跳过推送"
elif [ "$AHEAD" = "?" ]; then
  echo "✗ 读不到 origin/$BRANCH（fetch 失败？）"; exit 1
else
  echo "   领先远端 $AHEAD 个提交"
  PUSHED=0
  for i in 1 2 3; do
    timeout 240 git -c http.proxy="$PROXY" push origin "$BRANCH" 2>&1 | tail -2
    A=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo "?")
    if [ "$A" = "0" ]; then PUSHED=1; break; fi
    echo "   第 $i 次未成功，重试…"; sleep 3
  done
  [ "$PUSHED" = "1" ] || { echo "✗ 推送失败（代理线路？）"; exit 1; }
fi
HEAD_SHA=$(git rev-parse --short HEAD)
echo "   HEAD = $HEAD_SHA"

# ── 2. 取凭据（只读；不 echo 出来）────────────────────────────────────
TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' \
        | timeout 30 git credential fill 2>/dev/null | sed -n 's/^password=//p')
if [ -z "$TOKEN" ]; then
  echo "✗ 取不到 GitHub 凭据 —— credential manager 里没有？"
  echo "  （或临时用 GITHUB_TOKEN=... 跑本脚本）"
  TOKEN="${GITHUB_TOKEN:-}"
  [ -z "$TOKEN" ] && exit 1
fi
API="https://api.github.com/repos/$REPO"

# ── 3. 触发 ──────────────────────────────────────────────────────────
echo "── 2/4 触发 $WORKFLOW ──"
# 先看有没有正在跑的：concurrency 是 cancel-in-progress: false，
# 所以本次触发**不会掐掉它**，只会排队 —— 但也别以为立刻就开跑。
BUSY=$(timeout 40 curl -sS -H "Authorization: Bearer $TOKEN" "$API/actions/runs?per_page=5" \
       | "$PY" -c "
import json,sys
for r in json.load(sys.stdin).get('workflow_runs', []):
    if r['status'] in ('in_progress','queued','requested','waiting','pending'):
        print(f\"#{r['run_number']} {r['status']}\"); break
" 2>/dev/null)
if [ -n "$BUSY" ]; then
  echo "   ⚠ 已有一轮在跑（$BUSY）；本次触发会排队等它（不会互相掐）"
fi
CODE=$(timeout 60 curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $TOKEN" -H 'Accept: application/vnd.github+json' \
  "$API/actions/workflows/$WORKFLOW/dispatches" \
  -d "{\"ref\":\"$BRANCH\"}")
if [ "$CODE" = "204" ]; then
  echo "   已触发（HTTP 204）"
else
  echo "✗ 触发失败 HTTP $CODE"
  case "$CODE" in
    401) echo "   → 凭据无效/过期";;
    403) echo "   → 凭据缺 workflow 权限（PAT 需勾 workflow scope）";;
    404) echo "   → workflow 文件名或仓库不对";;
    422) echo "   → ref 不存在";;
  esac
  exit 1
fi

[ "$WATCH" = "0" ] && { echo "（--no-watch：不等结果）"; exit 0; }

# ── 4. 等这轮 run 出现（dispatch 到 run 建立有几秒延迟）────────────────
echo "── 3/4 等待运行开始 ──"
RID=""
for _ in $(seq 1 20); do
  sleep 6
  RID=$(timeout 40 curl -sS -H "Authorization: Bearer $TOKEN" \
        "$API/actions/runs?per_page=8" | "$PY" -c "
import json,sys
for r in json.load(sys.stdin).get('workflow_runs', []):
    if r['head_sha'].startswith('$HEAD_SHA') and r['event'] == 'workflow_dispatch':
        print(r['id']); break
" 2>/dev/null)
  [ -n "$RID" ] && break
done
if [ -z "$RID" ]; then
  echo "✗ 没等到对应的 run（可能被排队很久，去网页看）"
  exit 1
fi
echo "   run id = $RID"

# ── 5. 等它跑完（最多约 25 分钟）────────────────────────────────────
echo "── 4/4 运行中（每 25 秒看一次）──"
STATUS=""; CONCL="-"
for i in $(seq 1 60); do
  sleep 25
  read -r STATUS CONCL < <(timeout 40 curl -sS -H "Authorization: Bearer $TOKEN" \
    "$API/actions/runs/$RID" | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('status','?'), d.get('conclusion') or '-')
" 2>/dev/null)
  echo "   [$i] $STATUS / $CONCL"
  [ "$STATUS" = "completed" ] && break
done

# ── 6. 结论 + 逐步骤 ────────────────────────────────────────────────
echo ""
timeout 40 curl -sS -H "Authorization: Bearer $TOKEN" "$API/actions/runs/$RID/jobs" | "$PY" -c "
import json,sys
d = json.load(sys.stdin)
for j in d.get('jobs', []):
    print(f\"JOB {j['name']}: {j['status']}/{j.get('conclusion')}\")
    for s in j.get('steps', []):
        c = s.get('conclusion')
        mark = {'success': 'OK  ', 'failure': '>>  ', 'skipped': '--  '}.get(c, '    ')
        print(f\"  {mark}[{c}] {s['number']:>2}. {s['name']}\")
"
echo ""
echo "   https://github.com/$REPO/actions/runs/$RID"

if [ "$CONCL" = "success" ]; then
  echo "✓ 这一轮通过"; exit 0
fi
echo "✗ 这一轮 $CONCL"; exit 2
