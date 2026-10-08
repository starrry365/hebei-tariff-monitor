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

页面形态（2026-10-06 升格，功能与视觉都对齐主界面）：单文件、零依赖、约 35KB 级 ——
  极光背景 + 玻璃顶栏 + 统计卡 + 状态页签 / 搜索 / 分类 chips / 收藏关注 / CSV 导出
  + 全列 Excel 式列筛选（漏斗面板：facet 条数 / 搜索 / 全选反选 / chip 摘除）
  + 同款表格（吸顶表头 / 斑马纹 / 点行展开 detgrid 详情 / 全列排序），
  深浅主题可切换（记住选择）。
变化历史（2026-10-07 加，参考河北主界面）：构建期先走 shct_monitor 的
  变化跟踪（每日快照归档 → diff → change_guard 护栏 → changes/shct-*.md
  → shct_history.json），页面注入裁剪后的 HIST，渲染同款时间线
  （「资费明细 / 变化历史」视图页签切换）。页面不感知磁盘文件 —— HIST
  是它唯一的数据源，与主页面「不解析 changes/*.md」同一条纪律。
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
#
# 视觉与功能（2026-10-06）：与河北主界面同一套「极光版」设计语言 ——
#   同一组 CSS 变量（电信品牌色直接内建）、极光背景、玻璃顶栏、深浅主题切换、
#   同款表格（吸顶表头 / 斑马纹 / 行悬停 / ▸ 展开指示）与 detgrid 详情网格；
#   功能上补齐主界面的招牌：全列 Excel 式多选筛选（cfpop 单例面板）、
#   收藏关注（localStorage 星标 + 只看收藏）、CSV 导出、全列排序。
TPL = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>上海电信 · 资费公示</title>
<style>
/* ═══ 设计变量：与主界面同源，电信品牌色内建（不随网切换）═══ */
:root{
  --op1:#0057b8; --op2:#00a0e9; --op3:#4ad3c8; --op1d:#004494;
  --bg0:#eef4fb; --bg1:#e7f0fa; --bg2:#f7fbff; --bg3:#ffffff;
  --card:#ffffff; --card2:#f0f6fc; --card3:#e2ecf7;
  --glass:rgba(255,255,255,.60); --glass2:rgba(255,255,255,.80); --glass3:rgba(255,255,255,.94);
  --bd:rgba(255,255,255,.92); --bd2:rgba(8,32,64,.12);
  --tx:#0a1f30; --tx2:#38536d; --tx3:#6a829c;
  --ok:#0b9e6d; --okbg:rgba(11,158,109,.10);
  --warn:#b45309; --warnbg:rgba(214,124,12,.10);
  --bad:#e11d48; --badbg:rgba(225,29,72,.08);
  --ac:var(--op1d); --ac2:rgba(0,87,184,.10);
  --sh:0 8px 24px rgba(10,40,80,.08); --sh2:0 18px 44px rgba(10,40,80,.16);
  --r:16px; --r2:10px; --blur:18px;
  --zebra:rgba(8,40,80,.026); --hover:rgba(0,87,184,.055);
  --blobmix:multiply; --blobop:.36; --glowop:.55; --glowmix:multiply; --ribbonop:.5;
}
[data-theme="dark"]{
  --bg0:#040b16; --bg1:#071120; --bg2:#0a1a2e; --bg3:#0e2338;
  --card:#0c2036; --card2:#122c49; --card3:#183556;
  --glass:rgba(12,28,48,.55); --glass2:rgba(16,36,60,.72); --glass3:rgba(20,44,72,.92);
  --bd:rgba(255,255,255,.12); --bd2:rgba(255,255,255,.16);
  --tx:#eef6ff; --tx2:#c8dcee; --tx3:#94abc4;
  --ok:#2fd598; --okbg:rgba(47,213,152,.13);
  --warn:#fbbf24; --warnbg:rgba(251,191,36,.12);
  --bad:#fb7185; --badbg:rgba(251,113,133,.12);
  --ac:var(--op2); --ac2:rgba(0,160,233,.14);
  --sh:0 10px 28px rgba(0,0,0,.40); --sh2:0 22px 52px rgba(0,0,0,.50);
  --zebra:rgba(255,255,255,.028); --hover:rgba(255,255,255,.06);
  --blobmix:screen; --blobop:.5; --glowop:.72; --glowmix:screen; --ribbonop:.4;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
html{color-scheme:light dark}
body{min-height:100vh;background:var(--bg1);color:var(--tx);
  font:400 14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  overflow-x:hidden;transition:background .4s,color .4s}

/* ── 极光背景（与主界面同款结构）── */
.aurora{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none}
.aurora::before{content:'';position:absolute;inset:0;
  background:radial-gradient(1100px 720px at 14% -12%,var(--bg2),transparent 62%),
             radial-gradient(960px 660px at 96% 4%,var(--bg3),transparent 60%),
             linear-gradient(180deg,var(--bg1),var(--bg0) 55%,var(--bg1));
  transition:background .5s}
.band{position:absolute;left:-30vw;width:160vw;height:36vh;border-radius:50%;
  filter:blur(72px);opacity:var(--ribbonop);mix-blend-mode:var(--blobmix);will-change:transform}
.bd1{top:-24vh;background:linear-gradient(100deg,transparent 18%,var(--op2) 46%,var(--op1) 62%,transparent 84%);
  animation:drift1 24s ease-in-out infinite alternate}
.bd2{top:4vh;background:linear-gradient(80deg,transparent 22%,var(--op3) 48%,var(--op2) 66%,transparent 86%);
  animation:drift2 31s ease-in-out infinite alternate}
@keyframes drift1{from{transform:rotate(-9deg) translate3d(-3vw,-1vh,0) scaleY(1)}
  to{transform:rotate(-6deg) translate3d(4vw,3vh,0) scaleY(1.18)}}
@keyframes drift2{from{transform:rotate(7deg) translate3d(3vw,2vh,0) scaleY(1.1)}
  to{transform:rotate(4deg) translate3d(-4vw,-2vh,0) scaleY(.94)}}
.blob{position:absolute;border-radius:50%;filter:blur(84px);opacity:var(--blobop);
  mix-blend-mode:var(--blobmix);transition:background .5s}
.b1{width:520px;height:520px;background:var(--op1);top:-170px;left:-130px;animation:fl1 22s ease-in-out infinite}
.b2{width:430px;height:430px;background:var(--op2);top:22%;right:-150px;animation:fl2 27s ease-in-out infinite}
@keyframes fl1{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(64px,54px) scale(1.12)}}
@keyframes fl2{0%,100%{transform:translate(0,0) scale(1.05)}50%{transform:translate(-70px,44px) scale(.93)}}
@media(prefers-reduced-motion:reduce){.blob,.band{animation:none}*{transition:none!important}}
.aurora::after{content:'';position:absolute;inset:0;opacity:var(--glowop);
  mix-blend-mode:var(--glowmix);pointer-events:none;
  background:radial-gradient(50% 44% at 6% -2%,color-mix(in srgb,var(--op2) 74%,transparent),transparent 70%),
             radial-gradient(48% 46% at 98% 2%,var(--op3),transparent 72%),
             radial-gradient(56% 50% at 70% 104%,color-mix(in srgb,var(--op1) 52%,transparent),transparent 74%)}
.grain{position:fixed;inset:0;z-index:1;pointer-events:none;opacity:.028;
  background-image:radial-gradient(currentColor 1px,transparent 1px);background-size:3px 3px}

.wrap{position:relative;z-index:2;max-width:1180px;margin:0 auto;padding:12px 16px 48px}

/* ── 玻璃顶栏（同款）── */
.topbar{position:relative;z-index:40;margin-bottom:10px;
  background:var(--glass);backdrop-filter:blur(var(--blur)) saturate(160%);
  -webkit-backdrop-filter:blur(var(--blur)) saturate(160%);
  border:1px solid var(--bd);border-radius:var(--r);box-shadow:var(--sh)}
.tb-in{display:flex;align-items:center;gap:14px;padding:9px 14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;min-width:0}
#logo{width:36px;height:36px;border-radius:12px;flex:none;display:grid;place-items:center;
  background:linear-gradient(140deg,var(--op1),var(--op2) 68%,var(--op3));color:#fff;
  font-weight:800;font-size:16px;box-shadow:0 6px 16px rgba(0,0,0,.20)}
.brand-t{min-width:0}
#h1{font-size:15px;font-weight:750;letter-spacing:-.2px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:56vw}
#sub{font-size:11px;color:var(--tx3);margin-top:1px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:60vw;font-variant-numeric:tabular-nums}
.tb-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:none;margin-left:auto}
.tb-cnt{font-size:11px;color:var(--tx3);white-space:nowrap;padding:4px 10px;border-radius:999px;
  background:var(--glass2);border:1px solid var(--bd2)}
.tb-cnt b{color:var(--tx);font-weight:800;font-size:12px;font-variant-numeric:tabular-nums}
#themeBtn{width:34px;height:34px;border-radius:50%;border:1px solid var(--bd2);background:var(--glass2);
  cursor:pointer;display:grid;place-items:center;color:var(--tx2);transition:transform .25s,border-color .2s,color .2s}
#themeBtn:hover{border-color:var(--ac);color:var(--ac);transform:rotate(20deg)}
#themeBtn .ic{width:17px;height:17px;display:none}
[data-theme="light"] #themeBtn .ic-moon{display:block}
[data-theme="dark"] #themeBtn .ic-sun{display:block}
#bk{width:6px;height:6px;border-radius:50%;background:var(--bd2);opacity:.5;flex:none;
  display:inline-block;transition:.2s}
#bk:hover,#bk:focus{opacity:1;background:var(--ac);box-shadow:0 0 0 4px var(--ac2)}

/* ── 统计卡（同款 .stat）── */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:12px}
.stat{padding:14px 16px;border-radius:var(--r);background:var(--glass);border:1px solid var(--bd2);
  box-shadow:var(--sh);position:relative;overflow:hidden}
.stat::after{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,var(--op1),var(--op2))}
.stat .sn{font-size:25px;font-weight:800;letter-spacing:-1px;line-height:1.15;
  background:linear-gradient(135deg,var(--op1),var(--op2));-webkit-background-clip:text;
  background-clip:text;-webkit-text-fill-color:transparent;font-variant-numeric:tabular-nums}
.stat .sl{font-size:11.5px;color:var(--tx3);margin-top:3px;font-weight:600}

/* ── 工具卡：状态页签 + 搜索 + 动作按钮 + 分类 chips + 生效条件 chips ── */
.deck{background:var(--card);border-radius:var(--r);
  box-shadow:0 0 0 1px var(--bd2),0 12px 26px rgba(0,40,80,.10);margin-bottom:12px}
.deck-in{padding:12px}
.brow1{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
#tabs{display:inline-flex;gap:2px;padding:3.5px;border:1px solid var(--bd2);border-radius:12px;
  background:var(--glass2);flex:none}
#tabs button{border:0;background:transparent;padding:6.5px 13px;border-radius:9px;
  font-size:12.5px;cursor:pointer;color:var(--tx2);font-weight:600;font-family:inherit;
  transition:.15s;white-space:nowrap}
#tabs button:hover{color:var(--ac)}
#tabs button.on{background:linear-gradient(135deg,var(--op1),var(--op2));color:#fff;
  box-shadow:0 3px 9px rgba(0,0,0,.16)}
.kwwrap{position:relative;flex:1 1 260px;min-width:160px;display:flex;align-items:center}
.kwwrap>svg{position:absolute;left:11px;width:15px;height:15px;color:var(--tx3);pointer-events:none}
#kw{width:100%;padding:8.5px 12px 8.5px 33px;border:1px solid var(--bd2);border-radius:11px;
  font-size:13.5px;background:var(--glass3);color:var(--tx);font-family:inherit;color-scheme:light dark;transition:.15s}
#kw::placeholder{color:var(--tx3)}
#kw:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--ac2)}
.tbtn{padding:8.5px 13px;border:1px solid var(--bd2);background:var(--glass3);
  border-radius:10px;cursor:pointer;font-size:12.5px;color:var(--tx2);transition:.16s;
  white-space:nowrap;font-family:inherit}
.tbtn:hover{border-color:var(--ac);color:var(--ac)}
.tbtn.on{border-color:var(--warn);color:var(--warn);background:var(--warnbg)}
.chips{display:flex;flex-wrap:wrap;gap:6px;padding-top:11px;align-items:center}
.chips button{padding:5px 12px;border:1px solid var(--bd2);background:var(--glass3);border-radius:999px;
  font-size:12.5px;cursor:pointer;color:var(--tx2);transition:.14s;font-family:inherit}
.chips button:hover{border-color:var(--ac);color:var(--ac)}
.chips button.on{background:linear-gradient(135deg,var(--op1),var(--op2));border-color:transparent;color:#fff}
.chips button i{font-style:normal;margin-left:4px;font-size:10.5px;opacity:.65}
.chips button.favchip.on{background:var(--warnbg);border-color:var(--warn);color:var(--warn)}
.fchips{display:inline-flex;flex-wrap:wrap;gap:5px;vertical-align:middle}
.fchip{padding:1px 9px;border:1px solid var(--bd2);background:var(--ac2);color:var(--ac);
  border-radius:999px;font-size:12px;cursor:pointer;line-height:1.7;font-family:inherit}
.fchip:hover{border-color:var(--ac)}
.fchip i{font-style:normal;font-weight:700;margin-left:4px}
.rbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 2px}
#stat{flex:1 1 auto;min-width:0;color:var(--tx2);font-size:12.5px}
#stat b{color:var(--tx)}

/* ── 列筛选（Excel 式多选，与主界面同款）：th 漏斗 + body 单例面板 ── */
.cfx{margin-left:4px;padding:0 4px;border:1px solid var(--bd2);border-radius:4px;
     background:transparent;color:inherit;font:inherit;font-size:10px;line-height:15px;
     cursor:pointer;vertical-align:1px;opacity:.55}
.cfx:hover{opacity:1;border-color:var(--ac);color:var(--ac)}
.cfx.on{opacity:1;background:var(--ac);border-color:var(--ac);color:#fff}
#cfpop{position:fixed;z-index:95;width:280px;max-width:92vw;display:none;flex-direction:column;
       max-height:min(480px,70vh);background:var(--card);color:inherit;
       border:1px solid var(--bd2);border-radius:10px;box-shadow:0 14px 36px rgba(0,0,0,.35)}
#cfpop.open{display:flex}
#cfpop .cfph{padding:8px;border-bottom:1px solid var(--bd2);display:flex;gap:6px;align-items:center}
#cfpop .cfph b{font-size:12px;color:var(--tx);margin-right:auto}
#cfpop .cfph input{flex:1;min-width:0;padding:5px 8px;font:inherit;font-size:12px;
       border:1px solid var(--bd2);border-radius:6px;background:var(--card2);color:inherit}
#cfpop .cfa{display:flex;gap:6px;align-items:center;padding:6px 8px;border-bottom:1px solid var(--bd2)}
#cfpop .cfa button{padding:2px 8px;font:inherit;font-size:11px;border:1px solid var(--bd2);
       border-radius:6px;background:var(--card2);color:inherit;cursor:pointer}
#cfpop .cfa button:hover{border-color:var(--ac);color:var(--ac)}
#cfpop .cfa .cfn{margin-left:auto;opacity:.7;font-size:11px}
#cfpop .cfl{overflow:auto;padding:3px 0;min-height:36px}
#cfpop label.cfi{display:flex;gap:7px;align-items:center;padding:4px 10px;cursor:pointer;font-size:12px}
#cfpop label.cfi:hover{background:var(--card2)}
#cfpop .cft{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#cfpop label.cfi .cfn{margin-left:auto;opacity:.6;font-size:11px}
#cfpop .cfmore{padding:6px 10px;font-size:11px;opacity:.65}

/* ── 视图页签（资费明细 / 变化历史）—— 样式与状态页签同源 ── */
#vtabs{display:inline-flex;gap:2px;padding:3.5px;border:1px solid var(--bd2);border-radius:12px;
  background:var(--glass2);flex:none}
#vtabs button{border:0;background:transparent;padding:6.5px 13px;border-radius:9px;
  font-size:12.5px;cursor:pointer;color:var(--tx2);font-weight:600;font-family:inherit;
  transition:.15s;white-space:nowrap}
#vtabs button:hover{color:var(--ac)}
#vtabs button.on{background:linear-gradient(135deg,var(--op1),var(--op2));color:#fff;
  box-shadow:0 3px 9px rgba(0,0,0,.16)}

/* ── 变化历史（时间线）—— 与河北主界面同款 ─────────────────── */
.tl{position:relative;padding-left:22px}
.tl::before{content:'';position:absolute;left:5px;top:5px;bottom:5px;width:2px;
  background:linear-gradient(180deg,var(--op1),var(--op2),transparent);border-radius:2px;opacity:.5}
.tnode{position:relative;padding:11px 0 11px 3px}
.tnode::before{content:'';position:absolute;left:-20px;top:17px;width:10px;height:10px;
  border-radius:50%;background:var(--op1);box-shadow:0 0 0 3px var(--bg1);transition:background .4s}
.tnode.zero::before{background:var(--tx3);opacity:.45}
.thd{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:12.5px;cursor:pointer}
.thd .ts{font-weight:700;color:var(--tx)}
.tbd{display:none;margin-top:9px}
.tnode.open .tbd{display:block}
.tnet{padding:10px 13px;border-radius:10px;background:var(--glass2);border:1px solid var(--bd2)}
.tnet .nh{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:12.5px;font-weight:700}
.tnet .nn{color:var(--tx2);font-weight:400}
.tcaps{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}
.tcap{padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600;
  background:var(--glass3);border:1px solid var(--bd2);color:var(--tx2)}
.tcap.a{background:var(--okbg);color:var(--ok);border-color:transparent}
.tcap.r{background:var(--badbg);color:var(--bad);border-color:transparent}
.tcap.c{background:var(--warnbg);color:var(--warn);border-color:transparent}
.tcap.w{background:var(--glass3);color:var(--bad);border:1px dashed var(--bad)}
/* 字段变更对照卡：左=旧值，右=新值（与主界面同款） */
.dc{margin-top:8px;border:1px solid var(--bd2);border-radius:10px;overflow:hidden;
  background:var(--glass3)}
.dch{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;padding:7px 11px;
  font-size:12.5px;font-weight:700;border-bottom:1px solid var(--bd2);
  background:var(--glass2)}
.dch .nn{font-weight:400;color:var(--tx3)}
.drow{display:grid;grid-template-columns:minmax(58px,88px) 1fr 1fr;font-size:12px}
.drow+.drow{border-top:1px solid var(--bd2)}
.dlab{padding:7px 4px 7px 11px;color:var(--tx2);font-weight:600;
  display:flex;align-items:flex-start}
.dold{padding:7px 11px;color:var(--tx3);word-break:break-word;
  border-right:1px dashed var(--bd2);
  background:linear-gradient(90deg,rgba(226,68,68,.05),rgba(226,68,68,0))}
.dnew{padding:7px 11px;color:var(--tx);word-break:break-word;
  background:linear-gradient(90deg,rgba(30,166,92,0),rgba(30,166,92,.055))}
.dtg{display:inline-block;padding:0 5px;border-radius:5px;font-size:10.5px;
  font-weight:700;line-height:16px;margin-right:5px;vertical-align:1px;flex:none}
.dtg.o{background:var(--badbg);color:var(--bad)}
.dtg.n{background:var(--okbg);color:var(--ok)}
.dold.plain,.dnew.plain{color:var(--tx3);background:none}
.drow.chg .dlab{color:var(--tx)}
.dstat{margin-left:auto;font-weight:400;font-size:11px;color:var(--tx3)}
.dc.full .drow{font-size:11.5px}
.dc.full .dlab,.dc.full .dold,.dc.full .dnew{padding-top:5px;padding-bottom:5px}
.dmorebtn{margin:8px 0 0 11px;padding:4px 12px;border:1px solid var(--bd2);
  background:var(--glass2);border-radius:999px;font-size:12px;cursor:pointer;
  color:var(--tx2);font-family:inherit}
.dmorebtn:hover{border-color:var(--ac);color:var(--ac)}
.hh{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.hh .ht{font-size:14.5px;font-weight:750}
#histNote{font-size:11.5px;color:var(--tx3)}

/* ── 表格（同款：吸顶实色表头 / 斑马纹 / 悬停 / ▸ 指示）── */
.tblwrap{overflow:visible}
table{width:100%;border-collapse:separate;border-spacing:0;
  background:var(--glass);border:1px solid var(--bd2);border-radius:var(--r);overflow:clip;
  font-size:13px;box-shadow:var(--sh)}
th{position:sticky;top:0;z-index:5;background:var(--card2);
  text-align:left;padding:11px 10px;font-weight:700;font-size:12.5px;color:var(--tx2);
  border-bottom:1px solid var(--bd2);white-space:nowrap;user-select:none}
th[data-k]{cursor:pointer}
th[data-k]:hover{color:var(--ac)}
th .ar{opacity:.3;margin-left:3px;font-size:10px}
th.act{color:var(--ac)}
th.act .ar{opacity:1}
td{padding:10px;border-bottom:1px solid var(--bd2);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr.row.zeb td{background:var(--zebra)}
tr.row:hover td{background:var(--hover);cursor:pointer}
tr.row:focus-visible{outline:2px solid var(--ac);outline-offset:-2px}
td.fee::before{content:"▸";display:inline-block;width:11px;font-size:10px;
  opacity:.3;transition:transform .15s,opacity .15s}
tr.row.open td.fee::before{transform:rotate(90deg);opacity:.9;color:var(--ac)}
.nm{font-weight:600}
.tg{display:inline-block;padding:1px 8px;border-radius:6px;background:var(--ac2);color:var(--ac);
  font-size:11.5px;font-weight:650;white-space:nowrap}
.fee{font-weight:750;white-space:nowrap;font-variant-numeric:tabular-nums}
.mut{color:var(--tx3);font-size:11.5px}
.bg{display:inline-block;padding:0 6px;border-radius:5px;font-size:11px;font-weight:700;line-height:17px;white-space:nowrap}
.bg-new{background:var(--okbg);color:var(--ok)}
.bg-stop{background:var(--badbg);color:var(--bad)}
.nm .bg{margin-left:5px}
.favbtn{background:none;border:0;cursor:pointer;font-size:14px;line-height:1;
  padding:0 4px 0 0;color:var(--tx3);vertical-align:baseline;font-family:inherit}
.favbtn:hover,.favbtn.on{color:var(--warn)}
tr.row.stopped .nm,tr.row.stopped .fee{color:var(--tx3)}
.det{display:none}
.det.on{display:table-row}
.det>td{padding:0;background:var(--glass2)}
.det .detwrap{padding:13px 16px;border-bottom:1px solid var(--bd2)}
.detgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:7px 26px;font-size:12.5px;line-height:1.75}
.detgrid .ditem{display:flex;gap:9px;min-width:0}
.detgrid .ditem>span{flex:none;width:64px;color:var(--tx3);font-weight:600;font-size:11.5px;padding-top:1px}
.detgrid .ditem>em{font-style:normal;color:var(--tx);word-break:break-all;min-width:0}
/* 有效期/条款徽章（对齐主界面配色语义）：红=硬门槛，橙=预存，蓝=计费规则 */
.vptg{display:inline-block;padding:0 7px;border-radius:5px;font-size:11px;font-weight:700;line-height:17px;white-space:nowrap;margin-left:5px}
.vptg-soon{background:var(--badbg);color:var(--bad);border:1px solid currentColor}
.vptg-mid{background:var(--warnbg);color:var(--warn)}
.vptg-far{background:var(--card3);color:var(--tx3);font-weight:500}
.vptg-rn{background:none;color:var(--tx3);border:1px dashed var(--bd2);font-weight:500}
.vptg-raw{background:var(--card3);color:var(--tx3);font-weight:500;border:1px dashed var(--bd2)}
/* 名称列内的有效期徽章：不占独立列（用户要求），档位配色与详情同源 */
.ctc{white-space:nowrap}
.ctg{display:inline-block;padding:0 7px;border-radius:5px;font-size:11px;font-weight:700;line-height:17px;white-space:nowrap;margin-right:4px;cursor:default}
.ctg-bad{background:var(--badbg);color:var(--bad);border:1px solid currentColor}
.ctg-warn{background:var(--warnbg);color:var(--warn)}
.ctg-info{background:var(--ac2);color:var(--ac);border:1px solid currentColor}
.ctg-more{background:var(--card3);color:var(--tx3);font-weight:500}
.ditem.vp-hi{background:var(--warnbg);border-radius:8px;padding:4px 8px;margin:2px -4px}
.ditem.vp-hi em{font-style:normal}
.ditem.vp-hi .vpraw{color:var(--tx)}
.ditem.full{grid-column:1/-1}
.empty{padding:48px;text-align:center;color:var(--tx2)}
.empty .tbtn{margin-top:12px}
footer{padding:14px 2px;font-size:11px;color:var(--tx3)}
button:focus-visible,input:focus-visible{outline:2px solid var(--ac);outline-offset:1px}
</style>
</head>
<body>
<div class="aurora" aria-hidden="true">
  <div class="band bd1"></div><div class="band bd2"></div>
  <div class="blob b1"></div><div class="blob b2"></div>
</div>
<div class="grain" aria-hidden="true"></div>
<div class="wrap">
  <div class="topbar">
    <div class="tb-in">
      <div class="brand">
        <div id="logo">沪</div>
        <div class="brand-t">
          <div id="h1">上海电信 · 资费公示</div>
          <div id="sub">__META__</div>
        </div>
      </div>
      <div class="tb-right">
        <span class="tb-cnt">在售 <b id="cnt-on">—</b> · 共 <b id="cnt-all">—</b> 条</span>
        <button type="button" id="themeBtn" title="切换深色 / 浅色主题" aria-label="切换主题">
          <svg class="ic ic-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>
          <svg class="ic ic-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"/></svg>
        </button>
        <a id="bk" href="index.html" title=" " aria-label="·" tabindex="-1"></a>
      </div>
    </div>
  </div>

  <div class="stats" id="stats"></div>

  <div id="viewTbl">
  <div class="deck">
    <div class="deck-in">
      <div class="brow1">
        <div id="vtabs" role="tablist">
          <button type="button" data-v="tbl" class="on">资费明细</button>
          <button type="button" data-v="hist">变化历史</button>
        </div>
        <div id="tabs" role="tablist">
          <button type="button" data-st="" class="on">全部</button>
          <button type="button" data-st="0">在售</button>
          <button type="button" data-st="1">已下架</button>
        </div>
        <div class="kwwrap">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.8-3.8"/></svg>
          <input id="kw" type="search" placeholder="搜索名称 / 适用人群 / 资费说明 / 报备编号…">
        </div>
        <button type="button" class="tbtn" id="favTog" title="只看收藏的资费（星标存在本机浏览器里）">★ 收藏</button>
        <button type="button" class="tbtn" id="csv" title="把当前筛选结果导出为 CSV（Excel 可直接打开）">导出 CSV</button>
      </div>
      <div class="chips" id="catChips"></div>
      <div class="chips" id="condChips" hidden></div>
    </div>
  </div>

  <div class="rbar"><span id="stat"></span></div>

  <div class="tblwrap">
  <table><thead><tr>
    <th data-k="f">月费<span class="ar"></span><button type="button" class="cfx" data-cf="f" title="列筛选">▽</button></th>
    <th data-k="n">名称<span class="ar"></span></th>
    <th data-k="g">流量<span class="ar"></span><button type="button" class="cfx" data-cf="g" title="列筛选">▽</button></th>
    <th data-k="c">通话<span class="ar"></span><button type="button" class="cfx" data-cf="c" title="列筛选">▽</button></th>
    <th data-k="o">上架<span class="ar"></span><button type="button" class="cfx" data-cf="o" title="列筛选">▽</button></th>
    <th data-k="e">下线<span class="ar"></span><button type="button" class="cfx" data-cf="e" title="列筛选">▽</button></th>
  </tr></thead><tbody id="tb"></tbody></table>
  <div class="empty" id="empty" hidden>没有匹配的资费<br>
    <button type="button" class="tbtn" id="resetAll">清空全部筛选</button></div>
  </div>
  </div><!-- /viewTbl -->

  <div id="viewHist" hidden>
    <div class="deck"><div class="deck-in">
      <div class="hh"><span class="ht">资费变更时间线</span><span id="histNote"></span></div>
      <div class="tl" id="histBox"></div>
    </div></div>
  </div>

  <div id="cfpop" role="dialog" aria-modal="false"></div>

  <footer>来源：中国电信上海公司「资费专区」（189.cn）· 与河北站同一套监控管线，每日自动更新 · 仅作个人查询参考，办理以官方渠道为准</footer>
</div>
<script>
__ROWS__;
__HIST__;
__METAJS__;
const $=s=>document.querySelector(s);
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}
  [c]))}
function fmtD(s){s=String(s||"");return /^\\d{8}$/.test(s)?s.slice(0,4)+"-"+s.slice(4,6)+"-"+s.slice(6,8):"—"}
function fmtGb(g){if(g==null||g===""||g===0)return"—";
  if(g>=1024)return(g/1024).toFixed(1).replace(/\\.0$/,"")+"TB";
  if(g>=1)return(g%1?+g.toFixed(1):g)+"GB";return Math.round(g*1024)+"MB"}
/* —— 有效期/条款解析（2026-10-07 对齐主界面，口径同款）——
   主界面第二轮普查结论直接复用：7 类门槛条款（合约期/违约金/最低消费/
   预存/首月优惠/一次性费用/限办次数）；扫描范围 x+ex+ap+vy；
   违约金/预存用**肯定语境判定**：命中点前几字是 无/免/不 则跳过 ——
   「无需承担违约责任」「退订无需违约金」「无需预存」全部不触发。 */
var COND_DEFS=[
  {lab:"合约期",cls:"ctg-bad",find:function(s){
     var m,RE=/承诺(在网|使用)|协议期|合约期|合同期|约定期限|最低在网|连续在网|在网(至少|不少于)/g;
     while((m=RE.exec(s))){
       if(/[无没免不]/.test(s.slice(Math.max(0,m.index-3),m.index)))continue;
       if(/(无需|不需要?|不用|免)承担/.test(s.slice(m.index,m.index+28)))continue;
       return m;
     }
     return null;
   },tip:"承诺在网/协议期，期内退订可能有代价"},
  {lab:"违约金",cls:"ctg-bad",find:function(s){
     var m,RE=/(?:承担|收取|产生|支付|赔付|赔偿|扣除|加收)[^，。；]{0,8}违约|违约金\\s*[按照为]|解约金|提前解约[^，。；]{0,10}(?:金|费|赔付|赔偿)|(?:退订|销户|携转|解约)[^，。；无免不没]{0,6}(?:需|须|要)[^，。；无免不没]{0,10}(?:赔付|赔偿|缴纳|支付)/g;
     while((m=RE.exec(s))){
       if(/[无免不]/.test(s.slice(Math.max(0,m.index-3),m.index)))continue;
       return m;
     }
     return null;
   },tip:"期内退订/销户/携转可能产生违约金"},
  {lab:"最低消费",cls:"ctg-bad",pat:/最低消费|保底消费|承诺(最低)?消费|最低月消费|月消费(不低于|不少于)|每月?消费不低于|消费不足(需|要)?补齐/,tip:"有最低消费/保底/消费补齐要求"},
  {lab:"预存",cls:"ctg-warn",find:function(s){
     var m,RE=/预存|押金|预交|预缴|预存款|一次性存入|存入话费|首充/g;
     while((m=RE.exec(s))){
       if(/[无免不]/.test(s.slice(Math.max(0,m.index-2),m.index)))continue;
       return m;
     }
     return null;
   },tip:"需预存话费/押金/首充"},
  {lab:"首月优惠",cls:"ctg-info",pat:/首月(免费|0元|零元|按天|折算|收取|扣费|半价|五折|减半|免收)|恢复原价|个月后恢复|优惠期(满|结束)|次月(恢复|起按|开始按|按原价)|到期(后)?恢复|体验期(满|结束)|活动期(满|结束)|按(日|自然日)折算/,tip:"首月计费规则特殊 / 到期恢复原价"},
  {lab:"一次性费用",cls:"ctg-warn",pat:/调测费|安装费|工料费|开户费|一次性(缴纳|支付|收取|费用)/,tip:"办理时需一次性缴纳的费用（调测/装机等）"},
  {lab:"限办次数",cls:"ctg-info",pat:/每个(证件|身份证)|同一(证件|身份证|用户|客户)[^，。；]{0,10}(不超过|限|最多)|办理次数不超过|名下(已有|最多|同时)|每人限办?|限办\\d/,tip:"同一证件/名下可办理次数有限"}
];
function condHitsS(r){
  /* 2026-10-08 第三轮：与主版 condHits 同款字段偏移表 —— snip 钳制在本字段内
     不跨界；匹配区间越过字段边界（拼接串里跨「｜」的伪命中）一律拒绝。 */
  var FIELDS=[r.x,r.ex,r.ap,r.vy],s="",off=[];
  for(var j=0;j<FIELDS.length;j++){
    var t=String(FIELDS[j]||"");
    off.push([s.length,s.length+t.length]);
    s+=t+"｜";
  }
  if(!s.replace(/｜/g,""))return[];
  var out=[];
  for(var i=0;i<COND_DEFS.length;i++){
    var c=COND_DEFS[i],m=c.find?c.find(s):c.pat.exec(s);
    if(!m)continue;
    if(c.neg&&c.neg.test(s.slice(Math.max(0,m.index-30),m.index)))continue;
    var seg=null;
    for(var k=0;k<off.length;k++){if(m.index>=off[k][0]&&m.index<off[k][1]){seg=off[k];break}}
    if(!seg)seg=off[off.length-1];
    if(m.index+m[0].length>seg[1])continue;
    var a=Math.max(seg[0],m.index-24),b=Math.min(seg[1],m.index+30);
    out.push({def:c,snip:s.slice(a,b).trim()});
  }
  return out;
}
function vpInfoS(v){
  v=String(v||"").trim();
  if(!v||v==="长期")return null;
  var best=null,m,RE=/20\\d{2}\\s*[年\\-\\/.]\\s*\\d{1,2}\\s*[月\\-\\/.]\\s*\\d{1,2}/g;
  while((m=RE.exec(v))){
    var p=m[0].match(/\\d+/g),y=+p[0],mo=+p[1],dy=+p[2]||1;
    if(mo<1||mo>12||dy<1||dy>31)continue;
    var t=Date.UTC(y,mo-1,dy);
    if(t>=Date.UTC(2020,0,1)&&t<=Date.UTC(2040,11,31)&&(best==null||t>best))best=t;
  }
  var term=null,tm;
  if((tm=v.match(/(\\d+)\\s*个?\\s*月/))&&+tm[1]>0&&+tm[1]<=120)term=+tm[1];
  if(term==null&&(tm=v.match(/(\\d+)\\s*年/))&&+tm[1]>0&&+tm[1]<=10)term=+tm[1]*12;
  if(best==null&&term==null)return null;
  return{date:best,term:term,renew:/自动续|自动顺延|续展|自动延续/.test(v)};
}
function vpTagS(r){
  var i=vpInfoS(r.vy);if(!i)return"";
  var rn=i.renew?' <span class="vptg vptg-rn" title="到期后自动顺延/续约">自动续</span>':"";
  if(i.date!=null){
    var lf=Math.ceil((i.date-Date.now())/864e5),ds=new Date(i.date).toISOString().slice(0,10);
    if(lf<=0)return' <span class="vptg vptg-soon" title="标称有效期已过（'+ds+'）">'+ds+' 已过</span>'+rn;
    if(lf<=90)return' <span class="vptg vptg-soon" title="距标称有效期（'+ds+'）还有 '+lf+' 天">至 '+ds+'</span>'+rn;
    return' <span class="vptg '+(lf<=365?"vptg-mid":"vptg-far")+'" title="标称有效期至 '+ds+'">至 '+ds+'</span>'+rn;
  }
  var y=Math.floor(i.term/12),mo=i.term%12;
  var ts=y?(mo?y+"年"+mo+"个月":y+"年"):mo+"个月";
  return' <span class="vptg '+(i.term<=12?"vptg-mid":"vptg-far")+'" title="有效期约 '+ts+'（订购起算）">'+ts+'</span>'+rn;
}
function detVp(r){
  if(!r.vy)return"";
  var i=vpInfoS(r.vy);
  if(!i)return'<div class="ditem"><span>有效期限</span><em>'+esc(r.vy)+'</em></div>';
  return'<div class="ditem vp-hi"><span>有效期限</span><em>'+vpTagS(r)
    +' <span class="vpraw">'+esc(r.vy)+'</span></em></div>';
}
function detCond(r){
  var h=condHitsS(r);if(!h.length)return"";
  return'<div class="ditem vp-hi"><span>办理必读</span><em>'
    +h.map(function(x){return'<span class="ctg '+x.def.cls+'">'+esc(x.def.lab)+'</span> '+esc(x.snip)}).join("；")
    +'</em></div>';
}
/* 主题切换（记住选择，同主页面行为） */
(function(){let t=null;try{t=localStorage.getItem("shtheme")}catch(e){}
  if(!t)t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";
  document.documentElement.dataset.theme=t})();
$("#themeBtn").addEventListener("click",()=>{
  const t=document.documentElement.dataset.theme==="dark"?"light":"dark";
  document.documentElement.dataset.theme=t;
  try{localStorage.setItem("shtheme",t)}catch(e){}
});
/* 统计卡：总量 / 在售 / 30 天内新上架 / 30 天变更量 */
(function(){
  const cut=new Date(Date.now()-30*864e5).toISOString().slice(0,10).replace(/-/g,"");
  const cutD=cut.slice(0,4)+"-"+cut.slice(4,6)+"-"+cut.slice(6,8);
  const on=ROWS.filter(r=>!r.st).length;
  const nw=ROWS.filter(r=>r.o&&r.o>=cut&&!r.st).length;
  const ch30=(HIST||[]).filter(x=>String(x.d||"")>=cutD)
    .reduce((s,x)=>s+(x.a||0)+(x.r||0)+(x.c||0),0);
  $("#cnt-on").textContent=on;$("#cnt-all").textContent=ROWS.length;
  $("#stats").innerHTML=[["资费总条数",ROWS.length],["在售",on],["30 天内新上架",nw],["30 天变更",ch30]]
    .map(p=>'<div class="stat"><div class="sn">'+p[1]+'</div><div class="sl">'+p[0]+'</div></div>').join("");
})();
/* ── 收藏关注（localStorage，按套餐名） ───────────────────────── */
let FAVS;
try{FAVS=new Set(JSON.parse(localStorage.getItem("shfavs")||"[]"))}catch(e){FAVS=new Set()}
let FAV=false;   // 「只看收藏」开关
function saveFavs(){try{localStorage.setItem("shfavs",JSON.stringify([...FAVS]))}catch(e){}}
$("#favTog").addEventListener("click",()=>{FAV=!FAV;$("#favTog").classList.toggle("on",FAV);view()});
/* ── 分类 chips（动态生成，带条数） ───────────────────────────── */
let CAT="",ST="",SK=null,SD=1;
const catBox=$("#catChips");
catBox.innerHTML='<button type="button" data-c="" class="on">全部分类</button>'+
  CATS.map(c=>'<button type="button" data-c="'+esc(c)+'">'+esc(c)+
    "<i>"+ROWS.filter(r=>r.cat===c).length+"</i></button>").join("");
catBox.addEventListener("click",e=>{
  const b=e.target.closest("button");if(!b)return;
  CAT=b.dataset.c;
  catBox.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));view();
});
$("#tabs").addEventListener("click",e=>{
  const b=e.target.closest("button");if(!b)return;
  ST=b.dataset.st;
  $("#tabs").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));view();
});
/* ── 列排序：点表头切换字段，再点反向 ─────────────────────────── */
document.querySelectorAll("th[data-k]").forEach(th=>th.addEventListener("click",()=>{
  const k=th.dataset.k;
  if(SK===k)SD=-SD;else{SK=k;SD=1}
  document.querySelectorAll("th[data-k]").forEach(x=>{
    x.classList.toggle("act",x===th);
    x.querySelector(".ar").textContent=x===th?(SD>0?"↑":"↓"):"";
  });
  view();
}));
/* ── 列筛选（Excel 式多选）：COLF_DEF 单一来源 ────────────────────
   与主界面同一套设计：漏斗只声明取值函数，面板清单 / match 判据 / facet
   条数全部从它派生。facet 口径 = 摘掉本列勾选、其余条件照常。 */
const COLF_DEF={
  cat:{lab:"分类",get:r=>r.cat||""},
  sub:{lab:"细分",get:r=>r.sub||""},
  f:{lab:"月费",get:r=>{
    if(r.f==="")return"未标注";const v=+r.f;
    if(isNaN(v))return"其他";
    return v===0?"0 元":v<20?"1-19 元":v<40?"20-39 元":v<80?"40-79 元":"80+ 元";}},
  g:{lab:"流量",get:r=>{
    if(r.g===""||!r.g)return"未标注";
    if(r.g<10)return"<10GB";if(r.g<50)return"10-49GB";
    if(r.g<100)return"50-99GB";if(r.g<1024)return"100-999GB";return"1TB+"}},
  c:{lab:"通话",get:r=>{
    const v=+r.c;
    if(!r.c||isNaN(v)||v===0)return"无/未标注";
    if(v<100)return"<100 分钟";if(v<500)return"100-499 分钟";return"500 分钟+"}},
  o:{lab:"上架",get:r=>/^\\d{8}$/.test(r.o)?r.o.slice(0,4)+"-"+r.o.slice(4,6):"未标注"},
  e:{lab:"下线",get:r=>{
    if(!/^\\d{8}$/.test(r.e))return"未设定";
    return r.st?"已过期":"未到期"}}
};
let COLF={};   // {code: Set(取值)}；只存「勾选了」的列
const CF_BASE=[["f",1],["g",1],["c",1],["o",1],["e",1],["cat",1],["sub",1]];
const tb=$("#tb"),kw=$("#kw"),stat=$("#stat"),condBox=$("#condChips");
let cur=[];
function basePass(r,skipCode){
  if(CAT&&r.cat!==CAT&&skipCode!=="cat")return false;
  if(ST!==""&&String(r.st)!==ST)return false;
  if(FAV&&!FAVS.has(r.n))return false;
  for(const k in COLF){
    if(k===skipCode)continue;
    if(!COLF[k].has(COLF_DEF[k].get(r)))return false;
  }
  return true;
}
function view(){
  const q=kw.value.trim().toLowerCase();
  cur=ROWS.filter(r=>{
    if(!basePass(r,null))return false;
    if(q){if(!r._s){r._s=[r.n,r.ap,r.x,r.ch,r.r,r.sub,r.cat].join(" ").toLowerCase()}
      if(r._s.indexOf(q)<0)return false}
    return true;
  });
  cur.sort(cmp);
  stat.innerHTML="匹配 <b>"+cur.length+"</b> / "+ROWS.length+" 条"+(FAV?" · ★ 只看收藏":"");
  const today=new Date().toISOString().slice(0,10).replace(/-/g,"");
  const cut=new Date(Date.now()-30*864e5).toISOString().slice(0,10).replace(/-/g,"");
  const buf=[];
  cur.forEach((r,i)=>{
    const fee=r.f===""?"—":r.f+" 元";
    const isNew=r.o&&r.o>=cut&&!r.st;
    buf.push('<tr class="row'+(r.st?" stopped":"")+'" data-i="'+i+'" tabindex="0">'
      +'<td class="fee">'+esc(fee)+'</td>'
      +'<td class="nm"><button type="button" class="favbtn'+(FAVS.has(r.n)?" on":"")
        +'" data-fn="'+esc(r.n)+'" title="收藏 / 取消收藏">'+(FAVS.has(r.n)?"★":"☆")+'</button>'
        +'<span class="nmt">'+esc(r.n)+'</span>'
        +(isNew?'<span class="bg bg-new">NEW</span>':"")
        +(r.st?'<span class="bg bg-stop">已下架</span>':"")
        +vpTagS(r)
        +(r.sub?'<span class="tg">'+esc(r.sub)+'</span>':"")
        +'<div class="mut">'+esc(r.cat||"")+(r.ap?" · "+esc(r.ap):"")+'</div></td>'
      +'<td>'+fmtGb(r.g)+'</td>'
      +'<td>'+(r.c&&r.c!=="0"?esc(r.c)+" 分钟":"—")+'</td>'
      +'<td>'+fmtD(r.o)+'</td><td>'+fmtD(r.e)+'</td></tr>'
      +'<tr class="det"><td colspan="6"><div class="detwrap"><div class="detgrid">'
        +(r.sub?'<div class="ditem"><span>细分</span><em>'+esc(r.sub)+'</em></div>':"")
        +(r.ap?'<div class="ditem"><span>适用人群</span><em>'+esc(r.ap)+'</em></div>':"")
        +detVp(r)
        +detCond(r)
        +(r.ch?'<div class="ditem"><span>办理渠道</span><em>'+esc(r.ch)+'</em></div>':"")
        +(r.r?'<div class="ditem"><span>报备编号</span><em>'+esc(r.r)+'</em></div>':"")
        +(r.x?'<div class="ditem full"><span>资费说明</span><em>'+esc(r.x)+'</em></div>':"")
      +'</div></div></td></tr>');
  });
  tb.innerHTML=buf.join("");
  zebrafy();
  $("#empty").hidden=!!cur.length;
  drawCondChips();drawFunnels();
}
/* 收藏星标（事件委托；点击不触发行的展开） */
tb.addEventListener("click",e=>{
  const fb=e.target.closest(".favbtn");
  if(fb){
    const n=fb.dataset.fn;
    if(FAVS.has(n))FAVS.delete(n);else FAVS.add(n);
    saveFavs();view();
    e.stopPropagation();return;
  }
  const tr=e.target.closest("tr.row");if(!tr)return;
  tr.classList.toggle("open");
  tr.nextElementSibling.classList.toggle("on");
});
tb.addEventListener("keydown",e=>{
  if(e.key!=="Enter"&&e.key!==" ")return;
  const tr=e.target.closest("tr.row");if(!tr)return;
  e.preventDefault();tr.classList.toggle("open");
  tr.nextElementSibling.classList.toggle("on");
});
/* 生效条件 chips（列筛选勾选摘要，可点整枚摘除） */
function drawCondChips(){
  const keys=Object.keys(COLF).filter(k=>COLF[k].size);
  condBox.hidden=!keys.length;
  if(!keys.length){condBox.innerHTML="";return}
  condBox.innerHTML=keys.map(k=>'<button type="button" class="fchip" data-ck="'+esc(k)+'">'
    +esc(COLF_DEF[k].lab)+": "+COLF[k].size+'<i>✕</i></button>').join("")
    +'<button type="button" class="fchip" data-ck="*" style="border-style:dashed">清空全部<i>✕</i></button>';
}
condBox.addEventListener("click",e=>{
  const b=e.target.closest(".fchip");if(!b)return;
  const k=b.dataset.ck;
  if(k==="*")COLF={};else delete COLF[k];
  view();
});
/* 漏斗亮灭态 */
function drawFunnels(){
  document.querySelectorAll(".cfx").forEach(b=>
    b.classList.toggle("on",!!(COLF[b.dataset.cf]&&COLF[b.dataset.cf].size)));
}
/* cfpop 单例面板（fixed 定位，按漏斗落点） */
const pop=$("#cfpop");
let popCode=null;
document.querySelectorAll(".cfx").forEach(b=>b.addEventListener("click",e=>{
  e.stopPropagation();
  if(popCode===b.dataset.cf&&pop.classList.contains("open")){pop.classList.remove("open");popCode=null;return}
  openPop(b.dataset.cf,b);
}));
function openPop(code,btn){
  popCode=code;
  const def=COLF_DEF[code],sel=COLF[code]||new Set();
  /* facet：摘掉本列勾选、其余条件照常 —— 面板里看到的是「选了它还剩几条」 */
  const facet=new Map();
  for(const r of ROWS){
    if(!basePass(r,code))continue;
    const v=def.get(r);facet.set(v,(facet.get(v)||0)+1);
  }
  const items=[...facet.entries()].sort((a,b)=>b[1]-a[1]||(a[0]||"").localeCompare(b[0]||"","zh"));
  const POP_MAX=400;
  pop.innerHTML='<div class="cfph"><b>'+esc(def.lab)+'</b>'
    +'<input id="cfq" type="search" placeholder="搜索取值…"></div>'
    +'<div class="cfa"><button type="button" data-a="all">全选</button>'
    +'<button type="button" data-a="inv">反选</button>'
    +'<button type="button" data-a="none">清空</button>'
    +'<span class="cfn" id="cfn"></span></div>'
    +'<div class="cfl">'+items.slice(0,POP_MAX).map(it=>
      '<label class="cfi"><input type="checkbox" value="'+esc(it[0])+'"'+(sel.has(it[0])?" checked":"")+'>'
      +'<span class="cft">'+(esc(it[0])||"(空白)")+'</span><span class="cfn">'+it[1]+'</span></label>').join("")
    +(items.length>POP_MAX?'<div class="cfmore">取值过多，仅显示前 '+POP_MAX+' 项（共 '+items.length+'）</div>':"")
    +'</div>';
  pop.classList.add("open");
  const r=btn.getBoundingClientRect();
  const pw=Math.min(280,innerWidth*.92);
  pop.style.left=Math.max(6,Math.min(r.left,innerWidth-pw-6))+"px";
  pop.style.top=Math.min(r.bottom+6,innerHeight-pop.offsetHeight-8)+"px";
  const applyBadge=()=>{
    const boxes=[...pop.querySelectorAll(".cfl input")];
    const n=sel.size;
    $("#cfn").textContent="已勾 "+n;
    drawFunnels();
  };
  pop.querySelector(".cfl").addEventListener("change",e=>{
    const cb=e.target;if(cb.type!=="checkbox")return;
    if(cb.checked)sel.add(cb.value);else sel.delete(cb.value);
    if(sel.size)COLF[code]=sel;else delete COLF[code];
    applyBadge();
  });
  pop.querySelector(".cfa").addEventListener("click",e=>{
    const a=e.target.closest("button")&&e.target.closest("button").dataset.a;if(!a)return;
    const boxes=[...pop.querySelectorAll(".cfl input")];
    if(a==="all")boxes.forEach(cb=>{cb.checked=true;sel.add(cb.value)});
    if(a==="inv")boxes.forEach(cb=>{cb.checked=!cb.checked;cb.checked?sel.add(cb.value):sel.delete(cb.value)});
    if(a==="none"){boxes.forEach(cb=>cb.checked=false);sel.clear()}
    if(sel.size)COLF[code]=sel;else delete COLF[code];
    applyBadge();
  });
  pop.querySelector("#cfq").addEventListener("input",e=>{
    const q=e.target.value.trim().toLowerCase();
    pop.querySelectorAll(".cfl .cfi").forEach(l=>{
      l.style.display=!q||l.querySelector(".cft").textContent.toLowerCase().indexOf(q)>=0?"":"none";
    });
  });
  applyBadge();
}
document.addEventListener("click",e=>{
  if(!pop.classList.contains("open"))return;
  if(e.target.closest("#cfpop")||e.target.closest(".cfx"))return;
  pop.classList.remove("open");popCode=null;
  view();   // 关面板才重渲染（勾选过程不抖滚动）
});
/* 空态一键重置 */
$("#resetAll").addEventListener("click",()=>{
  COLF={};CAT="";ST="";FAV=false;kw.value="";
  $("#favTog").classList.remove("on");
  catBox.querySelectorAll("button").forEach(x=>x.classList.toggle("on",!x.dataset.c));
  $("#tabs").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x.dataset.st===""));
  view();
});
/* CSV 导出（当前筛选结果，BOM 让 Excel 直开不乱码） */
$("#csv").addEventListener("click",()=>{
  const head=["月费(元)","名称","分类","细分","流量(GB)","通话(分钟)","上架","下线","状态","适用人群","办理渠道","有效期限","办理条款","报备编号","资费说明"];
  const q=s=>'"'+String(s==null?"":s).replace(/"/g,'""')+'"';
  const lines=[head.map(q).join(",")];
  cur.forEach(r=>lines.push([r.f,r.n,r.cat,r.sub,r.g,r.c,
    fmtD(r.o),fmtD(r.e),r.st?"已下架":"在售",r.ap,r.ch,r.vy,
    condHitsS(r).map(function(x){return x.def.lab}).join("/"),r.r,r.x].map(q).join(",")));
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob(["\\uFEFF"+lines.join("\\r\\n")],{type:"text/csv;charset=utf-8"}));
  a.download="上海电信资费_"+new Date().toISOString().slice(0,10)+".csv";
  a.click();URL.revokeObjectURL(a.href);
});
function cmp(a,b){
  if(SK===null)return(a.f===b.f?0:(a.f===""?1:b.f===""?-1:+a.f-+b.f))||a.n.localeCompare(b.n,"zh");
  let x=a[SK],y=b[SK];
  if(x===""||y==="")return(x==="")-(y==="")||a.n.localeCompare(b.n,"zh");
  const nx=+x,ny=+y;
  if(!isNaN(nx)&&!isNaN(y)&&String(nx)===String(x)&&String(ny)===String(y))return SD*(nx-ny);
  return SD*x.localeCompare(y,"zh");
}
/* 斑马纹只数可见行（与主页面同一纪律） */
function zebrafy(){
  let z=false;
  tb.querySelectorAll("tr.row").forEach(tr=>{
    tr.classList.toggle("zeb",z);z=!z;
  });
}
kw.addEventListener("input",view);
/* ── 视图切换：资费明细 / 变化历史 ─────────────────────────────── */
document.querySelectorAll("#vtabs button").forEach(b=>b.addEventListener("click",()=>{
  const v=b.dataset.v;
  document.querySelectorAll("#vtabs button").forEach(x=>x.classList.toggle("on",x===b));
  $("#viewTbl").hidden=v!=="tbl";
  $("#viewHist").hidden=v!=="hist";
}));
/* CATS 与 ROWS 一起由 _inject 注入（const CATS=[...]，跟在 ROWS 声明后） */
view();

/* ── 变化历史（时间线）──────────────────────────────────────────
   数据来自构建脚本注入的 HIST（shct_history.json 的 items 裁剪版），
   渲染逻辑与河北主界面同构（单网简化：每天一个节点）。
   ★ 抑制原因必须显示：a=r=c=0 有两种完全不同的含义 ——
     「上游确实没变」和「本轮护栏抑制/基线回弹，没敢记变化」。
     只显示「本次无变化」会把后者伪装成前者。 */
const NOTE_CN={"degraded":"数据异常·已冻结上一版","rebound":"基线回弹·不计变化",
  "schema":"字段结构变更·仅重建基线","noise":"采样噪声·疑似整批轮换，不计变化",
  "baseline":"首版基线","resync":"重同步基线",
  "collect-error":"采集失败·沿用上一版","snapshot-fallback":"沿用快照"};
/* 对照卡每轮先铺的张数，其余折叠（数据侧上限与主链路一致） */
const DIFF_SHOW=6;
function fmtDiffs(ds){
  if(ds.length<=DIFF_SHOW)return ds.join("");
  return ds.slice(0,DIFF_SHOW).join("")
    +'<div class="dmore" hidden>'+ds.slice(DIFF_SHOW).join("")+'</div>'
    +'<button type="button" class="dmorebtn" data-n="'+ds.length+'">展开其余 '
    +(ds.length-DIFF_SHOW)+' 条字段变更</button>';
}
function renderHistory(){
  const box=$("#histBox"),note=$("#histNote");
  const items=(HIST||[]).slice().sort((a,b)=>String(b.ts||"").localeCompare(String(a.ts||"")));
  if(!items.length){
    box.innerHTML='<div class="empty">还没有可展示的变更记录 —— 自本次部署起开始累积，'
      +'下一轮巡检有变化时就会出现。</div>';
    note.textContent="";return;
  }
  const byD={};
  items.forEach(x=>{const d=x.d||String(x.ts||"").slice(0,10);(byD[d]=byD[d]||[]).push(x)});
  const days=Object.keys(byD).sort().reverse();
  note.textContent="共 "+items.length+" 次巡检 · "+days.length+" 天 · 点击任一天展开";
  box.innerHTML=days.map(d=>{
    const ns=byD[d];
    let A=0,R=0,C=0;
    ns.forEach(x=>{A+=x.a||0;R+=x.r||0;C+=x.c||0});
    const zero=!(A||R||C);
    const notas=[...new Set(ns.map(x=>x.note).filter(Boolean))];
    return '<div class="tnode'+(zero?" zero":"")+'">'
      +'<div class="thd"><span class="ts">'+esc(d)+'</span>'
      +(zero
        ?'<span class="tcap">全天无变化</span>'
        :((A?'<span class="tcap a">新增 '+A+'</span>':"")
          +(R?'<span class="tcap r">下架 '+R+'</span>':"")
          +(C?'<span class="tcap c">字段变更 '+C+'</span>':"")))
      +(notas.length?'<span class="tcap w" title="'+esc(notas.map(n=>NOTE_CN[n]||n).join("；"))+'">⚠️ 有抑制 '+notas.length+'</span>':"")
      +'</div><div class="tbd">'+ns.map(x=>{
          const caps=[];
          if(x.note)caps.push('<span class="tcap w" title="本轮未记入任何变化">⚠ '+esc(NOTE_CN[x.note]||x.note)+'</span>');
          if(x.a)caps.push('<span class="tcap a">新增 '+x.a+'</span>');
          if(x.r)caps.push('<span class="tcap r">下架 '+x.r+'</span>');
          if(x.c)caps.push('<span class="tcap c">字段变更 '+x.c+'</span>');
          /* 护栏数字：不计入上面的新增/下架，但不显示就等于把
             「为什么数字比想象的小」这件事藏起来。 */
          if(x.gl)caps.push('<span class="tcap" title="同一条资费换了栏目/板块，不算新增也不算下线">漂移合并 '+x.gl+'</span>');
          if(x.gm)caps.push('<span class="tcap" title="在售目录 ↔ 停售目录之间迁移，只按一个方向计一次">在售↔停售 '+x.gm+'</span>');
          if(x.gf)caps.push('<span class="tcap" title="下线日期尚未到期，判为漏采而非下架">假下架抑制 '+x.gf+'</span>');
          if(x.gs)caps.push('<span class="tcap" title="上线日早于上一轮基线，属漏采补录">补录 '+x.gs+'</span>');
          if(!caps.length)caps.push('<span class="tcap">本次无变化</span>');
          const chips=[],diffs=[];
          (x.smp||[]).forEach(s=>{
            if(s.k==="more")return;   // 截断标记，循环后单独渲染
            if(s.k==="c"&&((s.rows&&s.rows.length)||(s.ch&&s.ch.length))){
              const its=(s.rows&&s.rows.length)
                ?s.rows.map(r=>({f:r[0],o:r[1],n:r[2],ch:!!r[3]}))
                :s.ch.map(c=>({f:c.f,o:c.o,n:c.n,ch:true}));
              const nch=its.filter(i=>i.ch).length;
              const rows=its.map(i=>{
                const o=(i.o==null?"":String(i.o))||"—",n=(i.n==null?"":String(i.n))||"—";
                if(!i.ch)return '<div class="drow"><div class="dlab">'+esc(i.f||"")+'</div>'
                  +'<div class="dold plain">'+esc(o)+'</div>'
                  +'<div class="dnew plain">'+esc(n)+'</div></div>';
                return '<div class="drow chg"><div class="dlab">'+esc(i.f||"字段")+'</div>'
                  +'<div class="dold"><span class="dtg o">旧</span>'+esc(o)+'</div>'
                  +'<div class="dnew"><span class="dtg n">新</span>'+esc(n)+'</div></div>';
              }).join("");
              diffs.push('<div class="dc'+(nch<its.length?" full":"")
                +'"><div class="dch">⇄ '+esc(s.n||"")
                +(s.ty?'<span class="nn">'+esc(s.ty)+'</span>':"")
                +(nch<its.length?'<span class="dstat">变更 '+nch+' 项 / 共 '
                  +its.length+' 项 · 高亮为变更</span>':"")
                +'</div>'+rows+'</div>');
              return;
            }
            const mark=s.k==="a"?"＋":s.k==="r"?"－":"⇄";
            chips.push('<span class="tcap'+(s.k==="a"?" a":s.k==="r"?" r":" c")+'" title="'
              +esc(s.ty||"")+'">'+mark+esc(s.n||"")+'</span>');
          });
          const more=(x.smp||[]).find(s=>s.k==="more");
          if(more&&more.n)chips.push('<span class="tcap" title="为控制页面体积，本轮样本做了截断；完整明细见仓库 changes/shct-*.md 归档">…还有 '
            +more.n+' 条明细</span>');
          return '<div class="tnet"><div class="nh"><span>'+esc(x.net||"上海电信")
            +'</span><span class="nn">'+(x.n==null?"":x.n+" 条")+'</span></div>'
            +'<div class="tcaps">'+caps.join("")+'</div>'
            +(chips.length?'<div class="tcaps">'+chips.join("")+'</div>':"")
            +fmtDiffs(diffs)
            +'</div>';
        }).join("")+'</div></div>';
  }).join("");
  box.querySelectorAll(".thd").forEach(h=>{
    h.onclick=()=>h.parentNode.classList.toggle("open");
  });
  box.querySelectorAll(".dmorebtn").forEach(b=>{
    b.onclick=()=>{
      const m=b.parentNode.querySelector(".dmore");
      if(!m)return;
      const n=+b.dataset.n||0;
      if(m.hidden){m.hidden=false;b.textContent="收起字段变更（共 "+n+" 条）"}
      else{m.hidden=true;b.textContent="展开其余 "+(n-DIFF_SHOW)+" 条字段变更"}
    };
  });
}
/* 🔴 调用必须在 NOTE_CN/DIFF_SHOW（const）定义**之后** —— JS 的 const 有
   暂时性死区，提前调用会在第一次给 histBox 赋 innerHTML 时抛 ReferenceError：
   症状是 histNote 有文案、时间线区域一片空白且无任何报错提示（eval 静默）。 */
renderHistory();
</script>
</body>
</html>
"""


def _inject(tpl, rows, meta, metajs, hist):
    """数据注入：</script> 防 breakout（HTML 里数据段先于脚本结束标签闭合检查）。"""
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    mblob = json.dumps(meta, ensure_ascii=False).replace("</", "<\\/")
    hblob = json.dumps(hist, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    cats = json.dumps(sorted({r.get("cat") or "" for r in rows}), ensure_ascii=False)
    out = tpl.replace("__ROWS__;", "const ROWS=" + blob + ";\nconst CATS=" + cats + ";")
    out = out.replace("__HIST__;", "const HIST=" + hblob + ";")
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
    # 变化跟踪（快照归档 → diff → 护栏 → 报告 → shct_history）。
    # 🔴 整块兑住：自用源的跟踪挂了不能拖死小页构建，但每一处失败都 log。
    try:
        trk = shct_monitor.run_change_tracking(d, log)
    except Exception as e:
        log("!! 上海变化跟踪异常（不影响小页）：%r" % e)
        trk = None
    # 页面时间线只带裁剪后的样本（近 7 天每轮 30 条 / 更早 12 条）——
    # 磁盘上的 shct_history.json 仍是全量，与主链路 history.json 同一取舍。
    hist = shct_monitor.TM.hist_for_page(shct_monitor.load_history().get("items") or [])
    rows = build_rows(d)
    total = len(rows)
    cats = {r["cat"] for r in rows}
    meta = "共 %d 条 · %s · 采集于 %s" % (
        total, " / ".join("%s %d" % (c, sum(1 for r in rows if r["cat"] == c)) for c in sorted(cats)),
        d.get("fetchedAt", "?"))
    metajs = {"src": src, "fetchedAt": d.get("fetchedAt", ""), "url": d.get("sourceUrl", "")}
    html = _inject(TPL, rows, meta, metajs, hist)

    # 校验：数据容器在、没有把模板占位符留下
    if MARK not in html or "const CATS=" not in html or "const HIST=" not in html:
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
