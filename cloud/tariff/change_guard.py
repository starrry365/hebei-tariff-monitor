# -*- coding: utf-8 -*-
"""变更可信化护栏 —— 让「新增 / 下线 / 字段变更」这三个数字经得起追问。

为什么单独一个模块：
  移动走 ``tariff_monitor.main()``、其余三网走 ``net_round()``，两条路**都**要在
  同一条护栏上跑。抄成两份的那天，症状是「某网静默地不再保护数据」，
  而日志里一切正常、页面也照常更新 —— 这类失效最难发现。

本模块只做「上游不可信」的防御，不含任何业务判据：

  ① 逐条日期核验   假下架 = 下线日还没到；假新增 = 上线日早于上一轮基线。
  ② 身份漂移       同一条资费换了栏目/板块（身份键里含栏目名）⇒ 被记成
                   「下线一条 + 新增一条」。它不是业务变化，是身份键不稳定。
  ③ 指标抖动       本轮的新增正好是上一轮的下线（增删互换）⇒ 基线回弹，丢弃。
  ④ 结构变更       字段结构变了（我们改了采集字段 / 上游加了字段）⇒ 重建基线、
                   **不通知** —— 否则一次改动会炸出上千条「字段变更」。
  ⑤ 降级状态机     数据量偏低连续 N 轮才接受新数据重建基线；只低一轮就冻结旧数据。

★★ 全部判据遵循同一条原则：**判不出来就保留**。
   护栏要摘掉的是「明显是假的」那一批，不是「拿不准的」那一批 ——
   后者会让真变化静默消失，而**少报比多报严重得多**（多报会被人追问，
   少报没人会知道）。

历史教训（本仓库实测）：
  · 联通 2026-09-25 报告「新增 120 · 下线 123」，按报备编号重算只有 +5 / −8：
    115 条是同一批业务从「营销活动/促销」挪到「加装包/流量包」，身份键里
    带着栏目名，同一条就被记了两次（②）。
  · 参考同类项目时发现其判据用的是「分类被限流降级 ⇒ 下轮补回」这一层
    （①④⑤），而本项目当时只有单轮 0.6 的条数护栏，没有「连续」与「逐条」的概念。
"""
import datetime
import json
import os
import re

# ── 开关（全部可用环境变量临时关闭，便于故障时逐个排除）──────────────────
# 🔴 默认全开。关掉任何一条之前先问：你是在排除护栏的干扰，还是在掩盖一个真变化？
ON = os.getenv("GUARD", "1").strip() != "0"
REJECT_FUTURE_OFFLINE = os.getenv("REJECT_FUTURE_OFFLINE", "1").strip() != "0"   # ①假下架
REJECT_STALE_ONLINE = os.getenv("REJECT_STALE_ONLINE", "1").strip() != "0"       # ①假新增
DETECT_RELOCATE = os.getenv("DETECT_RELOCATE", "1").strip() != "0"               # ②身份漂移
DETECT_REBOUND = os.getenv("DETECT_REBOUND", "1").strip() != "0"                 # ③抖动
REBUILD_ON_SCHEMA = os.getenv("REBUILD_ON_SCHEMA", "1").strip() != "0"           # ④结构
# ⑤降级：单轮跌破这个比例先冻结旧数据，连续 N 轮才接受新数据
DEGRADE_RATIO = float(os.getenv("DEGRADE_RATIO") or "0.6")
DEGRADE_ACCEPT_ROUNDS = int(os.getenv("DEGRADE_ACCEPT_ROUNDS") or "3")
# ①拿不到上一轮基线日期时的兜底阈值：上线日早于今日 N 天以上才算补录
STALE_ONLINE_DAYS = int(os.getenv("STALE_ONLINE_DAYS") or "2")
# ③回弹判定要多少条才认（样本太小不下结论，2 条互换天天都可能发生）
REBOUND_MIN = int(os.getenv("REBOUND_MIN") or "3")
# ③回弹判定的重合率阈值
REBOUND_OVERLAP = float(os.getenv("REBOUND_OVERLAP") or "0.6")

# 「停售桶」的中文名 —— 这一族里的条目**分类本身就是「已下架」这个状态**
# （联通的 99 类）。一条资费从在售挪进停售桶是**真实业务事件**，不是身份漂移，
# 所以 ② 那条要把这种移动排除掉，否则「本日有 N 条资费下架」会被静默吃掉。
STOPPED_TYS = ("停售套餐",)


# ════════════════════════════════════════════════════════════════════════
#  日期解析：上游日期格式不统一，判不出返回 None（**不猜**）
# ════════════════════════════════════════════════════════════════════════
_DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")


def parse_day(v):
    """上游日期 → ``datetime.date``；判不出返回 ``None``。

    覆盖实测到的四种写法（四网各不相同）：
      ``20260926``（移动/联通/电信的紧凑式）、``2026-09-26 00:00:00``（带时刻）、
      ``2026年9月26日``、``2026/9/26`` / ``2026.9.26``，以及更长的紧凑式
      ``20260926000000``（取前 8 位）。

    🔴 返回 None 就是「判不出来」，调用方必须**保留**该条目 —— 不要在这里
       兜一个「今天就当过期」之类的默认值：那等于用猜测去删真实数据。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 8:          # 20260926 / 20260926000000
        s8 = s[:8]
        try:
            d = datetime.date(int(s8[:4]), int(s8[4:6]), int(s8[6:8]))
        except ValueError:
            return None
        return d if 1990 <= d.year <= 2100 else None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return d if 1990 <= d.year <= 2100 else None


def d8(today):
    """``2026-09-26`` / ``20260926`` / ``date`` → ``20260926``；判不出返回 ``""``。"""
    if isinstance(today, datetime.date):
        return today.strftime("%Y%m%d")
    s = str(today or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return s
    d = parse_day(s)
    return d.strftime("%Y%m%d") if d else ""


def _day_of_ts(ts):
    """抓取时刻字符串 → date（取日期部分）。``2026-09-25 08:18:28`` → 2026-09-25。"""
    return parse_day(str(ts or "")[:10])


# ════════════════════════════════════════════════════════════════════════
#  原子写：先写 .tmp 再 os.replace，绝不让「写了一半」的文件成为新基线
# ════════════════════════════════════════════════════════════════════════
def atomic_write_bytes(path, data):
    """把字节原子地写进 ``path``（同目录 .tmp → ``os.replace``）。

    🔴 为什么必须这样写：CI 有 ``timeout-minutes: 20``，进程随时可能被 kill。
       直接 ``open(path,'wb').write()`` 被打断会留下**半个文件**，而它长得
       和正常文件一样 —— 下一轮读它当基线，要么解压失败，要么 diff 出一整批
       假变化。``os.replace`` 在同一文件系统内是原子的：要么旧文件，要么新文件，
       不存在中间态。
    """
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def atomic_write_text(path, text, newline=None):
    """文本版原子写。``newline="\\n"`` 可锁死行尾（跨平台交替提交的老坑）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def read_json(path, default=None):
    """读 JSON，缺失/损坏一律返回 default（不抛）—— 状态文件坏了不该打断巡检。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj, indent=1):
    """写 JSON（原子 + 锁死 LF）。

    🔴 ``newline="\\n"`` 必须显式给：Windows 本地跑会把 ``\\n`` 翻成 ``\\r\\n``，
       与 CI（Linux，写 LF）交替提交时整个文件每行都显示为「已修改」，
       真正的变更淹没在行尾噪音里。history.json 当年就踩过这个坑。
    """
    txt = json.dumps(obj, ensure_ascii=False, indent=indent)
    return atomic_write_text(path, txt, newline="\n")


# ════════════════════════════════════════════════════════════════════════
#  ④ 字段结构签名：结构变了就重建基线，不通知
# ════════════════════════════════════════════════════════════════════════
def schema_sig(obj, fields, sample=800, min_ratio=0.01):
    """本网「有值的比对字段」集合签名。

    ★ 只看 ``fields``（= 真正参与 diff 的那批 KEY_FIELDS）里**有没有值**，
      不看上游的全部键 —— 上游给每条资费挂的键本来就参差不齐（联通在售条目
      有 type3Name、停售条目没有），拿全键集合作签名会天天「变」。
    ★ ``min_ratio``：某字段要有 ≥1% 的采样条目有值才算「本网有这个字段」。
      🔴 这条阈值是必须的：像 ``brandwidth``（宽带）这种字段，移动侧平时一条
        都不填，某天有一条宽带资费带上它 —— 零阈值签名立刻变化，于是**白白
        重建一次基线、静默跳过一天的真实变更**。1% 的噪声过滤把「偶发一条」
        和「上游开始普遍填充」区分开。
    ★ 采样前 ``sample`` 条：这是**结构**判据不是数量判据，抽样足够；
      全量遍历在大快照上纯属浪费。

    返回 ``tuple``（有序，便于比对与日志打印）。
    """
    cnt, n = {}, 0
    for entries in _iter_entries(obj):
        for e in entries:
            n += 1
            if n > sample:
                break
            for k in fields:
                if str(e.get(k) or "").strip():
                    cnt[k] = cnt.get(k, 0) + 1
        if n > sample:
            break
    if not n:
        return ()
    bar = max(1, int(n * min_ratio))
    return tuple(sorted(k for k, v in cnt.items() if v >= bar))


def _iter_entries(obj):
    """遍历数据源对象里的条目 —— 兼容 ``{groups:[{entries:[]}]}`` 与 ``{entries:[]}``。"""
    if not isinstance(obj, dict):
        return
    for g in obj.get("groups") or []:
        yield g.get("entries") or []
    if obj.get("entries"):
        yield obj["entries"]


# ════════════════════════════════════════════════════════════════════════
#  ① ② 逐条核验 + 身份漂移
# ════════════════════════════════════════════════════════════════════════
def _sid(row):
    """稳定业务身份：报备编号。**绝不**用价格/文案字段（它们本来就会变）。"""
    return str(row.get("_reportNo") or "").strip()


def _nm(row):
    return str(row.get("_name") or row.get("_tname") or "").strip()


def _ident(row):
    return _sid(row) or _nm(row)


def _idx_by(rows, fn):
    out = {}
    for k, r in (rows or {}).items():
        v = fn(r)
        if v:
            out.setdefault(v, []).append(k)
    return out


def _stopped(key, row):
    """该条目是否在「停售桶」里。用**两个信号**判，不靠单一字段：
      · 身份键的第一段就是 type2（``index_rows`` 的键格式 ``type2|attr|name``）
        —— ``99`` 是联通的停售套餐桶；
      · 分类中文名落在 ``STOPPED_TYS`` 里。
    🔴 为什么要两个：``type2Name`` 是采集侧给的，万一某天没给（那时 ``_ty`` 会退成
       ``"?"``），只看中文名就会把「转入停售目录」判成「同侧移动 = 身份漂移」，
      于是一次真实的批量下架被静默吃掉。键里的 type2 是**结构信息**，不会缺。
    """
    t2 = str(key or "").split("|")[0].strip()
    return t2 == "99" or str(row.get("_ty") or "").strip() in STOPPED_TYS


def audit_diff(added, removed, changed, old_rows, new_rows, *,
               today, base_day="", old2_rows=None, prev_rec=None,
               log=None):
    """对一轮 diff 做逐条核验，返回**可信的**变化集合。

    入参：
      ``added`` / ``removed`` / ``changed``   ``diff_rows()`` 的原始输出
        （added / removed 是键列表；changed 是 ``[(键, {字段:(旧,新)})]``）
      ``old_rows`` / ``new_rows``             ``index_rows()`` 的结果（键 → 行）
      ``old2_rows``                           上**上**一轮的 ``index_rows()``，没有就 None
      ``prev_rec``                            history.json 里该网上一条记录（含 a/r）
      ``base_day``                            上一轮快照的日期（8 位或带横线都行）
      ``today``                               今天（8 位或带横线都行）

    返回 dict：
      ``added`` 真新增 · ``removed`` 真下线 · ``changed`` 字段变更
      ``restored`` 补录（上线日早于基线，从 added 移出）
      ``fake_removed`` 假下架（下线日还没到，从 removed 移出）
      ``relocated`` 身份漂移对 ``[(旧键, 新键)]``（同侧移动，两侧都不算）
      ``state_moved`` 状态桶迁移 ``[(旧键, 新键, "to_stopped"/"from_stopped")]``
        （在售↔停售；只按一个方向记一次，避免同一条被记成「新增+下线」两条）
      ``rebound`` 是否判定为基线回弹 · ``suspected`` 回弹证据文案列表
      ``notes`` 给人看的核验摘要（写进报告/日志）
    """
    log = log or (lambda m: None)
    today_d = parse_day(today)
    base_d = _day_of_ts(base_day) if base_day else None
    if base_d is None:
        base_d = parse_day(base_day)        # 允许直接给 8 位日期

    added = list(added or [])
    removed = list(removed or [])
    changed = list(changed or [])
    notes = []
    out = {"added": added, "removed": removed, "changed": changed,
           "restored": [], "fake_removed": [], "relocated": [], "state_moved": [],
           "rebound": False, "suspected": [], "notes": notes}
    # 日期核验要豁免的键（见下：跨停售桶的迁移不能按「上下线日期」再判一次）
    date_exempt_rm, date_exempt_ad = set(), set()

    # ── ② 身份漂移 / 状态桶迁移：同一条资费在新库里**还在**，只是身份键变了 ──
    # 判据必须是「稳定业务编号」而不是名字：两条不同资费可能同名
    # （「流量包」这种名字在四网里各有一堆），按名字合并会把两条真资费并成一条。
    # 名字只在**两侧都唯一**时作为退路（电信/广电的条目不一定有报备编号）。
    #
    # 配对之后分三种，处理**完全不同**：
    #   同侧（都在售 / 都停售）   ⇒ 纯身份键漂移：既不算新增也不算下线
    #   在售 → 停售               ⇒ **真实下架**：算 1 条下线，新键丢弃
    #   停售 → 在售               ⇒ **重新上架**：算 1 条新增，旧键丢弃
    # 🔴 第三种/第二种区分不能省。实测联通 2026-09-25 那 115 对里 110 对是
    #    「套餐 → 停售套餐」，逐条核过原始 entries：09-24 只在在售目录、09-25 只在
    #    停售目录，**两轮都没有一条同时挂在两个桶里**（跨桶重复 0）⇒ 是真下架，
    #    不是采集侧的桶归属抖动。把它当漂移抹掉，就等于把一次 110 条的批量下架
    #    静默吃掉；反过来把它当新增+下线各一条，就是那个 +120/−123 的老毛病。
    if ON and DETECT_RELOCATE and (added or removed):
        by_sid_new = _idx_by(new_rows, _sid)
        by_nm_new = _idx_by(new_rows, _nm)
        by_nm_old = _idx_by(old_rows, _nm)
        added_set = set(added)
        new_used, not_count_rm, not_count_ad = set(), set(), set()
        for k in removed:
            r = old_rows.get(k)
            if not r:
                continue
            cands = []
            s = _sid(r)
            if s and s in by_sid_new:
                # 🔴 候选**限定在 added 里**。同一报备编号在库里可能有多条
                #    （同一条资费挂多个栏目），若只要求「新库有这条编号」，
                #    会配到那条**从未变化**的副本上：真正的 added 键留在原地继续
                #    冒充「新增」，而 removed 键被错配成漂移 —— 实测联通一轮里
                #    115 对只认出 5 对，就是这个原因（正确率 4%）。
                cands = [kk for kk in by_sid_new[s]
                         if kk in added_set and kk not in new_used]
            elif not s:
                # 名字退路：仅当这条名字在**新旧两侧都唯一**时才敢用 ——
                # 「流量包」这种名字在四网里各有一堆，按名字配对会把两条真资费并成一条。
                n = _nm(r)
                if (n and len(by_nm_old.get(n, [])) == 1 and
                        len(by_nm_new.get(n, [])) == 1):
                    cands = [kk for kk in by_nm_new[n]
                             if kk in added_set and kk not in new_used]
            if not cands:
                continue
            nk = cands[0]
            new_used.add(nk)
            o_st, n_st = _stopped(k, r), _stopped(nk, new_rows.get(nk) or {})
            if o_st == n_st:
                out["relocated"].append((k, nk))
                not_count_rm.add(k)
                not_count_ad.add(nk)
            elif not o_st and n_st:
                out["state_moved"].append((k, nk, "to_stopped"))
                not_count_ad.add(nk)          # 只算下线；新键不是「新增」
                date_exempt_rm.add(k)         # 桶归属就是下架证据，日期不再判
            else:
                out["state_moved"].append((k, nk, "from_stopped"))
                not_count_rm.add(k)           # 只算新增；旧键不是「下线」
                date_exempt_ad.add(nk)        # 重新上架同理
        if not_count_rm or not_count_ad:
            out["removed"] = [k for k in removed if k not in not_count_rm]
            out["added"] = [k for k in added if k not in not_count_ad]
        down = sum(1 for _, _, d in out["state_moved"] if d == "to_stopped")
        up = len(out["state_moved"]) - down
        if out["relocated"]:
            notes.append("身份漂移 %d 条：同一条资费换了栏目/板块（身份键含栏目名），"
                         "不算新增也不算下线" % len(out["relocated"]))
            log("   [护栏] 身份漂移 %d 条（同编号换了栏目/板块），不计入新增/下线：%s"
                % (len(out["relocated"]),
                   "、".join(_nm(old_rows.get(a) or {}) or a for a, _ in out["relocated"][:3])))
        if down:
            notes.append("转入停售目录 %d 条：上游把它挪进了「停售套餐」桶（一级分类 99），"
                         "**计为真实下架**、其新键不另算新增" % down)
            log("   [护栏] 转入停售目录 %d 条，计为真实下架（同一编号不再重复算新增）" % down)
        if up:
            notes.append("从停售目录转回在售 %d 条，计为新增" % up)

    # ── ①a 假下架：下线日还没到，却被判成下架 ⇒ 是「没采到」，不是「过期」──
    # 🔴 豁免「转入停售目录」那批（``date_exempt_rm``）：它们的下架证据是
    #    **桶归属变了**，不是日期。联通那一桶的 endDate 实测**一条都没过期**
    #    （见 state_of 注释）—— 拿日期去判它们，会把 110 条真下架全部判成假下架。
    if ON and REJECT_FUTURE_OFFLINE and out["removed"]:
        keep, fake, unknown, exempt = [], [], 0, 0
        for k in out["removed"]:
            if k in date_exempt_rm:
                exempt += 1
                keep.append(k)
                continue
            r = old_rows.get(k) or {}
            d = parse_day(r.get("offineDay"))
            if d is None:
                unknown += 1
                keep.append(k)                       # 判不出来 → 保留
            elif today_d is not None and d >= today_d:
                fake.append(k)
            else:
                keep.append(k)
        if fake:
            out["removed"] = keep
            out["fake_removed"] = fake
            notes.append("假下架 %d 条：下线日期尚未到期（≥ 今天），判为漏采而非下架"
                         % len(fake))
            log("   [护栏] 假下架 %d 条（下线日 ≥ 今天，其实还在售）：%s%s"
                % (len(fake), "、".join((_nm(old_rows.get(k) or {}) or k)[:24] for k in fake[:3]),
                   "…" if len(fake) > 3 else ""))
        if exempt:
            log(f"   [护栏] {exempt} 条走「转入停售目录」判据，不按下线日期复核")

    # ── ①b 假新增：上线日早于上一轮基线 ⇒ 上一轮就该采到，本轮是补录 ─────
    # 🔴 同理豁免「从停售转回在售」那批：重新上架是真事件，日期是旧的。
    if ON and REJECT_STALE_ONLINE and out["added"]:
        keep, restored, unknown = [], [], 0
        for k in out["added"]:
            if k in date_exempt_ad:
                keep.append(k)
                continue
            r = new_rows.get(k) or {}
            d = parse_day(r.get("onlineDay"))
            if d is None:
                unknown += 1
                keep.append(k)                       # 判不出来 → 按新增保留
            elif base_d is not None:
                # 🔴 必须用**严格小于**：``onlineDay == 基线日`` 的那批里既有
                #    「上一轮漏采、本轮补回」，也有「基线当天下的真新增」。
                #    用 <= 会把后者误判成补录 —— 实测移动那批保定系资费正落在边界上。
                (restored if d < base_d else keep).append(k)
            else:
                (restored if (today_d and (today_d - d).days > STALE_ONLINE_DAYS)
                 else keep).append(k)
        if restored:
            out["added"] = keep
            out["restored"] = restored
            basis = (f"上线日早于上一轮基线 {base_d}" if base_d
                     else f"上线日早于今日 {STALE_ONLINE_DAYS} 天以上")
            notes.append("补录 %d 条：%s —— 属漏采补回，不计新增、不推送" % (len(restored), basis))
            log("   [护栏] 补录 %d 条（%s，不是新上架）：%s%s"
                % (len(restored), basis,
                   "、".join((_nm(new_rows.get(k) or {}) or k)[:24] for k in restored[:3]),
                   "…" if len(restored) > 3 else ""))

    # ── ③ 基线回弹：本轮新增 == 上一轮下线（增删互换）⇒ 基线没落地 ────────
    if ON and DETECT_REBOUND:
        out["rebound"], out["suspected"] = _detect_rebound(
            out, old_rows, new_rows, old2_rows, prev_rec)
        if out["rebound"]:
            notes.append("基线回弹：本轮增删与上一轮整体互换 —— 判定为基线/上游回弹，"
                         "本轮不计变化、不推送")
            log("   [护栏] 基线回弹 —— %s，本轮不计变化" % "；".join(out["suspected"]))

    out["changed"] = changed
    return out


def _detect_rebound(out, old_rows, new_rows, old2_rows, prev_rec):
    """③ 回弹判定。返回 ``(bool, 证据文案列表)``。

    ★ 判据是「**两个方向都一致地翻转**」，不是「有一个方向像」——
      只匹配一边是常见的正常churn（一批下线的同时另一批上线），
      两边同时整体互换才叫回弹。

    ★★ 注意 ③ 与 ② 的分工：栏目搬家时**业务编号根本没变**，
       身份集合完全一样、看不出任何翻转 —— 那种变化由 ②（身份漂移）拦，
       不归 ③。这里管的是「一整批业务真的消失又真的回来」。

    两条判据，**任一成立即认定回弹**：
      甲（强）身份级：上一轮新增的那批，正好是本轮下线的那批（反向亦然），
                     两侧重合率都 ≥ ``REBOUND_OVERLAP``。
      乙（弱）计数级：上一轮记录 ``a=N·r=M``，本轮 ``a=M·r=N``（完全互换），
                     且条数 ≥ ``REBOUND_MIN``。
                     —— 拿不到上上轮快照时的兜底（引用同类项目的判据）。
    """
    ev = []
    cur_add = {_ident(new_rows.get(k) or {}) for k in out["added"]} - {""}
    cur_rm = {_ident(old_rows.get(k) or {}) for k in out["removed"]} - {""}

    def _dir_ok(x, y):
        """一个方向是否「一致地翻转」：两边都空＝无事发生也算一致。"""
        if not x and not y:
            return True, ""
        m = min(len(x), len(y))
        if m < REBOUND_MIN:
            return False, ""
        ov = len(x & y) / float(m)
        return (ov >= REBOUND_OVERLAP,
                "重合 %d/%d（%.0f%%）" % (len(x & y), m, ov * 100))

    # 甲：身份级（需要上上轮快照，且它真的非空）
    #   🔴 ``old2_rows`` 为空**不算证据**：那说明上上轮没有基线（比如上一轮才是
    #      首版基线），此时「本轮整批下线」是一批真实的删除，不是回弹。
    if old2_rows:
        ids2 = {_ident(r or {}) for r in old2_rows.values()} - {""}
        ids1 = {_ident(r or {}) for r in old_rows.values()} - {""}
        ad_prev, rm_prev = ids1 - ids2, ids2 - ids1
        ok1, t1 = _dir_ok(ad_prev, cur_rm)
        ok2, t2 = _dir_ok(rm_prev, cur_add)
        if ok1 and ok2 and (ad_prev or rm_prev):
            if t1:
                ev.append("上轮新增↔本轮下线 " + t1)
            if t2:
                ev.append("上轮下线↔本轮新增 " + t2)
            return True, ev or ["上轮与本轮增删完全对空"]

    # 乙：计数级（需要上一条 history 记录）
    if isinstance(prev_rec, dict):
        pa, pr = int(prev_rec.get("a") or 0), int(prev_rec.get("r") or 0)
        ca, cr = len(out["added"]), len(out["removed"])
        if min(ca, cr) >= REBOUND_MIN and pa == cr and pr == ca and (ca + cr) > 0:
            return True, ["计数互换：上轮 a=%d/r=%d，本轮 a=%d/r=%d" % (pa, pr, ca, cr)]
    return False, []


# ════════════════════════════════════════════════════════════════════════
#  ⑤ 降级状态机：连续 N 轮偏低才接受新数据重建基线
# ════════════════════════════════════════════════════════════════════════
def degrade_step(state, code, n, n_old, stamp=""):
    """比对「本轮条数 vs 上一轮条数」，返回 ``(动作, 新状态)``。

    动作：
      ``"ok"``     正常，清零计数。
      ``"hold"``   本轮偏低但还没连续够 ``DEGRADE_ACCEPT_ROUNDS`` 轮
                   ⇒ **保留旧数据**（不写快照、不出报告），计数 +1。
      ``"accept"`` 连续偏低够轮数且本轮非 0 ⇒ 认定「源站现状如此」，
                   接受新数据并重建基线（本轮按基线处理，不报变更）。

    🔴 为什么需要「连续」而不是单轮一刀切：
      单轮阈值只挡得住**骤降**。上游小幅限流（河南同类项目实测 3885→2403，
      61.9%，没跌破 60% 的老阈值）会一路放行，把「本轮没采到」记成真下架。
      反过来，**永远冻结旧数据更危险** —— 页面看着正常，实际是过期数据，
      而且没有任何一处会报警。所以必须有一个「认清现实」的出口。
    """
    st = dict(state or {})
    rec = st.get(code) if isinstance(st.get(code), dict) else {}
    rounds = int(rec.get("rounds") or 0)
    below = n_old > 0 and n < n_old * DEGRADE_RATIO

    if not below:
        st[code] = {"rounds": 0, "last_ok": stamp, "last_n": n, "baseline_n": n}
        return "ok", st

    rounds += 1
    if n > 0 and rounds >= DEGRADE_ACCEPT_ROUNDS:
        st[code] = {"rounds": 0, "accepted": stamp, "from": n_old, "to": n,
                    "last_n": n, "baseline_n": n}
        return "accept", st

    st[code] = {"rounds": rounds, "last_new": n, "last_old": n_old,
                "updated": stamp, "need": DEGRADE_ACCEPT_ROUNDS,
                "last_n": n, "baseline_n": n_old}
    return "hold", st


# ════════════════════════════════════════════════════════════════════════
#  自测：判据本身就是代码，改错了不会报错，只会静默少一批 —— 必须能自证
# ════════════════════════════════════════════════════════════════════════
def _selftest():
    import datetime as _dt
    fails = []

    def ck(name, got, want):
        if got != want:
            fails.append("%s: 期望 %r，实得 %r" % (name, want, got))

    ck("parse_day 紧凑", parse_day("20260926"), _dt.date(2026, 9, 26))
    ck("parse_day 带横线", parse_day("2026-09-26"), _dt.date(2026, 9, 26))
    ck("parse_day 中文", parse_day("2026年9月26日"), _dt.date(2026, 9, 26))
    ck("parse_day 斜杠", parse_day("2026/9/26"), _dt.date(2026, 9, 26))
    ck("parse_day 长紧凑", parse_day("20260926000000"), _dt.date(2026, 9, 26))
    ck("parse_day 空", parse_day(""), None)
    ck("parse_day 垃圾", parse_day("长期有效"), None)
    ck("parse_day 非法月", parse_day("20261326"), None)
    ck("d8", d8("2026-09-26"), "20260926")

    def row(name, rn="", on="", off="", ty="套餐"):
        return {"_name": name, "_reportNo": rn, "_ty": ty,
                "onlineDay": on, "offineDay": off}

    # ① 假下架：下线日 2045 年（明显还在售）
    old = {"k1": row("A", "R1", off="20451231"), "k2": row("B", "R2", off="20240101")}
    new = {}
    r = audit_diff(["k1", "k2"], ["k1", "k2"], [], old, new,
                   today="20260926", base_day="20260925")
    ck("假下架被摘", r["fake_removed"], ["k1"])
    ck("真下架保留", r["removed"], ["k2"])

    # ① 假新增：上线日早于上一轮基线 ⇒ 补录
    r = audit_diff(["n1", "n2"], [], [], {},
                   {"n1": row("C", "R3", on="20250101"),
                    "n2": row("D", "R4", on="20260925")},
                   today="20260926", base_day="20260925")
    ck("补录被摘", r["restored"], ["n1"])
    ck("边界日(==基线)按真新增保留", r["added"], ["n2"])

    # ① 判不出来就保留
    r = audit_diff([], ["k1"], [], {"k1": row("E", "R5", off="")}, {},
                   today="20260926", base_day="20260925")
    ck("无日期→保留", r["removed"], ["k1"])

    # ② 身份漂移：同一编号换了栏目 ⇒ 不算新增/下线
    old = {"1|2|A": row("A", "R9")}
    new = {"3|2|A": row("A", "R9")}
    r = audit_diff(["3|2|A"], ["1|2|A"], [], old, new,
                   today="20260926", base_day="20260925")
    ck("漂移对已识别", len(r["relocated"]), 1)
    ck("漂移不计新增", r["added"], [])
    ck("漂移不计下线", r["removed"], [])

    # ② 跨停售桶：在售 → 停售 = **真实下架**（算下线，新键不另算新增）
    #    （实测依据：联通那 110 条在 09-24 只在在售目录、09-25 只在停售目录，
    #      两轮都无跨桶重复 ⇒ 是上游真的挪了，不是采集侧的桶归属抖动）
    old = {"1|2|A": row("A", "R9", ty="套餐")}
    new = {"99|2|A": row("A", "R9", ty="停售套餐")}
    r = audit_diff(["99|2|A"], ["1|2|A"], [], old, new,
                   today="20260926", base_day="20260925")
    ck("在售→停售不算漂移", r["relocated"], [])
    ck("在售→停售新键不冒充新增", r["added"], [])
    ck("在售→停售保留为下线", r["removed"], ["1|2|A"])
    ck("在售→停售记为状态迁移", [d for _, _, d in r["state_moved"]], ["to_stopped"])
    # 反向：停售 → 在售 = 重新上架（算新增，旧键不另算下线）
    old = {"99|2|B": row("B", "R8", ty="停售套餐")}
    new = {"2|2|B": row("B", "R8", ty="加装包")}
    r = audit_diff(["2|2|B"], ["99|2|B"], [], old, new,
                   today="20260926", base_day="20260925")
    ck("停售→在售算新增", r["added"], ["2|2|B"])
    ck("停售→在售不算下线", r["removed"], [])
    # 🔴 停售桶的下架证据是**桶归属**，不是日期：那批 endDate 实测一条都没过期，
    #    拿日期复核会把真下架全判成假下架（豁免必须生效）
    old = {"1|2|C": row("C", "R7", ty="套餐", off="20991231")}
    new = {"99|2|C": row("C", "R7", ty="停售套餐", off="20991231")}
    r = audit_diff(["99|2|C"], ["1|2|C"], [], old, new,
                   today="20260926", base_day="20260925")
    ck("转停售时日期核验须豁免", (r["removed"], len(r["fake_removed"])),
       (["1|2|C"], 0))

    # ③ 回弹（身份级）：上轮新增的那批，本轮原样下线；上轮下线的那批，本轮原样回来
    o2 = {"p1": row("P", "RP1"), "p2": row("P", "RP2"), "p3": row("P", "RP3")}
    o1 = {"a1": row("X", "RX1"), "a2": row("X", "RX2"), "a3": row("X", "RX3")}
    n3 = {"p1": row("P", "RP1"), "p2": row("P", "RP2"), "p3": row("P", "RP3")}
    r = audit_diff([k for k in n3 if k not in o1], [k for k in o1 if k not in n3],
                   [], o1, n3, today="20260926", base_day="20260925", old2_rows=o2)
    ck("回弹(身份级·双向互换)判定", r["rebound"], True)

    # ③ 只匹配一个方向（一批下线的同时另一批上线）＝正常 churn，不是回弹
    o2 = {"z1": row("Q", "RQ1")}
    o1 = {"a1": row("X", "RX1"), "a2": row("X", "RX2"), "a3": row("X", "RX3")}
    r = audit_diff([], ["a1", "a2", "a3"], [], o1, {},
                   today="20260926", base_day="20260925", old2_rows=o2)
    ck("单方向不判回弹", r["rebound"], False)

    # ③ 上上轮没有基线（空快照）时不下回弹结论 —— 那批是真删除
    o1 = {"a1": row("X", "RX1"), "a2": row("X", "RX2"), "a3": row("X", "RX3")}
    r = audit_diff([], ["a1", "a2", "a3"], [], o1, {},
                   today="20260926", base_day="20260925", old2_rows={})
    ck("无上上轮基线不判回弹", r["rebound"], False)

    # ③ 回弹（计数级兜底）
    r = audit_diff(["x1", "x2", "x3"], ["y1", "y2", "y3"], [], {}, {},
                   today="20260926", base_day="20260925",
                   prev_rec={"a": 3, "r": 3})
    ck("回弹(计数级)判定", r["rebound"], True)

    # ⑤ 降级状态机
    st = {}
    a, st = degrade_step(st, "move", 500, 5000, "t1")
    ck("首次偏低→hold", a, "hold")
    a, st = degrade_step(st, "move", 500, 5000, "t2")
    ck("第二次偏低→hold", a, "hold")
    a, st = degrade_step(st, "move", 500, 5000, "t3")
    ck("第三次偏低→accept", a, "accept")
    ck("接受后计数清零", st["move"]["rounds"], 0)
    a, st = degrade_step(st, "move", 5000, 5000, "t4")
    ck("恢复正常→ok", a, "ok")
    a, st = degrade_step(st, "move", 0, 5000, "t5")
    ck("0 条不接受", a, "hold")

    # ④ 结构签名
    ck("结构签名", schema_sig({"entries": [{"fees": "1", "data": "", "call": "x"}]},
                             ["fees", "data", "call"]), ("call", "fees"))

    if fails:
        print("自测失败 %d 项：" % len(fails))
        for x in fails:
            print("  ✗", x)
        return 1
    print("护栏自测通过（日期解析 / 假下架 / 假新增 / 身份漂移 / 停售桶例外 / "
          "回弹两级 / 降级状态机 / 结构签名）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
