#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一 DPI 感知（2026-10-18）

不开 DPI 感知时，Windows 会把整个窗口**位图拉伸**（175% 缩放下字是糊的）。
开之后 Tk 按真实分辨率渲染，字就清晰了；窗口/坐标要乘 SCALE 才能保持原来的观感大小。

用法（每个 UI 进程的 main 里、**创建 Tk 之前**调用一次）：
    import ui_dpi
    ui_dpi.enable()
    ...
    width = int(300 * ui_dpi.SCALE)

环境变量 SVWB_NO_DPI=1 可临时关闭（出问题时的退路）。
"""
import os

SCALE = 1.0


def enable():
    """开启 DPI 感知，返回缩放系数（1.0 ~ 3.0）。"""
    global SCALE
    if os.environ.get("SVWB_NO_DPI") == "1":
        SCALE = 1.0
        return SCALE
    try:
        import ctypes
        try:
            # 2 = PROCESS_PER_MONITOR_DPI_AWARE（Win8.1+，文字按真实分辨率渲染）
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            SCALE = float(ctypes.windll.user32.GetDpiForSystem()) / 96.0
        except Exception:
            SCALE = 1.0
    except Exception:
        SCALE = 1.0
    if not (1.0 <= SCALE <= 3.0):
        SCALE = 1.0
    return SCALE


def px(v):
    """逻辑像素 → 物理像素（DPI 感知后要用）"""
    try:
        return int(round(float(v) * SCALE))
    except Exception:
        return int(v)
