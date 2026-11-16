#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗陪玩 · Live2D 宠物窗口（2026-10-27 v3）

pygame + OpenGL 渲染鲸鱼娘：
  · 无边框、置顶、可拖动（锁定时不可拖）；
  · 右上角锁图标：点击切换锁定；
  · 右键循环 小/中/大；双击关闭。

注：OpenGL 窗口无法用 GDI 色键透明，暂用深色底（和模型配色融合）。
"""
import ctypes
import ctypes.wintypes
import json
import os
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "_l2d", "c_0120.model3.json")
CONFIG = os.path.join(HERE, "pet_config.json")

SIZES = (200, 260, 340)
DEFAULT_SIZE = 260
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
LOCK_W, LOCK_H = 26, 26


def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"size": DEFAULT_SIZE, "locked": False}


def save_cfg(cfg):
    try:
        with open(CONFIG, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _cursor():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _win_pos(hwnd):
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top


def _move(hwnd, x, y):
    try:
        u = ctypes.windll.user32
        u.SetWindowPos.argtypes = [ctypes.wintypes.HWND,
                                   ctypes.wintypes.HWND,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.wintypes.BOOL
        u.SetWindowPos(hwnd, ctypes.wintypes.HWND(-1),
                       int(x), int(y), 0, 0, SWP_NOSIZE)
    except Exception:
        pass


def _draw_lock(x0, y0, locked):
    """用 OpenGL 画一个小锁（金/灰）。"""
    from OpenGL.GL import glColor3f, glBegin, glEnd, glVertex2f, GL_QUADS
    r, g, b = (0.95, 0.78, 0.30) if locked else (0.55, 0.60, 0.70)
    glColor3f(r, g, b)
    glBegin(GL_QUADS)
    glVertex2f(x0 + 5, y0 + 10)
    glVertex2f(x0 + 21, y0 + 10)
    glVertex2f(x0 + 21, y0 + 24)
    glVertex2f(x0 + 5, y0 + 24)
    glEnd()
    glColor3f(r * 0.8, g * 0.8, b * 0.8)
    glBegin(GL_QUADS)
    glVertex2f(x0 + 8, y0 + 4)
    glVertex2f(x0 + 18, y0 + 4)
    glVertex2f(x0 + 18, y0 + 6)
    glVertex2f(x0 + 8, y0 + 6)
    glEnd()


def run():
    cfg = load_cfg()
    size = int(cfg.get("size") or DEFAULT_SIZE)
    if size not in SIZES:
        size = DEFAULT_SIZE
    locked = bool(cfg.get("locked"))

    import pygame
    from OpenGL.GL import (glClearColor, glClear, GL_COLOR_BUFFER_BIT,
                           glMatrixMode, glLoadIdentity, GL_PROJECTION)
    from OpenGL.GLU import gluOrtho2D
    import live2d

    W = size
    H = int(size * 1.3)

    pygame.init()
    pygame.display.set_mode(
        (W, H), pygame.DOUBLEBUF | pygame.OPENGL | pygame.NOFRAME)
    pygame.display.set_caption("coo")
    hwnd = pygame.display.get_wm_info().get("window", 0)

    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    print("DBG screen", sw, sh, "win", W, H, "hwnd", hwnd, flush=True)
    _move(hwnd, sw - W - 30, sh - H - 90)

    live2d.init()
    live2d.glInit()
    model = live2d.Model()
    model.LoadModelJson(MODEL)
    model.Resize(W, H)

    def _resize(new):
        nonlocal W, H, size
        size = new
        W = size
        H = int(size * 1.3)

    drag = None
    last_click = 0.0

    def _open_settings():
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        m = tk.Menu(root, tearoff=0)

        def _toggle_lock():
            nonlocal locked
            locked = not locked
            cfg["locked"] = locked
            save_cfg(cfg)

        def _set_size(n):
            _resize(n)
            cfg["size"] = size
            save_cfg(cfg)
            pygame.display.set_mode(
                (W, H), pygame.DOUBLEBUF | pygame.OPENGL | pygame.NOFRAME)
            try:
                model.Resize(W, H)
            except Exception:
                pass

        m.add_command(label=("解锁位置" if locked else "锁定位置"),
                      command=_toggle_lock)
        m.add_separator()
        labels = {200: "小 (200)", 260: "中 (260)", 340: "大 (340)"}
        for n in SIZES:
            m.add_command(label=labels.get(n, str(n)),
                          command=lambda n=n: _set_size(n))
        m.add_separator()
        m.add_command(label="关闭",
                      command=lambda: pygame.event.post(
                          pygame.event.Event(pygame.QUIT)))
        try:
            m.tk_popup(*_cursor())
        finally:
            try:
                root.update()
                root.destroy()
            except Exception:
                pass

    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return
            if e.type == pygame.MOUSEBUTTONDOWN:
                if e.button == 1:
                    now = time.time()
                    if now - last_click < 0.35:
                        return
                    last_click = now
                    if not locked:
                        cx, cy = _cursor()
                        wx, wy = _win_pos(hwnd)
                        drag = (cx - wx, cy - wy)
                elif e.button == 3:
                    _open_settings()
            elif e.type == pygame.MOUSEBUTTONUP:
                drag = None
        if drag and not locked:
            try:
                cx, cy = _cursor()
                _move(hwnd, cx - drag[0], cy - drag[1])
            except Exception:
                pass

        glClearColor(0.055, 0.065, 0.095, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        model.Update()
        model.Draw()
        pygame.display.flip()
        time.sleep(0.016)


if __name__ == "__main__":
    run()
