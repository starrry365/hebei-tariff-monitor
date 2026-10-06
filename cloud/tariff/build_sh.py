#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上海电信小页构建器 —— ``docs/sh.html``（自用隐秘入口，2026-10-06）。

主链路（tariff_monitor.build_html → docs/index.html）**完全不感知**本脚本：
  · 不读 state.json / history.json，不产生 changes / 通知；
  · 页面与 docs/index.html 同目录（GitHub Pages 整目录上传，自然跟着上线），
    入口是主页顶栏右侧那颗几乎看不见的 6px 圆点（template.html #shdoor）。

数据取舍（宁旧勿错）：
  1. .shct_raw.json 存在（本轮真实浏览器采到）→ 用它，并顺手刷新快照；
  2. 否则退 snapshots/shct_latest.json（上一次成功采集的归一化存档）；
  3. 两者都没有 → 报错退出（CI 里不许生成空页顶替好页）。
  ★ 为什么不按「raw 是否当天」设闸：快照本来就是 raw 的存档（save_snapshot
    只在 raw 解析成功后写），raw 永远不旧于快照 —— 再设日期闸只会白算。

页面形态（刻意与主页不同）：单文件、零依赖、无外链字体、约 20KB 级 ——
  搜索 + 大类下拉 + 状态页签 + 表格（点行展开资费说明全文）。
  主题跟随系统（prefers-color-scheme），不设切换按钮。
"""
import gzip
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import shct_monitor  # noqa: E402

DOCS = os.path.join(BASE, "docs")
OUT = os.path.join(DOCS, "sh.html")
OUT_GZ = os.path.join(BASE, "page", "sh.html.gz")

MARK = "const ROWS="   # 生成页必须含数据容器（写入后校验用）


def log(m):
    print(time.strftime("[%H:%M:%S] ") + m, flush=True)


def load_data():
    """raw 优先、快照兜底（见模块头「数据取舍」）。返回 (数据, 来源说明)。"""
    if shct_monitor.raw_path():
        # save_snapshot 而不是裸 fetch_all：顺手把归一化产物刷进快照 ——
        # CI 提交的就是它，下一轮 raw 缺席时「退快照」退到的才是最近一次。
        d = shct_monitor.save_snapshot()
        return d, "本轮采集"
    if os.path.exists(shct_monitor.SNAP):
        with open(shct_monitor.SNAP, encoding="utf-8") as f:
            d = json.load(f)
        return d, "沿用快照（%s）" % d.get("fetchedAt", "?")
    raise SystemExit("!! raw 与 snapshots/shct_latest.json 都不存在，拒绝生成空页")


# ══ 页面模板 ═════════════════════════════════════════════════════
# 数据经 __ROWS__/__META__ 注入；</script> 防 breakout 在 _inject 里统一转义。
TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>S · 资费</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--tx:#1c2733;--tx2:#5b6b7d;--tx3:#8a99ab;
  --bd:#e3e9f1;--ac:#0a5cb8;--acbg:#eaf3ff;--bad:#c0392b;--ok:#1d8a4e}
@media (prefers-color-scheme:dark){:root{--bg:#10161f;--card:#1a222e;--tx:#e8edf4;
  --tx2:#a3b2c2;--tx3:#6d7d8f;--bd:#28323f;--ac:#5aa2f0;--acbg:#1b2a3d;--bad:#e07b6d;--ok:#57c98a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
  font:14px/1.55 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:18px 18px 14px;background:linear-gradient(135deg,#0a4fb0,#2f8af5);color:#fff}
header h1{margin:0;font-size:16px;font-weight:700;letter-spacing:.3px}
header .m{font-size:11.5px;opacity:.85;margin-top:3px;font-variant-numeric:tabular-nums}
.bar{position:sticky;top:0;z-index:5;display:flex;gap:8px;padding:9px 18px;
  background:var(--card);border-bottom:1px solid var(--bd);flex-wrap:wrap;align-items:center}
#kw{flex:1 1 180px;min-width:140px;padding:7px 11px;border:1px solid var(--bd);border-radius:9px;
  background:var(--bg);color:var(--tx);font:inherit;outline:none}
#kw:focus{border-color:var(--ac)}
select{padding:7px 9px;border:1px solid var(--bd);border-radius:9px;background:var(--bg);
  color:var(--tx);font:inherit;outline:none;cursor:pointer}
#stat{font-size:12px;color:var(--tx2);font-variant-numeric:tabular-nums}
main{max-width:1080px;margin:0 auto;padding:0 10px 40px}
table{width:100%;border-collapse:separate;border-spacing:0 6px}
th{font-size:11.5px;color:var(--tx3);font-weight:600;text-align:left;padding:2px 10px;white-space:nowrap}
td{background:var(--card);padding:9px 10px;vertical-align:top;border-top:1px solid var(--bd);border-bottom:1px solid var(--bd)}
tr.row td:first-child{border-left:1px solid var(--bd);border-radius:10px 0 0 10px}
tr.row td:last-child{border-right:1px solid var(--bd);border-radius:0 10px 10px 0}
tr.row{cursor:pointer}
tr.row:hover td{border-color:var(--ac)}
tr.stopped td{opacity:.55}
.fee{font-weight:750;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--ac)}
.nm b{font-weight:650}
.sub{font-size:11.5px;color:var(--tx3);margin-top:1px}
.tag{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:999px;
  background:var(--acbg);color:var(--ac);margin-right:4px;white-space:nowrap}
.tag.off{background:transparent;border:1px solid var(--bad);color:var(--bad)}
.tag.ok{background:transparent;border:1px solid var(--ok);color:var(--ok)}
tr.det td{border-radius:0 0 10px 10px;padding:0 14px;display:none}
tr.det.on td{display:block}
.det{padding:10px 2px 12px;font-size:13px;color:var(--tx2);white-space:pre-wrap}
.empty{padding:40px 0;text-align:center;color:var(--tx3)}
footer{padding:14px 18px;font-size:11px;color:var(--tx3)}
a{color:var(--tx3)}
#bk{position:fixed;right:10px;bottom:10px;width:8px;height:8px;border-radius:50%;
  background:var(--bd);opacity:.5;transition:.2s}
#bk:hover{opacity:1;background:var(--ac)}
</style>
</head>
<body>
<header>
  <h1>上海电信 · 资费公示</h1>
  <div class="m">__META__</div>
</header>
<div class="bar">
  <input id="kw" type="search" placeholder="搜索名称 / 适用人群 / 说明…">
  <select id="cat"><option value="">全部分类</option></select>
  <select id="sta">
    <option value="">全部状态</option>
    <option value="0">在售</option>
    <option value="1">已下架</option>
  </select>
  <span id="stat"></span>
</div>
<main>
<table><thead><tr>
  <th>月费</th><th>名称</th><th>流量</th><th>通话</th><th>上架</th><th>下线</th>
</tr></thead><tbody id="tb"></tbody></table>
<div class="empty" id="empty" hidden>没有匹配的资费</div>
</main>
<footer>来源：中国电信上海公司「资费专区」（189.cn）· 仅作个人查询参考，办理以官方渠道为准</footer>
<a id="bk" href="index.html" aria-label="·" tabindex="-1"></a>
<script>
__ROWS__;
__METAJS__;
const $=s=>document.querySelector(s);
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}
  [c]))}
function fmtD(s){s=String(s||"");return /^\\d{8}$/.test(s)?s.slice(0,4)+"-"+s.slice(4,6)+"-"+s.slice(6,8):"—"}
function fmtGb(g){if(g==null||g===""||g===0)return"—";
  if(g>=1024)return(g/1024).toFixed(1).replace(/\\.0$/,"")+"TB";
  if(g>=1)return(g%1?+g.toFixed(1):g)+"GB";return Math.round(g*1024)+"MB"}
const tb=$("#tb"),kw=$("#kw"),cat=$("#cat"),sta=$("#sta"),stat=$("#stat");
[...new Set(ROWS.map(r=>r.cat))].sort().forEach(c=>{
  const o=document.createElement("option");o.value=c;o.textContent=c;cat.appendChild(o)});
let cur=[];
function view(){
  const q=kw.value.trim().toLowerCase(),cv=cat.value,sv=sta.value;
  cur=ROWS.filter(r=>{
    if(cv&&r.cat!==cv)return false;
    if(sv!==""&&String(r.st)!==sv)return false;
    if(q&&!(r._s)){r._s=[r.n,r.ap,r.x,r.ch,r.r,r.sub].join(" ").toLowerCase()}
    return !q||r._s.indexOf(q)>=0;
  });
  cur.sort((a,b)=>(a.f===b.f?0:(a.f===""?1:b.f===""?-1:+a.f-+b.f))||a.n.localeCompare(b.n,"zh"));
  stat.textContent="匹配 "+cur.length+" / "+ROWS.length+" 条";
  const buf=[];
  cur.forEach((r,i)=>{
    const fee=r.f===""?"—":r.f+" 元";
    buf.push('<tr class="row'+(r.st?" stopped":"")+'" data-i="'+i+'">'
      +'<td class="fee">'+esc(fee)+'</td>'
      +'<td class="nm"><b>'+esc(r.n)+'</b><div class="sub">'
        +(r.sub?'<span class="tag">'+esc(r.sub)+'</span>':"")
        +'<span class="tag'+(r.st?" off":" ok")+'">'+(r.st?"已下架":"在售")+'</span>'
        +esc(r.ap||"")+'</div></td>'
      +'<td>'+fmtGb(r.g)+'</td>'
      +'<td>'+(r.c&&r.c!=="0"?esc(r.c)+" 分钟":"—")+'</td>'
      +'<td>'+fmtD(r.o)+'</td><td>'+fmtD(r.e)+'</td></tr>'
      +'<tr class="det"><td colspan="6"><div class="det">'
        +(r.x?"资费说明："+esc(r.x):"")+(r.ch?"\\n办理渠道："+esc(r.ch):"")
        +(r.vy?"\\n有效期限："+esc(r.vy):"")+(r.r?"\\n报备编号："+esc(r.r):"")
      +'</div></td></tr>');
  });
  tb.innerHTML=buf.join("");
  $("#empty").hidden=!!cur.length;
}
tb.addEventListener("click",e=>{
  const tr=e.target.closest("tr.row");if(!tr)return;
  tr.nextElementSibling.classList.toggle("on");
});
kw.addEventListener("input",view);
cat.addEventListener("change",view);sta.addEventListener("change",view);
view();
</script>
</body>
</html>
"""


def _inject(tpl, rows, meta, metajs):
    """数据注入：</script> 防 breakout（HTML 里数据段先于脚本结束标签闭合检查）。"""
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    mblob = json.dumps(meta, ensure_ascii=False).replace("</", "<\\/")
    out = tpl.replace("__ROWS__;", "const ROWS=" + blob + ";")
    out = out.replace("__METAJS__;", "const META=" + mblob + ";")
    out = out.replace("__META__", esc_html(meta))
    return out


def esc_html(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_rows(d):
    """归一化 entry → 小页行（瘦字段 + 预算好的状态位）。"""
    today = time.strftime("%Y%m%d")
    rows = []
    for e in d["entries"]:
        try:
            g = float(e.get("data") or 0)
        except (TypeError, ValueError):
            g = 0.0
        off = str(e.get("offineDay") or "")
        rows.append({
            "n": e.get("name") or "",
            "f": str(e.get("fees") or ""),
            "g": g if g else "",
            "c": str(e.get("call") or ""),
            "cat": e.get("type2Name") or "",
            "sub": (e.get("type3Name") or "") if e.get("type3Name") else "",
            "o": e.get("onlineDay") or "",
            "e": off,
            "st": 1 if (off and re.match(r"^\d{8}$", off) and off < today) else 0,
            "ap": (e.get("applicablePeople") or "")[:60],
            "ch": e.get("channel") or "",
            "vy": e.get("validPeriod") or "",
            "r": e.get("reportNo") or "",
            "x": e.get("otherContent") or "",
        })
    return rows


def main():
    d, src = load_data()
    rows = build_rows(d)
    total = len(rows)
    cats = {r["cat"] for r in rows}
    meta = "共 %d 条 · %s · 采集于 %s" % (
        total, " / ".join("%s %d" % (c, sum(1 for r in rows if r["cat"] == c)) for c in sorted(cats)),
        d.get("fetchedAt", "?"))
    metajs = {"src": src, "fetchedAt": d.get("fetchedAt", ""), "url": d.get("sourceUrl", "")}
    html = _inject(TPL, rows, meta, metajs)

    # 校验：数据容器在、没有把模板占位符留下
    if MARK not in html:
        raise SystemExit("!! 生成页缺数据容器，拒绝写入")
    if "__ROWS__" in html or "__META__" in html:
        raise SystemExit("!! 生成页残留模板占位符，拒绝写入")

    # 原子写（先 .new 校验再替换）—— 与 tariff_monitor.write_page 同一纪律
    os.makedirs(DOCS, exist_ok=True)
    new = OUT + ".new"
    with open(new, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(new, OUT)
    gz = OUT_GZ + ".tmp"
    os.makedirs(os.path.dirname(OUT_GZ), exist_ok=True)
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        f.write(html)
    os.replace(gz, OUT_GZ)
    log("上海电信小页：%d 条（%s）→ docs/sh.html（%d 字节）+ page/sh.html.gz"
        % (total, src, len(html.encode("utf-8"))))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
