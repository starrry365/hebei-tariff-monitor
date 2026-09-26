# -*- coding: utf-8 -*-
"""变更推送 —— 两个通道：**邮件（SMTP）** 与 **PushPlus（微信）**。

设计要点（每一条都是踩过或差点踩到的）：

① **一次变化只推一条。**
   ``tariff_monitor`` 一轮巡检里要跑四网、可能重跑（定时 + 手动 dispatch + 重试）。
   推送若写在各网内部，同一次变化会被推 4 次、重跑再推 1 次 —— 用户会直接
   把这个通知静音，然后真变化也看不见了。所以：
     · 消息在 ``main()`` **末尾统一汇总成一条**发出去；
     · ``SELF_NOTIFY=0`` 时本脚本完全不推（交给外部编排），
       这是「同一个变化被两处各推一次」的正解；
     · 另外用 ``.notify_state.json`` 记**内容指纹**，同一天同一份变化重复推送直接跳过
       （对 ``workflow_dispatch`` 反复重跑是硬性的——CI 每次都是全新 checkout，
        光靠内存去重等于没有去重，所以指纹必须**落盘并入库**）。

② **通道互不拖累。** 一个通道失败（token 过期 / SMTP 被拒）不能让另一个也不发；
   同样，推送失败**绝不能让整个巡检失败** —— 数据采到了、页面更新了才是主要产物，
   通知只是附加。所以这里所有异常都吞掉并打印 ``::warning``。

③ **不配置就静默跳过**，不报错。本地开发、fork 别人仓库跑都不该因为没配 SMTP 而红。

④ 正文用 **Markdown**：PushPlus 原生支持 ``template=markdown``；
   邮件那边转成 HTML + 纯文本两份（只发纯文本会在手机上挤成一团，
   只发 HTML 又会被纯文本客户端判成垃圾）。

配置（环境变量，CI 里走 Actions Secrets）：

    邮件：SMTP_HOST SMTP_PORT(465) SMTP_USER SMTP_PASS MAIL_TO MAIL_FROM MAIL_FROM_NAME
    微信：PUSHPLUS_TOKEN（必填） PUSHPLUS_TOPIC（群组编码，可选）

    开关：SELF_NOTIFY(默认 1，设 0 = 本脚本不推) / NOTIFY(默认 1，设 0 = 全关)
          NOTIFY_ALWAYS(默认 0，设 1 = 无变化也每天报个平安)
          ⚠️ 「无变化」**不含异常态**：任一网掉了降级 / 结构变更 / 采集失败，
             一律照推 —— 那正是**需要人看一眼**的时刻，静默掉它等于把事故藏起来。

用法:
    python notify.py --test          # 只发一条自检消息，验证配置
    python notify.py --check         # 只看有哪些通道可用，不发
"""
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.request
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, ".notify_state.json")
PUSHPLUS_API = "https://www.pushplus.plus/send"

# 直连：与 tariff_monitor 同一原则 —— 抓取/推送都不该被本机代理劫持
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _env(k, d=""):
    return str(os.environ.get(k) or d).strip()


def enabled():
    """总开关。``NOTIFY=0`` 全关；``SELF_NOTIFY=0`` 只关本脚本这一路。"""
    if _env("NOTIFY", "1") == "0":
        return False
    if _env("SELF_NOTIFY", "1") == "0":
        return False
    return True


def log(msg):
    print(f"[notify {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def warn(msg):
    # ::warning 在 GitHub Actions 里会显示成黄色告警条，不失败
    print(f"::warning title=推送::{msg}", flush=True)


# ════════════════════════════════════════════════════════════════════════
#  正文渲染：Markdown → (纯文本, HTML)
# ════════════════════════════════════════════════════════════════════════
def _md_to_text(md):
    s = re.sub(r"^#{1,6}\s*", "", md, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", s)
    s = re.sub(r"^\s*[-*]\s+", "· ", s, flags=re.M)
    return s


def _md_to_html(md):
    """只支持本模块自己会生成的那几种写法（标题/加粗/行内码/列表/链接）。

    🔴 刻意**不**引入 markdown 库：CI 里多一个依赖就多一个断点，而我们的输入
       是自己拼的，格式完全可控。转义先做，再套标签 —— 反过来会把上游资费文案里
       的 ``<`` 当标签放进去（上游文案实测带真 HTML，见 tariff_monitor.js_json）。
    """
    out, in_ul = [], False
    for raw in md.splitlines():
        line = (raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        m = re.match(r"^(#{1,6})\s*(.*)$", line)
        if m:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            lv = min(6, len(m.group(1)) + 1)
            out.append(f"<h{lv}>{m.group(2)}</h{lv}>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + re.sub(r"^\s*[-*]\s+", "", line) + "</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        out.append(("<p>" + line + "</p>") if line.strip() else "")
    if in_ul:
        out.append("</ul>")
    s = "\n".join(out)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]*)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return ("<html><body style=\"font-family:-apple-system,'Segoe UI',"
            "Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6;"
            "color:#222\">" + s + "</body></html>")


def _trunc(s, n):
    """按字符截断并显式标注 —— 静默截断会让「只看到前一半」的人以为就这些。"""
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "\n\n…（已截断，完整内容见仓库 changes/ 或页面）"


# ════════════════════════════════════════════════════════════════════════
#  通道
# ════════════════════════════════════════════════════════════════════════
def send_pushplus(title, md):
    """PushPlus（微信推送）。返回 (是否可用, 是否成功, 说明)。"""
    token = _env("PUSHPLUS_TOKEN")
    if not token:
        return False, False, "未配置 PUSHPLUS_TOKEN"
    body = {"token": token, "title": title,
            "content": _trunc(md, 18000), "template": "markdown"}
    topic = _env("PUSHPLUS_TOPIC")
    if topic:
        body["topic"] = topic
    try:
        req = urllib.request.Request(
            PUSHPLUS_API, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with _OPENER.open(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8", "replace") or "{}")
        if str(j.get("code")) == "200":
            return True, True, "ok"
        # 🔴 900 = 触发限流（同 token 高频）。这是**能修**的信息，别只报「失败」。
        msg = str(j.get("msg") or j)
        if str(j.get("code")) == "900":
            msg += "（推送频率超限，注意「一次变化只推一条」）"
        return True, False, msg
    except Exception as e:
        return True, False, f"{type(e).__name__}: {e}"


def send_email(title, md):
    """邮件（SMTP）。返回 (是否可用, 是否成功, 说明)。"""
    host, user = _env("SMTP_HOST"), _env("SMTP_USER")
    to, pwd = _env("MAIL_TO"), _env("SMTP_PASS")
    if not (host and to):
        return False, False, "未配置 SMTP_HOST / MAIL_TO"
    port = int(_env("SMTP_PORT", "465") or 465)
    sender = _env("MAIL_FROM") or user or to
    name = _env("MAIL_FROM_NAME", "资费监控")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = formataddr((str(Header(name, "utf-8")), sender))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    txt = _md_to_text(md)
    msg.attach(MIMEText(txt, "plain", "utf-8"))
    msg.attach(MIMEText(_md_to_html(md), "html", "utf-8"))
    try:
        if _env("SMTP_SSL", "1") != "0":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
                if user:
                    s.login(user, pwd)
                s.sendmail(sender, [x.strip() for x in to.split(",") if x.strip()],
                           msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                if user:
                    s.login(user, pwd)
                s.sendmail(sender, [x.strip() for x in to.split(",") if x.strip()],
                           msg.as_string())
        return True, True, "ok"
    except Exception as e:
        return True, False, f"{type(e).__name__}: {e}"


CHANNELS = (
    ("PushPlus", send_pushplus),
    ("邮件", send_email),
)


def available():
    """哪些通道「配置齐了」。用于 --check 与日志，不发消息。"""
    out = []
    if _env("PUSHPLUS_TOKEN"):
        out.append("PushPlus")
    if _env("SMTP_HOST") and _env("MAIL_TO"):
        out.append("邮件")
    return out


# ════════════════════════════════════════════════════════════════════════
#  内容指纹去重：同一天同一份变化绝不推第二次
# ════════════════════════════════════════════════════════════════════════
def _digest(title, md):
    h = hashlib.sha256()
    h.update(str(title or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(str(md or "").encode("utf-8"))
    return h.hexdigest()[:16]


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"schema": 1, "sent": []}


def _save_state(st):
    st["sent"] = list(st.get("sent") or [])[-120:]      # 只留最近 120 条指纹
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def send_all(title, md, dedup=True):
    """把所有**已配置**的通道发一遍。返回 {通道: 结果说明}。

    ``dedup=True`` 时先比内容指纹：推过就跳过（并打印原因）。
    🔴 指纹落盘（``.notify_state.json``）且**必须入库**：
       CI 每轮都是全新 checkout，只在内存里去重等于没去重，
       而 workflow_dispatch 反复重跑是常态。
    """
    if not enabled():
        log(f"推送已关闭（NOTIFY={_env('NOTIFY','1')} / SELF_NOTIFY={_env('SELF_NOTIFY','1')}），跳过")
        return {}
    chans = [c for c in CHANNELS if _is_on(c[0])]
    if not chans:
        log("没有任何通道配置了凭据（PUSHPLUS_TOKEN / SMTP_HOST+MAIL_TO），跳过推送")
        return {}

    dg = _digest(title, md)
    if dedup:
        st = _load_state()
        if dg in (st.get("sent") or []):
            log(f"同一份内容已推送过（指纹 {dg}），本次跳过 —— 一次变化只推一条")
            return {"_dedup": "skipped"}
    results = {}
    ok_any = False
    for nm, fn in chans:
        try:
            usable, ok, why = fn(title, md)
        except Exception as e:                      # 通道实现里的兜底，绝不外抛
            usable, ok, why = True, False, f"{type(e).__name__}: {e}"
        results[nm] = ("ok" if ok else why) if usable else f"跳过（{why}）"
        if ok:
            ok_any = True
            log(f"{nm} 推送成功：{title}")
        elif usable:
            warn(f"{nm} 推送失败：{why}")
    if ok_any and dedup:
        st = _load_state()
        st.setdefault("sent", []).append(dg)
        st["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_state(st)
    elif not ok_any and dedup:
        # 全失败**不记指纹** —— 否则修好之后永远推不出去了（指纹已经被写死）
        warn("所有通道都失败了，不记指纹（下次仍会尝试）")
    return results


def _is_on(name):
    if name == "PushPlus":
        return bool(_env("PUSHPLUS_TOKEN"))
    if name == "邮件":
        return bool(_env("SMTP_HOST") and _env("MAIL_TO"))
    return False


# ════════════════════════════════════════════════════════════════════════
#  变更正文：把各网的核验结果拼成一条消息
# ════════════════════════════════════════════════════════════════════════
def build_body(day, rounds, extra_notes=None, repo="", max_items=12):
    """拼推送正文。

    ``rounds``：``[{code, net, n, added, removed, changed, restored,
                   fake_removed, relocated, note, samples}]``
      ``samples``：``[{"n":名称, "ty":分类, "k":"a"/"r"/"c"}]``
    ``note`` 非空表示这一网本轮**没有记变更**（``degraded`` / ``rebound`` / ``schema``），
    正文里必须显式写出来 —— 否则「本轮无变化」与「本轮数据异常已冻结」长得一模一样，
    而后者是**需要人看一眼**的。
    """
    L = [f"# 河北四网资费巡检 {day}", ""]
    tot_a = tot_r = tot_c = 0
    for r in rounds or []:
        if r.get("note"):
            L.append(f"- **{r['net']}** {r['n']} 条 · 本轮未记变更（{_NOTE_CN.get(r['note'], r['note'])}）")
            continue
        a, rm, c = r.get("added", 0), r.get("removed", 0), r.get("changed", 0)
        tot_a += a
        tot_r += rm
        tot_c += c
        bits = [f"新增 {a}", f"下线 {rm}", f"字段变更 {c}"]
        for k, cn in (("state_moved", "在售↔停售迁移"), ("restored", "补录"),
                      ("fake_removed", "假下架已抑制"),
                      ("relocated", "栏目漂移已合并")):
            if r.get(k):
                bits.append(f"{cn} {r[k]}")
        L.append(f"- **{r['net']}** {r['n']} 条 · " + " · ".join(bits))
    L.append("")
    if not (tot_a or tot_r or tot_c):
        L.append("本次巡检未记入任何变化。")
    else:
        L.append(f"**合计：新增 {tot_a} · 下线 {tot_r} · 字段变更 {tot_c}**")
    L.append("")

    for r in rounds or []:
        smp = r.get("samples") or []
        if not smp:
            continue
        L.append(f"## {r['net']}")
        for s in smp[:max_items]:
            mark = {"a": "🆕", "r": "🔻", "c": "✏️"}.get(s.get("k"), "·")
            L.append(f"- {mark} {s.get('n') or ''}"
                     + (f"〔{s['ty']}〕" if s.get("ty") else ""))
        L.append("")
    for n in (extra_notes or []):
        L.append(f"> {n}")
    if repo:
        L.append("")
        L.append(f"页面：https://{repo.split('/')[0]}.github.io/{repo.split('/')[-1]}/")
    return "\n".join(L)


_NOTE_CN = {"degraded": "数据量异常，已冻结上一版（等下一轮复采）",
            "rebound": "检测到基线回弹，本轮不计入变化",
            "schema": "字段结构变更，仅重建基线、不通知",
            "baseline": "首版基线建立",
            "resync": "连续偏低后重同步基线",
            "collect-error": "本轮采集失败，沿用上一版快照",
            "snapshot-fallback": "沿用仓库快照渲染"}


#: 这些 note 只是「本轮没有可比基线」，属于正常过程 ——
#: 不该因为它们的存在就把「零变化不推」的静默打破（否则首版基线当天必发一条废话）。
_QUIET_NOTES = ("baseline", "snapshot-fallback")


def has_change(rounds):
    """本轮**是否值得推**。

    🔴 没有这道闸门时的一个真事故形态：正文首行是 ``# ... {day}``，
       日期每天不同 ⇒ 内容指纹每天都不同 ⇒ **零变化的日子也会照发一条**
       （去重对它完全无效，因为「不是同一份内容」）。
       天天推「无变化」的结果是人把通知静音，然后真变化也看不见 ——
       这正是参考项目里那条「一次变化只推一条」要防的事。

    异常态（降级 / 结构变更 / 采集失败）**算变化**：
    ``build_body`` 里「本轮未记变更（数据异常已冻结）」与「无变化」在正文里
    长得很像，前者却需要人介入 ⇒ 一律推出去，靠标题的 ``_headline`` 区分。
    """
    for r in rounds or []:
        note = r.get("note")
        if note and note not in _QUIET_NOTES:
            return True
        if r.get("added") or r.get("removed") or r.get("changed"):
            return True
    return False


def notify_change(day, rounds, extra_notes=None, repo="", title=None):
    """巡检末尾调这一个就够了。返回通道结果 dict（未推送时为空）。"""
    if not enabled():
        return {}
    if _env("NOTIFY_ALWAYS", "0") != "1" and not has_change(rounds):
        log("本轮四网均无变化，跳过推送（想每天报平安就设 NOTIFY_ALWAYS=1）")
        return {}
    body = build_body(day, rounds, extra_notes, repo=repo)
    t = title or f"资费巡检 {day}：" + _headline(rounds)
    return send_all(t, body)


def _headline(rounds):
    a = sum(r.get("added", 0) for r in rounds or [] if not r.get("note"))
    rm = sum(r.get("removed", 0) for r in rounds or [] if not r.get("note"))
    notes = [r["net"] for r in rounds or [] if r.get("note")]
    if a or rm:
        return f"新增 {a} · 下线 {rm}"
    if notes:
        return "数据异常已冻结（%s）" % "、".join(notes)
    return "无变化"


# ════════════════════════════════════════════════════════════════════════
#  自测：把「推送该不该发」这条判据钉死（改了 has_change 必跑）
# ════════════════════════════════════════════════════════════════════════
def _selftest():
    fails = []

    def ck(name, got, want):
        if got != want:
            fails.append(f"{name}: 得到 {got!r}，期望 {want!r}")

    R = lambda **kw: dict({"net": "联通", "n": 100}, **kw)     # noqa: E731

    # ① 零变化 → 不推（这是本闸门存在的唯一理由）
    ck("零变化不推", has_change([R(added=0, removed=0, changed=0)]), False)
    ck("空列表不推", has_change([]), False)
    ck("None 不推", has_change(None), False)
    # ② 任一项非零 → 推
    ck("有新增要推", has_change([R(added=1), R()]), True)
    ck("有下线要推", has_change([R(removed=1)]), True)
    ck("有字段变更要推", has_change([R(changed=1)]), True)
    # ③ 异常态必须推（哪怕三个数字全是 0）
    for n in ("degraded", "schema", "rebound", "collect-error", "resync"):
        ck(f"异常态 {n} 要推", has_change([R(added=0, removed=0, changed=0, note=n)]), True)
    # ④ 但「只是还没有基线」不算异常 —— 否则首版基线当天必发一条废话
    for n in _QUIET_NOTES:
        ck(f"安静态 {n} 不推", has_change([R(note=n)]), False)
    # ⑤ 标题、转义：正文里的 < 必须被转义（上游资费文案带真 HTML）
    ck("标题·有变化", _headline([R(added=3, removed=2)]), "新增 3 · 下线 2")
    ck("标题·异常", _headline([R(note="degraded")]), "数据异常已冻结（联通）")
    ck("标题·无变化", _headline([R()]), "无变化")
    ck("HTML 转义", "&lt;b&gt;" in _md_to_html("<b>粗</b>"), True)
    ck("纯文本去标记", _md_to_text("# 标题\n- **粗**\n").strip().startswith("标题"), True)

    if fails:
        print("推送自测失败 %d 项：" % len(fails))
        for x in fails:
            print("  ✗", x)
        return 1
    print("推送自测通过（零变化闸门 / 异常态必推 / 安静态 / 标题 / 转义）")
    return 0


# ════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        sys.exit(_selftest())
    if "--check" in argv:
        print("可用通道：", "、".join(available()) or "（无，推送会自动跳过）")
        print("总开关 enabled() =", enabled())
        sys.exit(0)
    if "--test" in argv:
        ch = available()
        if not ch:
            print("没有任何通道配置了凭据。")
            print("  邮件：SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / MAIL_TO")
            print("  PushPlus：PUSHPLUS_TOKEN（可加 PUSHPLUS_TOPIC）")
            sys.exit(1)
        print("将向以下通道发送自检消息：", "、".join(ch))
        res = send_all(
            "资费监控 推送自检",
            "# 资费监控 · 推送自检\n\n- 这条消息来自 `python notify.py --test`\n"
            "- 收到即表示通道可用\n- **加粗**、`行内码`、[链接](https://example.com) "
            "三种写法应都能正常显示\n",
            dedup=False)
        for k, v in (res or {}).items():
            print(f"  {k}: {v}")
        sys.exit(0 if any(v == "ok" for v in (res or {}).values()) else 1)
    print(__doc__)
