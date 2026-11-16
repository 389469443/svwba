#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live2d-py 渲染测试：先建 OpenGL 上下文，再 init live2d。"""
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "_l2d", "c_0120.model3.json")
W, H = 420, 520

import pygame

pygame.init()
screen = pygame.display.set_mode((W, H), pygame.DOUBLEBUF | pygame.OPENGL)
pygame.display.set_caption("l2d")

import live2d

live2d.init()
live2d.glInit()

model = live2d.Model()
model.LoadModelJson(MODEL)
model.Resize(W, H)

from OpenGL.GL import glClearColor, glClear, GL_COLOR_BUFFER_BIT

t0 = time.time()
shot = False
while time.time() - t0 < 4.0:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            raise SystemExit
    glClearColor(0.05, 0.06, 0.09, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    model.Update()
    model.Draw()
    pygame.display.flip()
    time.sleep(0.016)
    if not shot and time.time() - t0 > 1.5:
        shot = True
        from PIL import ImageGrab
        ImageGrab.grab().save(os.path.join(HERE, "logs", "l2d_screen.png"))
        print("shot")
pygame.quit()
print("done")
