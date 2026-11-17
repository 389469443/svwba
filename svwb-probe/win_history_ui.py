#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史胜率面板（控制台「影之诗」页 →「历史胜率」按钮）。

一块一块的卡组卡片：卡组名 + 张数标签 + 「当前使用」角标，
下面一行「共 N 场 · 胜率 X%」、一行「最近使用」，
最底下一个日期下拉（全部日期 / 某一天）和「删除」。

下拉就是「以一天为索引」的入口：选「全部日期」看这套牌的总账，
选某天就看那一天的分账（同一天可能被切卡组切过几次，这里是合并后的）。
视觉照用户给的参考图：圆角深色卡片、粉色描边角标、玫红「删除」。
"""
import os
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

import win_history

try:
    import ui_dpi
except Exception:
    class _NoDpi:
        SCALE = 1.0
    ui_dpi = _NoDpi()

BG = "#12161d"
CARD = "#191f2a"
CARD_HI = "#1d2531"
BD = "#2c3542"
BD_HI = "#3b4759"
FG = "#efe9dc"
DIM = "#9aa7a0"
DIM2 = "#6f7b86"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"
PINK = "#ef7295"
RED = "#c07b6b"
GREEN = "#8fbf8a"
RULE = "#2a3038"


def _rr(cv, x0, y0, x1, y1, r, **kw):
    """圆角矩形（多边形 + smooth，Tk 没有原生圆角）。"""
    r = max(0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
           x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
           x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)
    return cv.create_polygon(pts, smooth=True, **kw)


def _md(day):
    """2026-11-08 → 11/8（卡片上的「最近使用」用）。"""
    try:
        y, m, d = day.split("-")
        return "%d/%d" % (int(m), int(d))
    except Exception:
        return day or "--"


def _fit(font, text, maxw):
    """按宽度截断加省略号。"""
    if font.measure(text) <= maxw:
        return text
    s = text
    while s and font.measure(s + "…") > maxw:
        s = s[:-1]
    return (s + "…") if s else "…"


class Panel:
    def __init__(self, parent=None):
        self.S = getattr(ui_dpi, "SCALE", 1.0)
        win = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.win = win
        win.title("历史胜率")
        win.configure(bg=BG)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            win.attributes("-alpha", 0.95)
        except Exception:
            pass
        if parent is not None:
            try:
                win.transient(parent)
            except Exception:
                pass
        self._apply_title_dark()

        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w = min(self.px(520), max(360, sw - 40))
        h = min(self.px(680), max(420, sh - 80))
        win.geometry("%dx%d" % (w, h))
        win.minsize(self.px(420), self.px(420))

        self.f_title = tkfont.Font(family="Microsoft YaHei UI", size=13,
                                   weight="bold")
        self.f_name = tkfont.Font(family="Microsoft YaHei UI", size=11,
                                  weight="bold")
        self.f_body = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.f_small = tkfont.Font(family="Microsoft YaHei UI", size=8)
        self.scope = {}          # 卡组名/职业名 → 选中的日期（None = 全部）
        self._dd = None          # 打开的日期浮层（{"name": 卡组名}）
        self._dd_rects = {}      # 卡组名 → 下拉框矩形（浮层贴着它往下开）
        self._dd_hot = -1
        self._dd_box = None
        # 统计方式：按卡组 / 按职业（存在账本里，下次打开还是这个选择）
        self.mode = win_history.get_mode()
        self._mode_pop = None
        self._sig = None
        self._build()
        self._render()
        self._poll()

    # ---------------- 基础 ----------------
    def px(self, v):
        return int(round(float(v) * self.S))

    def _apply_title_dark(self):
        def _go():
            try:
                import ctypes
                hwnd = (ctypes.windll.user32.GetParent(self.win.winfo_id())
                        or self.win.winfo_id())
                v = ctypes.c_int(1)
                for attr in (20, 19):
                    try:
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            self.win.after(150, _go)
        except Exception:
            pass

    def _build(self):
        S, win = self.S, self.win
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=self.px(16), pady=(self.px(12), self.px(2)))
        tk.Label(head, text="历史胜率", bg=BG, fg=GOLD,
                 font=self.f_title, anchor="w").pack(side="left")
        tk.Button(head, text="✕", command=win.destroy, bg=BG, fg=RED,
                  relief="flat", bd=0, padx=self.px(6), font=self.f_body,
                  activebackground=BG, cursor="hand2").pack(side="right")
        tk.Button(head, text="刷新", command=self._render, bg=BG, fg=GOLD2,
                  relief="flat", bd=0, padx=self.px(4), font=self.f_body,
                  activebackground=BG, cursor="hand2").pack(side="right")

        tk.Label(win, text="数据来源：记牌器胜率统计 · 切换卡组或每天 00:00 "
                          "自动归档到当天（北京时间）",
                 bg=BG, fg=DIM2, font=self.f_small, anchor="w",
                 justify="left").pack(fill="x", padx=self.px(16))
        tk.Frame(win, bg=RULE, height=1).pack(fill="x", padx=self.px(14),
                                              pady=self.px(8))

        # 统计方式按钮：放在「累计 N 场」这一行的最右边（用户 2026-11-16 指定）。
        # 菜单是独立的 Toplevel，压在内容之上，不会把下面的卡片挤下去。
        self.sum_row = tk.Frame(win, bg=BG)
        self.sum_row.pack(fill="x", padx=self.px(16))
        self.sum_lbl = tk.Label(self.sum_row, text="", bg=BG, fg=FG,
                                font=self.f_body, anchor="w")
        self.sum_lbl.pack(side="left")
        self.mode_btn = tk.Button(self.sum_row, text=self._mode_text(),
                                  command=self._open_mode, bg=BG, fg=GOLD2,
                                  activebackground=BG, relief="flat", bd=0,
                                  padx=self.px(8), font=self.f_body,
                                  cursor="hand2")
        self.mode_btn.pack(side="right")
        self.pend_lbl = tk.Label(win, text="", bg=BG, fg=GOLD2,
                                 font=self.f_small, anchor="w")
        self.pend_lbl.pack(fill="x", padx=self.px(16), pady=(self.px(2), 0))

        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True, padx=(self.px(10), self.px(4)),
                  pady=(self.px(8), 0))
        self.cv = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.cv.pack(side="left", fill="both", expand=True)
        # 不用 tk.Scrollbar：Windows 上它按系统主题画，bg/troughcolor 全被忽略，
        # 在这么暗的面板里就是一坨亮白色块。自己画一条细的。
        self._sy = (0.0, 1.0)
        self._drag = None
        self.cv.configure(yscrollcommand=self._on_scroll)
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cv.bind("<Configure>", self._on_configure)
        self.cv.bind("<Button-1>", self._canvas_click, add="+")

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=self.px(14), pady=self.px(10))
        tk.Button(foot, text="关闭", command=win.destroy, bg=BG, fg=DIM,
                  relief="flat", bd=0, padx=self.px(14), pady=self.px(5),
                  font=self.f_body, activebackground=BG,
                  cursor="hand2").pack(side="right")
        tk.Button(foot, text="清空全部记录", command=self._clear_all, bg=BG,
                  fg=RED, relief="flat", bd=0, padx=self.px(8),
                  font=self.f_small, activebackground=BG,
                  cursor="hand2").pack(side="left")

    def _on_scroll(self, first, last):
        self._sy = (float(first), float(last))
        self._draw_thumb()

    def _draw_thumb(self):
        """右上角一条细滑块：内容装得下就不画。"""
        cv = self.cv
        first, last = self._sy
        cv.delete("thumb")
        if last - first >= 0.999:
            return
        h = cv.winfo_height()
        w = cv.winfo_width()
        if h < 40 or w < 40:
            return
        pad = self.px(2)
        tw = self.px(4)
        x1 = w - pad
        x0 = x1 - tw
        span = h - 2 * pad
        y0 = pad + span * first
        y1 = pad + span * last
        if y1 - y0 < self.px(24):                 # 太短不好抓
            y1 = y0 + self.px(24)
            y0 = min(y0, h - pad - (y1 - y0))
        _rr(cv, x0, y0, x1, y1, tw / 2.0, fill="#3b4759", outline="",
            tags=("thumb",))
        cv.tag_bind("thumb", "<Enter>",
                    lambda e: cv.configure(cursor="sb_v_double_arrow"))
        cv.tag_bind("thumb", "<Leave>", lambda e: cv.configure(cursor=""))
        cv.tag_bind("thumb", "<Button-1>", self._thumb_press)
        cv.tag_bind("thumb", "<B1-Motion>", self._thumb_drag)
        cv.tag_raise("thumb")

    def _thumb_press(self, ev):
        self._drag = (ev.y, self._sy[0], self._sy[1] - self._sy[0])

    def _thumb_drag(self, ev):
        if not self._drag:
            return
        y0, first, size = self._drag
        h = max(1, self.cv.winfo_height())
        frac = (ev.y - y0) / float(h)
        try:
            self.cv.yview_moveto(max(0.0, min(1.0 - size, first + frac)))
        except Exception:
            pass

    def _wheel(self, ev):
        if self._dd:                      # 浮层贴着卡片，一滚就错位，直接收起
            self._close_dd()
        try:
            self.cv.yview_scroll(int(-ev.delta / 120), "units")
        except Exception:
            pass

    def _on_configure(self, ev):
        """宽度变了要重排（卡片是按宽度画的）；只变高时重画一下滑块就够。"""
        w, h = int(ev.width), int(ev.height)
        if w == getattr(self, "_last_w", 0):
            if h != getattr(self, "_last_h", 0):
                self._last_h = h
                self._draw_thumb()
            return
        self._last_w, self._last_h = w, h
        self._render()

    # ---------------- 数据 → 画布 ----------------
    # ---------------- 统计方式（按卡组 / 按职业） ----------------
    def _mode_text(self):
        t = ("按职业统计" if self.mode == win_history.MODE_CLASS
             else "按卡组统计")
        # 用 ▾/▴（实心三角）而不是 ⌄/⌃：小字号下细线条的符号几乎看不清
        return t + ("  ▴" if self._mode_pop is not None else "  ▾")

    def _open_mode(self):
        """点按钮：在它下面弹两个选项。

        用独立 Toplevel 而不是往 window 里 pack 一块：菜单浮在内容之上，
        既不会把下面的卡片往下顶，也不会被画布裁掉（用户 2026-11-16 要求）。
        """
        if self._mode_pop is not None:
            self._close_mode()
            return
        try:
            pop = tk.Toplevel(self.win)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)
            pop.configure(bg=BD_HI)
            box = tk.Frame(pop, bg="#0d121a", padx=1, pady=1)
            box.pack(fill="both", expand=True)
            for key, label in ((win_history.MODE_DECK, "按卡组统计"),
                               (win_history.MODE_CLASS, "按职业统计")):
                on = (key == self.mode)
                row = tk.Label(box, text=("● " if on else "　 ") + label,
                               bg="#0d121a", fg=(GOLD if on else FG),
                               font=self.f_body, anchor="w",
                               padx=self.px(10), pady=self.px(6),
                               cursor="hand2")
                row.pack(fill="x")
                row.bind("<Button-1>", lambda e, k=key: self._set_mode(k))
                row.bind("<Enter>", lambda e, r=row: r.configure(bg=CARD_HI))
                row.bind("<Leave>", lambda e, r=row: r.configure(bg="#0d121a"))
            pop.update_idletasks()
            pw = max(self.px(140), pop.winfo_reqwidth())
            ph = pop.winfo_reqheight()
            bx = self.mode_btn.winfo_rootx()
            by = self.mode_btn.winfo_rooty() + self.mode_btn.winfo_height()
            x = bx + self.mode_btn.winfo_width() - pw
            sw = self.win.winfo_screenwidth()
            x = max(4, min(x, sw - pw - 4))
            pop.geometry("%dx%d+%d+%d"
                         % (pw, ph, int(x), int(by + self.px(4))))
            self._mode_pop = pop
            self.mode_btn.config(text=self._mode_text())
        except Exception:
            self._mode_pop = None

    def _close_mode(self):
        pop = self._mode_pop
        self._mode_pop = None
        if pop is not None:
            try:
                pop.destroy()
            except Exception:
                pass
        try:
            self.mode_btn.config(text=self._mode_text())
        except Exception:
            pass

    def _set_mode(self, key):
        self._close_mode()
        if key == self.mode:
            return
        self.mode = win_history.set_mode(key)
        self.scope = {}          # 作用域从「卡组名」变成「职业名」，旧的作废
        self._dd = None
        self._render()

    def _render(self):
        if not self._alive():
            return
        cv = self.cv
        cv.delete("all")
        self._dd_rects = {}
        self.mode = win_history.get_mode()      # 账本里改过就跟着变
        is_cls = (self.mode == win_history.MODE_CLASS)
        try:
            self.mode_btn.config(text=self._mode_text())
        except Exception:
            pass
        rows = win_history.by_class() if is_cls else win_history.decks()
        self._rows = rows
        summ = win_history.summary()
        self.sum_lbl.config(
            text="累计 %d 场 · 胜率 %.0f%%%s"
                 % (summ["total"], summ["rate"],
                    ("　·　%d 个职业" % len(rows)) if is_cls else ""))
        p = win_history.pending()
        if p["total"]:
            self.pend_lbl.config(
                text="记牌器当前：%s  %d 胜 %d 负 · 尚未归档"
                     % (p["deck"] or "（未选卡组）", p["wins"], p["losses"]),
                fg=GOLD2)
        else:
            self.pend_lbl.config(
                text="记牌器当前：%s  暂无未归档对局"
                     % (p["deck"] or "（未选卡组）"), fg=DIM2)

        W = max(260, cv.winfo_width())
        pad = self.px(12)
        card_h = self.px(124)
        gap = self.px(10)
        y = self.px(2)
        if not rows:
            cv.create_text(W / 2.0, self.px(60),
                           text="还没有历史记录。\n\n打完一局后胜负会自动累计到"
                                "当前卡组；\n切换卡组或每天 00:00 会归档到当天，"
                                "然后记牌器清零。",
                           fill=DIM, font=self.f_body, justify="center",
                           width=W - self.px(50))
        for i, deck in enumerate(rows):
            self._draw_card(i, deck, y, W - pad, card_h)
            y += card_h + gap
        cv.configure(scrollregion=(0, 0, W, max(y, self.px(10))))
        self._draw_dd()          # 浮层最后画，天然压在卡片上
        self._draw_thumb()

    def _draw_card(self, i, deck, y0, x1, ch):
        cv = self.cv
        pad = self.px(12)
        x0 = pad
        active = deck["active"]
        tag = "c%d" % i
        _rr(cv, x0, y0, x1, y0 + ch, self.px(12),
            fill=CARD, outline=(PINK if active else BD),
            width=(1.6 if active else 1.0), tags=(tag,))
        # 行 1：卡组名 + 张数标签 + 「当前使用」
        ty = y0 + self.px(24)
        name = _fit(self.f_name, deck["name"],
                    max(self.px(80), (x1 - x0) - self.px(200)))
        nid = cv.create_text(x0 + self.px(16), ty, text=name, anchor="w",
                             fill=FG, font=self.f_name, tags=(tag,))
        bx = cv.bbox(nid)[2] + self.px(9)
        is_cls = (self.mode == win_history.MODE_CLASS)
        if is_cls:
            # 按职业统计时不显示「40 张」，改成「几套卡组」—— 同名卡组之外
            # 还能一眼看出这个职业底下堆了几套牌（用户 2026-11-16）。
            txt = "%d 套卡组" % (deck.get("n_decks") or 0)
        else:
            size = deck.get("size") or 0
            txt = ("%d 张" % size) if size else ""
        if txt:
            tw = self.f_small.measure(txt)
            _rr(cv, bx, ty - self.px(9), bx + tw + self.px(14), ty + self.px(9),
                self.px(9), fill="", outline=BD_HI, width=1, tags=(tag,))
            cv.create_text(bx + self.px(7) + tw / 2.0, ty, text=txt,
                           fill=DIM, font=self.f_small, tags=(tag,))
        if active:
            txt = "含当前卡组" if is_cls else "当前使用"
            tw = self.f_small.measure(txt)
            rx = x1 - self.px(16) - tw - self.px(14)
            _rr(cv, rx, ty - self.px(9), rx + tw + self.px(14), ty + self.px(9),
                self.px(9), fill="", outline=PINK, width=1, tags=(tag,))
            cv.create_text(rx + self.px(7) + tw / 2.0, ty, text=txt,
                           fill=PINK, font=self.f_small, tags=(tag,))

        # 行 2：场次 / 胜率（选了某天就换成那天的）
        scope = self.scope.get(deck["name"])
        day_rec = None
        if scope:
            day_rec = next((d for d in deck["days"] if d["day"] == scope), None)
        if day_rec is None and scope:
            self.scope.pop(deck["name"], None)      # 那天没了（被删/清空）
        use = day_rec or deck
        y2 = y0 + self.px(52)
        cx = x0 + self.px(16)
        if use["total"]:
            a = ("%s · %d 场" % (_md(use["day"]), use["total"])
                 if day_rec else "共 %d 场" % use["total"])
            tid = cv.create_text(cx, y2, text=a, anchor="w", fill=DIM,
                                 font=self.f_body, tags=(tag,))
            cx = cv.bbox(tid)[2] + self.px(6)
            cv.create_text(cx, y2, text="· 胜率", anchor="w", fill=DIM,
                           font=self.f_body, tags=(tag,))
            cx += self.f_body.measure("· 胜率") + self.px(6)
            cv.create_text(cx, y2, text="%.0f%%" % use["rate"], anchor="w",
                           fill=(GOLD if use["rate"] >= 50 else RED),
                           font=self.f_name, tags=(tag,))
        else:
            cv.create_text(cx, y2, text="还没有对局记录", anchor="w",
                           fill=DIM2, font=self.f_body, tags=(tag,))

        # 行 3：最近使用
        if day_rec:
            sub = "该日 %d 胜 %d 负" % (day_rec["wins"], day_rec["losses"])
        elif deck["days"]:
            sub = "最近使用：%s · 共 %d 天" % (_md(deck["days"][0]["day"]),
                                           len(deck["days"]))
        else:
            sub = "最近使用：--"
        cv.create_text(x0 + self.px(16), y0 + self.px(74), text=sub,
                       anchor="w", fill=DIM2, font=self.f_small, tags=(tag,))

        # 行 4：日期下拉 + 删除
        dy0 = y0 + self.px(88)
        dy1 = dy0 + self.px(26)
        dw = self.px(150)
        dtxt = ("全部日期  ⌄" if not day_rec else "%s  ⌄" % _md(day_rec["day"]))
        dd_open = bool(self._dd) and self._dd.get("name") == deck["name"]
        if dd_open:
            dtxt = dtxt.replace("⌄", "⌃")
        did = _rr(cv, x0 + self.px(14), dy0, x0 + self.px(14) + dw, dy1,
                  self.px(6), fill=("#222b3a" if dd_open else CARD_HI),
                  outline=(GOLD2 if dd_open else BD_HI), width=1,
                  tags=(tag, "dd%d" % i))
        self._dd_rects[deck["name"]] = (x0 + self.px(14), dy0,
                                        x0 + self.px(14) + dw, dy1)
        cv.create_text(x0 + self.px(14) + self.px(11),
                       (dy0 + dy1) / 2.0, text=dtxt, anchor="w", fill=FG,
                       font=self.f_small, tags=(tag, "dd%d" % i))
        # 「删除」只对单套卡组开放：职业行是几套牌加起来的，删它没有明确语义
        if is_cls:
            del_id = None
        else:
            del_id = cv.create_text(x1 - self.px(16), (dy0 + dy1) / 2.0,
                                    text="删除", anchor="e", fill=RED,
                                    font=self.f_small, tags=(tag, "del%d" % i))
            cv.tag_bind("del%d" % i, "<Button-1>",
                        lambda e, d=deck: self._delete(d))
        for t in ("dd%d" % i, "del%d" % i):
            cv.tag_bind(t, "<Enter>",
                        lambda e, c=cv: c.configure(cursor="hand2"))
            cv.tag_bind(t, "<Leave>",
                        lambda e, c=cv: c.configure(cursor=""))
        cv.tag_bind("dd%d" % i, "<Button-1>",
                    lambda e, d=deck: self._open_dd(d))
        return did, del_id

    # ---------------- 日期下拉（自绘浮层） ----------------
    # 不用 tk.Menu：Windows 上 tk_popup 会自带一个模态消息循环，把整个 Tk
    # 主循环卡在里面（动画、定时刷新全停），而且样式是系统的、跟这块面板不搭。
    def _dd_items(self, deck):
        out = [("全部日期（%d 场）" % deck["total"], None)]
        for d in deck["days"]:
            out.append(("%s · %d 场 · %.0f%%" % (_md(d["day"]), d["total"],
                                                 d["rate"]), d["day"]))
        return out

    def _open_dd(self, deck):
        if self._dd and self._dd.get("name") == deck["name"]:
            self._dd = None
        else:
            self._dd = {"name": deck["name"]}
        self._dd_hot = -1
        self._render()

    def _close_dd(self):
        if not self._dd:
            return
        self._dd = None
        self._dd_hot = -1
        self._render()

    def _draw_dd(self):
        """把浮层画在卡片之上：世界坐标，绝对压住下面的卡片，不占布局。"""
        if not self._dd:
            return
        name = self._dd.get("name")
        rect = self._dd_rects.get(name)
        deck = next((d for d in getattr(self, "_rows", []) if d["name"] == name),
                    None)
        if not rect or deck is None:
            self._dd = None
            return
        items = self._dd_items(deck)
        cv = self.cv
        pad = self.px(6)
        ih = self.px(28)
        bx0, by0, bx1, _by1 = rect
        bw = bx1 - bx0
        for txt, _day in items:                    # 至少放得下最长的文字
            bw = max(bw, self.f_small.measure(txt) + self.px(26))
        W = max(260, cv.winfo_width())
        bx1 = min(bx0 + bw, W - self.px(4))
        box_h = ih * len(items) + pad * 2
        ty0 = by0 + self.px(30)
        self._dd_box = (bx0, ty0, bx1, ty0 + box_h)
        _rr(cv, bx0, ty0, bx1, ty0 + box_h, self.px(8),
            fill="#0d121a", outline=BD_HI, width=1, tags=("ddp",))
        for k, (txt, day) in enumerate(items):
            y0 = ty0 + pad + k * ih
            if k == getattr(self, "_dd_hot", -1):
                _rr(cv, bx0 + self.px(4), y0, bx1 - self.px(4), y0 + ih,
                    self.px(5), fill=CARD_HI, outline="", tags=("ddp",))
            chosen = (self.scope.get(name) or None) == day
            cv.create_text(bx0 + self.px(12), y0 + ih / 2.0, text=txt,
                           anchor="w", fill=(GOLD if chosen else FG),
                           font=self.f_small, tags=("ddp", "ddp%d" % k))
            cv.tag_bind("ddp%d" % k, "<Enter>",
                        lambda e, n=k: self._dd_hover(n))
            cv.tag_bind("ddp%d" % k, "<Leave>",
                        lambda e: self._dd_hover(-1))
            cv.tag_bind("ddp%d" % k, "<Button-1>",
                        lambda e, d=day, nm=name: self._pick(nm, d))
        cv.tag_raise("ddp")

    def _dd_hover(self, k):
        if getattr(self, "_dd_hot", -1) == k:
            return
        self._dd_hot = k
        try:
            self.cv.configure(cursor=("hand2" if k >= 0 else ""))
        except Exception:
            pass
        self._render()

    def _canvas_click(self, ev):
        """点浮层 / 下拉框以外的地方：收起浮层。

        用坐标判，不靠「哪个绑定先跑」—— Tk 的画布级绑定和图元级绑定的先后
        顺序在不同版本里不一样，靠顺序会出「点选项反而被关掉」这种 bug。
        """
        if getattr(self, "_mode_pop", None) is not None:
            self._close_mode()
        if not self._dd:
            return
        try:
            x = self.cv.canvasx(ev.x)
            y = self.cv.canvasy(ev.y)
        except Exception:
            return
        for box in (getattr(self, "_dd_box", None),
                    self._dd_rects.get(self._dd.get("name"))):
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return
        self._close_dd()

    # ---------------- 交互 ----------------
    def _pick(self, name, day):
        self.scope[name] = day
        self._dd = None
        self._dd_hot = -1
        self._render()

    def _delete(self, deck):
        if not messagebox.askyesno(
                "删除记录",
                "删除「%s」的历史胜率记录？\n\n只删记录，不影响卡组本身。"
                % deck["name"], parent=self.win):
            return
        win_history.clear_deck(deck["name"])
        self.scope.pop(deck["name"], None)
        self._dd = None
        self._render()

    def _clear_all(self):
        if not messagebox.askyesno(
                "清空全部记录", "把所有卡组的历史胜率记录都删掉？\n\n"
                "只删记录，不影响卡组，也不动记牌器当前的胜率。",
                parent=self.win):
            return
        win_history.clear_all()
        self.scope = {}
        self._dd = None
        self._render()

    # ---------------- 自动刷新 ----------------
    def _alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _sig_now(self):
        def mt(p):
            try:
                return os.path.getmtime(p)
            except Exception:
                return 0
        return (mt(win_history.PATH), mt(win_history.DECKS_PATH),
                mt(os.path.join(os.path.dirname(win_history.PATH),
                                "battle_result.json")),
                win_history.active_deck())

    def _poll(self):
        if not self._alive():
            return
        try:
            sig = self._sig_now()
            if sig != self._sig:
                self._sig = sig
                self._render()
        except Exception:
            pass
        try:
            self.win.after(2500, self._poll)
        except Exception:
            pass


_LAST = {"p": None}


def open_panel(parent=None):
    """同一个控制台只留一个面板；已经开着就把它拎到前面并刷新。"""
    p = _LAST.get("p")
    if p is not None and p._alive():
        try:
            p.win.deiconify()
            p.win.lift()
            p.win.focus_force()
        except Exception:
            pass
        try:
            p._render()
        except Exception:
            pass
        return p.win
    p = Panel(parent)
    _LAST["p"] = p
    return p.win


def demo():  # pragma: no cover - 手动预览
    p = Panel(None)
    p.win.mainloop()


if __name__ == "__main__":
    demo()
