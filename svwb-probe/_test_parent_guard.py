#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parent_guard.running() / 宠物退出联动 / 右键菜单 的离线自测。

不弹窗、不启动宠物；只操作互斥量 + 读源码。
"""
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import parent_guard as PG

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def main():
    print("== 1. running() ==")
    base = PG.running()
    print("  初始 running():", base)
    # 模拟一个控制台持有互斥量
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k.CreateMutexW(None, False, "Global\\SvwbConsole")
    check("模拟控制台后 running()=True", PG.running() is True)
    k.CloseHandle(h)
    check("关闭互斥量后回到初始值", PG.running() == base, PG.running())

    print("== 2. orphaned() 语义 ==")
    PG.write_pid(99999)          # 不存在的 pid
    check("死 pid：orphaned()=True", PG.orphaned() is True)
    # 控制台可能正在运行（互斥量存在），此时 running() 本来就该是 True
    check("死 pid：running() 不受死 pid 影响",
          base or PG.running() is False, (base, PG.running()))
    PG.clear_pid()
    check("无 pid 文件：orphaned()=False（原 bug 根因）", PG.orphaned() is False)
    check("无 pid 文件：running() 只看互斥量",
          base or PG.running() is False, (base, PG.running()))

    print("== 3. 宠物退出接线 ==")
    src = open(os.path.join(HERE, "run_companion.py"), encoding="utf-8").read()
    check("run_companion tick 用 running()", "parent_guard.running()" in src)
    check("run_companion 不再只用 orphaned()",
          "parent_guard.orphaned()" not in src)
    pv = open(os.path.join(HERE, "pet_view.py"), encoding="utf-8").read()
    check("pet_view 启动检查用 running()", "parent_guard.running()" in pv)
    check("右键菜单没有「导入模型」", '"導入模型…"' not in pv and "导入模型…" not in pv)
    check("打开控制台会激活已有窗口",
          "FindWindowW" in pv and "SetForegroundWindow" in pv)

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
