#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡组窗口开窗冒烟：真实 Tk 窗口，检查能开、在屏内、无异常，然后自动关闭。"""
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


def toplevels(root):
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


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
    f.open_deck_import = types.MethodType(appmod.App.open_deck_import, f)
    f.open_deck_list = types.MethodType(appmod.App.open_deck_list, f)

    def _fake_toplevel(self, title, geometry, alpha=0.9):
        w = tk.Toplevel(self.root)
        w.title(title)
        try:
            w.attributes("-alpha", alpha)
        except Exception:
            pass
        return w

    f._toplevel = types.MethodType(_fake_toplevel, f)

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    try:
        f.open_deck_import()
        root.update()
        wins = toplevels(root)
        check("导入窗口打开了", len(wins) >= 1, len(wins))
        if wins:
            w = wins[-1]
            geo = w.winfo_geometry()
            check("导入窗口在屏内", w.winfo_x() < sw and w.winfo_y() < sh,
                  geo)
            w.destroy()

        f.open_deck_list()
        root.update()
        wins = toplevels(root)
        check("查看窗口打开了", len(wins) >= 1, len(wins))
        if wins:
            w = wins[-1]
            check("查看窗口在屏内", w.winfo_x() < sw and w.winfo_y() < sh,
                  w.winfo_geometry())
            w.destroy()
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
