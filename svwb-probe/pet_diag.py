#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：pygame OpenGL 无边框窗口的句柄/位置/可见性。"""
import ctypes
import ctypes.wintypes
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))

import pygame

pygame.init()
screen = pygame.display.set_mode(
    (360, 460), pygame.DOUBLEBUF | pygame.OPENGL | pygame.NOFRAME)
info = pygame.display.get_wm_info()
hwnd = info.get("window", 0)
print("wm_info:", info)

u = ctypes.windll.user32
print("hwnd:", hwnd, "visible:", u.IsWindowVisible(hwnd) if hwnd else "?")

r = ctypes.wintypes.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
print("rect:", r.left, r.top, r.right - r.left, r.bottom - r.top)

sw = u.GetSystemMetrics(0)
sh = u.GetSystemMetrics(1)
print("screen:", sw, sh)

u.SetWindowPos(hwnd, -1, sw - 380, sh - 520, 0, 0, 0x0002)
time.sleep(0.5)
u.GetWindowRect(hwnd, ctypes.byref(r))
print("after move:", r.left, r.top)

import live2d
live2d.init()
live2d.glInit()
model = live2d.Model()
model.LoadModelJson(os.path.join(HERE, "_l2d", "c_0120.model3.json"))
model.Resize(360, 460)

from OpenGL.GL import glClearColor, glClear, GL_COLOR_BUFFER_BIT

t0 = time.time()
while time.time() - t0 < 3:
    pygame.event.pump()
    glClearColor(0.05, 0.06, 0.09, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    model.Update()
    model.Draw()
    pygame.display.flip()
    time.sleep(0.016)

from PIL import ImageGrab
ImageGrab.grab(bbox=(sw - 420, sh - 560, sw, sh)).save(
    os.path.join(HERE, "logs", "pet_diag.png"))
print("shot")
pygame.quit()
