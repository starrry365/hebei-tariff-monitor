#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标（纯 stdlib，零依赖，可复现）

为什么自己画而不是找一张图：
  ① 仓库是公开的，图标得能**跟着代码走**——放一张来源不明的 png 进来，
     以后没人说得清它能不能用、改颜色要不要重做；
  ② 页面自己的主题色就写着（--op1 #0085d0 / --op1d #0067b3），
     图标跟它对齐才不会「加到主屏后像别人家的」；
  ③ 手写 PNG 不需要 Pillow —— CI 里少一个依赖就少一个断点。

图案：品牌蓝渐变底 + 三条递增白色信号条（「资费/流量」的通用意象）+ 底部基线。
  圆角和竖条都按**归一化坐标**画，所以 32 到 512 是同一套几何，不会糊。

用法：
    python tools/make_icons.py                  # 生成到 cloud/tariff/docs/icons/
    python tools/make_icons.py --out /tmp/icons
"""
import argparse
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DEFAULT = os.path.join(ROOT, "cloud", "tariff", "docs", "icons")

# 与页面 :root 的主题色对齐
C_TOP = (0x00, 0x85, 0xD0)      # --op1
C_BOT = (0x00, 0x67, 0xB3)      # --op1d
C_FG = (0xFF, 0xFF, 0xFF)

#: 要出的尺寸 → 文件名（含 Android / iOS / 浏览器 favicon）
SIZES = ((512, "icon-512.png"), (192, "icon-192.png"),
         (180, "apple-touch-icon.png"), (32, "favicon-32.png"))


def _png(path, w, h, rows):
    """rows: list[bytes]，每行是 RGBA 字节串（长度 = w*4）。"""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + r for r in rows)      # 每行前面一个 filter byte
    out = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(out)


def _rounded_alpha(x, y, w, h, radius):
    """圆角遮罩：在四角区域外返回 0（透明），其余 255。"""
    cx = min(x, w - 1 - x)
    cy = min(y, h - 1 - y)
    if cx >= radius or cy >= radius:
        return 255
    dx, dy = radius - cx, radius - cy
    if dx * dx + dy * dy <= radius * radius:
        return 255
    return 0


def draw(size):
    """画一张 size×size 的图标 → list[bytes]（RGBA 行）。"""
    w = h = size
    radius = max(2, int(size * 0.22))            # 圆角 ≈ iOS 图标的观感
    # 三条信号条：(左边距, 宽度, 高度占比)，都按归一化算
    bars = ((0.24, 0.14, 0.36), (0.43, 0.14, 0.56), (0.62, 0.14, 0.76))
    base_y = 0.78                               # 底部基线的高度占比
    base_h = 0.06

    rows = []
    for y in range(h):
        row = bytearray()
        t = y / float(h - 1) if h > 1 else 0.0
        bg = tuple(int(round(C_TOP[i] + (C_BOT[i] - C_TOP[i]) * t)) for i in range(3))
        for x in range(w):
            a = _rounded_alpha(x, y, w, h, radius)
            r, g, b = bg
            # 竖条
            for (bx, bw, bh) in bars:
                x0, x1 = bx * w, (bx + bw) * w
                y0 = (base_y - bh) * h
                y1 = base_y * h
                if x0 <= x < x1 and y0 <= y < y1:
                    r, g, b = C_FG
                    break
            else:
                # 底部基线
                if base_y * h <= y < (base_y + base_h) * h and 0.24 * w <= x < 0.76 * w:
                    r, g, b = C_FG
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    return rows


def main():
    ap = argparse.ArgumentParser(description="生成 PWA 图标")
    ap.add_argument("--out", default=OUT_DEFAULT)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for size, name in SIZES:
        p = os.path.join(args.out, name)
        _png(p, size, size, draw(size))
        print("  %-24s %4d×%-4d %6d B" % (name, size, size, os.path.getsize(p)))
    print("图标已生成 → %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
