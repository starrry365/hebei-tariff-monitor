# -*- coding: utf-8 -*-
"""把既有的 ``changes/*.md`` 解析成 ``history.json``（页面「变化历史」的数据源）。

为什么需要回填：history.json 是**从今天起**由 ``write_report()`` 逐次追加的，
而 changes/ 里已经躺着前几天的真实变更报告。不回填的话，页面时间线的起点
就是「部署当天」，看起来像「这个站刚建、之前什么都没发生」—— 明明有历史却装作没有。

★ md 是**权威来源**：同 (日期, 网) 的既有记录会被 md 里的数字覆盖 —— md 可能被
  当天更晚的一轮巡检重写过。没有对应 md 的记录（例如基线那轮）不动，不会被抹掉。
★ 幂等：按 (日期, 网) 归并，跑一百遍结果一样。
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CHG = os.path.join(BASE, "changes")
HIST = os.path.join(BASE, "history.json")

# 报告文件名前缀 → 网 code。移动的报告**没有前缀**（历史上就这一家）。
TAG_CODE = {"": "move", "unicom": "unicom", "cbn": "cbn", "ct": "telecom"}
CN_NET = {"move": "河北移动", "unicom": "河北联通", "telecom": "河北电信", "cbn": "中国广电"}

RE_TS = re.compile(r"本次抓取：\s*([\d\-: ]+)")
RE_N = re.compile(r"条目数：\s*\d+\s*→\s*\*\*(\d+)\*\*")
RE_CNT = re.compile(r"新增\s*\*\*(\d+)\*\*\s*·\s*下线\s*\*\*(\d+)\*\*\s*·\s*字段变更\s*\*\*(\d+)\*\*")
RE_H2 = re.compile(r"^##\s+(新增资费|下线/下架资费|关键字段变更)", re.M)
RE_ITEM = re.compile(r"^-\s+\*\*(.+?)\*\*\s*〔(.+?)〕")
# 字段变更明细行（write_report 生成）：`  - 月费：\`39\` → \`29\``。
# 解析成 ch（旧值 → 新值对照），页面变化历史的左旧右新 diff 卡才有数据 ——
# 否则回填出来的历史只能显示「改了哪几个字段」，显示不了改成什么样。
RE_DELTA = re.compile(r"^  - (.+?)：`(.*)` → `(.*)`\s*$")

SNAP_DIR = os.path.join(BASE, "snapshots")
LEN_GZ = len(".json.gz")


def _enrich(rec):
    """把 md 解析出的「仅变更字段」样本升级成**整行**旧→新对照。

    页面的全字段 diff 卡（含上线/下线日等未变信息、变更项高亮）需要整行；
    md 只有变更字段。快照在 git 里留存（KEEP_SNAPSHOTS=60），按名称+类型
    把套餐对到「上一份快照 vs 当天快照」的行上：
    ★ 变更字段的旧值/新值**以 md 为准**（它是运行时权威产物）—— 不用快照
      重算 diff 来核对计数：运行时数字过过护栏（假新增抑制/回弹冻结等），
      原始 diff 对不上是常态而非异常，拿计数做闸门只会把大部分记录拒之门外。
    ★ 未变字段从快照行补全；套餐在快照里找不到（更名/快照被 prune）就整条
      跳过，该样本保持 md 解析的紧凑格式 —— 降级展示好过编造数据。
    """
    try:
        import gzip
        import tariff_monitor as TM
    except Exception as e:
        print("   ! 无法加载 tariff_monitor，跳过富化：%s" % e)
        return None
    prefix = TM.SNAP_PREFIX.get(rec.get("code"))
    if not prefix:
        return None
    day8 = (rec.get("d") or "").replace("-", "")
    if not re.fullmatch(r"\d{8}", day8):
        return None
    fs = sorted(f for f in os.listdir(SNAP_DIR)
                if f.startswith(prefix) and f.endswith(".json.gz"))
    dates = {f[len(prefix):-LEN_GZ]: f for f in fs}
    if day8 not in dates:
        return None                        # 当天快照已不在（被 prune），无从富化
    older = [d for d in sorted(dates) if d < day8]
    if not older:
        return None                        # 首版基线，没有比对对象
    with gzip.open(os.path.join(SNAP_DIR, dates[older[-1]]), "rt", encoding="utf-8") as f:
        old_idx = TM.index_rows(json.load(f))
    with gzip.open(os.path.join(SNAP_DIR, dates[day8]), "rt", encoding="utf-8") as f:
        new_idx = TM.index_rows(json.load(f))
    rev_cn = {v: k for k, v in TM.FIELD_CN.items()}

    def find(ix, nm, ty):
        hit = [v for v in ix.values()
               if (v.get("_name") or v.get("_tname") or "")[:60] == nm]
        byty = [v for v in hit if v.get("_ty") == ty]
        return byty or hit

    def pick(cands, deltas, side):
        """同名行可能不止一条：优先选「变更字段旧/新值与 md 一致」的那条。
        md 里空值写成「—」，比对前归一成空串。"""
        i = 0 if side == "o" else 1
        for cand in cands:
            ok = True
            for ff, do, dn in deltas:
                if not ff:
                    continue
                dv = (do if i == 0 else dn)
                dv = "" if dv == "—" else dv
                cv = str(cand.get(ff, "") or "").replace("\n", " ").strip()[:70]
                if cv != dv:
                    ok = False
                    break
            if ok:
                return cand
        return cands[0] if cands else None

    n_rich = 0
    for s in rec.get("smp", []):
        if s.get("k") != "c" or not s.get("ch"):
            continue
        deltas = [(rev_cn.get(c["f"], ""), str(c.get("o") or ""),
                   str(c.get("n") or "")) for c in s["ch"]]
        o_cands = find(old_idx, s["n"], s.get("ty") or "")
        n_cands = find(new_idx, s["n"], s.get("ty") or "")
        if not o_cands and not n_cands:
            continue
        o_row = pick(o_cands, deltas, "o") if o_cands else {}
        n_row = pick(n_cands, deltas, "n") if n_cands else {}
        rows = []
        for f in TM.DIFF_SHOW_FIELDS:
            flag = 0
            ov = nv = ""
            for ff, do, dn in deltas:
                if ff == f:
                    ov, nv, flag = do, dn, 1
                    break
            if not flag:
                ov = str(o_row.get(f, "") or "").replace("\n", " ").strip()[:48]
                nv = str(n_row.get(f, "") or "").replace("\n", " ").strip()[:48]
                if not ov and not nv:
                    continue
            rows.append([TM.FIELD_CN.get(f, f), ov, nv, flag])
        if rows:
            s["rows"] = rows
            n_rich += 1
    if not n_rich:
        return None                        # 一条都没富化成功，保持原样
    rich = dict(rec)
    return rich


def parse(md_path, tag):
    """解析一份变更报告 → 一条 history 记录（解析不出关键行就返回 None）。"""
    txt = open(md_path, encoding="utf-8").read()
    if "首版基线" in txt and not RE_CNT.search(txt):
        return None                      # 基线报告没有增删改计数，不进时间线
    m_ts, m_n, m_c = RE_TS.search(txt), RE_N.search(txt), RE_CNT.search(txt)
    if not (m_ts and m_n and m_c):
        print("   ! 跳过（关键行缺失）：%s" % os.path.basename(md_path))
        return None
    a, r, c = (int(x) for x in m_c.groups())

    # 按二级标题切段，逐段抽「- **名称** 〔类型〕」；
    # 字段变更段再往下抽「- 字段：`旧` → `新`」明细，挂到最后一条 c 样本上。
    # last_c 在「样本配额已满 / 换了条目」时置 None —— 明细行只能归属
    # 它上面最近的那条 c 样本，挂错对象比丢掉更糟。
    smp, cur, last_c = [], "", None
    for line in txt.split("\n"):
        h = RE_H2.match(line)
        if h:
            cur = h.group(1)
            last_c = None
            continue
        if cur:
            it = RE_ITEM.match(line)
            if it:
                k = {"新增资费": "a", "下线/下架资费": "r", "关键字段变更": "c"}[cur]
                cap = {"a": 6, "r": 4, "c": 120}[k]
                if sum(1 for x in smp if x["k"] == k) < cap:
                    entry = {"n": it.group(1)[:60], "ty": it.group(2), "k": k}
                    if k == "c":
                        entry["f"] = []
                        last_c = entry
                    smp.append(entry)
                else:
                    last_c = None
                continue
            if last_c is not None:
                dm = RE_DELTA.match(line)
                if dm and len(last_c["f"]) < 4:
                    last_c["f"].append(dm.group(1))
                    last_c.setdefault("ch", []).append(
                        {"f": dm.group(1), "o": dm.group(2), "n": dm.group(3)})
    d = m_ts.group(1).strip()[:10]
    return {"ts": m_ts.group(1).strip()[:19], "d": d, "code": TAG_CODE[tag],
            "net": CN_NET[TAG_CODE[tag]], "n": int(m_n.group(1)),
            "a": a, "r": r, "c": c, "smp": smp,
            "src": "backfill"}           # 标出来源：回填的，不是运行时逐次追加的


def load_hist():
    try:
        with open(HIST, encoding="utf-8") as f:
            h = json.load(f)
        if isinstance(h, dict) and isinstance(h.get("items"), list):
            return h
    except Exception:
        pass
    return {"schema": 1, "items": []}


def main():
    h = load_hist()

    # ① 既有记录先按 (日期, 网) 归并、保留**最后一条**。
    #    一天跑多轮会在文件里留下同一天同网的多条，时间线上表现为「同一天两套
    #    互相矛盾的数字」；后一轮的比对基线也是「上一个不同日期的快照」，
    #    数字本身就覆盖前一轮，所以留后一条是对的。
    items, seen, dup = [], {}, 0
    for x in h["items"]:
        k = (x.get("d"), x.get("code"))
        if k in seen:
            dup += 1
            items[seen[k]] = x
        else:
            seen[k] = len(items)
            items.append(x)

    # ② changes/*.md 覆盖同 (日期, 网) 的既有记录（见模块头注释：md 是权威来源）
    md, skip = {}, 0
    for p in sorted(glob.glob(os.path.join(CHG, "*.md"))):
        stem = os.path.basename(p)[:-3]
        day = stem.rsplit("-", 3)[-3:]          # 2026-09-22
        if len(day) != 3 or not day[0].isdigit():
            skip += 1
            continue
        tag = stem[:-(len("-".join(day)) + 1)]  # 去掉 "-2026-09-22"
        tag = tag.strip("-")
        if tag not in TAG_CODE:
            print("   ! 未知网前缀「%s」，跳过 %s" % (tag, stem))
            skip += 1
            continue
        rec = parse(p, tag)
        if not rec:
            continue
        rich = _enrich(rec)
        if rich:
            rec = rich              # 富化成功：数字与 md 一致，c 样本带整行对照
        md[(rec["d"], rec["code"])] = rec

    add, repl = [], 0
    for k, rec in md.items():
        if k in seen:
            items[seen[k]] = rec
            repl += 1
        else:
            seen[k] = len(items)
            items.append(rec)
            add.append(rec)

    items.sort(key=lambda x: (x.get("ts") or "", x.get("code") or ""))
    h["schema"] = 1
    h["items"] = items
    # newline="\n"：与 tariff_monitor.hist_append 保持一致，避免 Windows 写出
    # CRLF 而 CI（Linux）写 LF，让这个每天提交的文件整篇显示为「已修改」。
    with open(HIST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)
    print("回填：新增 %d 条 · 按 md 覆盖 %d 条 · 归并同天重复 %d 条（无关文件 %d），"
          "history.json 现有 %d 条" % (len(add), repl, dup, skip, len(items)))
    for r in add:
        print("   %s  %-6s 条数 %-5s 新增 %-3s 下线 %-3s 变更 %-3s 样本 %d"
              % (r["ts"], r["net"], r["n"], r["a"], r["r"], r["c"], len(r["smp"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
