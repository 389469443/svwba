#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
战绩面板 v0.1.0

设计要点（按用户反馈逐条落实）：

1. **标题写明「战　绩」** —— 和「胜　率」悬浮层区分，一眼看得懂。
2. **点击穿透由锁定状态决定**：锁定 → 整个窗口穿透，不挡游戏操作；
   解锁 → 整个面板可交互、可拖动。之前"悬停标题才可交互"在 DPI 缩放下
   永远判不准（Tk 逻辑像素 vs 光标物理像素），已废弃。
3. **锁定按钮常显**：悬停才出现等于找不到；而锁定后窗口是点击穿透的，
   更不可能靠"悬停标题"把解锁入口叫出来。
4. **解锁后任意位置都能拖**：拖动事件递归绑到所有子控件上。
   之前只绑了外层 frame / 标题，面板大部分面积被 canvas、body 和
   Label 盖住，点到内容区收不到事件 —— 这就是"拖不动"的根因。
   tk.Button / tk.Scrollbar 排除在外，否则会抢掉它们自己的点击。
5. **背景与文字透明度分开**：
   - 背景不透明度 → 窗口整体 alpha（真透明）
   - 文字不透明度 → 把字色向背景色混合（Tk 无法做逐控件 alpha，
     这是视觉等价的近似；背景越实，效果越准）
6. 位置钳制在屏幕内，防止拖出屏幕后\"消失\"。
"""
import ctypes
import ctypes.wintypes
import json
import os
import sys
import tkinter as tk
try:
    import ui_dpi
except Exception:
    class _NoDpi:
        SCALE = 1.0
    ui_dpi = _NoDpi()
import tkinter.font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 数据文件：默认 logs/history.json。
# SVWB_PANEL_HIST 仅供自测/排查把面板指到一份临时战绩副本（不影响正常运行）。
HIST = os.environ.get("SVWB_PANEL_HIST") or os.path.join(HERE, "logs", "history.json")
POSF = os.path.join(HERE, "logs", "panel-pos.json")
PANEL_SET = os.path.join(HERE, "logs", "panel-settings.json")
HERO_DIR = os.path.join(HERE, "hero-portraits")

CHROMA = "#010203"
BG = "#12161d"
BORDER = "#3a3a44"
FG = "#efe9dc"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"
DIM = "#9aa7a0"
RULE = "#2a3038"
GREEN = "#8fbf8a"
RED = "#c07b6b"

# 战绩表里「两行可输入备注」的配色：
# 输入框底比面板底（BG #12161d）亮一档，保证一眼看出这是能输入的地方。
NOTE_BG = "#232c3e"        # 可编辑时的输入框底色
NOTE_BG_RO = "#1b2433"     # 锁定（只读）时的底色
NOTE_FG = "#efe9dc"        # 输入文字：象牙白
NOTE_LABEL = GOLD          # 行首「流派 / 备注」标签

ALPHA = 0.88          # 背景不透明度默认
TEXT_ALPHA = 1.0      # 文字不透明度默认
FONT_SIZE = 9         # 基准字号（标题），小字比它小 1
FONT_MIN, FONT_MAX = 7, 16

WIN_W, WIN_H = 300, 340

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
HWND_NOTOPMOST = -2
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010


def blend(fg_hex, t):
    """
    把前景色按 t∈[0,1] 向背景色混合，用作\"文字不透明度\"的近似。

    Tk 没有逐控件 alpha，窗口 -alpha 会同时影响背景和文字。
    这里用颜色混合模拟文字淡出：t=1 为原色，t=0 与背景完全融合。
    """
    t = max(0.0, min(1.0, float(t)))
    try:
        f = tuple(int(fg_hex[i:i + 2], 16) for i in (1, 3, 5))
        b = tuple(int(BG[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(
            int(round(b[i] + (f[i] - b[i]) * t)) for i in range(3))
    except Exception:
        return fg_hex


def _num(v, default=None):
    """设置/数据里的数字容错：坏值不抛异常，返回 default"""
    if v is None or isinstance(v, bool):
        return default
    try:
        f = float(v)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


# ── 自动推断阵容（只用于显示，不写盘）─────────────────────────
# 用户规则：优先填拥有 3 个以上相同**非中立**种族、且中立不占多数（<3）的种族；
# 否则填「混合随从」。种族只有五种，卡名→种族从 knowledge/cards.json 查，
# 查不到就跳过（不当成中立）。
CARDS_JSON = os.path.normpath(os.path.join(
    HERE, "..", "dsh-shadowverse-huanjing", "lib", "knowledge", "cards.json"))
NEUTRAL_TRIBE = "中立"
# 多个种族同时 ≥3 且数量相同时的优先级（按用户要求）
TRIBE_PRIORITY = ("精灵", "皇家护卫", "梦魇", "龙族")
MIXED_TRIBE = "混合随从"
_TRIBE_BY_NAME = None          # name -> tribe 缓存（cards.json 只读一次）


def load_tribe_map():
    """加载 cards.json 的 name→tribe 映射；只读一次，结果缓存。"""
    global _TRIBE_BY_NAME
    if _TRIBE_BY_NAME is not None:
        return _TRIBE_BY_NAME
    m = {}
    try:
        with open(CARDS_JSON, encoding="utf-8") as fh:
            d = json.load(fh)
        cards = d.get("cards") or {}
        if isinstance(cards, dict):
            for rec in cards.values():
                if not isinstance(rec, dict):
                    continue
                name = str(rec.get("name") or "").strip()
                tribe = str(rec.get("tribe") or "").strip()
                if name and tribe:
                    m.setdefault(name, tribe)
    except Exception as e:
        print(f"（读取 cards.json 失败：{e}）", flush=True)
    _TRIBE_BY_NAME = m
    return m


def auto_line1_from_board(board):
    """
    按用户规则从 final board 推出自动阵容（显示在主战者名字后面）：
      · 非中立种族数量 ≥ 3 且 中立 < 3  → 该种族名
        （多个达标取数量最多的；相同则 精灵→皇家护卫→梦魇→龙族）
      · 其它情况 → 「混合随从」
      · board 为空 / 全部卡名查不到 → ""（不瞎填）
    """
    if not board:
        return ""
    m = load_tribe_map()
    if not m:
        return ""
    counts = {}
    for c in board:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        tribe = m.get(name)
        if not tribe:
            continue                    # 查不到的随从跳过（不算中立）
        counts[tribe] = counts.get(tribe, 0) + 1
    if not counts:
        return ""
    neutral = counts.get(NEUTRAL_TRIBE, 0)
    best, best_n = None, 0
    for tribe in TRIBE_PRIORITY:
        n = counts.get(tribe, 0)
        if n >= 3 and n > best_n:       # 相同数量时保留优先级靠前的
            best, best_n = tribe, n
    if best is not None and neutral < 3:
        return best
    return MIXED_TRIBE


def load_pos():
    try:
        with open(POSF, encoding="utf-8") as fh:
            d = json.load(fh)
        return int(d["x"]), int(d["y"]), bool(d.get("locked"))
    except Exception:
        return None, None, False


def save_pos(x, y, locked):
    # 原子写（tmp + replace）：设置对话框/拖动保存可能和读取同时发生，
    # 旧写法直接 open(...,"w") 有一个"文件是空的"的瞬间，读侧会解析失败。
    try:
        os.makedirs(os.path.dirname(POSF), exist_ok=True)
        tmp = "%s.tmp-%d" % (POSF, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"x": x, "y": y, "locked": locked}, fh)
        os.replace(tmp, POSF)
    except Exception:
        pass


# 自定义细滚动条：Windows 原生 tk.Scrollbar 走系统主题、颜色不可控，
# 所以用 Canvas 自己画（滑槽比面板底暗一档，滑块金色系）。
SCROLL_W = 9
SCROLL_TROUGH = "#0a0d13"
SCROLL_THUMB = GOLD
SCROLL_THUMB_HI = "#e6c88f"



def _above_game():
    """
    面板是否应该置顶：**只在游戏窗口位于前台时**才置顶。

    用户要求：三个面板只显示在游戏上面，不要显示在最上面。
    出错时退回 True（宁可看得见，也不要突然消失）。
    """
    try:
        import game_win as _GW
        return bool(_GW.game_is_foreground())
    except Exception:
        return True



def _apply_zorder(root):
    """
    更新本窗口的 Z 序：**贴着游戏，但不进置顶层**。

    用户要求：
      · 面板显示在游戏上面
      · 但如果有别的窗口（不占满屏幕）盖到它，那个窗口要能遮挡面板

    置顶层做不到第二点，所以改成把窗口插到「游戏正上方」的普通层位置。
    """
    try:
        import game_win as _GW
        _GW.sync_zorder(_GW.hwnd_of(root))
    except Exception:
        pass


class ThinScrollbar(tk.Canvas):
    """8~10px 宽的极简滚动条：支持拖动滑块 / 点击轨道 / 滚轮（滚轮由 canvas 统一处理）"""

    def __init__(self, master, command=None, width=SCROLL_W, **kw):
        kw.setdefault("bg", SCROLL_TROUGH)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(master, width=width, **kw)
        self.command = command
        self._first, self._last = 0.0, 1.0
        self._drag_off = None
        self._hover = False
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # 由 canvas 的 yscrollcommand 调用
    def set(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except Exception:
            return
        self._redraw()

    def _geom(self):
        h = max(1, self.winfo_height())
        top = int(self._first * h)
        bot = int(self._last * h)
        th = max(24, min(h, bot - top))
        if top + th > h:
            top = h - th
        return max(0, top), th, h

    def _redraw(self):
        try:
            self.delete("all")
            w = max(1, self.winfo_width())
            top, th, h = self._geom()
            col = SCROLL_THUMB_HI if (self._drag_off is not None or self._hover) else SCROLL_THUMB
            self.create_rectangle(0, 0, w, h, fill=SCROLL_TROUGH, outline="")
            if self._last - self._first < 0.999:
                self.create_rectangle(1, top + 1, w - 1, top + th - 1,
                                      fill=col, outline="")
        except Exception:
            pass

    def _on_press(self, e):
        top, th, _h = self._geom()
        if top <= e.y <= top + th:
            self._drag_off = e.y - top
            self._redraw()
        elif self.command:
            # 点击轨道 → 翻页
            self.command("scroll", -1 if e.y < top else 1, "pages")

    def _on_drag(self, e):
        if self._drag_off is None or not self.command:
            return
        _top, _th, h = self._geom()
        frac = (e.y - self._drag_off) / max(1, h)
        self.command("moveto", max(0.0, min(1.0, frac)))

    def _on_release(self, _e):
        self._drag_off = None
        self._redraw()

    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._redraw()


class Panel:
    # 这些控件有自己的点击 / 拖块语义，绑上窗口拖动会把它们的行为抢走
    # Entry 也不能绑拖动，否则在输入框里选字会变成拖窗口
    NO_DRAG = (tk.Button, tk.Scrollbar, tk.Entry, ThinScrollbar)

    def __init__(self, root):
        self.root = root
        self.last_sig = None
        self._page_cleared = False   # ↻ 手动重置后为 True，直到有新数据
        self._reset_sig = None       # 点 ↻ 那一刻的数据指纹（用来判断"有没有新战绩"）
        self._last_good = None       # 上次成功解析的 history.json（解析失败时兜底）
        self.locked = False
        self._hover_lock = False     # 锁定时鼠标是否停在锁按钮上（临时取消穿透）
        self._ct_on = None           # 当前 WS_EX_TRANSPARENT 状态（None=未知，需强制同步）
        self._drag = None
        self.pos = [None, None]
        self._interactive = None
        self._alpha = ALPHA
        self._text_alpha = TEXT_ALPHA
        self._note_entries = []      # 当前面板上的备注输入框（锁定时切只读）
        self._note_pairs = []        # [(idx0, 流派Entry, 备注Entry, game dict)]

        root.title("影之幻境 · 战绩")
        root.overrideredirect(True)
        self._streaming = False
        import stream_settings
        stream_settings.watch(root, self._set_stream_mode)
        import game_win as _GW
        root.attributes("-topmost", False)
        _opaque = os.environ.get("PANEL_OPAQUE") == "1"
        if not _opaque:
            try:
                root.attributes("-transparentcolor", CHROMA)
            except Exception:
                pass
        root.configure(bg=CHROMA)

        # ---- 读取设置 ----
        st = {}
        try:
            with open(PANEL_SET, encoding="utf-8") as fh:
                st = json.load(fh) or {}
        except Exception:
            pass
        sc = max(0.6, min(2.0, _num(st.get("scale"), 1.0)))
        self._scale = sc
        # 基准字号（来自 settings），窗口宽度随字号略微加宽，避免大字号挤爆行
        self._font_size = max(FONT_MIN, min(FONT_MAX,
                                           int(_num(st.get("font_size"), FONT_SIZE))))
        # 背景不透明度（窗口 -alpha）与文字不透明度（颜色混合）分开控制
        self._alpha = max(0.0, min(1.0, _num(st.get("alpha"), ALPHA)))
        self._text_alpha = max(0.5, min(1.0, _num(st.get("text_alpha"), TEXT_ALPHA)))
        self._settings_mtime = 0
        self._pos_mtime = 0
        self.hero_display = st.get("hero_display") or "both"
        if not isinstance(st.get("force_topmost"), bool):
            st["force_topmost"] = True
        if not isinstance(st.get("show_time"), bool):
            st["show_time"] = True
        self.force_topmost = st["force_topmost"]
        self.show_time = bool(st.get("show_time", True))
        self.locked = bool(st.get("locked"))
        self._has_locked_setting = isinstance(st.get("locked"), bool)

        global WIN_W, WIN_H
        WIN_W, WIN_H = self._calc_size(sc, self._font_size)

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y, locked_pos = load_pos()
        if x is not None and y is not None:
            # 位置文件里存的是"逻辑像素"，DPI 感知后要换算成物理像素
            x, y = int(x * getattr(ui_dpi, "SCALE", 1.0)), int(y * getattr(ui_dpi, "SCALE", 1.0))
        if not self._has_locked_setting:
            # 老配置里没有 locked 这个键：退回位置文件里的状态，避免
            # 「上次锁定启动、这次却被当成解锁」导致面板突然挡住游戏点击。
            self.locked = bool(locked_pos)
        if x is None or y is None:
            x, y = sw - WIN_W - 30, 90
        x = max(0, min(max(0, sw - WIN_W), x))
        y = max(0, min(max(0, sh - WIN_H), y))
        self.pos = [x, y]
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        self.f_t = tkfont.Font(family="Microsoft YaHei UI",
                               size=max(7, int(self._font_size * sc)),
                               weight="bold")
        self.f_s = tkfont.Font(family="Microsoft YaHei UI",
                               size=max(6, int(max(6, self._font_size - 1) * sc)))

        # 外层抠色（透明），内层深色底板
        self.frame = tk.Frame(root, bg=CHROMA, highlightthickness=0, bd=0)
        self.frame.pack(fill="both", expand=True)
        self.pad = tk.Frame(self.frame, bg=BG, highlightbackground=BORDER,
                            highlightthickness=1, bd=0)
        self.pad.pack(fill="both", expand=True, padx=3, pady=3)

        # ---- 标题行 ----
        head = tk.Frame(self.pad, bg=BG)
        head.pack(fill="x", padx=6, pady=(5, 0))
        self.title = tk.Label(head, text="影之幻境 · 战绩", bg=BG,
                              fg=blend(GOLD, self._text_alpha),
                              font=self.f_t, anchor="w", cursor="fleur")
        self.title.pack(side="left")

        # 锁定按钮**常显**：悬停才出现的话用户找不到入口，
        # 而锁定后窗口是点击穿透的，更不可能"悬停标题"把它叫出来。
        self.ctrl = tk.Frame(head, bg=BG)
        self.ctrl.pack(side="right")
        self.lock_btn = tk.Button(
            self.ctrl, text="🔒" if self.locked else "🔓",
            command=self._toggle_lock, bg=BG,
            fg=blend(GOLD2, self._text_alpha), relief="flat", bd=0,
            padx=3, font=self.f_s, activebackground=BG, cursor="hand2")
        self.lock_btn.pack(side="right")

        # 刷新按钮：放在**标题旁边**（用户要求）。
        # 面板本身 3 秒轮询一次 history.json，但判定器/文件异常时可能停在旧内容，
        # 这个按钮强制重读并重绘。
        self.refresh_btn = tk.Button(
            self.ctrl, text="↻", command=self.force_refresh, bg=BG,
            fg=blend(GOLD2, self._text_alpha), relief="flat", bd=0,
            padx=4, font=self.f_s, activebackground=BG, cursor="hand2")
        self.refresh_btn.pack(side="right", padx=(0, 2))

        # 关闭按钮：面板无边框、又挂成了游戏的从属窗口，
        # 没有系统标题栏可以关 —— 必须自己给一个，否则关不掉（用户反馈）。
        self.close_btn = tk.Button(
            self.ctrl, text="✕", command=self.close_panel, bg=BG,
            fg=blend(GOLD2, self._text_alpha), relief="flat", bd=0,
            padx=4, font=self.f_s, activebackground=BG, cursor="hand2")
        self.close_btn.pack(side="right", padx=(0, 2))

        self.info = tk.Label(self.pad, text="", bg=BG,
                             fg=blend(GOLD2, self._text_alpha), anchor="w",
                             font=self.f_s, wraplength=WIN_W - 22, justify="left")
        self.info.pack(fill="x", padx=6, pady=(1, 0))
        self.rule = tk.Frame(self.pad, bg=blend(RULE, self._text_alpha), height=1)
        self.rule.pack(fill="x", padx=4, pady=(3, 2))

        wrap = tk.Frame(self.pad, bg=BG)
        wrap.pack(fill="both", expand=True, padx=3, pady=(0, 3))
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0,
                                width=WIN_W - 26)
        # 自定义细滚动条：原生 tk.Scrollbar 在 Windows 上走系统主题、颜色不生效
        self.sb = ThinScrollbar(wrap, command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self.win, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        # 拖动：递归绑到所有子控件（Button / Scrollbar 除外），
        # 否则点到 canvas / body / Label 都不会触发拖动。
        self._bind_drag_tree(self.frame)
        self.title.bind("<Double-Button-1>", self._toggle_lock)

        self._apply_alpha()
        self.refresh()
        self._apply_style()
        root.update_idletasks()
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        root.after(3000, self._tick)
        root.after(500, self._poll_settings)
        root.after(120, self._poll_cursor)
        if os.environ.get("PANEL_DEBUG") == "1":
            root.after(2000, self._dump_tree)

    def _dump_tree(self):
        """调试：把控件树的状态打印出来（尺寸/是否映射/背景色）"""
        def walk(w, depth=0):
            try:
                print("%s%-14s mapped=%s %dx%d+%d+%d bg=%s" % (
                    "  " * depth, w.winfo_class(), w.winfo_ismapped(),
                    w.winfo_width(), w.winfo_height(),
                    w.winfo_x(), w.winfo_y(),
                    (w.cget("bg") if "bg" in w.keys() else "-")), flush=True)
            except Exception as e:
                print("%s?%s" % ("  " * depth, e), flush=True)
            for c in w.winfo_children():
                walk(c, depth + 1)
        print("=== 控件树 root=%dx%d ===" % (
            self.root.winfo_width(), self.root.winfo_height()), flush=True)
        walk(self.root)

    # ---------------- 透明度 ----------------
    @staticmethod
    def _calc_size(sc, font_size):
        """窗口尺寸：字号越大横向略加宽（纵向靠滚动条），默认 9 号仍是 300x340"""
        extra_w = max(0, int(font_size) - FONT_SIZE) * 10
        d = getattr(ui_dpi, "SCALE", 1.0)
        return (int((300 + extra_w) * sc * d), int(340 * sc * d))

    def _set_stream_mode(self, enabled):
        if self._streaming == enabled:
            return
        self._streaming = enabled
        self.root.overrideredirect(not enabled)
        self.root.attributes("-transparentcolor", "" if enabled else (CHROMA if os.environ.get("PANEL_OPAQUE") != "1" else ""))
        self._apply_alpha()
        if enabled:
            import stream_settings
            self.root.update_idletasks()
            stream_settings.capture_style(self.root)
        else:
            self._apply_style()

    def _apply_alpha(self):
        try:
            self.root.attributes("-alpha", 1.0 if self._streaming else self._alpha)
        except Exception:
            pass

    def _apply_text_styles(self):
        """文字不透明度变了：静态控件重新 blend，随后强制重建行"""
        t = self._text_alpha
        try:
            self.title.config(fg=blend(GOLD, t))
            self.lock_btn.config(fg=blend(GOLD2, t))
            self.info.config(fg=blend(GOLD2, t))
            self.rule.config(bg=blend(RULE, t))
            self._apply_note_state()
        except Exception:
            pass

    def _poll_settings(self):
        """每 500ms 读 panel-settings.json / panel-pos.json，按内容热更新（不重启）"""
        try:
            with open(PANEL_SET, encoding="utf-8") as fh:
                st = json.load(fh) or {}
            self._apply_live_settings(st)
        except Exception:
            pass
        try:
            x, y, _locked = load_pos()
            if x is not None and y is not None and self._drag is None:
                # 位置来自文件时也钳制在屏幕内：分辨率变小/换了显示器后，
                # 旧坐标会把面板顶到屏幕外面，只剩下几十像素能看见。
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                x = max(0, min(max(0, sw - WIN_W), int(x)))
                y = max(0, min(max(0, sh - WIN_H), int(y)))
                if [x, y] != self.pos:
                    self.pos = [x, y]
                    self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass
        # 每轮都同步一次 Z 序：游戏在前台 → 置顶压住游戏；切走 → 取消置顶
        if getattr(self, "force_topmost", True):
            _apply_zorder(self.root)
        try:
            self.root.after(500, self._poll_settings)
        except Exception:
            pass

    def _apply_live_settings(self, st):
        a = max(0.0, min(1.0, _num(st.get("alpha"), self._alpha)))
        t = max(0.5, min(1.0, _num(st.get("text_alpha"), self._text_alpha)))
        text_changed = abs(t - self._text_alpha) > 1e-4
        self._alpha, self._text_alpha = a, t
        self._apply_alpha()
        if text_changed:
            self._apply_text_styles()
        sc = max(0.6, min(2.0, _num(st.get("scale"), self._scale)))
        if abs(sc - self._scale) > 0.001:
            self._apply_scale(sc)
        fs = max(FONT_MIN, min(FONT_MAX,
                               int(_num(st.get("font_size"), self._font_size))))
        if fs != self._font_size:
            self._font_size = fs
            self._apply_fonts()
        # 锁定：只认文件里**明确写着**的 bool，缺键不动 ——
        # 否则老配置没有 locked 键时，每 500ms 会把面板强行解锁。
        lk = st.get("locked")
        if isinstance(lk, bool) and lk != self.locked:
            self.locked = lk
            try:
                self.lock_btn.config(text="🔒" if lk else "🔓")
            except Exception:
                pass
            self._hover_lock = False
            try:
                self._sync_clickthrough(force=True)
            except Exception:
                pass
            self._apply_note_state()
        # 显示时间：设置里改完**立即生效**（app.py 的对话框写着"热生效"）
        sh = st.get("show_time")
        if isinstance(sh, bool) and sh != self.show_time:
            self.show_time = sh
            self.last_sig = None
            self.refresh()
        ft = st.get("force_topmost")
        if isinstance(ft, bool):
            self.force_topmost = ft
        if text_changed:
            self.last_sig = None
            self.refresh()

    def _apply_fonts(self):
        """字号/缩放变化：重配共享 Font 对象，窗口加宽，重建行（热生效）"""
        global WIN_W, WIN_H
        sc = self._scale
        fs = self._font_size
        WIN_W, WIN_H = self._calc_size(sc, fs)
        try:
            self.f_t.configure(size=max(7, int(fs * sc)))
            self.f_s.configure(size=max(6, int(max(6, fs - 1) * sc)))
            self.canvas.configure(width=WIN_W - 26)
            self.info.configure(wraplength=WIN_W - 22)
            self.root.geometry(f"{WIN_W}x{WIN_H}+{self.pos[0]}+{self.pos[1]}")
        except Exception:
            pass
        self.last_sig = None
        self.refresh()

    def _apply_scale(self, sc):
        self._scale = sc
        self._apply_fonts()

    # ---------------- 悬停交互 ----------------
    def _title_rect_screen(self):
        """标题文字在屏幕上的物理像素矩形（保留给调试用）。"""
        return self._widget_rect_physical(self.title)

    def _widget_rect_physical(self, w):
        """
        控件在屏幕上的矩形，**物理像素**坐标系（与 GetCursorPos 一致）。

        ⚠️ winfo_rootx() 返回的是 Tk 的**逻辑像素**；开启 DPI 缩放时
        逻辑坐标和物理坐标差一个比例（175% 时 1118 ↔ 1957），
        直接拿来和 GetCursorPos 比会永远判不准悬停。
        做法：拿窗口的物理矩形，再把控件在窗口内的相对位置按比例换算。
        """
        try:
            self.root.update_idletasks()
            r = self._win_rect_physical()
            if not r:
                return None
            wx, wy, ww, wh = r
            lw = max(1, self.root.winfo_width())
            lh = max(1, self.root.winfo_height())
            sx = ww / lw
            sy = wh / lh
            ox = w.winfo_rootx() - self.root.winfo_rootx()
            oy = w.winfo_rooty() - self.root.winfo_rooty()
            return (wx + ox * sx, wy + oy * sy,
                    wx + (ox + w.winfo_width()) * sx,
                    wy + (oy + w.winfo_height()) * sy)
        except Exception:
            return None

    def _win_rect_physical(self):
        """窗口在屏幕上的物理矩形 (left, top, width, height)"""
        try:
            import ctypes
            import ctypes.wintypes
            r = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self._hwnd(), ctypes.byref(r))
            return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            return None

    @staticmethod
    def _cursor_pos():
        """光标在屏幕上的物理坐标；取不到返回 None。"""
        try:
            pt = ctypes.wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return int(pt.x), int(pt.y)
        except Exception:
            pass
        return None

    def _cursor_over_lock_btn(self):
        """光标是否停在锁定按钮上（往外扩 8px，方便对准）。"""
        try:
            pt = self._cursor_pos()
            if pt is None:
                return False
            r = self._widget_rect_physical(self.lock_btn)
            if not r:
                return False
            x0, y0, x1, y1 = r
            m = 8
            return (x0 - m <= pt[0] <= x1 + m) and (y0 - m <= pt[1] <= y1 + m)
        except Exception:
            return False

    def _want_clickthrough(self):
        """当前应该点击穿透吗：锁定，且鼠标不在锁按钮上。"""
        return bool(self.locked) and not self._hover_lock

    def _sync_clickthrough(self, force=False):
        """把一个目标状态真正写到窗口样式上（只在状态变化时写）。"""
        want = self._want_clickthrough()
        if not force and want == self._ct_on:
            return
        self._ct_on = want
        self._set_clickthrough(want)

    def _poll_cursor(self):
        """
        80ms 轮询光标位置，**唯一**决定点击穿透的地方。

        锁定 → 整个窗口穿透；但鼠标停在**锁按钮**上时临时取消穿透，
        这样点一下就能解开（否则锁定后永远点不到按钮，只能跑控制台）。

        ⚠️ 之前是两个 500ms 定时器互相打架：`_poll_cursor` 无条件打开穿透，
        `_hover_unlock` 又把它关掉 —— 锁按钮每 0.5 秒只有约 0.1 秒能点中，
        实测非常难解锁（用户反馈"锁了就解不开"）。现在只有一个状态机，
        80ms 内必然生效，点击基本不会漏。
        """
        try:
            if self.locked:
                self._hover_lock = self._cursor_over_lock_btn()
            else:
                self._hover_lock = False
            self._sync_clickthrough()
        except Exception:
            pass
        try:
            self.root.after(80, self._poll_cursor)
        except Exception:
            pass

    def _set_clickthrough(self, on):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = self._hwnd()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if on:
                ex |= WS_EX_TRANSPARENT
            else:
                ex &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            # 只在状态真的变化时打印，避免 PANEL_DEBUG=1 时每 80ms 刷一行
            if ex != getattr(self, "_last_ex", None):
                self._last_ex = ex
                if os.environ.get("PANEL_DEBUG") == "1":
                    print("[style] hwnd=%#x transparent=%s new_ex=%#x" %
                          (hwnd, on, ex), flush=True)
        except Exception as e:
            if os.environ.get("PANEL_DEBUG") == "1":
                print("[style] 失败:", e, flush=True)

    def _hwnd(self):
        """
        取**真正可见的顶层窗口**句柄。

        Tk 的 winfo_id() 返回的是内部客户区子窗口，样式要设在它的父窗口上
        （class 为 TkTopLevel 的包装窗口）才生效。实测：
            winfo_id()      = 0x4508ca   ← 改了没用
            GetParent(...)  = 0x240aac   ← 这个才是屏幕上的窗口
        """
        import ctypes
        h = ctypes.windll.user32.GetParent(self.root.winfo_id())
        return h or self.root.winfo_id()

    def _apply_style(self):
        if self._streaming:
            import stream_settings
            stream_settings.capture_style(self.root)
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = self._hwnd()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # ⚠️ **绝对不能**清 WS_EX_LAYERED。
            #    这个窗口的透明效果（-alpha 背景不透明度 + -transparentcolor 抠色）
            #    全靠分层属性；清掉之后 alpha 就完全失效 ——
            #    用户看到的现象正是"战绩的背景不透明度改不了"。
            #    之前这里写成 ex &= ~WS_EX_LAYERED，是把它当成了"防止 alpha 被冲掉"，
            #    实际效果正好相反。
            ex |= WS_EX_TOOLWINDOW
            # 一律不进置顶层：Z 序交给 _apply_zorder
            ex &= ~WS_EX_TOPMOST
            # 是否穿透由「锁定状态 + 光标是否停在锁按钮上」共同决定；
            # 启动时就按真实状态设好，否则解锁启动的面板前 80ms 内点击会漏给下层窗口。
            if self._want_clickthrough():
                ex |= WS_EX_TRANSPARENT
            else:
                ex &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            self._ct_on = self._want_clickthrough()
            # ⚠️ SetWindowLongW 会把分层窗口的属性（alpha / 抠色）重置掉，
            #    哪怕写回的是同一个值。之后 Tk 认为 "-alpha 没变" 就不再下发，
            #    窗口就变成"分层但没有任何 alpha" —— 背景不透明度怎么调都没反应。
            #    对策：先抖一下（设成略不同的值），再把真实值设回去，强制重发。
            try:
                self.root.attributes("-alpha", max(0.0, float(self._alpha) - 0.01))
                self.root.attributes("-alpha", 1.0 if self._streaming else float(self._alpha))
            except Exception:
                pass
            try:
                self.root.attributes("-transparentcolor", CHROMA)
            except Exception:
                pass
            if self.force_topmost:
                _apply_zorder(self.root)
        except Exception:
            pass

    # ---------------- 拖动 / 锁定 ----------------
    def _bind_drag_tree(self, w):
        """
        递归给控件树绑「按下 / 拖动 / 松开」。

        为什么必须递归：面板显示时标题和底板几乎被 canvas、body 和 Label
        完全盖住，只绑最外层的话，点在内容区的事件根本到不了拖动逻辑。
        为什么排除 Button / Scrollbar：它们自己的点击和拖块比"移窗口"重要。
        """
        if isinstance(w, self.NO_DRAG):
            return
        w.bind("<Button-1>", self._on_press)
        w.bind("<B1-Motion>", self._on_drag)
        w.bind("<ButtonRelease-1>", self._on_release)
        for child in w.winfo_children():
            self._bind_drag_tree(child)

    def _on_press(self, e):
        # 锁定时窗口本该点击穿透；万一 SetWindowLong 没生效，
        # 这里再兜一层，保证"锁定 = 不能拖"。
        if self.locked:
            return
        self._drag = (e.x_root, e.y_root, self.pos[0], self.pos[1])

    def _on_drag(self, e):
        if self.locked or not self._drag:
            return
        x0, y0, px, py = self._drag
        nx = px + (e.x_root - x0)
        ny = py + (e.y_root - y0)
        # 和 _poll_settings 的钳制保持一致：整块面板留在屏幕内，
        # 否则拖到边缘再重启会被拉回来，手感不一致。
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        nx = max(0, min(max(0, sw - WIN_W), nx))
        ny = max(0, min(max(0, sh - WIN_H), ny))
        self.pos = [nx, ny]
        self.root.geometry(f"+{nx}+{ny}")

    def _on_release(self, _e):
        self._drag = None
        if self.locked:
            return
        save_pos(self.pos[0], self.pos[1], self.locked)

    def _write_settings_locked(self, val):
        """
        把锁定状态写回 panel-settings.json —— 面板按钮和设置对话框共用一份状态。

        ⚠️ 之前这里用的是 `io.open`，但模块**从来没 import io**：
        NameError 被 except 吞掉，设置文件其实一次都没写成 ——
        用户看到的就是「面板上锁了，设置里还是没锁 / 重启又变回来」。
        另外改成 .tmp + os.replace，避免设置对话框正好在读半截文件。
        """
        try:
            # 先用面板当前的实际值打底，避免 panel-settings.json 本来不存在时
            # 只写一个 locked 键，把其它设置都变成"缺省"。
            st = {
                "show_time": bool(self.show_time),
                "alpha": round(float(self._alpha), 2),
                "text_alpha": round(float(self._text_alpha), 2),
                "locked": False,
                "scale": round(float(self._scale), 2),
                "font_size": int(self._font_size),
                "hero_display": getattr(self, "hero_display", "both"),
                "force_topmost": bool(getattr(self, "force_topmost", True)),
            }
            try:
                with open(PANEL_SET, encoding="utf-8") as fh:
                    old = json.load(fh) or {}
                if isinstance(old, dict):
                    st.update(old)      # 文件里已有的键优先，不覆盖用户设置
            except Exception:
                pass
            st["locked"] = bool(val)
            os.makedirs(os.path.dirname(PANEL_SET), exist_ok=True)
            tmp = "%s.tmp.%d" % (PANEL_SET, os.getpid())
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(st, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, PANEL_SET)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
            return True
        except Exception as e:
            print(f"（保存面板锁定状态失败：{e}）", flush=True)
            return False

    def _toggle_lock(self, _e=None):
        self.locked = not self.locked
        self.lock_btn.config(text="🔒" if self.locked else "🔓")
        # 刚点完按钮时光标就在按钮上：锁定后立即让这一块保持可点，
        # 用户移开 80ms 内又恢复穿透。这样"点上锁 → 再点一下解锁"顺手可用。
        self._hover_lock = bool(self.locked) and self._cursor_over_lock_btn()
        # 立刻生效，不等 _poll_cursor 的轮询：
        # 解锁后马上能拖，锁定后马上穿透（光标不在按钮上时）。
        self._sync_clickthrough(force=True)
        # 锁定 → 备注输入框切只读；解锁 → 恢复可编辑
        self._apply_note_state()
        save_pos(self.pos[0], self.pos[1], self.locked)
        # 写回设置文件 —— 让「面板上的锁按钮」和「设置里的锁定」保持同一份状态
        self._write_settings_locked(self.locked)
        try:
            self._apply_style()
        except Exception:
            pass
        print("战绩面板：" + ("已锁定" if self.locked else "已解锁"))
        return "break"

    # ---------------- 内容 ----------------
    def _tick(self):
        # 控制台没了就自己退出（避免关插件后残留面板）
        try:
            import parent_guard
            if parent_guard.orphaned():
                self.root.destroy()
                return
        except Exception:
            pass
        """
        定时重读 history.json **自动更新**（用户要求：插件负责更新战绩）。

        标题旁的 ↻ 不是"刷新数据"，而是**手动重置这个页面**（清空显示），
        所以这里必须继续自动读文件，否则新战绩永远不出现。
        """
        self._apply_alpha()
        self.refresh()
        self.root.after(3000, self._tick)

    def _load(self):
        """
        读 history.json；解析失败时退回**上次读成功的内容**。

        watch_mem 可能在写文件，偶尔读到半截 JSON —— 直接返回空数据会让
        面板闪一下「尚未结算」再恢复。有兜底就只是晚 3 秒刷新。
        """
        try:
            with open(HIST, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                self._last_good = d
                return d
        except Exception:
            pass
        if isinstance(getattr(self, "_last_good", None), dict):
            return self._last_good
        return {"records": [], "games": []}

    def close_panel(self):
        """
        标题旁的 ✕：关掉面板。

        面板是无边框窗口、又挂成游戏的从属窗口，没有系统标题栏可关，
        之前完全关不掉（用户反馈）。这里直接销毁窗口并退出进程；
        控制台再点「启动全部 / 重启」会重新拉起。
        """
        try:
            print("战绩面板：关闭")
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            import sys as _s
            _s.exit(0)
        except Exception:
            pass

    def force_refresh(self):
        """
        标题旁的 ↻ = **手动重置这个页面**（不是刷新数据）。

        点一下把已显示的战绩清空、显示「已重置」；
        之后插件写入新战绩时，3 秒轮询会自动把新内容读出来显示。

        记下重置那一刻的数据指纹：只有**指纹变了**（真有新战绩）才恢复显示，
        否则点击前刚写进来的那条会立刻又冒出来，变成"点了没重置"。
        """
        self._page_cleared = True
        try:
            self._reset_sig = self._sig_of(self._load())
        except Exception:
            self._reset_sig = None
        try:
            for w in self.body.winfo_children():
                w.destroy()
            self._note_entries = []
            self._note_pairs = []
            self.canvas.yview_moveto(0)
            self.info.config(text="已重置 · 等待新战绩")
        except Exception:
            pass
        try:
            print("战绩面板：已手动重置页面")
        except Exception:
            pass

    @staticmethod
    def _sig_of(d):
        """
        数据指纹：记录条数 + 每局 t/rank/hp/note + 两行备注 + 会话。

        备注行也要进指纹：只改了「流派/备注」而不重绘的话，输入框里刚打的字
        会在下一次自动刷新时被旧值覆盖。
        """
        recs = [r for r in (d.get("records") or []) if isinstance(r, dict)]
        games = [g for g in (d.get("games") or []) if isinstance(g, dict)]
        return (len(recs), len(games),
                tuple((g.get("t"), g.get("rank"), g.get("hp"), g.get("note"))
                      for g in games[-8:]),
                tuple(str(g.get("note_line1") or "") for g in games[-8:]),
                tuple(str(g.get("note_line2") or "") for g in games[-8:]),
                d.get("session"),
                d.get("placement"),
                tuple(r.get("actual_me") for r in recs))

    def refresh(self):
        """
        刷新面板。

        sig 里**必须带上 games 的信息**（长度 + 每条的 t/rank/hp/note）：
        新增一局、或者名次/备注被补写时都要重绘，否则面板会停在旧内容。
        """
        d = self._load()
        sig = self._sig_of(d)
        if sig == self.last_sig:
            return
        self.last_sig = sig
        # 用户手动重置过页面：等**新数据**到了再自动显示
        if getattr(self, "_page_cleared", False):
            if sig == getattr(self, "_reset_sig", None):
                return
            self._page_cleared = False

        recs = [r for r in (d.get("records") or []) if isinstance(r, dict)]
        raw_games = d.get("games") if isinstance(d.get("games"), list) else []
        # 保留原始下标：备注写回 history.json 用的就是它，过滤坏数据不能丢
        games_idx = [(i, g) for i, g in enumerate(raw_games) if isinstance(g, dict)]
        games_all = [g for _, g in games_idx]

        # 用户要的是**结算战绩**：血量归零/拿第一时的名次。优先展示 games
        try:
            parts = []
            _sess = d.get("session")
            if _sess:
                parts.append(f"开局 {_sess}")
            if games_all:
                _last = games_all[-1]
                rank = _last.get("rank")
                hp = _num(_last.get("hp"), None)
                if rank:
                    parts.append(f"本局第{rank}名")
                elif hp is not None and hp <= 0:
                    # 名次没读到 ≠ 还在进行；不能谎称"进行中"
                    parts.append("本局已淘汰 · 名次未记录")
                else:
                    parts.append("本局进行中")
                # ⚠️ 只统计**已结算**的局（hp 有值）。进行中的那条也在 games 里，
                #    之前直接 len(games) 就显示成"已完成 N 局"，
                #    用户看到"我还没打完怎么就完成一局了"。
                _settled = [g for g in games_all if _num(g.get("hp"), None) is not None]
                parts.append(f"已完成 {len(_settled)} 局")
            else:
                parts.append("尚未结算")
                if recs:
                    parts.append(f"已打 {len(recs)} 场")
            self.info.config(text="   ".join(parts))
        except Exception as e:
            print(f"（面板头部渲染失败：{e}）", flush=True)
            self.info.config(text="战绩数据读取异常")

        # 重建前先把还挂在旧输入框上的改动落盘，避免"正好有新增记录时打字丢失"
        for pair in getattr(self, "_note_pairs", []):
            try:
                self._save_game_notes(*pair, quiet=True)
            except Exception:
                pass
        for w in self.body.winfo_children():
            w.destroy()
        self._note_entries = []
        self._note_pairs = []
        try:
            if games_idx:
                for i, g in games_idx:
                    no = i + 1
                    try:
                        self._game_row(no, i, g)
                    except Exception as e:
                        # 单条坏数据不能把整个面板搞空白
                        print(f"（第{no}局渲染失败：{e}）", flush=True)
                        tk.Label(self.body, text=f"第{no}局 · 数据渲染失败",
                                 bg=BG, fg=blend(RED, self._text_alpha),
                                 font=self.f_s, anchor="w").pack(fill="x", pady=3)
            elif not recs:
                tk.Label(self.body, text="还没有战绩，打一局试试喵", bg=BG,
                         fg=blend(GOLD2, self._text_alpha),
                         font=self.f_s).pack(pady=16)
            else:
                for i, r in enumerate(recs, 1):
                    try:
                        self._row(i, r)
                    except Exception as e:
                        print(f"（第{i}场渲染失败：{e}）", flush=True)
        except Exception as e:
            print(f"（面板内容渲染失败：{e}）", flush=True)
            tk.Label(self.body, text="战绩内容渲染失败 · 详见 panel-err.txt",
                     bg=BG, fg=blend(RED, self._text_alpha),
                     font=self.f_s, anchor="w").pack(fill="x", pady=8)
        # 行控件每次刷新都会重建，绑定必须在重建后再做一遍，
        # 否则新 Label 上又拖不动了。
        self._bind_drag_tree(self.frame)
        # 内容高度变化后同步自定义滚动条（原生滚动条已换成 ThinScrollbar）
        try:
            self.root.update_idletasks()
            self.sb.set(*self.canvas.yview())
        except Exception:
            pass

    def _game_row(self, no, idx0, g):
        """
        一整局的结算战绩：序号 + 主战者 + 名次 + 自动阵容 + 两行备注。

        no   → 面板上显示的序号（1 起）
        idx0 → 在 history.json 的 games 里的真实下标（备注写回用）
        """
        import history as HIS
        t = self._text_alpha
        f = tk.Frame(self.body, bg=BG)
        f.pack(fill="x", pady=3)

        # 用户要求：**去掉整行状态**（"已出局"/"进行中"都不显示），
        # 时间挪到主战者那一行的最后面，且可在设置里关掉。
        rank = g.get("rank")

        # 主战者行：**只显示名字**（不拼种族），后面紧跟自动推断的阵容。
        # 自动阵容是派生值，即时计算，不写 history.json。
        hero = g.get("hero")
        try:
            name, _prof = HIS.hero_name(hero) if hero else ("未知英雄", "")
        except Exception:
            name = f"英雄{hero}"
        board = g.get("board") or []
        hero_row = tk.Frame(f, bg=BG)
        hero_row.pack(fill="x")
        # 序号：定宽 3 位、右对齐，避免 1 位/2 位数导致名字错位；颜色比名字暗
        tk.Label(hero_row, text=str(no), bg=BG, fg=blend(GOLD2, t),
                 font=self.f_s, width=3, anchor="e").pack(side="left", padx=(0, 4))
        tk.Label(hero_row, text=name, bg=BG, fg=blend(FG, t), anchor="w",
                 justify="left", wraplength=max(80, WIN_W - 50),
                 font=self.f_s).pack(side="left")
        # 名次写在**主战者名字后面**（用户要求）；第 1 名用金色
        if rank:
            tk.Label(hero_row, text=f"第{rank}名", bg=BG,
                     fg=blend(GOLD if rank == 1 else GOLD2, t),
                     font=self.f_s).pack(side="left", padx=(6, 0))
        try:
            auto_tribe = auto_line1_from_board(board)
        except Exception:
            auto_tribe = ""
        if auto_tribe:
            # board 为空 / 卡名全查不到时 auto_tribe == ""，这里什么都不画。
            # 大字号时换到下一行，避免"名字 + 阵容"横向挤爆面板。
            if self._font_size >= 12:
                tk.Label(f, text="   " + auto_tribe, bg=BG,
                         fg=blend(GOLD2, t), anchor="w", justify="left",
                         wraplength=max(80, WIN_W - 40),
                         font=self.f_s).pack(fill="x", padx=(28, 0))
            else:
                tk.Label(hero_row, text="   " + auto_tribe, bg=BG,
                         fg=blend(GOLD2, t), anchor="w", justify="left",
                         wraplength=max(80, WIN_W - 80),
                         font=self.f_s).pack(side="left")
        # 时间：放在这一行的**最后面**（用户要求），可在面板设置里关闭
        if getattr(self, "show_time", True):
            try:
                _t_txt = str(g.get("t") or "").strip()
                if _t_txt:
                    tk.Label(hero_row, text="  " + _t_txt, bg=BG,
                             fg=blend(DIM, t), font=self.f_s).pack(side="left")
            except Exception:
                pass
        # 用户要求：阵容只留种族名，不要星级/数量；终局随从明细不再单独占一行
        # （note: 旧 `_brief` 的 ★攻/血 明细已从这里去掉）。
        self._note_inputs(f, idx0, g, t)

    def _note_inputs(self, parent, idx0, g, t):
        """同一行两个输入框：流派 / 备注——失焦或回车时写回 history.json。"""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(2, 0))
        entries = []
        for label, key in (("流派", "note_line1"), ("备注", "note_line2")):
            tk.Label(row, text=label, bg=BG, fg=blend(NOTE_LABEL, t),
                     font=self.f_s, width=3, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(g.get(key) or ""))
            ent = tk.Entry(row, textvariable=var, width=1,
                           bg=NOTE_BG, fg=blend(NOTE_FG, t),
                           insertbackground=blend(NOTE_FG, t),
                           readonlybackground=NOTE_BG_RO,
                           relief="flat", bd=0, highlightthickness=1,
                           highlightbackground=blend(RULE, t),
                           highlightcolor=blend(GOLD, t),
                           font=self.f_s)
            ent.pack(side="left", fill="x", expand=True, padx=(2, 6))
            entries.append(ent)

        def save(_e=None):
            self._save_game_notes(idx0, entries[0], entries[1], g)

        for ent in entries:
            ent.bind("<FocusOut>", save)
            ent.bind("<Return>", save)
        self._note_entries.extend(entries)
        self._note_pairs.append((idx0, entries[0], entries[1], g))
        self._apply_note_state()

    def _save_game_notes(self, idx0, ent1, ent2, g, quiet=False):
        """把两行备注写回 history.json（只在内容真的变了时写盘）。"""
        try:
            v1 = ent1.get().strip()
            v2 = ent2.get().strip()
            old1 = str(g.get("note_line1") or "").strip()
            old2 = str(g.get("note_line2") or "").strip()
            if (v1, v2) == (old1, old2):
                return
            import history as HIS
            if HIS.update_game(idx0, note_line1=v1, note_line2=v2):
                g["note_line1"] = v1
                g["note_line2"] = v2
                if not quiet:
                    print(f"战绩备注已保存：第{idx0 + 1}局 流派={v1!r} "
                          f"备注={v2!r}", flush=True)
        except Exception as e:
            if not quiet:
                print(f"（保存战绩备注失败：{e}）", flush=True)

    def _apply_note_state(self):
        """锁定（点击穿透）时输入框切只读，避免误触；解锁后恢复可编辑。"""
        ro = bool(self.locked)
        for ent in getattr(self, "_note_entries", []):
            try:
                if ro:
                    ent.config(state="readonly", readonlybackground=NOTE_BG_RO,
                               fg=blend(NOTE_FG, self._text_alpha))
                else:
                    ent.config(state="normal", bg=NOTE_BG,
                               fg=blend(NOTE_FG, self._text_alpha))
            except Exception:
                pass

    def _row(self, idx, r):
        import history as HIS
        t = self._text_alpha
        f = tk.Frame(self.body, bg=BG)
        f.pack(fill="x", pady=2)

        head = tk.Frame(f, bg=BG)
        head.pack(fill="x")
        win = _num(r.get("win"), 0)
        loss = _num(r.get("loss"), 0)
        col = GREEN if win >= 50 else (RED if loss >= 50 else DIM)
        tk.Label(head, text=f"第{idx}把", bg=BG, fg=blend(GOLD, t),
                 font=self.f_t).pack(side="left")
        tk.Label(head, text=f" {r.get('t', '')} 胜{win:g}%", bg=BG,
                 fg=blend(col, t), font=self.f_s).pack(side="left")
        if False:  # 用户要求：不显示"最后一次受到多少伤害"
            tk.Label(head, text="", bg=BG,
                     fg=blend(DIM, t), font=self.f_s).pack(side="left")

        hero = r.get("me_hero")
        try:
            name, prof = HIS.hero_name(hero) if hero else ("未知英雄", "")
        except Exception:
            name, prof = (f"英雄{hero}", "")
        tag = name + (f"·{prof}" if prof else "")
        lv = r.get("me_lv", "?")
        hp = r.get("me_hp", "?")
        tk.Label(f, text=f"{tag} Lv{lv} 血{hp}", bg=BG,
                 fg=blend(FG, t), anchor="w", justify="left",
                 wraplength=max(80, WIN_W - 30),
                 font=self.f_s).pack(fill="x")
        tk.Label(f, text=self._brief(r.get("me_board") or []), bg=BG,
                 fg=blend(DIM, t), anchor="w", justify="left",
                 wraplength=WIN_W - 30, font=self.f_s).pack(fill="x")

    @staticmethod
    def _brief(board, limit=4):
        items = []
        for c in board or []:
            if not isinstance(c, dict):
                continue
            items.append(f"{c.get('star', '?')}★{c.get('attack', '?')}/{c.get('health', '?')}")
        if not items:
            return "阵容 （空）"
        extra = f" +{len(items)-limit}" if len(items) > limit else ""
        return "阵容 " + " ".join(items[:limit]) + extra


def main():

    # 单实例守卫：同名窗口已存在就别再开一个
    try:
        import single_instance as _SI
        if _SI.already_running("影之幻境 · 战绩"):
            print("已有同名面板在运行，退出")
            import sys as _s
            _s.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass
    try:
        ui_dpi.enable()
    except Exception:
        pass
    root = tk.Tk()
    try:
        import ui_icon
        ui_icon.set_window_icon(root)
    except Exception:
        pass
    Panel(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
