#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""源码包 —— 把仓库源码打成一个 zip，随页面一起发布

为什么要打进站点：站点是公开的（GitHub Pages），但源码在仓库里，
对「只想拿走一份可跑的代码、不想 clone 整个仓库」的人不方便；
另外站点自带一份源码包，等于给「页面长这样」留了个可复核的底。

🔴 入包黑名单 —— 与 .gitignore **同源**，但必须在代码里再挡一次：
    .env / .nrapigate_key / .fiddler_mcp_key  → 凭据。本仓库是**公开**仓库，
                                                 一旦打进去等于直接公开密钥。
    snapshots/ · .*_cache.json · .ct_raw.json → 数据快照，几十 MB 且属产物
    docs/index.html                           → 11 MB 的生成物
    __pycache__ / *.pyc / .git                → 噪音
  靠「记得排除」是不够的：所以打完包会**再验一遍**（见 audit()），
  发现漏网的凭据或数据文件直接报错退出，不让它发出去。

为什么固定 date_time：zip 默认把文件 mtime 写进条目头，
每次 checkout 的 mtime 都不同 ⇒ 同一份源码每打一次 zip 字节都不同 ⇒
CI 每次都会产生一个无意义的提交。钉死时间戳后 zip 变成**确定性**的：
源码没改，打出来的字节完全一致。

用法：
    python cloud/tariff/make_source_zip.py            # 打包
    python cloud/tariff/make_source_zip.py --dry      # 只报会收哪些文件
    python cloud/tariff/make_source_zip.py --out /tmp/x.zip
"""
import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DEFAULT = os.path.join(ROOT, "cloud", "tariff", "docs",
                           "hebei-tariff-monitor-source.zip")
ARC_PREFIX = "hebei-tariff-monitor"

EXCLUDE_DIRS = {".git", "__pycache__", ".conformance", "snapshots",
                "chrome-prof", "cdp_prof", ".venv", "venv", "node_modules"}
EXCLUDE_NAMES = {".env", ".env.local", ".nrapigate_key", ".fiddler_mcp_key",
                 ".ct_raw.json", ".nojekyll", ".DS_Store"}
EXCLUDE_SUFFIX = (".pyc", ".zip", ".gz", ".log", ".bak", ".tmp", ".new")
#: evidence/ 下的 UI 取证截图：单张 400–700 KB，一个目录就 8 MB。
#: 它们是「当时界面长这样」的材料，不是源码 —— 留在仓库里做取证，
#: 但不该让每个下载源码包的人都拖 8 MB 走。
EXCLUDE_IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
#: 相对仓库根的精确排除（生成物 / 体积过大）
EXCLUDE_REL = {"cloud/tariff/docs/index.html",
               "cloud/tariff/page/index.html.gz"}


def keep(rel):
    """rel 用 / 分隔。返回 True 表示可以入包。"""
    parts = rel.split("/")
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return False
    name = parts[-1]
    if name in EXCLUDE_NAMES:
        return False
    if name.endswith(EXCLUDE_SUFFIX):
        return False
    # 隐藏缓存：.unicom_cache.json / .cbn_cache.json …
    if name.startswith(".") and name.endswith(".json"):
        return False
    # 取证截图（只针对 evidence/，别误伤将来的站点图标）
    if "evidence/" in rel and name.lower().endswith(EXCLUDE_IMAGE_SUFFIX):
        return False
    if rel in EXCLUDE_REL:
        return False
    return True


def collect():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if keep(rel):
                out.append(rel)
    return sorted(out)


def audit(names):
    """打包前/后的最后一道闸：凭据与数据文件一个都不许在包里。

    返回问题列表；空列表 = 干净。
    """
    bad = []
    for n in names:
        low = n.lower()
        base = low.rsplit("/", 1)[-1]
        if base in {".env", ".nrapigate_key", ".fiddler_mcp_key"}:
            bad.append(n)
        elif "cookie" in base or base.endswith(".ct_raw.json"):
            bad.append(n)
        elif base.startswith(".") and base.endswith("_cache.json"):
            bad.append(n)
        elif "/snapshots/" in n or n.startswith("snapshots/"):
            bad.append(n)
    return bad


def build(out, verbose=True):
    files = collect()
    bad = audit(files)
    if bad:
        print("::error::以下文件不该入包（凭据/数据）：")
        for b in bad:
            print("   ✗", b)
        return None, files, bad

    os.makedirs(os.path.dirname(out), exist_ok=True)
    fixed = (2020, 1, 1, 0, 0, 0)          # 固定时间戳 ⇒ 确定性 zip
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            zi = zipfile.ZipInfo("%s/%s" % (ARC_PREFIX, rel), date_time=fixed)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(os.path.join(ROOT, rel), "rb") as f:
                z.writestr(zi, f.read())
    os.replace(tmp, out)
    if verbose:
        size = os.path.getsize(out)
        print("已打包 %d 个文件 → %s（%.1f KB）"
              % (len(files), os.path.relpath(out, ROOT), size / 1024.0))
    return out, files, []


def main():
    ap = argparse.ArgumentParser(description="打源码包（凭据与数据不入包）")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry", action="store_true", help="只列出会收哪些文件")
    args = ap.parse_args()

    if args.dry:
        files = collect()
        bad = audit(files)
        print("将入包 %d 个文件：" % len(files))
        for f in files:
            print("   ", f)
        print("\n凭据/数据审计：%s" % ("❌ 发现 %d 个问题" % len(bad) if bad
                                      else "✅ 干净"))
        return 1 if bad else 0

    out, files, bad = build(args.out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
