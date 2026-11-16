#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗陪玩 · 独立气泡窗口（2026-10-27）

左边鲸鱼娘头像，右边气泡显示 AI 短评；无边框、置顶、可拖动；
右键菜单可关闭；气泡 12 秒后自动收起。
"""
import os
import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
AVATAR = os.path.join(HERE, "assets", "whale.png")

BG_KEY = "#010203"          # 透明色键
BUBBLE_BG = "#1d2433"
TEXT_FG = "#e8edf7"
TEXT_DIM = "#9aa7bd"

WIN_W, WIN_H = 420, 220
AVATAR_SIZE = 176
BUBBLE_W, BUBBLE_H = 216, 168
BUBBLE_RADIUS = 18


def _avatar_image(size):
    im = Image.open(AVATAR).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=26, fill=255)
    im.putalpha(mask)
    return im


def _bubble_image(w, h, radius=BUBBLE_RADIUS):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=BUBBLE_BG)
    return im


class BubbleWindow:
    def __init__(self, master=None):
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_KEY)
        try:
            self.root.attributes("-transparentcolor", BG_KEY)
        except Exception:
            pass

        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                                bg=BG_KEY, bd=0, highlightthickness=0)
        self.canvas.pack()

        # 头像
        self._av = ImageTk.PhotoImage(_avatar_image(AVATAR_SIZE))
        self.av_id = self.canvas.create_image(0, (WIN_H - AVATAR_SIZE) // 2,
                                              image=self._av, anchor="nw")
        # 气泡底
        self._bub = ImageTk.PhotoImage(_bubble_image(BUBBLE_W, BUBBLE_H))
        bx = AVATAR_SIZE + 14
        by = (WIN_H - BUBBLE_H) // 2
        self.bub_id = self.canvas.create_image(bx, by, image=self._bub,
                                               anchor="nw", state="hidden")
        # 名字
        self.canvas.create_text(bx + 16, by + 14, text="鲸鲸", anchor="nw",
                                fill=TEXT_DIM, font=("Microsoft YaHei UI", 9))
        # 正文
        self.text_id = self.canvas.create_text(
            bx + 16, by + 40, text="", anchor="nw", fill=TEXT_FG,
            font=("Microsoft YaHei UI", 10), width=BUBBLE_W - 32)

        self._after_hide = None
        self._drag = None
        for w in (self.canvas,):
            w.bind("<Button-1>", self._press)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._release)
            w.bind("<Button-3>", self._menu)
        self.root.bind("<Double-Button-1>", lambda e: self.close())

        # 默认位置：屏幕右下
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry("+%d+%d" % (sw - WIN_W - 40, sh - WIN_H - 90))
        except Exception:
            self.root.geometry("+1000+600")

        self._menu = tk.Menu(self.root, tearoff=0, bg=BUBBLE_BG, fg=TEXT_FG)
        self._menu.add_command(label="收起气泡", command=self.hide_bubble)
        self._menu.add_command(label="关闭", command=self.close)

    # ---------------- 拖动 ----------------
    def _press(self, ev):
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        if not self._drag:
            return
        self.root.geometry("+%d+%d" % (ev.x_root - self._drag[0],
                                       ev.y_root - self._drag[1]))

    def _release(self, ev):
        self._drag = None

    def _menu(self, ev):
        try:
            self._menu.tk_popup(ev.x_root, ev.y_root)
        except Exception:
            pass

    # ---------------- 显示/隐藏 ----------------
    def show(self, text, hide_after=12000):
        text = (text or "").strip()
        if not text:
            return

        def _u():
            try:
                self.canvas.itemconfig(self.bub_id, state="normal")
                self.canvas.itemconfig(self.text_id, text=text)
                if self._after_hide:
                    try:
                        self.root.after_cancel(self._after_hide)
                    except Exception:
                        pass
                if hide_after:
                    self._after_hide = self.root.after(hide_after,
                                                       self.hide_bubble)
            except Exception:
                pass

        try:
            self.root.after(0, _u)
        except Exception:
            pass

    def hide_bubble(self):
        try:
            self.canvas.itemconfig(self.bub_id, state="hidden")
            self.canvas.itemconfig(self.text_id, text="")
        except Exception:
            pass
        self._after_hide = None

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    w = BubbleWindow()
    if len(sys.argv) > 1:
        w.show(" ".join(sys.argv[1:]))
    else:
        w.show("抽到一张好牌啦，主人这波稳了～")
    w.root.mainloop()


if __name__ == "__main__":
    main()
