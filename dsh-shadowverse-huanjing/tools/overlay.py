#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之幻境 · 游戏画面悬浮层（类似酒馆插件 Hearthstone Deck Tracker 的胜率浮窗）。

实现方式（实测环境只有 tkinter，无 Qt/wx）：
  · tkinter 无边框窗口  → overrideredirect(True)
  · Win32 分层窗口      → WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
      - LAYERED       : 支持透明
      - TRANSPARENT   : **鼠标点击穿透**（否则会挡住操作游戏）
      - TOPMOST       : 压在游戏之上
  · 抠色透明            → -transparentcolor，把背景色挖掉，只留文字

**拖动与锁定**（用户需求）：
  · 默认**锁定**（点击穿透），不会挡操作，也不怕误拖
  · 解锁：双击浮层 / Alt+O 切换 / 改 overlay-pos.json 里的 locked=false
  · 解锁后：按住左键拖动 → 松手自动存位置；再双击即重新锁定
  · 位置与锁定状态存 shots/live/overlay-pos.json，**下次启动自动恢复**

数据来源：轮询 shots/live/overlay.json（由监视器写）。

用法：
  python tools/overlay.py --demo            # 演示（假数据，验证悬浮效果）
  python tools/overlay.py                   # 跟随 live/overlay.json
  python tools/overlay.py --pos 40,60 --scale 1.2
  python tools/overlay.py --unlocked        # 启动即解锁（可直接拖）
"""
import ctypes
import json
import os
import sys
import time
import traceback

import tkinter as tk
try:
    _SD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "svwb-probe")
    if _SD not in sys.path:
        sys.path.insert(0, _SD)
    import ui_dpi
except Exception:
    class _NoDpi:
        SCALE = 1.0
    ui_dpi = _NoDpi()

# 调试开关（环境变量，启动前设置）：
#   PANEL_DEBUG=1  → 把每次渲染的内容 / 异常打到 stdout（app.py 会写进 overlay-out.txt）
#   PANEL_OPAQUE=1 → 不抠色（不设 -transparentcolor），用于截图/排查"看不见"
DEBUG = os.environ.get("PANEL_DEBUG") == "1"
OPAQUE = os.environ.get("PANEL_OPAQUE") == "1"


def dbg(msg):
    if DEBUG:
        try:
            print(msg, flush=True)
        except Exception:
            pass


HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
LIVE = os.path.join(PKG, "shots", "live")
STATE = os.path.join(LIVE, "overlay.json")
POSFILE = os.path.join(LIVE, "overlay-pos.json")
SETTINGS = os.path.join(LIVE, "overlay-settings.json")

CHROMA = "#010203"          # 抠色透明用的键色
# ── 低调奢华的配色 ────────────────────────────────────────────
# 深墨蓝底 + 香槟金描边 + 象牙白正文：不用高饱和色，靠层次和留白撑质感
PANEL_BG = "#12161d"        # 底板：深墨蓝（比纯黑有厚度）
PANEL_BORDER = "#3a3a44"    # 描边：极细的枪灰金边
TITLE_FG = "#c9a86a"        # 标题：香槟金（可拖动时）
TITLE_LOCKED_FG = "#8d7a52" # 锁定时标题压暗，不抢视线
BODY_FG = "#efe9dc"         # 正文：象牙白
DETAIL_FG = "#9aa7a0"       # 明细：柔和青灰（替代原来的亮绿）
NOTE_FG = "#8d7a52"         # 备注：压暗的金
RULE_FG = "#2a3038"         # 分隔线：比底板略亮的细线
ALPHA = 0.80                # 略降不透明度，浮在画面上更轻
FONT_SIZE = 16              # 基准字号（胜率主行），其余文字按比例缩
FONT_MIN, FONT_MAX = 9, 24

# ── 设置窗口控件配色 ───────────────────────────────────────────
# 设置窗里**可交互的控件**（滑块 / 按钮 / 开关）在深色底上太暗会看不清，
# 统一比底板亮一档；滑块（tk.Scale 的 bg 就是滑块本体）用金色系，
# 与「香槟金 + 象牙白」的整体风格一致。只调颜色，不改布局。
SET_BG = "#1b2130"          # 设置窗底板（沿用原色）
SET_BG2 = "#242e42"         # 输入框 / 次级底（比底板亮一档）
SET_FG = BODY_FG            # 控件正文：象牙白
SET_MUTED = "#aebbd0"       # 次要说明文字（原 #8b98ad 太暗）
SET_TROUGH = "#5a6d8f"      # 滑槽：比底板明显亮一档（最容易看不清的就是它）
SET_SLIDER = TITLE_FG       # 滑块本体：香槟金
SET_SLIDER_HI = "#e6c88f"   # 拖动 / 悬停时的高亮金
SET_SELECT = "#4a5f85"      # 勾选框 / 单选框的选中底色
SET_BTN = "#39465e"         # 普通按钮底
SET_BTN_HI = "#4d5d7d"      # 普通按钮悬停底
SET_DARKTEXT = "#1a1209"    # 金色按钮上的深色字


def blend_text(fg_hex, alpha, bg_hex=None):
    """
    把文字色按 alpha 向底板色混合，模拟"文字不透明度"。

    Tk 没有逐控件 alpha：窗口 -alpha 会同时影响背景与文字。
    所以文字这一项用颜色混合实现 —— 背景越实，视觉上越接近真实的半透明文字。
    alpha=1.0 → 原色；alpha=0.0 → 与底板完全融合（看不见）。
    """
    try:
        a = max(0.0, min(1.0, float(alpha)))
        bg = bg_hex or PANEL_BG
        f = tuple(int(fg_hex[i:i + 2], 16) for i in (1, 3, 5))
        b = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(
            int(round(b[i] + (f[i] - b[i]) * a)) for i in range(3))
    except Exception:
        return fg_hex



GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
HWND_NOTOPMOST = -2
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# 全局热键：Ctrl+Alt+O 切换「可拖动 / 点击穿透锁定」。
#
# ⚠️ 实测教训（两个必须避免的坑）：
#   1) 不能用 GetAsyncKeyState 轮询判断修饰键：Ctrl/Alt 的**虚拟键码**是 0x11/0x12，
#      而 MOD_CONTROL/MOD_ALT 是**修饰标志**（0x0002/0x0001）。
#      之前误把标志当键码传，导致热键永远不触发、用户"拖不动"。
#   2) 不能用"双击浮层"切换：浮层若是点击穿透，点击会穿过它去游戏，
#      浮层根本收不到双击 —— 所以**默认必须可点击**，锁定才是可选状态。
HOTKEY_ID = 0xB001
MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
VK_CONTROL, VK_MENU, VK_O, VK_S, VK_Q = 0x11, 0x12, 0x4F, 0x53, 0x51


def _num(v, default):
    """设置文件里的数字容错：坏值/空值 → default（避免 float() 抛异常把窗口搞没）"""
    try:
        f = float(v)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def load_pos():
    try:
        with open(POSFILE, encoding="utf-8") as fh:
            d = json.load(fh)
            # 默认 **不锁定**（可拖动）：点击穿透时收不到鼠标事件，既没法拖也没法双击
            return ((int(_num(d.get("x"), 60)), int(_num(d.get("y"), 60))),
                    max(0.6, min(2.2, _num(d.get("scale"), 1.0))),
                    bool(d.get("locked", False)))
    except Exception:
        return (60, 60), 1.0, False


def save_pos(x, y, scale, locked):
    os.makedirs(LIVE, exist_ok=True)
    try:
        with open(POSFILE, "w", encoding="utf-8") as fh:
            json.dump({"x": int(x), "y": int(y), "scale": round(scale, 2), "locked": bool(locked)},
                      fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_state(path=None, tries=3, delay=0.03):
    """
    读 overlay.json（判定器写的 feed）。

    为什么要重试：判定器是**原子替换**写入（tmp + os.replace），但 Windows 上
    "读者正开着目标文件"仍可能让某一次读失败。旧实现一次失败就把界面退回
    「快来一场」的默认文案 —— 用户看到的就是"这一场没出数"。
    """
    p = path or STATE
    tries = max(1, int(tries))
    last = None
    for i in range(tries):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(delay)
    raise last



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


class Overlay:
    def __init__(self, pos=None, scale=None, demo=False, unlocked=False):
        p, s, locked = load_pos()
        self.pos = pos if pos is not None else p
        if self.pos:
            _d = getattr(ui_dpi, "SCALE", 1.0)
            self.pos = (int(round(self.pos[0] * _d)), int(round(self.pos[1] * _d)))
        self.scale = scale if scale is not None else s
        self.font_size = FONT_SIZE
        # --unlocked 强制可拖；否则沿用上次保存的状态（默认不锁定 = 可拖）
        self.locked = locked if not unlocked else False
        self.text_alpha = 1.0   # 文字不透明度（1=原色）
        self.demo = demo
        self._drag = None
        # 设置/位置热重载
        self._set_mtime = 0
        self._pos_mtime = 0
        self._settings_key = None
        self.live_mode = False
        self._last_hot = 0.0
        # 渲染状态：保证"任何情况下都有可见文字"，也用来判断"读取中"卡了多久
        self._rendered_once = False
        self._last_phase = None
        self._wait_since = None
        self._last_style_at = 0.0
        self._last_render = None
        self._last_data = None
        self._last_render_at = 0.0

        self.root = tk.Tk()
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'svwb-probe'))
            import ui_icon
            ui_icon.set_window_icon(self.root)
        except Exception:
            pass
        self.root.overrideredirect(True)
        # 唯一标题：单实例判断只认它自己的窗口（之前用「影之幻境」会被控制台/悬浮窗误判）
        try:
            self.root.title("影之幻境 · 胜率浮层")
        except Exception:
            pass
        self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))) + "/svwb-probe")
            import game_win as _GW
            self.root.attributes("-topmost", False)
        except Exception:
            self.root.attributes("-topmost", True)
        if not OPAQUE:
            try:
                # 抠色透明：只把 CHROMA 挖掉，底板与文字保留
                self.root.attributes("-transparentcolor", CHROMA)
            except Exception:
                pass
        else:
            dbg("[init] PANEL_OPAQUE=1：跳过抠色透明")
        try:
            # 半透明：让底板呈现"半透明长方形"效果
            self.root.attributes("-alpha", ALPHA)
        except Exception:
            pass
        self.root.configure(bg=CHROMA)

        # 长方形底板：外层 root 用抠色（不可见），内层 frame 用底板 + 描边
        # ⚠️ 用户要求：**只有"能拖动"（未锁定）时才显示淡粉标题与透明框**，
        #    作为"现在可以拖我"的视觉提示；锁定后恢复朴素样式，不挡视线。
        # 1px 枪灰金描边：让浮层有轮廓，但不用亮色
        self.frame = tk.Frame(self.root, bg=PANEL_BG,
                              highlightbackground=PANEL_BORDER,
                              highlightcolor=PANEL_BORDER,
                              highlightthickness=1, bd=0)
        self.frame.pack(padx=1, pady=1, fill="both", expand=True)
        self.pad = tk.Frame(self.frame, bg=PANEL_BG)
        self.pad.pack(fill="both", expand=True, padx=12, pady=8)

        f = self._font
        # 标题行（只放标题）。
        # ⚠️ 用户要求：**去掉这里右上角的齿轮** —— 设置入口已统一到上方的控制条，
        #    面板里再放一个齿轮属于重复入口，还占宽度、影响观感。
        top = tk.Frame(self.pad, bg=PANEL_BG)
        top.pack(fill="x", anchor="w")
        # 字间加空格制造"字距感"（Tk 不支持 letter-spacing）
        self.title = tk.Label(top, text="影之幻境 · 胜率", bg=PANEL_BG, fg=TITLE_LOCKED_FG,
                              font=f(13, True), anchor="w")
        self.title.pack(side="left")

        # 极细金色分隔线：撑出排版层次
        self.rule = tk.Frame(self.pad, bg=RULE_FG, height=1)
        self.rule.pack(fill="x", pady=(6, 6))

        # 用户要求：胜率与掉血概率**居中显示**
        self.line = tk.Label(self.pad, text="", bg=PANEL_BG, fg=BODY_FG,
                             font=f(16, True), anchor="center", justify="center")
        self.line.pack(anchor="center", fill="x")
        self.detail = tk.Label(self.pad, text="", bg=PANEL_BG, fg=DETAIL_FG,
                               font=f(10, False), anchor="center", justify="center")
        self.detail.pack(anchor="center", fill="x")
        self.note = tk.Label(self.pad, text="", bg=PANEL_BG, fg=NOTE_FG,
                             font=f(11, False), anchor="center", justify="center")
        self.note.pack(anchor="center", fill="x")

        for w in (self.root, self.frame, self.pad, self.title, self.rule,
              self.line, self.detail, self.note):
            w.bind("<Button-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Double-Button-1>", self._toggle_lock)

        self._apply_font_sizes()
        self.root.update_idletasks()
        _s = load_settings()
        if _s:
            self.apply_settings(_s)
        self._apply_style()
        self._refresh_look()
        self._register_hotkey()
        self._last_mtime = 0
        self._tick()

    def apply_settings(self, s):
        """
        应用设置面板（或设置文件）里的参数，**立即生效**。

        可调：不透明度 alpha、字号 scale、标题色 titleColor、锁定 locked。
        监视器侧参数（interval/settle）由 battle_monitor 每轮读同一文件，不在此处理。
        """
        global TITLE_FG, ALPHA
        try:
            if "alpha" in s:
                ALPHA = max(0.3, min(1.0, _num(s["alpha"], ALPHA)))
                self.root.attributes("-alpha", ALPHA)
            if "scale" in s:
                self.scale = max(0.6, min(2.2, _num(s["scale"], self.scale)))
                self._apply_font_sizes()
            if "font_size" in s:
                fs = max(FONT_MIN, min(FONT_MAX,
                                       int(_num(s["font_size"], self.font_size))))
                if fs != self.font_size:
                    self.font_size = fs
                    self._apply_font_sizes()
            if s.get("titleColor"):
                TITLE_FG = str(s["titleColor"])
            if "text_alpha" in s:
                self.text_alpha = max(0.15, min(1.0, _num(s["text_alpha"], 1.0)))
            if "locked" in s:
                self.locked = bool(s["locked"])
                self._apply_style()
            self._refresh_look()
            # liveMode 放最后：它会覆盖 alpha / 分层样式
            if "liveMode" in s:
                self._set_live_mode(bool(s["liveMode"]))
        except Exception as e:
            print(f"（应用设置失败：{e}）")

    def _set_live_mode(self, on):
        """
        直播模式：把同一个窗口变成**普通不透明窗口**，让 OBS「窗口采集」能抓到。
        透明分层窗口（WS_EX_LAYERED + transparentcolor）在 OBS 里通常全黑。

        做法（不重建窗口，位置/字号/内容都不变）：
          · alpha = 1.0
          · transparentcolor 设成一个不会出现的颜色（等于关闭色键）
          · 直接清掉 WS_EX_LAYERED，刷新框架
        关闭时恢复 CHROMA 色键 + ALPHA，并重新应用样式。
        """
        on = bool(on)
        if on == getattr(self, "live_mode", None):
            return
        self.live_mode = on
        try:
            if on:
                self.root.attributes("-alpha", 1.0)
                try:
                    self.root.attributes("-transparentcolor", "#f0f1f2")
                except Exception:
                    pass
                self.root.configure(bg=PANEL_BG)
                hwnd = self._hwnd()
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ex &= ~WS_EX_LAYERED
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | 0x0020)
                self.root.configure(bg=PANEL_BG)
                # 内层 frame/pad 本来就是 PANEL_BG，边框也保持
            else:
                self.root.configure(bg=CHROMA)
                # 直播模式里我们手动清掉了 WS_EX_LAYERED，Tk 自己不会再加回来
                hwnd = self._hwnd()
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ex |= WS_EX_LAYERED
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | 0x0020)
                # 先切到一个临时色键再切回 CHROMA，强制 Tk 重设分层属性
                try:
                    self.root.attributes("-transparentcolor", "#f0f1f2")
                    self.root.attributes("-transparentcolor", CHROMA)
                except Exception:
                    pass
                self.root.attributes("-alpha", ALPHA)
                self._apply_style()
            self._refresh_look()
            print("悬浮层直播模式：" + ("开启（不透明，可被 OBS 采集）" if on else "关闭（透明）"))
        except Exception as e:
            print(f"（切换直播模式失败：{e}）")

    def _refresh_look(self):
        """
        按锁定状态切换外观（用户要求）：
          · 未锁定（可拖动）→ 标题**淡粉色** + **长方形透明描边框**（提示"可以拖我"）
          · 已锁定（点击穿透）→ 标题恢复淡蓝，描边隐藏（不挡游戏画面）
        """
        try:
            a = getattr(self, "text_alpha", 1.0)
            if self.locked:
                self.title.config(fg=blend_text(TITLE_LOCKED_FG, a))
                self.frame.config(highlightthickness=0,
                                  highlightbackground=PANEL_BG, highlightcolor=PANEL_BG)
            else:
                self.title.config(fg=blend_text(TITLE_FG, a))
                self.frame.config(highlightthickness=2,
                                  highlightbackground=blend_text(PANEL_BORDER, a),
                                  highlightcolor=blend_text(PANEL_BORDER, a))
            # 正文 / 明细 / 备注 也按同一比例淡出
            self.line.config(fg=blend_text(BODY_FG, a))
            self.detail.config(fg=blend_text(DETAIL_FG, a))
            self.note.config(fg=blend_text(NOTE_FG, a))
            self._resize_to_content()
        except Exception:
            pass

    def _resize_to_content(self):
        """
        让窗口**按内容自适应大小**，避免文字被裁。

        ⚠️ 实测教训：描边厚度（highlightthickness）变化时，若用 geometry 写死宽高，
        内容会比窗口宽，右侧文字被裁掉。所以这里**只移动窗口、不锁定尺寸**，
        由 tkinter 依内容自行调整。
        """
        try:
            self.root.update_idletasks()
            self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")
        except Exception:
            pass

    def _open_settings(self, _evt=None):
        """
        打开设置面板（右上角齿轮）。

        为什么设置面板必须能点：浮层在**可拖动**状态下才收得到点击；
        若已锁定（点击穿透），齿轮也点不到 —— 所以点击时若处于锁定态，
        先自动切到可拖动，再打开设置。
        """
        try:
            if self.locked:
                self.locked = False
                self._apply_style()
                self._refresh_look()
            SettingsWindow(self)
        except Exception as e:
            print(f"（打开设置失败：{e}）")
        return "break"

    # ---------- 样式（锁定=点击穿透） ----------
    def _hwnd(self):
        return ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()

    def _apply_style(self):
        try:
            hwnd = self._hwnd()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # ⚠️ 不要手动置 WS_EX_LAYERED：
            # Tk 的 -alpha / -transparentcolor 已经把窗口设成分层窗口，
            # 这里再 SetWindowLong 写一次会冲掉分层属性 → 窗口"存在但看不见"
            # （控制台曾经就是这么变空白的，面板那边有同样的记录）。
            # 这里只补充 TOOLWINDOW / TOPMOST / TRANSPARENT，LAYERED 保持原样。
            ex |= WS_EX_TOOLWINDOW
            # 一律不进置顶层：Z 序交给 _apply_zorder（贴游戏、可被遮挡）
            ex &= ~WS_EX_TOPMOST
            if self.locked:
                ex |= WS_EX_TRANSPARENT          # 点击穿透
            else:
                ex &= ~WS_EX_TRANSPARENT         # 可接收鼠标
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            # 贴到游戏正上方（普通层）；游戏不在前台会自动退回普通层
            if getattr(self, "force_topmost", True):
                _apply_zorder(self.root)
            if ex != getattr(self, "_last_ex", None):
                self._last_ex = ex
                dbg("[style] hwnd=%#x ex=%#x locked=%s" % (hwnd, ex, self.locked))
        except Exception as e:
            print(f"（设置窗口样式失败：{e}）")

    def _toggle_lock(self, _evt=None):
        self.locked = not self.locked
        self._apply_style()
        self._refresh_look()
        save_pos(self.pos[0], self.pos[1], self.scale, self.locked)
        print(f"悬浮层：{'已锁定（点击穿透，不能拖）' if self.locked else '已解锁（可拖动）'}")
        return "break"

    def _register_hotkey(self):
        """注册全局热键 Ctrl+Alt+O 切换锁定（失败则只能双击切换）"""
        try:
            if user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_O):
                self.root.after(120, self._poll_hotkey)
                print("全局热键：Ctrl+Alt+O 切换锁定・Ctrl+Alt+S 设置・Ctrl+Alt+Q 关闭")
            else:
                print("（全局热键注册失败，用双击浮层切换锁定）")
        except Exception:
            pass

    def _poll_hotkey(self):
        """
        轮询全局热键（用**正确的虚拟键码**：Ctrl=0x11、Alt=0x12）。

        ⚠️ 实测教训：用 GetAsyncKeyState 判断修饰键时**不能传 MOD_CONTROL/MOD_ALT** ——
        那是修饰标志（0x0002/0x0001），不是虚拟键码，会导致热键永远不触发。

        两个热键：
          Ctrl+Alt+O → 切换「可拖动 / 锁定穿透」
          Ctrl+Alt+S → 打开设置（**锁定状态下也能用** —— 否则点击穿透后齿轮点不到，
                       设置就再也打不开了，这是用户实际踩到的坑）
          Ctrl+Alt+Q → 关闭悬浮层（等同控制条的 ✕）
        """
        try:
            ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
            alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
            if ctrl and alt:
                if user32.GetAsyncKeyState(VK_O) & 0x8000:
                    self._toggle_lock()
                    time.sleep(0.5)
                elif user32.GetAsyncKeyState(VK_S) & 0x8000:
                    self._open_settings()
                    time.sleep(0.5)
                elif user32.GetAsyncKeyState(VK_Q) & 0x8000:
                    # 关闭：不弹确认框（热键场景下弹框很烦），直接退出
                    self.root.quit()
                    return
        except Exception:
            pass
        self.root.after(200, self._poll_hotkey)

    # ---------- 拖动 ----------
    def _on_press(self, evt):
        if self.locked:
            return "break"
        self._drag = (evt.x_root - self.pos[0], evt.y_root - self.pos[1])
        return "break"

    def _on_drag(self, evt):
        if self.locked or not self._drag:
            return "break"
        x = evt.x_root - self._drag[0]
        y = evt.y_root - self._drag[1]
        self.pos = (x, y)
        self.root.geometry(f"+{x}+{y}")
        return "break"

    def _on_release(self, _evt):
        if self.locked:
            return "break"
        self._drag = None
        save_pos(self.pos[0], self.pos[1], self.scale, self.locked)
        print(f"位置已保存：{self.pos}")
        return "break"

    def _apply_font_sizes(self):
        """字号变化：重配 4 个文本控件，并按字号设置换行宽度"""
        try:
            f = self._font
            self.title.config(font=f(13, True), wraplength=max(260, int(420 * self.font_size / 16.0)))
            self.line.config(font=f(16, True), wraplength=max(300, int(520 * self.font_size / 16.0)))
            self.detail.config(font=f(10, False), wraplength=max(300, int(520 * self.font_size / 16.0)))
            self.note.config(font=f(11, False), wraplength=max(300, int(520 * self.font_size / 16.0)))
        except Exception:
            pass

    def _font(self, size, bold):
        """字体缓存：render() 每次调用都新建 Font 会在 Tk 里累积字体对象。

        基准字号 font_size（默认 16）按当前 size/16 的比例缩放；
        再叠加 scale（窗口整体缩放），默认观感与原来的 size*scale 完全一致。
        """
        import tkinter.font as tkfont
        key = (int(size), bool(bold), round(float(self.scale), 2),
               int(self.font_size))
        cache = getattr(self, "_font_cache", None)
        if cache is None:
            cache = self._font_cache = {}
        f = cache.get(key)
        if f is None:
            f = tkfont.Font(family="Microsoft YaHei UI",
                            size=max(6, int(size * (self.font_size / 16.0)
                                            * self.scale)),
                            weight="bold" if bold else "normal")
            cache[key] = f
        return f

    # ---------- 渲染 ----------
    def render(self, data):
        """
        把一份 feed 渲染到界面上。

        ⚠️ 硬性要求：**任何分支都必须有可见文字**。
        之前只要中途抛异常（比如 damageDist 结构不对），_tick 的 try/except
        会把异常吞掉，界面就停在上一帧甚至全空 —— 用户看到的就是"面板空白"。
        所以这里：
          1. 只有 win 为 None / 无法解析成数字才算"没有结果"，
             **0 是合法结果**（0% 胜率也必须照常显示）；
          2. 掉血分布单独 try，坏了也不影响胜率行；
          3. 整个函数兜底，异常时显示一行可诊断的提示，而不是留白。
        """
        try:
            data = data if isinstance(data, dict) else {}
            phase = data.get("phase")
            if phase != self._last_phase:
                self._last_phase = phase
                self._wait_since = None

            win = _to_num(data.get("win"))
            # 用户 2026-10-18：**运营阶段一律不显示胜率**（哪怕数据里还带着上一场的 win）
            if phase == "operate":
                win = None
            if win is None:
                # 没有可用的胜利概率：按状态给文案，永远不留白。
                #   运营（operate）        → 运营文案
                #   战斗（battle）未出结果 → 「读取中……」
                #   其它一切（other/缺失/损坏） → 默认「快来一场紧张刺激的影之幻境喵」
                txt = None
                size = 11
                status = data.get("status")
                if status:
                    txt = str(status)
                elif phase == "operate":
                    txt = "运营ing……正在等待战斗阶段喵"
                elif phase == "battle":
                    now = time.time()
                    if self._wait_since is None:
                        self._wait_since = now
                    waited = now - self._wait_since
                    if waited >= 15:
                        # 仍属于"战斗中未出结果"，保持"读取中"，只补一句失败提示
                        txt = "读取中……（超过 15 秒，可能读取失败）"
                        size = 10
                    else:
                        txt = "读取中……正在读双方阵容"
                else:
                    txt = "快来一场紧张刺激的影之幻境喵"
                self.line.config(text=txt, fg="#e3c98c", font=self._font(size, True))
                self.detail.config(text="", fg=DETAIL_FG)
                self.note.config(text="", fg=PANEL_BG)
                self._rendered_once = True
                self._last_render = ("none", phase, win, data.get("loss"), data.get("draw"))
                dbg(f"[render] phase={phase!r} win=None → {txt}")
                return

            # 有结果：0 也要正常显示。缺字段用 "?"，绝不因为缺项整块不画。
            self._wait_since = None
            loss = _fmt_pct(data.get("loss"))
            draw = _fmt_pct(data.get("draw"))
            self.line.config(
                text=f"胜 {_fmt_pct(win)}%   ·   败 {loss}%   ·   平 {draw}%",
                fg="#d9bd82" if win >= 50 else "#b08a6a",
                font=self._font(16, True))

            # 用户核心需求：**受到不同伤害的可能性**。
            # 分布单独兜底：结构异常/为空时退回 detail，再不行给可见占位。
            try:
                dist = build_dist_text(data)
            except Exception:
                dbg("[render] build_dist_text 异常:\n" + traceback.format_exc())
                dist = str(data.get("detail") or "")
            if not dist:
                dist = "（本场没有掉血概率数据）"
            self.detail.config(text=dist, fg=DETAIL_FG, font=self._font(10, False))
            # 用户要求：不显示"面板已读全"这类备注，保持面板干净
            self.note.config(text="", fg=PANEL_BG)
            self._rendered_once = True
            self._last_render = ("result", phase, win, data.get("loss"), data.get("draw"))
            dbg("[render] 胜%s%% 败%s%% 平%s%% | %s" % (
                _fmt_pct(win), loss, draw, dist.replace("\n", " / ")))
        except Exception:
            # 兜底：宁可显示"渲染失败"也不能留白
            dbg("[render] 渲染异常:\n" + traceback.format_exc())
            try:
                self.line.config(text="数据已收到，但渲染失败 · 详见日志",
                                 fg="#c07b6b", font=self._font(11, True))
                self.detail.config(text="")
                self.note.config(text="")
            except Exception:
                pass
            self._rendered_once = True

    def _poll_hot(self):
        """每 500ms 读一次设置/位置文件，按内容比较后热更新（不依赖 mtime 粒度）"""
        try:
            s = load_settings()
            if s:
                key = (s.get("alpha"), s.get("text_alpha"), s.get("scale"),
                       s.get("font_size"), s.get("titleColor"), s.get("locked"),
                       bool(s.get("liveMode", False)))
                if key != self._settings_key:
                    self._settings_key = key
                    self.apply_settings(s)
                    dbg("[hot] 悬浮层设置已热更新")
        except Exception:
            pass
        try:
            # 位置文件只提供 x/y；scale/locked 以 overlay-settings.json 为准
            p, _sc, _lk = load_pos()
            if self._drag is None and p != tuple(self.pos):
                self.pos = p
                self.root.geometry(f"+{p[0]}+{p[1]}")
        except Exception:
            pass

    def _tick(self):
        if self.demo:
            try:
                self.render(demo_payload())
            except Exception:
                dbg("[tick] demo 渲染失败:\n" + traceback.format_exc())
        else:
            try:
                if os.path.isfile(STATE):
                    mt = os.path.getmtime(STATE)
                    if mt != self._last_mtime or not self._rendered_once:
                        data = load_state(STATE)      # 内含重试（见 load_state）
                        # ⚠️ 只有**读成功**才记住 mtime：读失败要能下一轮再试，
                        #    否则文件没变就永远不再读，界面会卡在旧/默认文案。
                        self._last_mtime = mt
                        self._last_data = data
                        self.render(data)
                        self._last_render_at = time.time()
                else:
                    # feed 文件不存在 → 默认文案（"快来一场…"），不能空着
                    self._last_mtime = 0
                    self._last_data = {"phase": "other", "win": None}
                    self.render(self._last_data)
                    self._last_render_at = time.time()
            except Exception:
                # JSON 损坏 / 重试后仍读不到 → **保留上一份好数据**继续显示，
                # 只有从来没读到过任何数据时才退回默认文案。
                dbg("[tick] 读取 feed 失败:\n" + traceback.format_exc())
                if self._last_data is None:
                    self._last_data = {"phase": "other", "win": None}
                try:
                    self.render(self._last_data)
                    self._last_render_at = time.time()
                except Exception:
                    pass
        # 每 2 秒用**同一份数据**重绘一次：
        #   "读取中" 靠时间推进，必须定期重绘，15 秒后才会变成
        #   "本场未算出结果（读取失败）"；否则 feed 不再更新时会永远卡在"读取中"。
        #   重绘同一内容是幂等的，不会闪烁。
        now = time.time()
        if now - self._last_hot > 0.5:
            self._last_hot = now
            self._poll_hot()
        if not self.demo and self._last_data is not None and now - self._last_render_at >= 2.0:
            self._last_render_at = now
            self.render(self._last_data)
        # 定期重新确认置顶 / 点击穿透：
        # 有些游戏（或控制台/面板）会把窗口挤到后面，之前只在启动时设一次，
        # 表现就是"窗口还在、但被游戏盖住看不见"。
        if now - self._last_style_at > 3.0:
            self._last_style_at = now
            self._apply_style()
        # 定期确保样式正确（有些程序会抢置顶）
        try:
            import parent_guard
            if parent_guard.orphaned():
                self.root.destroy()
                return
        except Exception:
            pass
        self.root.after(400, self._tick)

    def run(self):
        # 用户要求：**不要控制条小窗口**（设置/锁定入口统一到控制台）。
        # 悬浮层只保留文字；拖动在解锁状态下按住文字区域即可。
        self.root.mainloop()


class SettingsWindow:
    """
    设置面板（由浮层右上角齿轮打开）。

    可调项直接对应代码里的参数，改完**立即生效**并写入 overlay-settings.json，
    下次启动自动恢复。监视器侧参数（抓帧间隔/过渡等待）也写进同一文件，
    由 battle_monitor 每轮读取，所以改完不用重启。

    为什么放在这里而不是独立工具：
      用户在游戏里看到一个数不对时，第一反应是"能不能调" ——
      设置必须就在浮层上，不能让他去命令行。
    """

    def __init__(self, overlay):
        self.ov = overlay
        self.win = tk.Toplevel(overlay.root)
        self.win.title("影之幻境 · 悬浮层设置")
        self.win.configure(bg=SET_BG)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.97)
        except Exception:
            pass
        self.win.resizable(False, False)
        self.vars = {}

        s = load_settings()
        f = ("Microsoft YaHei UI", 10)
        fb = ("Microsoft YaHei UI", 11, "bold")

        tk.Label(self.win, text="悬浮层设置", bg=SET_BG, fg="#ffb6c1",
                 font=fb).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        def add_row(r, label, key, from_, to, step=1, fmt="{:.0f}"):
            tk.Label(self.win, text=label, bg=SET_BG, fg=SET_FG,
                     font=f).grid(row=r, column=0, sticky="w", padx=(14, 8), pady=3)
            v = tk.DoubleVar(value=_num(s.get(key), overlay_scale_default(key)))
            self.vars[key] = v
            frame = tk.Frame(self.win, bg=SET_BG)
            frame.grid(row=r, column=1, sticky="e", padx=(0, 14))
            val = tk.Label(frame, text=fmt.format(v.get()), bg=SET_BG, fg="#b6f0c4",
                           font=f, width=6)

            def on_move(_=None):
                val.config(text=fmt.format(v.get()))
            # ⚠️ tk.Scale 的 bg 就是**滑块本体**的颜色；troughcolor 是那条细滑槽。
            # 两者原来都是 #1b2130/#2a3346（与底板几乎同色）→ 看不出可拖。
            scale = tk.Scale(frame, from_=from_, to=to, orient="horizontal", variable=v,
                             resolution=step, showvalue=False, length=190,
                             bg=SET_SLIDER, fg=SET_DARKTEXT,
                             troughcolor=SET_TROUGH,
                             activebackground=SET_SLIDER_HI,
                             highlightthickness=0, command=on_move)
            scale.pack(side="left")
            val.pack(side="left")
            return v

        add_row(1, "整体不透明度 (0.3~1.0)", "alpha", 0.3, 1.0, 0.02, "{:.2f}")
        add_row(2, "字号缩放 (0.6~2.2)", "scale", 0.6, 2.2, 0.05, "{:.2f}")
        add_row(3, "字号 (9~24)", "font_size", 9, 24, 1, "{:.0f}")

        tk.Label(self.win, text="标题颜色", bg=SET_BG, fg=SET_FG,
                 font=f).grid(row=4, column=0, sticky="w", padx=(14, 8), pady=3)
        colors = [("淡粉", "#ffb6c1"), ("亮粉", "#ff9ec4"), ("淡蓝", "#8fd3ff"),
                  ("白", "#ffffff"), ("淡绿", "#9fe8b0"), ("淡黄", "#ffcf6b")]
        self.color_var = tk.StringVar(value=s.get("titleColor", TITLE_FG))
        box = tk.Frame(self.win, bg=SET_BG)
        box.grid(row=4, column=1, sticky="e", padx=(0, 14))
        for i, (name, hexv) in enumerate(colors):
            b = tk.Label(box, text=name, bg=hexv, fg="#10151f", font=("Microsoft YaHei UI", 9),
                         padx=6, pady=2, cursor="hand2")
            b.grid(row=0, column=i, padx=2)
            b.bind("<Button-1>", lambda _e, h=hexv: (self.color_var.set(h), None)[1])
        # 也允许直接输入色号
        ent = tk.Entry(box, textvariable=self.color_var, width=9, font=("Consolas", 10),
                       bg=SET_BG2, fg=SET_FG, insertbackground=SET_FG,
                       highlightthickness=1, highlightbackground=SET_TROUGH,
                       highlightcolor=SET_SLIDER)
        ent.grid(row=0, column=len(colors), padx=(6, 0))

        # 「胜率模式」：动态 = 全程持续重算（**任何情况都出数**）；
        #              静态 = 开打时算一次并锁定，之后不再变。
        tk.Label(self.win, text="胜率模式", bg=SET_BG, fg=SET_FG,
                 font=f).grid(row=5, column=0, sticky="w", padx=(14, 8), pady=3)
        self.mode_var = tk.StringVar(value=str(s.get("mode", "dynamic")))
        mbox = tk.Frame(self.win, bg=SET_BG)
        mbox.grid(row=5, column=1, sticky="e", padx=(0, 14))
        for label, val in (("动态（一直更新）", "dynamic"), ("静态（开打锁定）", "static")):
            tk.Radiobutton(mbox, text=label, value=val, variable=self.mode_var,
                           bg=SET_BG, fg=SET_FG, selectcolor=SET_SELECT, font=f,
                           activebackground=SET_BG2, activeforeground="#ffffff",
                           highlightthickness=0).pack(side="left", padx=4)

        # 判定器相关参数（抓帧间隔/过渡等待/模拟次数）不再提供界面入口，
        # 由判定器使用内置默认值。

        self.lock_var = tk.BooleanVar(value=self.ov.locked)
        tk.Checkbutton(self.win, text="锁定（点击穿透，不可拖动）", variable=self.lock_var,
                       bg=SET_BG, fg=SET_FG, selectcolor=SET_SELECT, font=f,
                       activebackground=SET_BG2, activeforeground="#ffffff",
                       highlightthickness=0,
                       command=self._toggle_lock).grid(row=8, column=0, columnspan=2,
                                                       sticky="w", padx=14, pady=(8, 2))

        btns = tk.Frame(self.win, bg=SET_BG)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", padx=14, pady=(6, 12))
        tk.Button(btns, text="应用并保存", command=self.apply,
                  bg=SET_SLIDER, fg=SET_DARKTEXT,
                  activebackground=SET_SLIDER_HI, activeforeground="#000000",
                  font=fb, padx=12, pady=4, relief="flat",
                  cursor="hand2").pack(side="left", padx=4)
        tk.Button(btns, text="关闭", command=self.win.destroy,
                  bg=SET_BTN, fg=SET_FG,
                  activebackground=SET_BTN_HI, activeforeground="#ffffff",
                  font=f, padx=12, pady=4, relief="flat",
                  cursor="hand2").pack(side="left")

        tk.Label(self.win, text="改完点「应用并保存」；悬浮层与监视器都会立即使用新数值",
                 bg=SET_BG, fg=SET_MUTED, font=("Microsoft YaHei UI", 9)
                 ).grid(row=11, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))

    def _toggle_lock(self):
        self.ov.locked = bool(self.lock_var.get())
        self.ov._apply_style()
        self.ov._refresh_look()

    def apply(self):
        s = load_settings()
        s["alpha"] = round(float(self.vars["alpha"].get()), 2)
        s["scale"] = round(float(self.vars["scale"].get()), 2)
        s["font_size"] = int(round(float(self.vars["font_size"].get())))
        s["titleColor"] = self.color_var.get().strip() or TITLE_FG
        s["mode"] = self.mode_var.get()
        save_settings(s)
        self.ov.apply_settings(s)
        print(f"设置已保存：{s}")


def overlay_scale_default(key):
    return {"alpha": ALPHA, "scale": 1.0, "font_size": FONT_SIZE,
            "interval": 0.6, "settle": 0.25}.get(key, 1.0)


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_settings(s):
    """原子写：先写临时文件再 replace，避免热读方读到半截 JSON"""
    os.makedirs(LIVE, exist_ok=True)
    try:
        tmp = SETTINGS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS)
    except Exception:
        pass


def _to_num(v):
    """把 feed 里的值安全转成 float；None / 空串 / 坏值 → None（不抛异常）"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return f


def _fmt_pct(v):
    """百分比：整数显示整数，否则一位小数；缺值显示 '?'（不是 0）"""
    f = _to_num(v)
    if f is None:
        return "?"
    if abs(f - round(f)) < 0.05:
        return str(int(round(f)))
    return f"{f:.1f}"


def _fmt_dmg(v):
    f = _to_num(v)
    if f is None:
        return "?"
    if abs(f - round(f)) < 0.05:
        return str(int(round(f)))
    return f"{f:.1f}"


def _dist_rows(rows):
    """
    把分布行统一成 [(damage, pPercent), ...]，按伤害升序。

    兼容三种写法（引擎 / 旧 feed / 手写测试数据）：
        {"damage": 6, "pPercent": 25.0}
        {"value": 6, "probabilityPercent": 25.0}
        [6, 25.0]
    """
    out = []
    for r in rows or []:
        dmg = pct = None
        if isinstance(r, dict):
            dmg = r.get("damage", r.get("value"))
            pct = r.get("pPercent", r.get("probabilityPercent", r.get("pct")))
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            dmg, pct = r[0], r[1]
        d = _to_num(dmg)
        if d is None:
            continue
        p = _to_num(pct)
        out.append((d, 0.0 if p is None else p))
    out.sort(key=lambda t: t[0])
    return out


def build_dist_text(data, limit=12):
    """
    把 damageDist 渲染成文本供悬浮层显示（居中）。

    用户 2026-10-29 要求：**只显示区间和最大伤害**，不再逐档列概率：
        我方受伤：3~12 点（最大 12）
        造成伤害：0~9 点（最大 9）

    我方掉血优先（这是用户最关心的"我会掉多少"），其次造成伤害（对方掉血）。
    健壮性：damageDist 为空 / 结构不对 / 数值是字符串，都不能抛异常，
    否则整个悬浮层会停在上一帧（表现为"面板空白"）。
    """
    data = data if isinstance(data, dict) else {}
    d = data.get("damageDist") or {}
    if not isinstance(d, dict):
        d = {}
    lines = []
    for key, tag in (("toPlayer", "我方受伤"), ("toOpponent", "造成伤害")):
        rows = _dist_rows(d.get(key))
        if not rows:
            continue
        lo, hi = rows[0][0], rows[-1][0]
        if lo == hi:
            seg = f"{_fmt_dmg(lo)} 点"
        else:
            seg = (f"{_fmt_dmg(lo)}~{_fmt_dmg(hi)} 点"
                   f"（最大 {_fmt_dmg(hi)}）")
        lines.append(f"{tag}：{seg}")
    if lines:
        return "\n".join(lines)
    # 没有分布数据 → 退回 detail（空场规则等文案），再不行由调用方给占位
    detail = data.get("detail")
    return str(detail) if detail else ""

def demo_payload():
    t = time.time() % 12
    if t < 5:
        return {"status": "运营ing，等待下一场战斗喵"}
    w = 40 + int(40 * abs((t - 5) / 7 - 0.5) * 2)
    return {"win": w, "loss": 100 - w - 3, "draw": 3,
            "detail": "对方掉血 期望 8.4 点（12 点 34%）",
            "note": "3 项低置信度 · 部分数值待确认"}



def _clamp_pos(x, y, w=280, h=96):
    """把窗口坐标夹进可见区域内。

    之前保存的位置可能是负数（例如 y=-1），窗口会跑到屏幕上边缘外面，
    表现就是「胜率悬浮层不见了」。加载时必须夹一次。
    """
    try:
        import ctypes as _c
        u = _c.windll.user32
        u.SetProcessDPIAware()
        sw = u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(1)
    except Exception:
        sw, sh = 1920, 1080
    try:
        x = int(x); y = int(y)
    except Exception:
        x, y = 80, 80
    x = max(0, min(x, max(0, sw - 40)))
    y = max(0, min(y, max(0, sh - 40)))
    return x, y


def main():

    # 先开 DPI 感知：不开的话窗口位图会被系统拉伸，字发虚（用户要求高清）
    try:
        ui_dpi.enable()
    except Exception:
        pass

    # 单实例守卫：同名窗口已存在就别再开一个
    try:
        import single_instance as _SI
        if _SI.already_running("影之幻境 · 胜率浮层"):
            print("已有同名面板在运行，退出")
            import sys as _s
            _s.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass
    pos = scale = None
    if "--pos" in sys.argv:
        a, b = sys.argv[sys.argv.index("--pos") + 1].split(",")
        pos = (int(a), int(b))
    if "--scale" in sys.argv:
        scale = float(sys.argv[sys.argv.index("--scale") + 1])
    demo = "--demo" in sys.argv
    unlocked = "--unlocked" in sys.argv
    force_lock = "--locked" in sys.argv
    p, s, locked = load_pos()
    print(f"悬浮层启动：位置 {pos or p}，缩放 {scale or s}，"
          f"{'演示模式' if demo else '跟随 ' + STATE}")
    print(f"调试：PANEL_DEBUG={'on' if DEBUG else 'off'}  "
          f"PANEL_OPAQUE={'on' if OPAQUE else 'off'}")
    print("锁定状态：" + ("已锁定（点击穿透）" if (locked and not unlocked) else "可拖动"))
    print("操作：双击浮层 或 Ctrl+Alt+O 切换锁定；解锁后按住左键拖动")
    ov = Overlay(pos=pos, scale=scale, demo=demo, unlocked=unlocked)
    if force_lock:
        ov.locked = True
        ov._apply_style()
    ov.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
