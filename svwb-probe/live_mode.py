#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之幻境 · 直播信息窗（OBS 可采集的独立画面源）

为什么需要这个文件
------------------
原来的胜率浮层（overlay.py）是「透明分层窗口」：
    WS_EX_LAYERED + -transparentcolor + -alpha
OBS / 直播软件的「窗口采集」抓到这种窗口通常是**全黑**的
（分层窗口的内容不在普通窗口的位图里），而「显示器采集」又会把整个游戏画面抓进去。

本文件给出一个**完全普通、不透明**的 Tk 窗口：
    · 不调用 -transparentcolor
    · 不调用 -alpha
    · 不设置 WS_EX_LAYERED（启动后会自检并写日志）
因此 OBS 的「窗口采集」可以直接把它当成一个独立画面源。

渲染内容（来自只读文件 shots/live/overlay.json）
------------------------------------------------
第一行：胜 34%  ·  败 38%  ·  平 28%
下面  ：我方受到伤害  6点 25%  7点 32%  ...  （范围 6~11 点）
phase=operate / win=null 时显示运营态提示，不会空白。

用法
----
    pythonw live_mode.py --x 40 --y 40 --w 420 --h 160
    python  live_mode.py --file D:\\tmp\\overlay.json --w 460 --h 180
    python  live_mode.py --rect-out live-rect.json     # 输出客户区精确坐标给 OBS 裁剪

说明
----
· --x/--y 指**客户区**（OBS 抓到的画面）左上角的屏幕物理像素坐标；
  窗口标题栏会自动向上/向左让位，所以 OBS 用 --x/--y/--w/--h 就能框住内容。
· 每 400ms 轮询，只有 mtime/size 变化才重读重绘；文件缺失或 JSON 损坏都不会崩。
· 只用标准库 + tkinter，没有任何未验证的第三方依赖。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import io
import json
import math
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

APP_TITLE = "影之幻境 · 直播信息"
FONT_FAMILY = "Microsoft YaHei UI"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_FALLBACK = os.path.join(
    # 兜底路径改成包内相对位置：
    # 原来写死的是开发机绝对路径（换台机器一定不存在，
    # 发包也不该带别人的用户名）。
    os.path.dirname(HERE),
    "dsh-shadowverse-huanjing", "shots", "live", "overlay.json")


def _init_console():
    """Windows 控制台/重定向时统一 UTF-8，避免中文乱码（pythonw 下 stdout 为 None）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_init_console()

# ── 配色：深墨蓝底 + 香槟金 + 象牙白，和原来的浮层保持同一套视觉 ──────────
BG = "#0b0f14"          # 窗口最外层：近黑
PANEL_BG = "#12161d"    # 面板底：深墨蓝
PANEL_BORDER = "#3a4250"  # 细描边
WIN_FG = "#e6c98a"      # 胜率主色：香槟金
WIN_LOW_FG = "#c98f7a"  # 胜率 < 50 时压暗的暖红
BODY_FG = "#efe9dc"     # 正文：象牙白
SUB_FG = "#9aa7b8"      # 次级文字：青灰
DIM_FG = "#6d7a8a"      # 页脚：压暗
HINT_FG = "#e3c98c"     # 提示：暖金
BAD_FG = "#e06c75"      # 异常：柔和红
LIVE_FG = "#e06c75"     # LIVE 圆点

# ── 窗口样式常量（只读自检用，绝不修改样式） ────────────────────────────
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# Win11 DWM 外观（只影响标题栏/圆角，不影响 OBS 采集）
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1

user32 = ctypes.windll.user32
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.restype = wt.BOOL
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL


def _enable_dpi_awareness():
    """必须在创建任何 Tk 窗口之前调用，否则坐标会被系统 DPI 虚拟化。"""
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()


# ──────────────────────────────────────────────────────────────────────
# 数据 → 文本
# ──────────────────────────────────────────────────────────────────────
def _num(value):
    """把 34 / 34.0 / "34.5" 统一成短字符串；非法值返回 '?'。"""
    if value is None:
        return "?"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "?"
    if abs(f - round(f)) < 0.05:
        return str(int(round(f)))
    return ("%.1f" % f).rstrip("0").rstrip(".")


def _pct(value):
    """百分比数字 → '34%'；None / 非法 → '?%'（不伪造数据）。"""
    n = _num(value)
    return "?" if n == "?" else n + "%"


def build_win_line(data):
    """第一行：胜 xx% · 败 xx% · 平 xx%；win 为 null 时返回运营态提示。"""
    phase = str(data.get("phase") or "").strip().lower()
    win = data.get("win")
    if win is None:
        if phase == "operate":
            return "运营阶段 · 等待下一场战斗"
        if phase == "battle":
            return "战斗进行中 · 结果读取中…"
        return "等待对局开始…"
    return "胜 %s  ·  败 %s  ·  平 %s" % (
        _pct(win), _pct(data.get("loss")), _pct(data.get("draw")))


def build_damage_text(data, limit=6):
    """
    第二行：我方受到伤害的概率分布。

    返回 (文本, 是否有数据)。
      · 按伤害值升序展示；
      · 条目多时先取概率最高的 limit 项，避免一行放不下；
      · 末尾用完整区间（lo~hi）兜底，保证任何伤害值都能对上号。
    """
    dist = data.get("damageDist") or {}
    rows = dist.get("toPlayer") if isinstance(dist, dict) else None
    clean = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            dmg = float(row.get("damage"))
            prob = float(row.get("pPercent"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(dmg) and math.isfinite(prob)):
            continue
        clean.append((dmg, prob))
    if not clean:
        return "我方受到伤害  暂无数据（等待判定器计算）", False

    clean.sort(key=lambda item: item[0])
    lo, hi = clean[0][0], clean[-1][0]
    top = sorted(clean, key=lambda item: -item[1])[:limit]
    top.sort(key=lambda item: item[0])
    seg = "  ".join("%s点 %s" % (_num(dmg), _pct(prob)) for dmg, prob in top)
    text = "我方受到伤害  " + seg
    if lo != hi:
        text += "  （范围 %s~%s 点）" % (_num(lo), _num(hi))
    return text, True


def build_body_text(data):
    """第二行文本：有胜率时显示伤害分布，win=null 时显示对应阶段提示。"""
    phase = str(data.get("phase") or "").strip().lower()
    if data.get("win") is None:
        if phase == "battle":
            return "判定器出结果后自动刷新"
        return "进入战斗后自动显示胜率与伤害分布"
    return build_damage_text(data)[0]


def default_state_path():
    """默认 overlay.json 路径：环境变量 → 同级数据目录 → 任务给定的绝对路径。"""
    env = os.environ.get("SHADOWVERSE_LIVE_DIR")
    if env:
        return os.path.join(os.path.expanduser(env), "overlay.json")
    sibling = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing",
                           "shots", "live")
    if os.path.isdir(sibling):
        return os.path.join(sibling, "overlay.json")
    return DEFAULT_STATE_FALLBACK


def read_state(path, tries=3, delay=0.03):
    """读取 overlay.json（只读，不写）。任何异常都抛给调用方处理。

    判定器现在是原子替换写入（tmp + os.replace），但 Windows 上
    "读者正开着目标文件"仍可能让某一次读失败；先重试几次再抛，
    避免一次瞬时失败就闪一下「数据读取异常」。
    """
    tries = max(1, int(tries))
    last = None
    for i in range(tries):
        try:
            return _read_state_once(path)
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(delay)
    raise last


def _read_state_once(path):
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        # 记事本另存会带 BOM；给用户留一条活路。
        if "BOM" not in str(exc):
            raise
        with io.open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return data


# ──────────────────────────────────────────────────────────────────────
# 窗口
# ──────────────────────────────────────────────────────────────────────
class LiveWindow:
    """不透明、可拖动、可缩放的直播信息窗。"""

    def __init__(self, state_path, x=40, y=40, w=420, h=160,
                 interval=0.4, topmost=True, borderless=False,
                 scale=None, rect_out=None, log_path=None):
        self.state_path = os.path.abspath(state_path)
        self.interval = max(0.1, float(interval))
        self.topmost = bool(topmost)
        self.borderless = bool(borderless)
        self.forced_scale = float(scale) if scale else None
        self.rect_out = os.path.abspath(rect_out) if rect_out else None
        self.log_path = os.path.abspath(log_path) if log_path else None

        self._closing = False
        self._drag = None
        self._grip = None
        self._resize_job = None
        self._rect_job = None
        self._last_key = None
        self._last_data = None
        self._state = "init"
        self._bad_count = 0
        self._font_key = None
        self._wrap = None
        self._last_rect = None
        self._last_rect_write = 0.0

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(320, 140)
        if borderless:
            self.root.overrideredirect(True)
        self.root.geometry("%dx%d+0+0" % (int(w), int(h)))
        if self.topmost:
            try:
                self.root.attributes("-topmost", True)
            except tk.TclError:
                pass

        # 字体统一用**负数=像素**，避免 Tk 按系统 DPI（本机 168）把 point 放大 2.3 倍。
        self.f_win = tkfont.Font(root=self.root, family=FONT_FAMILY,
                                 size=-20, weight="bold")
        self.f_dmg = tkfont.Font(root=self.root, family=FONT_FAMILY,
                                 size=-13, weight="normal")
        self.f_foot = tkfont.Font(root=self.root, family=FONT_FAMILY,
                                  size=-10, weight="normal")

        self._build_ui()
        self.root.update_idletasks()
        self._align_client(x, y)
        self._apply_layout(force=True)
        self._style_report()
        self._write_rect()
        self.root.after(200, self._write_rect)
        if self.topmost:
            self.root.after(250, self._assert_topmost)
        self.root.after(220, self._apply_dwm_look)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # 不绑全局 Esc：直播时误按一下就把窗口关掉太危险；有标题栏时用 X 关。
        self._schedule_tick(immediate=True)

    # ---------- 界面 ----------
    def _build_ui(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)

        self.panel = tk.Frame(outer, bg=PANEL_BG, bd=0,
                              highlightbackground=PANEL_BORDER,
                              highlightcolor=PANEL_BORDER,
                              highlightthickness=1)
        self.panel.pack(fill="both", expand=True, padx=6, pady=6)

        self.inner = tk.Frame(self.panel, bg=PANEL_BG)
        self.inner.pack(fill="both", expand=True, padx=16, pady=12)

        self.win_line = tk.Label(self.inner, text="", bg=PANEL_BG, fg=WIN_FG,
                                 font=self.f_win, anchor="w", justify="left")
        self.win_line.pack(fill="x", anchor="w")

        self.rule = tk.Frame(self.inner, bg=PANEL_BORDER, height=1)
        self.rule.pack(fill="x", pady=(7, 7))

        self.foot = tk.Frame(self.inner, bg=PANEL_BG)
        self.foot.pack(fill="x", side="bottom", pady=(5, 0))
        self.foot_l = tk.Label(self.foot, text="", bg=PANEL_BG, fg=DIM_FG,
                               font=self.f_foot, anchor="w", justify="left")
        self.foot_l.pack(side="left")
        self.foot_r = tk.Label(self.foot, text="● LIVE", bg=PANEL_BG, fg=LIVE_FG,
                               font=self.f_foot, anchor="e")
        self.foot_r.pack(side="right")

        self.dmg_line = tk.Label(self.inner, text="", bg=PANEL_BG, fg=BODY_FG,
                                 font=self.f_dmg, anchor="w", justify="left")
        self.dmg_line.pack(fill="both", expand=True, anchor="nw")

        for widget in (self.panel, self.inner, self.win_line, self.rule,
                       self.dmg_line, self.foot, self.foot_l, self.foot_r):
            self._bind_drag(widget)

        self.inner.bind("<Configure>", self._on_configure, add="+")
        # 窗口被外部移动时，只有顶层会收到 Configure；必须一起绑，
        # 否则 --rect-out 的坐标会停在启动时的位置。
        self.root.bind("<Configure>", self._on_configure, add="+")

        if self.borderless:
            # 无边框时没有系统缩放边框，右下角放一个很小的拖拽手柄。
            self.grip = tk.Label(self.panel, text="◢", bg=PANEL_BG, fg=DIM_FG,
                                 font=self.f_foot, cursor="size_nw_se")
            self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-4, y=-4)
            self.grip.bind("<Button-1>", self._grip_press)
            self.grip.bind("<B1-Motion>", self._grip_motion)
            self.grip.bind("<ButtonRelease-1>", self._grip_release)
            # 无标题栏时给一个显式关闭按钮（不做 Esc 全局关闭，避免直播误触）。
            self.close_btn = tk.Label(self.panel, text="×", bg=PANEL_BG,
                                      fg=DIM_FG, font=self.f_foot,
                                      cursor="hand2")
            self.close_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-4, y=1)
            self.close_btn.bind("<Button-1>", lambda _e: self.close())

    def _bind_drag(self, widget):
        widget.bind("<Button-1>", self._on_press)
        widget.bind("<B1-Motion>", self._on_drag)
        widget.bind("<ButtonRelease-1>", self._on_release)

    # ---------- 位置 / 尺寸 ----------
    def _set_window_rect(self, left, top, width=None, height=None):
        """
        用 Win32 绝对定位，**不要**用 Tk 的 geometry 传负坐标。

        Tk 的 wm geometry 遵循 X11 约定：`-12` 表示「距屏幕底边 12px」，
        不是 y=-12。所以 `--x 40 --y 40` 在 175% DPI 下算出的外框 y=-12
        会被 Tk 解释成贴底，窗口直接跑到屏幕最下面。SetWindowPos 没有这个坑。
        """
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        if width is None or height is None:
            flags |= SWP_NOSIZE
            width = height = 0
        user32.SetWindowPos(self._hwnd(), None, int(left), int(top),
                            int(width), int(height), flags)

    def _align_client(self, x, y):
        """
        让**客户区**左上角落在 (x, y)。

        Tk 的 geometry +x+y 定位的是外框（含标题栏）；本机 175% DPI 下
        标题栏高 52px、左边框 12px，直接用会让 OBS 裁剪整体偏移。
        这里量一次外框与客户区的差值，再用 SetWindowPos 把外框反向移动。
        """
        self.root.update_idletasks()
        off_x = self.root.winfo_rootx() - self.root.winfo_x()
        off_y = self.root.winfo_rooty() - self.root.winfo_y()
        self._set_window_rect(int(x) - off_x, int(y) - off_y)

    def _on_configure(self, _event=None):
        if self._closing:
            return
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.root.after(120, self._apply_layout)
        self._schedule_rect_write()

    def _apply_layout(self, force=False):
        self._resize_job = None
        if self._closing:
            return
        try:
            width = self.inner.winfo_width()
            height = self.inner.winfo_height()
        except tk.TclError:
            return
        if width < 80 or height < 60:
            return

        if self.forced_scale:
            base = max(0.5, min(3.0, self.forced_scale))
        else:
            # 420x160 时 base≈1.0；窗口越大字越大，内容自动适配。
            base = max(0.72, min(1.9, min(width / 380.0, height / 122.0)))

        key = (max(14, int(round(20 * base))),
               max(10, int(round(13 * base))),
               max(8, int(round(10 * base))))
        if force or key != self._font_key:
            self._font_key = key
            self.f_win.configure(size=-key[0])
            self.f_dmg.configure(size=-key[1])
            self.f_foot.configure(size=-key[2])

        wrap = max(160, width - 4)
        if wrap != self._wrap:
            self._wrap = wrap
            self.dmg_line.configure(wraplength=wrap)

    # ---------- 拖动（无边框 / 有边框都能拖内容区） ----------
    def _on_press(self, event):
        self._drag = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())
        return "break"

    def _on_drag(self, event):
        if self._drag is None or self._closing:
            return "break"
        left = event.x_root - self._drag[0]
        top = event.y_root - self._drag[1]
        self._set_window_rect(left, top)
        return "break"

    def _on_release(self, _event=None):
        self._drag = None
        self._schedule_rect_write()
        return "break"

    def _grip_press(self, event):
        self._grip = (event.x_root, event.y_root,
                      self.root.winfo_x(), self.root.winfo_y(),
                      self.root.winfo_width(), self.root.winfo_height())
        return "break"

    def _grip_motion(self, event):
        if self._grip is None:
            return "break"
        x0, y0, left, top, w0, h0 = self._grip
        w = max(320, w0 + event.x_root - x0)
        h = max(140, h0 + event.y_root - y0)
        # 只有 --borderless 才有这个手柄，此时外框尺寸 == 客户区尺寸。
        self._set_window_rect(left, top, w, h)
        return "break"

    def _grip_release(self, _event=None):
        self._grip = None
        self._schedule_rect_write()
        return "break"

    # ---------- 客户区坐标输出（给 OBS 显示器采集+裁剪用） ----------
    def _schedule_rect_write(self):
        if not self.rect_out or self._closing:
            return
        if self._rect_job is not None:
            try:
                self.root.after_cancel(self._rect_job)
            except tk.TclError:
                pass
        self._rect_job = self.root.after(250, self._write_rect)

    def _write_rect(self):
        self._rect_job = None
        if not self.rect_out or self._closing:
            return
        try:
            payload = {
                "x": int(self.root.winfo_rootx()),
                "y": int(self.root.winfo_rooty()),
                "w": int(self.root.winfo_width()),
                "h": int(self.root.winfo_height()),
                "title": APP_TITLE,
                "updatedAt": time.strftime("%H:%M:%S"),
            }
            pos = (payload["x"], payload["y"], payload["w"], payload["h"])
            if self._last_rect and pos != self._last_rect:
                self._log("窗口矩形变化：%r -> %r" % (self._last_rect, pos))
            self._last_rect = pos
            folder = os.path.dirname(self.rect_out)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = self.rect_out + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.rect_out)
        except Exception as exc:
            self._log("写客户区坐标失败：%s" % exc)

    # ---------- 样式自检（只读，绝不设置 WS_EX_LAYERED） ----------
    def _hwnd(self):
        tk_hwnd = int(self.root.winfo_id())
        parent = user32.GetParent(tk_hwnd)
        return int(parent) if parent else tk_hwnd

    def _assert_topmost(self):
        """
        窗口映射后重新断言一次置顶。

        Tk 在窗口还没 map 时设置 -topmost，窗口可能落在其它 topmost 窗口
        （比如控制台面板）下面；启动后再 SetWindowPos 一次即可排到最前。
        """
        if not self.topmost or self._closing:
            return
        try:
            user32.SetWindowPos(self._hwnd(), HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._log("已断言置顶（OBS 窗口采集不依赖置顶，但屏幕显示需要）")
        except Exception as exc:
            self._log("置顶断言失败：%s" % exc)

    def _apply_dwm_look(self):
        """
        让标题栏变深色、窗口四角变直角。

        Win11 默认给窗口加圆角，圆角处会透出桌面，截图/OBS 里四角会有
        白色小三角；DONOTROUND 后是干净的矩形。只改 DWM 外观，不会设置
        WS_EX_LAYERED，不影响窗口采集。
        """
        try:
            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmSetWindowAttribute.argtypes = [
                wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            hwnd = self._hwnd()
            dark = ctypes.c_int(1)
            for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE, 19):
                if dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(dark),
                        ctypes.sizeof(dark)) == 0:
                    break
            corner = ctypes.c_int(DWMWCP_DONOTROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner), ctypes.sizeof(corner))
        except Exception as exc:
            self._log("DWM 外观设置跳过：%s" % exc)

    def _style_report(self):
        try:
            hwnd = self._hwnd()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            layered = bool(ex & WS_EX_LAYERED)
            self._log("窗口样式：hwnd=%#x exstyle=%#x layered=%s topmost=%s"
                      % (hwnd, ex & 0xFFFFFFFF, layered,
                         bool(ex & WS_EX_TOPMOST)))
            if layered:
                self._log("警告：检测到 WS_EX_LAYERED，OBS 窗口采集可能黑屏")
        except Exception as exc:
            self._log("窗口样式自检失败：%s" % exc)

    # ---------- 日志 ----------
    def _log(self, message):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        try:
            if sys.stdout is not None:
                print(line, flush=True)
        except Exception:
            pass
        if self.log_path:
            try:
                folder = os.path.dirname(self.log_path)
                if folder:
                    os.makedirs(folder, exist_ok=True)
                with io.open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass

    # ---------- 数据轮询 ----------
    def _schedule_tick(self, immediate=False):
        if self._closing:
            return
        delay = 10 if immediate else int(self.interval * 1000)
        try:
            self.root.after(delay, self._tick)
        except tk.TclError:
            pass

    def _tick(self):
        if self._closing:
            return
        try:
            self._poll_once()
        except Exception as exc:  # 任何意外都不能让窗口崩掉
            self._log("轮询异常：%s" % exc)
        # 窗口可能被系统/其它工具移动（例如 Win+方向键贴边），
        # Tk 不一定给顶层发 Configure；每秒兜底刷新一次客户区坐标。
        if self.rect_out and not self._closing:
            now = time.time()
            if now - self._last_rect_write >= 1.0:
                self._last_rect_write = now
                self._write_rect()
        self._schedule_tick()

    def _poll_once(self):
        try:
            stat = os.stat(self.state_path)
        except FileNotFoundError:
            self._bad_count = 0
            self._show_missing()
            return
        except OSError as exc:
            self._show_bad("无法访问文件：%s" % exc)
            return

        key = (stat.st_mtime_ns, stat.st_size)
        if key == self._last_key and self._state == "ok":
            return
        self._last_key = key

        try:
            data = read_state(self.state_path)
        except Exception as exc:
            # 下次轮询必须重读，否则文件一直是坏的时会永远不再重试。
            self._last_key = None
            self._bad_count += 1
            # 文件可能正好被非原子写入：先忍 2 个周期，避免闪一下错误。
            if self._last_data is not None and self._bad_count < 3:
                self._foot_note("数据读取异常，重试中…", BAD_FG)
                return
            self._show_bad("%s: %s" % (type(exc).__name__, exc))
            return

        self._bad_count = 0
        self._state = "ok"
        self._last_data = data
        self._last_key = key
        self.render(data)

    def _show_missing(self):
        if self._state == "missing":
            return
        self._state = "missing"
        self._last_key = None
        self._last_data = None
        self.win_line.configure(text="等待判定器写入…", fg=HINT_FG)
        self.dmg_line.configure(
            text="未找到 overlay.json\n请确认判定器 / 监视器已经启动", fg=SUB_FG)
        self.foot_l.configure(text="每 %.1f 秒自动重试" % self.interval, fg=DIM_FG)
        self._log("等待状态文件：%s" % self.state_path)

    def _show_bad(self, detail):
        if self._state == "bad":
            return
        self._state = "bad"
        self._last_key = None
        short = str(detail).replace("\n", " ")[:90]
        self.win_line.configure(text="数据读取失败", fg=BAD_FG)
        self.dmg_line.configure(
            text="窗口保持运行，等待文件恢复\n判定器重新写入后自动刷新", fg=SUB_FG)
        self.foot_l.configure(text="JSON 损坏或不可读 · 自动重试中", fg=BAD_FG)
        self._log("读取失败：%s" % short)

    def _foot_note(self, text, color=DIM_FG):
        try:
            self.foot_l.configure(text=text, fg=color)
        except tk.TclError:
            pass

    # ---------- 渲染 ----------
    def render(self, data):
        phase = str(data.get("phase") or "").strip().lower()
        win = data.get("win")
        updated = str(data.get("updatedAt") or "").strip()

        if win is None:
            self.win_line.configure(text=build_win_line(data), fg=HINT_FG)
            self.dmg_line.configure(text=build_body_text(data), fg=SUB_FG)
        else:
            try:
                strong = float(win) >= 50
            except (TypeError, ValueError):
                strong = True
            self.win_line.configure(text=build_win_line(data),
                                    fg=WIN_FG if strong else WIN_LOW_FG)
            text, has_data = build_damage_text(data)
            self.dmg_line.configure(text=text, fg=BODY_FG if has_data else SUB_FG)

        phase_label = {"battle": "战斗", "operate": "运营", "menu": "菜单",
                       "other": "其它"}.get(phase, phase or "未知")
        if updated:
            foot = "%s · 更新 %s" % (phase_label, updated)
        else:
            foot = "%s · %s 读取" % (phase_label, time.strftime("%H:%M:%S"))
        self._foot_note(foot, DIM_FG)

    # ---------- 退出 ----------
    def close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def build_parser():
    parser = argparse.ArgumentParser(
        description="影之幻境 · 直播信息窗（不透明窗口，OBS 窗口采集可用）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--file", default=None,
                        help="overlay.json 路径（默认自动定位）")
    parser.add_argument("--live-dir", default=None,
                        help="overlay.json 所在目录（与 --file 二选一）")
    parser.add_argument("--x", type=int, default=40,
                        help="客户区左上角屏幕 X（物理像素）")
    parser.add_argument("--y", type=int, default=40,
                        help="客户区左上角屏幕 Y（物理像素）")
    parser.add_argument("--w", type=int, default=420, help="客户区宽度（像素）")
    parser.add_argument("--h", type=int, default=160, help="客户区高度（像素）")
    parser.add_argument("--interval", type=float, default=0.4,
                        help="轮询间隔（秒），最小 0.1")
    parser.add_argument("--scale", type=float, default=None,
                        help="强制字体缩放（默认按窗口大小自适应）")
    parser.add_argument("--borderless", action="store_true",
                        help="无标题栏（客户区坐标严格等于 --x/--y）")
    parser.add_argument("--no-topmost", action="store_true",
                        help="不置顶（默认置顶，避免被游戏挡住导致采集到遮挡内容）")
    parser.add_argument("--rect-out", default=None,
                        help="把客户区精确屏幕矩形写到该 JSON（给 OBS 裁剪用）")
    parser.add_argument("--log", default=None, help="追加运行日志到该文件")
    parser.add_argument("--print-once", action="store_true",
                        help="只读取一次并打印渲染文本，不开窗（调试用）")
    return parser


def resolve_state_path(args):
    if args.file:
        return os.path.abspath(args.file)
    if args.live_dir:
        return os.path.join(os.path.abspath(args.live_dir), "overlay.json")
    return default_state_path()


def main(argv=None):
    args = build_parser().parse_args(argv)
    state_path = resolve_state_path(args)

    if args.print_once:
        try:
            data = read_state(state_path)
        except Exception as exc:
            print("读取失败：%s（%s）" % (state_path, exc))
            return 2
        print("状态文件：%s" % state_path)
        print(build_win_line(data))
        print(build_body_text(data))
        return 0

    try:
        app = LiveWindow(
            state_path,
            x=args.x, y=args.y, w=args.w, h=args.h,
            interval=args.interval, topmost=not args.no_topmost,
            borderless=args.borderless, scale=args.scale,
            rect_out=args.rect_out, log_path=args.log)
    except Exception as exc:
        message = "直播信息窗启动失败：%s: %s" % (type(exc).__name__, exc)
        try:
            if sys.stdout is not None:
                print(message, flush=True)
        except Exception:
            pass
        if args.log:
            try:
                with io.open(args.log, "a", encoding="utf-8") as fh:
                    fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), message))
            except Exception:
                pass
        return 1

    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 0
    app._log("启动：文件=%s 客户区=%d,%d %dx%d 间隔=%.2fs DPI=%s 屏幕=%dx%d"
             % (state_path, args.x, args.y, args.w, args.h, args.interval,
                dpi or "?", user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)))
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
