#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台新功能自测：强制重启 / 模块互斥 / 启动恢复 / 自动识别（源码级）。"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app

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
    check("restart_hard 存在", hasattr(app.App, "restart_hard"))
    check("_force_kill_proc 存在", hasattr(app.App, "_force_kill_proc"))
    check("_stop_other_module 存在", hasattr(app.App, "_stop_other_module"))
    check("_auto_detect_tab 存在", hasattr(app.App, "_auto_detect_tab"))

    sw = inspect.getsource(app.App.switch_tab)
    check("switch_tab 记录 self.tab", "self.tab = key" in sw)
    check("switch_tab 调模块互斥", "_stop_other_module" in sw)

    st = inspect.getsource(app.App._stop_other_module)
    check("互斥会停幻境进程", "overlay" in st and "judge" in st)
    check("互斥会停影之诗进程", "companion" in st and "deck_tracker" in st)

    rr = inspect.getsource(app.App._restore_running)
    check("启动只恢复当前页签模块", 'tab == "hj"' in rr and "deck" in rr)

    tk = inspect.getsource(app.App._tick)
    check("_tick 自动识别已停用（先不做）",
          "# self._auto_detect_tab()" in tk or "暂不启用" in tk)

    ad = inspect.getsource(app.App._auto_detect_tab)
    check("识别幻境用 phase", "phase" in ad)
    check("识别影之诗用 deck_live", "deck_live" in ad)
    check("识别后切页签", "switch_tab" in ad)

    rh = inspect.getsource(app.App.restart_hard)
    check("强制重启含判定器", '"judge"' in rh)
    check("强制重启含 frida", '"frida"' in rh)

    check("restart_console 存在", hasattr(app.App, "restart_console"))
    rc = inspect.getsource(app.App.restart_console)
    check("重启控制台会拉新进程", '"--delay"' in rc and "Popen" in rc)
    check("重启在后台停进程（不卡主线程）", "threading.Thread" in rc)
    check("main 支持 --delay 参数",
          "--delay" in inspect.getsource(app.main))
    with open(os.path.join(HERE, "app.py"), encoding="utf-8") as fh:
        full = fh.read()
    check("设置右边有「重启」按钮", 'text="重启"' in full and "restart_console" in full)

    sa = inspect.getsource(app.App.stop_all)
    check("退出时无条件结束判定器",
          '_force_kill_proc("judge")' in sa
          and "stop_judge_on_exit" not in sa)
    so = inspect.getsource(app.App.start_one)
    check("start_one 不再接管外部判定器",
          "_find_pid" not in so and "_ExternalProc" not in so)
    kl = inspect.getsource(app.App._kill)
    check("_kill 不再保留外部进程", "不主动结束" not in kl)

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
