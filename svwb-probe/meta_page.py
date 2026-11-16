#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阵容统计页（2026-10-19）

用户规则：
  · **只有连接服务器后才会收集数据，否则都不显示这个页面** → `open()` 先过
    `meta_client.available()`，不满足就直接不创建窗口。
  · 页面上常驻「数据不足，仅供参考」。

数据来自服务器下发的 `logs/meta_cache.json`（本地只读展示，不在这里联网）。
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import meta_client  # noqa: E402
import meta_stats    # noqa: E402

NOTE = "数据不足，仅供参考"

BG = "#12161d"
BG2 = "#171c25"
BORDER = "#3a3a44"
FG = "#efe9dc"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"
DIM = "#9aa7a0"
RULE = "#2a3038"

_OPEN = [None]


def render(cache):
    """
    纯函数：把缓存转成可直接渲染的数据。便于离线自测。
    兼容两种形状：
      {"updatedAt","server","stats":{"total","teams","cards","enough_min"}}
      {"total","teams","cards","enough_min"}（直接是 aggregate 结果）
    """
    out = {"note": NOTE, "status": "", "rows": [], "cards": [],
           "updated": "", "server": ""}
    if not cache:
        out["status"] = "暂无数据（服务器还没有下发统计）"
        return out
    stats = cache.get("stats") if isinstance(cache.get("stats"), dict) else cache
    teams = stats.get("teams") or []
    cards = stats.get("cards") or {}
    total = stats.get("total")
    out["updated"] = str(cache.get("updatedAt") or "")
    out["server"] = str(cache.get("server") or "")
    out["status"] = "样本 %s 局%s" % (
        total if total is not None else "?",
        (" · 更新于 " + out["updated"]) if out["updated"] else "")
    names = cache.get("names") or {}
    for t in teams[:50]:
        out["rows"].append({
            "team": meta_stats.describe_team(t, names),
            "samples": int(t.get("samples") or 0),
            "first": "%.1f%%" % (float(t.get("first_rate") or 0) * 100),
            "top4": "%.1f%%" % (float(t.get("top4_rate") or 0) * 100),
            "avg": "%.2f" % float(t.get("avg_rank") or 0),
            "enough": bool(t.get("enough")),
        })
    for cid, c in sorted(cards.items(),
                         key=lambda kv: (-(kv[1].get("champion_rate") or 0),
                                         -(kv[1].get("appearances") or 0)))[:40]:
        out["cards"].append({
            "name": names.get(str(cid)) or names.get(int(cid)) or ("#%s" % cid),
            "in_first": int(c.get("in_first") or 0),
            "apps": int(c.get("appearances") or 0),
            "rate": "%.2f" % float(c.get("champion_rate") or 0),
        })
    if not out["rows"]:
        out["status"] = "暂无数据（服务器还没有下发统计）"
    return out


def _load_names():
    try:
        return meta_stats.load_cards_db()
    except Exception:
        return {}


def _font(size, bold=False):
    import tkinter.font as tkfont
    return tkfont.Font(family="Microsoft YaHei UI", size=size,
                       weight="bold" if bold else "normal")


class MetaWindow:
    def __init__(self, master=None):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.title("影之幻境 · 阵容统计")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.f_t = _font(11, True)
        self.f_s = _font(9)
        self.f_note = _font(10, True)

        pad = tk.Frame(self.root, bg=BG)
        pad.pack(fill="both", expand=True, padx=14, pady=10)

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="阵容统计", bg=BG, fg=GOLD, font=self.f_t,
                 anchor="w").pack(side="left")
        tk.Button(head, text="✕", command=self.close, bg=BG, fg=DIM,
                  relief="flat", bd=0, padx=6, font=self.f_s,
                  activebackground=BG, cursor="hand2").pack(side="right")

        # 用户要求：这一行常驻
        tk.Label(pad, text=NOTE, bg=BG, fg=GOLD2, font=self.f_note,
                 anchor="w").pack(fill="x", pady=(2, 0))
        self.status = tk.Label(pad, text="", bg=BG, fg=DIM, font=self.f_s,
                               anchor="w")
        self.status.pack(fill="x")
        tk.Frame(pad, bg=RULE, height=1).pack(fill="x", pady=6)

        self.table = tk.Frame(pad, bg=BG)
        self.table.pack(fill="both", expand=True)

        bar = tk.Frame(pad, bg=BG)
        bar.pack(fill="x", pady=(8, 0))
        tk.Button(bar, text="刷新", command=self.refresh, bg=GOLD, fg="#1a1209",
                  relief="flat", bd=0, padx=14, pady=4, font=self.f_s,
                  activebackground="#d8b87a", cursor="hand2").pack(side="right")
        tk.Label(bar, text="样本 < 30 不参与排名；数据来自统计服务器",
                 bg=BG, fg=GOLD2, font=self.f_s).pack(side="left")

        self.refresh()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def refresh(self):
        tk = self.tk
        for w in self.table.winfo_children():
            w.destroy()
        data = render(meta_client.load_cache())
        self.status.config(text=data["status"])

        hdr = ("阵容", "样本", "吃鸡率", "前四率", "均名")
        for col, text in enumerate(hdr):
            tk.Label(self.table, text=text, bg=BG, fg=GOLD2, font=self.f_s,
                     anchor="w" if col == 0 else "e").grid(
                row=0, column=col, sticky="we", padx=4, pady=(0, 4))
        if not data["rows"]:
            tk.Label(self.table, text=data["status"], bg=BG, fg=DIM,
                     font=self.f_s, anchor="w").grid(
                row=1, column=0, columnspan=5, sticky="w", padx=4)
            return
        for r, row in enumerate(data["rows"], start=1):
            tk.Label(self.table, text=row["team"], bg=BG, fg=FG, font=self.f_s,
                     anchor="w").grid(row=r, column=0, sticky="we", padx=4)
            tk.Label(self.table, text=str(row["samples"]), bg=BG, fg=FG,
                     font=self.f_s).grid(row=r, column=1, sticky="e", padx=4)
            tk.Label(self.table, text=row["first"], bg=BG,
                     fg=GOLD if row["enough"] else DIM, font=self.f_s).grid(
                row=r, column=2, sticky="e", padx=4)
            tk.Label(self.table, text=row["top4"], bg=BG, fg=FG,
                     font=self.f_s).grid(row=r, column=3, sticky="e", padx=4)
            tk.Label(self.table, text=row["avg"], bg=BG, fg=FG,
                     font=self.f_s).grid(row=r, column=4, sticky="e", padx=4)

    def close(self):
        _OPEN[0] = None
        try:
            self.root.destroy()
        except Exception:
            pass


def open(master=None):
    """
    打开统计页。**未连接服务器 → 返回 None，不创建窗口**（用户规则）。
    """
    if not meta_client.available():
        return None
    if _OPEN[0] is not None:
        try:
            _OPEN[0].root.lift()
            return _OPEN[0]
        except Exception:
            _OPEN[0] = None
    w = MetaWindow(master)
    _OPEN[0] = w
    return w
