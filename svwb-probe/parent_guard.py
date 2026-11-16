#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
孤儿进程守卫（2026-10-18）

问题：插件关闭后有些面板还留着（控制台被强杀 / 崩溃时，子进程没人收尸）。
做法：控制台启动时写 logs/_app.pid；面板每轮检查这个 pid 还在不在，
     不在了就自己退出。文件不存在时不退出（允许单独启动某个面板调试）。
"""
import ctypes
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(HERE, "logs", "_app.pid")

STILL_ACTIVE = 259


def write_pid(pid=None):
    try:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with io.open(PID_FILE, "w", encoding="ascii") as fh:
            fh.write(str(pid if pid is not None else os.getpid()))
    except Exception:
        pass


def clear_pid():
    try:
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def _alive(pid):
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return True            # 判断不了就当活着，宁可留着


def orphaned():
    """控制台 pid 记录存在、但进程已经没了 → True（面板应当退出）。"""
    try:
        if not os.path.isfile(PID_FILE):
            return False
        with io.open(PID_FILE, encoding="ascii") as fh:
            pid = int((fh.read() or "0").strip())
    except Exception:
        return False
    if pid <= 0:
        return False
    return not _alive(pid)


def running():
    """控制台是否在跑（命名互斥量 Global\\SvwbConsole 是否存在）。

    ⚠️ 不能用 orphaned() 判断：控制台**正常退出**时会删 _app.pid，
    此时 orphaned() 返回 False（看着"没孤儿"），宠物就不会退出——
    这就是"插件关了模型没关"的 bug 根因。
    """
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenMutexW(0x00100000, False, "Global\\SvwbConsole")  # SYNCHRONIZE
        if h:
            k.CloseHandle(h)
            return True
        return False
    except Exception:
        # 判断不了就退回 pid 文件：文件在且进程活着才算在跑
        try:
            if not os.path.isfile(PID_FILE):
                return False
            return not orphaned()
        except Exception:
            return True
