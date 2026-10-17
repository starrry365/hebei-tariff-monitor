#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_brief.py —— 构建期「AI 今日看点」：把当日变更报告喂给 LLM，产出主页卡片数据。

设计原则（与巡检主链路的关系）：
  · 纯增量：任何失败（未配 key / 网络挂 / 返回不合规）一律返回 None，页面就不渲染
    卡片 —— **AI 失败 ≠ 巡检失败**（与「预取失败 ≠ 采集失败」同一哲学）。
  · 输入是 changes/*.md（给人读的 curated 报告），不是原始 diff：报告已含护栏摘除
    说明与口径，AI 只做摘要与分级，不自行判断变化真伪。
  · 输出只做「标注 + 排序」：护栏的规则判定不被 AI 覆盖 —— 黑箱化的护栏比没护栏
    更糟，AI 化的护栏同理（tariff_monitor 变更报告段同源原则）。
  · 成本封顶：单网报告截断 1800 字符、总输入 8000 字符、max_tokens=900、
    一日一班只调一次，DeepSeek 价位 ≈ 几分钱/天。
  · 防注入：套餐名等来自源站（外部可任意改），AI 输出在页面侧全部 esc 后才进
    innerHTML；本模块只做长度钳制与 grade 白名单校验。

配置（GitHub Actions Secrets，本机可不配）：
    DEEPSEEK_API_KEY   必填才启用；缺失时静默跳过
    AI_MODEL           默认 deepseek-chat
"""

import glob
import json
import os
import re
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
PER_REPORT_CAP = 1800    # 单网报告截断（保留头部：计数行 + 前几条明细）
TOTAL_CAP = 8000         # 总输入上限（≈ 5k token）
TIMEOUT = 45             # 单次请求超时（秒）
RETRY = 1                # 失败重试次数（总共最多 2 次请求）
MAX_ITEMS = 8            # 卡片条目上限（prompt 与解析双重钳制）

# changes/ 文件名：移动无前缀（YYYY-MM-DD.md），其余 {code}-YYYY-MM-DD.md
_NET_CODES = ("unicom", "telecom", "cbn", "shct")

SYS_PROMPT = (
    "你是运营商资费变化分析助手。输入是每日自动巡检的变更报告（Markdown，"
    "含护栏摘除的假变化说明）。只依据报告内容，不编造、不猜测。"
    "输出严格的 JSON 对象（不要 markdown 代码块，不要解释文字）：\n"
    '{"summary": "<=110字，今天哪些网有什么实质变化、最值得注意的一条",'
    ' "items": [{"net": "move|unicom|telecom|cbn", "name": "套餐名",'
    ' "grade": "实质|门槛|营销|文案", "note": "<=40字理由"}],'
    ' "pitfalls": ["<=50字，读者需要警惕的点，没有则空数组"]}\n'
    "规则：护栏判定为假变化的条目不进 items；items 最多 8 条，"
    "按 实质 > 门槛 > 营销 > 文案 排序；报告说无变化的网不要硬凑条目。"
)


def gather_reports(today):
    """收集 changes/ 下属于 today 的各网报告，返回 [{code, text}]。"""
    out, total = [], 0
    for p in sorted(glob.glob(os.path.join(BASE, "changes", f"*{today}.md"))):
        base = os.path.basename(p)
        if base == f"{today}.md":
            code = "move"
        else:
            code = base[: -(len(today) + 3)]      # 去掉 -YYYY-MM-DD.md
            if code not in _NET_CODES:
                continue
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        chunk = {"code": code, "text": text[:PER_REPORT_CAP]}
        out.append(chunk)
        total += len(chunk["text"])
        if total >= TOTAL_CAP:
            break
    return out


# 宽松匹配计数行：兼容「新增 **0** · 下线 **9** · 字段变更 **1**」与纯数字两种格式
_NUM = re.compile(r"新增\s*\*{0,2}\s*(\d+)")
_OFF = re.compile(r"下线\s*\*{0,2}\s*(\d+)")
_CHG = re.compile(r"(?:字段)?变更\s*\*{0,2}\s*(\d+)")


def _has_changes(reports):
    """任一报告的计数行出现非零 ⇒ 值得调 AI；全零/解析不出 ⇒ 跳过。"""
    for r in reports:
        head = r["text"][:600]
        n = 0
        for pat in (_NUM, _OFF, _CHG):
            m = pat.search(head)
            if m:
                n += int(m.group(1))
        if n > 0:
            return True
    return False


def _chat(user_text):
    """调 DeepSeek chat，返回 content 字符串或 None（不抛异常）。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": user_text}],
        "max_tokens": 900,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    last_err = None
    for _ in range(RETRY + 1):
        try:
            req = urllib.request.Request(API_URL, data=body, headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "hebei-tariff-ai-brief/1",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
            return (data.get("choices") or [{}])[0].get("message", {}).get("content")
        except Exception as e:                       # noqa: BLE001 —— AI 永不弄死巡检
            last_err = e
    print(f"[ai] LLM 调用失败（已重试）：{last_err}", flush=True)
    return None


def _parse(text):
    """解析并钳制 LLM 输出；不合规一律 None。"""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip())
    d = None
    try:
        d = json.loads(t)
    except Exception:                                # 容忍前后杂质，抠最外层 JSON
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
            except Exception:
                return None
    if not isinstance(d, dict):
        return None
    grades = ("实质", "门槛", "营销", "文案")
    items = []
    for it in (d.get("items") or []):
        if not isinstance(it, dict) or not str(it.get("name", "")).strip():
            continue
        g = str(it.get("grade", "")).strip()
        items.append({
            "net": str(it.get("net", "")).strip()[:10],
            "name": str(it.get("name", "")).strip()[:80],
            "grade": g if g in grades else "文案",
            "note": str(it.get("note", "")).strip()[:60],
        })
        if len(items) >= MAX_ITEMS:
            break
    summary = str(d.get("summary", "")).strip()
    if not summary:
        return None
    return {
        "summary": summary[:220],
        "items": items,
        "pitfalls": [str(x).strip()[:80]
                     for x in (d.get("pitfalls") or []) if str(x).strip()][:3],
        "model": os.environ.get("AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    }


def build(today):
    """主入口：返回 brief dict 或 None（不抛异常）。"""
    if not today:
        return None
    reports = gather_reports(today)
    if not reports:
        print("[ai] 今日无变更报告，跳过 AI 看点", flush=True)
        return None
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("[ai] 未配置 DEEPSEEK_API_KEY，跳过 AI 看点（不影响主链路）", flush=True)
        return None
    if not _has_changes(reports):
        print("[ai] 今日各网零变化，跳过 AI 看点", flush=True)
        return None
    user_text = f"日期：{today}\n\n" + "\n\n".join(
        f"== {r['code']} ==\n{r['text']}" for r in reports)
    brief = _parse(_chat(user_text))
    if brief:
        print(f"[ai] AI 看点就绪：{brief['summary'][:50]}…"
              f"（{len(brief['items'])} 条分级）", flush=True)
    else:
        print("[ai] AI 输出不合规或为空，本轮无看点卡片", flush=True)
    return brief


if __name__ == "__main__":
    # 本机试跑：python ai_brief.py 2026-10-17
    import sys
    b = build(sys.argv[1] if len(sys.argv) > 1 else "")
    print(json.dumps(b, ensure_ascii=False, indent=2) if b else "(null)")
