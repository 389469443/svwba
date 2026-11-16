#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""功能窗口透明度自测：卡组导入/查看窗口必须是半透明。"""
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app as appmod

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
    root = tk.Tk()
    root.withdraw()

    class Fake:
        pass

    f = Fake()
    f.root = root
    f.f_s = tkfont.Font(family="Microsoft YaHei", size=10)
    f.f_s_wrap = tkfont.Font(family="Microsoft YaHei", size=9)
    f.deck_status = tk.Label(root, text="")
    f._decks_path = types.MethodType(appmod.App._decks_path, f)
    f._refresh_deck_status = types.MethodType(
        appmod.App._refresh_deck_status, f)
    f._toplevel = types.MethodType(appmod.App._toplevel, f)
    f.open_deck_import = types.MethodType(appmod.App.open_deck_import, f)
    f.open_deck_list = types.MethodType(appmod.App.open_deck_list, f)

    def toplevels():
        return [w for w in root.winfo_children()
                if isinstance(w, tk.Toplevel)]

    try:
        win = f._toplevel("t", "300x200")
        a = float(win.attributes("-alpha"))
        check("_toplevel 默认 0.9 透明", abs(a - 0.9) < 0.02, a)
        win.destroy()

        f.open_deck_import()
        root.update()
        wins = toplevels()
        check("导入窗口打开了", len(wins) >= 1)
        if wins:
            a = float(wins[-1].attributes("-alpha"))
            check("导入窗口透明", abs(a - 0.9) < 0.02, a)
            wins[-1].destroy()

        f.open_deck_list()
        root.update()
        wins = toplevels()
        check("查看窗口打开了", len(wins) >= 1)
        if wins:
            a = float(wins[-1].attributes("-alpha"))
            check("查看窗口透明", abs(a - 0.9) < 0.02, a)
            wins[-1].destroy()
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
