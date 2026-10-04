#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PotPlayer 实机验证 Round3：PrintWindow 直接抓窗口内容，无视遮挡"""
import ctypes, ctypes.wintypes as wt, time, subprocess, os
from PIL import Image

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
u32.SetProcessDPIAware()
POT = r"D:\PotPlayer\PotPlayerMini64.exe"
OUT = r"D:\Work\WorkBuddy\_aptv_probe\_pot_verify"
os.makedirs(OUT, exist_ok=True)

class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                ("right", wt.LONG), ("bottom", wt.LONG)]

def find_pot():
    res = []
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            if "PotPlayer" in b.value:
                res.append((hwnd, b.value))
        return True
    u32.EnumWindows(cb, 0)
    return res[0] if res else None

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

def grab_window(hwnd):
    r = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u32.GetWindowDC(hwnd)
    mem = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mem, bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    bmpinfo = BITMAPINFOHEADER()
    bmpinfo.biSize = ctypes.sizeof(bmpinfo)
    bmpinfo.biWidth = w
    bmpinfo.biHeight = -h
    bmpinfo.biPlanes = 1
    bmpinfo.biBitCount = 32
    bmpinfo.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmpinfo), 0)
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    g32.DeleteObject(bmp); g32.DeleteDC(mem); u32.ReleaseDC(hwnd, hdc)
    return img, ok

TESTS = [
    ("R3_CCTV1_河北host",  "http://121.19.134.202:808/tsfile/live/0001_1.m3u8?key=txiptv&playlive=1&authid=0"),
    ("R3_CCTV13_河北host", "http://121.19.134.202:808/tsfile/live/0013_1.m3u8?key=txiptv&playlive=1&authid=0"),
    ("R3_湖南卫视",        "http://1.197.249.105:9901/tsfile/live/0128_1.m3u8?key=txiptv&playlive=1&authid=0"),
    ("R3_OTT自有源CCTV1",  "http://hbgslbserv.taipan.jda.bcs.ottcn.com:6060/030000001001/cctv-1/1.m3u8"),
]

for tag, url in TESTS:
    subprocess.run(["taskkill", "/f", "/im", "PotPlayerMini64.exe"], capture_output=True)
    time.sleep(1.5)
    subprocess.Popen([POT, url], cwd=r"D:\PotPlayer")
    time.sleep(13)
    hit = find_pot()
    if not hit:
        print("[%s] 未找到窗口" % tag)
        continue
    hwnd, title = hit
    time.sleep(2)
    img, ok = grab_window(hwnd)
    p = os.path.join(OUT, tag + ".png")
    img.save(p)
    print("[%s] PrintWindow=%d 标题=%s -> %s" % (tag, ok, title[:50], p))

print("done")
