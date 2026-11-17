#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陪玩独立气泡启动器：气泡窗口 + AI 陪玩 + （可选）Frida 常驻读取。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import singleton
if not singleton.acquire("SvwbPet"):
    print("宠物已经在运行")
    raise SystemExit(0)

# 宠物只允许从控制台启动：控制台没开就拒绝，避免“插件关了模型还在”
try:
    import parent_guard
    if not parent_guard.running():
        print("请先从控制台启动 AI 陪玩")
        raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    pass

import pet_view
import companion

AUTO_FRIDA = "--no-frida" not in sys.argv

w = pet_view.PetView()
c = companion.Companion(on_text=w.show, on_state=w.show)


def _ensure_frida():
    """游戏在、但 logs/deck_live.json 不新鲜时，启动 frida_live。"""
    try:
        import shadow_warchess_reader as RW
        pid, _ = RW.find_game_pid()
        if not pid:
            return
        p = os.path.join(HERE, "logs", "deck_live.json")
        fresh = False
        try:
            fresh = (time.time() - os.path.getmtime(p)) < 30
        except Exception:
            fresh = False
        if fresh:
            return
        log = open(os.path.join(HERE, "logs", "frida_live.log"), "a",
                   encoding="utf-8")
        subprocess.Popen(
            [sys.executable, "-u", os.path.join(HERE, "frida_live.py"),
             str(pid), "7200"],
            stdout=log, stderr=subprocess.STDOUT, cwd=HERE)
    except Exception:
        pass


if AUTO_FRIDA:
    try:
        _ensure_frida()
    except Exception:
        pass


def tick():
    # 控制台退出后自动收尸，别留孤儿宠物窗口
    # ⚠️ 用 running() 而不是 orphaned()：控制台正常退出会删 _app.pid，
    #    orphaned() 那时返回 False，宠物会一直留着（"插件关了模型没关"）。
    try:
        import parent_guard
        if not parent_guard.running():
            try:
                w.close()
            except Exception:
                pass
            os._exit(0)
    except Exception:
        pass
    try:
        c.tick()
    except Exception:
        pass
    try:
        w.root.after(2000, tick)
    except Exception:
        pass


w.root.after(1500, tick)
w.run()
