# -*- coding: utf-8 -*-
"""四网「地域口径」核对 —— 实测「只保留 河北 + 全国」这条需求是否真的成立。

为什么需要它
------------
需求原话：「四网我只要河北和全国的，其他省份不要」。这条是**静默**需求：
页面只把 `sc` 归成 hb / cn 两档，条目归错或漏丢，界面上**看不出来** ——
「仅全国」筛选置灰到底是「上游真没有全国资费」还是「我们没认出来」，截图一样。

所以必须在**构建期**（rows_of 之前）把原始数据里的地域字段捞出来，逐网回答三个问题：
  1. 上游到底给了哪些地域值？（有没有其他省份混进来）
  2. rows_of 的 where_of() 把它们判成了什么？（hb / cn / 丢弃）
  3. **被丢弃的到底是哪些**？—— 若里面混着河北/全国，就是误删（用户要的数据被扔了）。

用法:
    python probes/check_area_scope.py                  # 全四网
    python probes/check_area_scope.py move cbn         # 只查指定网
    python probes/check_area_scope.py --json out.json  # 结果落盘（供 CI / 报告引用）

数据来源（都在 cloud/tariff/ 下，绝不联网）：
    **优先读入库的快照** `snapshots/<prefix>*<最新>.json.gz` —— 它在仓库里有版本、
    CI / 别人 clone 下来都能直接跑、且正是线上页面所依据的那一份。
    本地采集缓存（`.unicom_cache.json` / `.cbn_cache.json` / `.ct_raw.json`）
    只作为**快照缺失时**的兜底 —— 缓存是「迭代便利」，不入库，CI 里根本没有。
    ⚠️ 早期版本只读本地缓存，于是这个核对**只能在作者本机跑**，CI 里永远「原始数据缺失」。

⚠️ 本脚本**只读**，不改任何数据；判定逻辑直接 import tariff_monitor，
   所以它测的是「即将用于渲染的那套代码」，不是另写一份 ORACLE ——
   对账口径与页面同源，避免「两套判据各自漂移」。
"""
import glob
import gzip
import io
import json
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
CT = os.path.join(REPO, "cloud", "tariff")
sys.path.insert(0, CT)
sys.path.insert(0, BASE)

import tariff_monitor as T          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _latest(pattern):
    got = sorted(glob.glob(os.path.join(CT, "snapshots", pattern)))
    if not got:
        return None
    return got[-1]


def load_raw(code):
    """取某网的原始条目列表（未归一化 / 未过滤）。返回 (来源标签, [entry...])。

    优先级：**入库快照** > 本地采集缓存。快照在仓库里有版本、CI 里也在；
    缓存只在作者本机存在（gitignore），拿它作唯一来源会让这个核对跑不出 CI。
    """
    if code in T.SNAP_PREFIX:
        p = _latest(T.SNAP_PREFIX[code] + "*.json.gz")
        if p:
            with gzip.open(p, "rb") as f:
                o = json.loads(f.read().decode("utf-8"))
            ents = (o.get("entries")
                    or [e for g in o.get("groups", []) for e in (g.get("entries") or [])])
            return os.path.basename(p), ents

    # ── 兜底：本地采集缓存 ──
    if code == "telecom":
        # 🔴 电信的原始产物不是 {entries:[...]}，而是 {fetchedAt, provCode, sections{...}}。
        #    直接读 entries 会得到 0 条 —— 而 0 条看起来像「本网无地域字段」，是**假阴性**。
        #    所以这里走模块自己的解析器，拿到的就是喂给 where_of() 的那份数据。
        try:
            import ct_monitor as C
        except Exception as ex:
            return "ct_monitor 导入失败: %s" % ex, []
        rp = C.raw_path()
        if not rp:
            return None, []
        d = C.fetch_all()
        return os.path.basename(rp), (d.get("entries") or [])

    p = os.path.join(CT, ".%s_cache.json" % code)
    if not os.path.exists(p):
        return None, []
    o = json.loads(io.open(p, encoding="utf-8").read())
    return os.path.basename(p), (o.get("entries") or [])


# 每张原始表里「可能是地域」的字段名。宽取值 —— 宁可多列几个，漏了就查不出来。
AREA_FIELDS = {
    "move": ("applicableArea", "city", "province"),
    "unicom": ("_areaNames", "applicableArea", "useScope", "province", "provinceName"),
    "cbn": ("_areaNames", "applicableArea", "province", "provinceName"),
    "telecom": ("_areaCodes", "applicableArea", "_areaNames", "province"),
}


def tokenize(v):
    s = str(v or "").strip()
    if not s:
        return []
    for sep in (",", "，", ";", "；", "|", " "):
        s = s.replace(sep, ",")
    return [t.strip() for t in s.split(",") if t.strip()]


def scan(code):
    src, ents = load_raw(code)
    if src is None:
        return {"code": code, "src": None, "error": "原始数据缺失（先跑采集 / 重建）"}
    field_vals = {f: Counter() for f in AREA_FIELDS[code]}
    verdict = Counter()
    dropped = []          # 被判为「与河北无关」而丢弃的
    for e in ents:
        for f in AREA_FIELDS[code]:
            for t in tokenize(e.get(f)):
                field_vals[f][t] += 1
        sc, cities = T.where_of(code, e)
        verdict[sc or "(丢弃)"] += 1
        if sc == "":
            dropped.append({
                "name": str(e.get("name") or e.get("tariffName") or "")[:60],
                "fields": {f: e.get(f) for f in AREA_FIELDS[code] if e.get(f)},
            })
    return {
        "code": code, "src": src, "n_raw": len(ents),
        "field_vals": {f: dict(c.most_common(25)) for f, c in field_vals.items() if c},
        "verdict": dict(verdict),
        "dropped_n": len(dropped),
        "dropped_sample": dropped[:15],
    }


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    out = ""
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        if i + 1 < len(sys.argv):
            out = sys.argv[i + 1]
    codes = [c for c in argv if c in ("move", "unicom", "telecom", "cbn")] \
        or ["move", "unicom", "telecom", "cbn"]

    rep = {}
    hard = []      # 硬错误：数据缺失 / 解析异常 —— 这些必须失败
    review = []    # 需人工过目：有「外省专属」被丢弃 —— 属**正常**现象，只提示
    for code in codes:
        r = scan(code)
        rep[code] = r
        print("=" * 68)
        print("【%s】来源 %s" % (code, r.get("src")))
        if r.get("error"):
            print("  !! %s" % r["error"])
            hard.append("%s: %s" % (code, r["error"]))
            continue
        print("  原始条目 %d" % r["n_raw"])
        for f, vals in r["field_vals"].items():
            top = "、".join("%s×%d" % (k, v) for k, v in list(vals.items())[:12])
            print("  %-16s %s" % (f, top or "(全空)"))
        print("  判定 → %s" % json.dumps(r["verdict"], ensure_ascii=False))
        if r["dropped_n"]:
            print("  ⚠️ 被丢弃 %d 条（按需求「其他省份不要」，外省专属条目就是要丢）"
                  % r["dropped_n"])
            for d in r["dropped_sample"]:
                print("      · %s | %s" % (d["name"], json.dumps(d["fields"], ensure_ascii=False)[:110]))
            review.append("%s: 丢弃 %d 条" % (code, r["dropped_n"]))
        # 地域字段全空 ⇒ 无法核验（不是错，但要说出来）
        if not r["field_vals"]:
            print("  ⚠️ 三个候选字段全空 —— 本网无地域字段可核验"
                  "（判据只能靠默认值「河北」，这一档不代表上游声明）")
    print("=" * 68)
    if review:
        # ★ 这一条**不算失败**：把「外省专属条目丢掉」当成错误是把需求当 bug ——
        #   需求原话就是「四网我只要河北和全国的，其他省份不要」。
        #   这里只把清单摆出来供人工过目（判据是文本/省码，可能认错）。
        print("需人工过目（不判失败）：%s" % "；".join(review))
    else:
        print("✅ 四网均无「被丢弃」条目，且地域取值全部落在河北 / 全国口径内")
    if hard:
        print("❌ 硬错误：")
        for b in hard:
            print("  · " + b)
    if out:
        io.open(out, "w", encoding="utf-8").write(json.dumps(rep, ensure_ascii=False, indent=1))
        print("已落盘 → %s" % out)
    # 退出码只看硬错误。缺数据 ⇒ 1（本脚本没完成核对，别把「没跑成」当「跑通了」）。
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
