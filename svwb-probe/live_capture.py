#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之幻境 · 区域截图工具（给 OBS「图片源」喂一个固定画面）

用途
----
从屏幕的**指定矩形**抓图并存成 PNG。配合 `--loop` 持续覆盖同一个文件，
OBS 里用「图片源」读取这个 png，就得到一个独立、可采集的插件画面源 ——
不需要抓整个显示器，也不需要抓透明悬浮层（那种窗口 OBS 会抓成全黑）。

特点
----
· 纯 ctypes BitBlt（GDI），只用标准库 + Pillow；失败时自动回退 ImageGrab。
· 启动先 SetProcessDPIAware()，避免 175% / 150% 缩放下坐标被系统虚拟化。
· 用 GetSystemMetrics / GetDeviceCaps 校验「逻辑像素 vs 物理像素」。
· 写文件用「临时文件 + os.replace」原子替换，OBS 不会读到半张图。

用法
----
    # 抓一次
    python live_capture.py --rect 40,40,420,160 --out out.png

    # 每 0.5 秒覆盖同一个 out.png（供 OBS 图片源）
    python live_capture.py --rect 40,40,420,160 --out out.png --loop 0.5

    # 直接读 live_mode.py --rect-out 生成的矩形
    python live_mode.py --x 40 --y 40 --w 420 --h 160 --rect-out live-rect.json
    python live_capture.py --rect-file live-rect.json --out out.png --loop 0.5
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import re
import sys
import time

if sys.platform != "win32":
    raise SystemExit("live_capture.py 只支持 Windows")

from PIL import Image, ImageGrab, ImageStat


def _init_console():
    """Windows 控制台/重定向时统一 UTF-8，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_init_console()

# ── Win32 ─────────────────────────────────────────────────────────────
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000

# GetDeviceCaps
HORZRES, VERTRES = 8, 10
DESKTOPHORZRES, DESKTOPVERTRES = 118, 117
LOGPIXELSX, LOGPIXELSY = 88, 90


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wt.DWORD * 3)]


user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                         ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                            wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = wt.BOOL
gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int


def enable_dpi_awareness():
    """
    必须在任何窗口/截图之前调用。

    不调用时，175% 缩放下的进程里 GetSystemMetrics 会返回 1463x914 这类
    “逻辑尺寸”，而屏幕上实际是 2560x1600；按逻辑坐标 BitBlt 会抓错位置。
    """
    try:
        user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


DPI_AWARE_CALL_OK = enable_dpi_awareness()


def screen_info():
    """采集屏幕尺寸 / DPI 信息，用于校验坐标是不是物理像素。"""
    info = {
        "primary_w": int(user32.GetSystemMetrics(SM_CXSCREEN)),
        "primary_h": int(user32.GetSystemMetrics(SM_CYSCREEN)),
        "vx": int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        "vy": int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        "vw": int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        "vh": int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
        "gdi_w": 0, "gdi_h": 0, "phys_w": 0, "phys_h": 0,
        "dpi": 0, "dpi_y": 0, "system_dpi": 0,
    }
    hdc = user32.GetDC(None)
    if hdc:
        try:
            info["gdi_w"] = int(gdi32.GetDeviceCaps(hdc, HORZRES))
            info["gdi_h"] = int(gdi32.GetDeviceCaps(hdc, VERTRES))
            info["phys_w"] = int(gdi32.GetDeviceCaps(hdc, DESKTOPHORZRES))
            info["phys_h"] = int(gdi32.GetDeviceCaps(hdc, DESKTOPVERTRES))
            info["dpi"] = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX))
            info["dpi_y"] = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSY))
        finally:
            user32.ReleaseDC(None, hdc)
    try:
        info["system_dpi"] = int(user32.GetDpiForSystem())
    except Exception:
        info["system_dpi"] = info["dpi"]
    return info


def _grab_gdi(x, y, w, h, capture_blt=True):
    """用 BitBlt 抓屏幕矩形，返回 PIL.Image（RGB）。失败抛异常。"""
    srcdc = user32.GetDC(None)
    if not srcdc:
        raise ctypes.WinError(ctypes.get_last_error())
    memdc = gdi32.CreateCompatibleDC(srcdc)
    if not memdc:
        user32.ReleaseDC(None, srcdc)
        raise ctypes.WinError(ctypes.get_last_error())
    bmp = gdi32.CreateCompatibleBitmap(srcdc, w, h)
    if not bmp:
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, srcdc)
        raise ctypes.WinError(ctypes.get_last_error())

    old = gdi32.SelectObject(memdc, bmp)
    try:
        flags = SRCCOPY | (CAPTUREBLT if capture_blt else 0)
        ok = gdi32.BitBlt(memdc, 0, 0, w, h, srcdc, x, y, flags)
        if not ok and capture_blt:
            # 少数驱动不支持 CAPTUREBLT，退回普通 SRCCOPY。
            ok = gdi32.BitBlt(memdc, 0, 0, w, h, srcdc, x, y, SRCCOPY)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        # GetDIBits 要求位图**没有**被选进 DC。
        if old:
            gdi32.SelectObject(memdc, old)
            old = None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # 负数 = 自顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bmi.bmiHeader.biSizeImage = w * h * 4
        buf = (ctypes.c_ubyte * (w * h * 4))()
        got = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi),
                              DIB_RGB_COLORS)
        if got != h:
            raise ctypes.WinError(ctypes.get_last_error())

        image = Image.frombuffer("RGB", (w, h), bytes(buf),
                                 "raw", "BGRX", 0, 1)
        image.load()   # 脱离 buf 生命周期
        return image
    finally:
        if old:
            gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, srcdc)


def save_png_atomic(image, out_path):
    """临时文件 + os.replace，避免 OBS 读到写了一半的 PNG。"""
    out = os.path.abspath(out_path)
    folder = os.path.dirname(out)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = out + ".tmp"
    image.save(tmp, format="PNG")
    last_exc = None
    for _ in range(6):
        try:
            os.replace(tmp, out)
            return out
        except OSError as exc:
            last_exc = exc
            time.sleep(0.04)
    # OBS 可能短暂占着文件；最后退回直接覆盖，宁可多一次重试也不要中断循环。
    try:
        image.save(out, format="PNG")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not os.path.exists(out):
        raise last_exc if last_exc else OSError("写入失败：%s" % out)
    return out


def capture_rect(x, y, w, h, out_path=None, capture_blt=True):
    """
    抓取屏幕矩形 (x, y, w, h)，返回 RGB 的 PIL.Image。

    传了 out_path 就原子地存成 PNG（目录不存在会自动创建）。
    坐标是**物理像素**；函数开头会再确保一次 DPI 感知。
    """
    enable_dpi_awareness()
    try:
        x, y, w, h = int(x), int(y), int(w), int(h)
    except (TypeError, ValueError):
        raise ValueError("矩形必须是四个整数：x, y, w, h")
    if w <= 0 or h <= 0:
        raise ValueError("矩形宽高必须大于 0：%dx%d" % (w, h))

    try:
        image = _grab_gdi(x, y, w, h, capture_blt=capture_blt)
    except Exception:
        # 极少数驱动 / 远程桌面下 BitBlt 失败，退回 Pillow 的 ImageGrab。
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        if image.mode != "RGB":
            image = image.convert("RGB")
        if image.size != (w, h):
            image = image.resize((w, h))

    if out_path:
        save_png_atomic(image, out_path)
    return image


# ──────────────────────────────────────────────────────────────────────
# CLI 辅助
# ──────────────────────────────────────────────────────────────────────
def parse_rect(text):
    parts = [p for p in re.split(r"[,\s]+", str(text).strip()) if p]
    if len(parts) != 4:
        raise ValueError("--rect 需要四个数：x,y,w,h（收到 %r）" % text)
    try:
        x, y, w, h = [int(p, 10) for p in parts]
    except ValueError:
        raise ValueError("--rect 必须是整数：x,y,w,h（收到 %r）" % text)
    if w <= 0 or h <= 0:
        raise ValueError("--rect 的宽高必须大于 0（收到 %dx%d）" % (w, h))
    return x, y, w, h


def rect_from_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return (int(data["x"]), int(data["y"]),
            int(data["w"]), int(data["h"]))


def rect_bounds(x, y, w, h):
    """返回 (是否与虚拟桌面有交集, 是否完整在虚拟桌面内, 交集矩形)。"""
    info = screen_info()
    vx, vy, vw, vh = info["vx"], info["vy"], info["vw"], info["vh"]
    ix1, iy1 = max(x, vx), max(y, vy)
    ix2, iy2 = min(x + w, vx + vw), min(y + h, vy + vh)
    intersects = ix2 > ix1 and iy2 > iy1
    fully = x >= vx and y >= vy and x + w <= vx + vw and y + h <= vy + vh
    return intersects, fully, (ix1, iy1, ix2 - ix1, iy2 - iy1)


def print_screen_info(info):
    dpi = info.get("system_dpi") or info.get("dpi") or 0
    pct = int(round(dpi * 100.0 / 96.0)) if dpi else 0
    print("DPI：system=%s (%s%%)  GDI=%s  SetProcessDPIAware=%s"
          % (dpi or "?", pct or "?", info.get("dpi") or "?",
             "OK" if DPI_AWARE_CALL_OK else "FAIL"))
    print("屏幕：主屏 %dx%d  虚拟桌面 (%d,%d) %dx%d"
          % (info["primary_w"], info["primary_h"], info["vx"], info["vy"],
             info["vw"], info["vh"]))
    print("GDI ：逻辑 %dx%d  物理 %dx%d"
          % (info["gdi_w"], info["gdi_h"], info["phys_w"], info["phys_h"]))
    if info["phys_w"] and info["primary_w"] and \
            info["primary_w"] != info["phys_w"]:
        print("⚠ 警告：GetSystemMetrics 与物理分辨率不一致，"
              "坐标可能被 DPI 虚拟化，截图会错位。", file=sys.stderr)
    elif info["phys_w"] and info["dpi"] and info["dpi"] != 96:
        print("坐标校验：进程拿到的是物理像素（%d DPI，1 逻辑像素 = %.2f 物理像素）"
              % (info["dpi"], info["dpi"] / 96.0))


def build_parser():
    parser = argparse.ArgumentParser(
        description="影之幻境 · 指定矩形区域截图（给 OBS 图片源）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rect", metavar="X,Y,W,H",
                       help="屏幕物理像素矩形，例如 40,40,420,160")
    group.add_argument("--rect-file", metavar="JSON",
                       help="读取 live_mode.py --rect-out 生成的矩形 JSON")
    parser.add_argument("--out", required=True, help="输出 PNG 路径（循环时覆盖）")
    parser.add_argument("--loop", type=float, default=None, metavar="SECONDS",
                        help="循环间隔（秒）；不给则只抓一次")
    parser.add_argument("--frames", type=int, default=0,
                        help="循环时最多抓多少帧（0 = 不限）")
    parser.add_argument("--quiet", action="store_true", help="不打印每帧信息")
    parser.add_argument("--no-captureblt", action="store_true",
                        help="BitBlt 时不加 CAPTUREBLT（默认加，能抓到分层窗口）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        if args.rect:
            x, y, w, h = parse_rect(args.rect)
            rect_source = "--rect"
        else:
            x, y, w, h = rect_from_json(args.rect_file)
            rect_source = args.rect_file
    except Exception as exc:
        print("矩形参数错误：%s" % exc, file=sys.stderr)
        return 2

    info = screen_info()
    print_screen_info(info)

    intersects, fully, inter = rect_bounds(x, y, w, h)
    print("矩形：(%d,%d) %dx%d  来源=%s  完整在屏幕内=%s"
          % (x, y, w, h, rect_source, fully))
    if not intersects:
        print("错误：矩形完全在虚拟桌面 (0,0) %dx%d 之外。"
              % (info["vw"], info["vh"]), file=sys.stderr)
        return 2
    if not fully:
        print("⚠ 警告：矩形部分超出屏幕，越界区域会是黑色（交集 %d,%d %dx%d）。"
              % inter, file=sys.stderr)

    out = os.path.abspath(args.out)
    if os.path.isdir(out):
        print("错误：--out 是目录，请给一个 .png 文件路径：%s" % out,
              file=sys.stderr)
        return 2

    interval = None
    if args.loop is not None:
        interval = max(0.05, float(args.loop))
        if not args.quiet:
            print("循环：每 %.2f 秒覆盖 %s（Ctrl+C 停止）" % (interval, out))

    frames = 0
    try:
        while True:
            started = time.perf_counter()
            try:
                image = capture_rect(x, y, w, h, None,
                                     capture_blt=not args.no_captureblt)
                save_png_atomic(image, out)
            except Exception as exc:
                print("截图失败：%s: %s" % (type(exc).__name__, exc),
                      file=sys.stderr)
                if args.loop is None:
                    return 1
                time.sleep(max(0.5, interval or 0.5))
                continue
            frames += 1
            elapsed = time.perf_counter() - started
            if not args.quiet:
                mean = ImageStat.Stat(image.convert("L")).mean[0]
                print("[%s] #%d %dx%d 平均亮度=%.1f 耗时=%.0fms -> %s"
                      % (time.strftime("%H:%M:%S"), frames, w, h, mean,
                         elapsed * 1000.0, out))

            if interval is None:
                return 0
            if args.frames and frames >= args.frames:
                return 0
            delay = interval - (time.perf_counter() - started)
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\n已停止（Ctrl+C）。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
