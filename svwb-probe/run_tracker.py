#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键启动：Frida 常驻读取 + 记牌器窗口。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shadow_warchess_reader as RW

pid, _ = RW.find_game_pid()
print("pid", pid)
log = open(os.path.join(HERE, "logs", "frida_live.log"), "w", encoding="utf-8")
if pid:
    subprocess.Popen(
        [sys.executable, "-u", os.path.join(HERE, "frida_live.py"),
         str(pid), "3600"],
        stdout=log, stderr=subprocess.STDOUT, cwd=HERE)
    time.sleep(3)

import deck_reader
print("warm_deck:", deck_reader.warm_deck())

import deck_tracker

w = deck_tracker.open()
try:
    w.root.geometry("+80+80")
    w.root.attributes("-topmost", True)
except Exception:
    pass
w.root.mainloop()
