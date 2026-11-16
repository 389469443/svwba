#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗陪玩 · 透明宠物窗口（2026-10-27 v5）

渲染：隐藏的 pygame OpenGL 上下文渲染 Live2D → glReadPixels 读像素 → PIL（带 alpha）。
显示：Tk 无边框窗口 + -transparentcolor 色键（模型外完全镂空）。
交互：左键拖动、右键设置（锁定/尺寸/关闭）。
"""
import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import time
import tkinter as tk

# ⚠️ keyboard 库会装全局低级键盘钩子，部分机器上会导致大多数按键失效
# （用户 2026-11-07 反馈）。这里禁用全局钩子，只用窗口内 Tk 事件绑定。
_keyboard = None

from PIL import Image, ImageTk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "_l2d", "c_0120.model3.json")
CONFIG = os.path.join(HERE, "pet_config.json")

SIZES = (200, 260, 340, 480)
DEFAULT_SIZE = 340
# 宠物动画帧率：低 = 省 CPU/GPU（默认 12fps，用户 2026-10-29 反馈打字延迟）
FPS_CHOICES = (8, 12, 24)
DEFAULT_FPS = 24
KEY = "#010203"          # 透明色键
BUBBLE_W = 260
BUBBLE_BG = "#1d2433"
TEXT_FG = "#e8edf7"

# 点模型时的本地回应（不耗 API）
POKE_LINES = [
    "诶？", "干嘛戳我……", "哼，才不理你", "在的在的", "主人~",
    "别戳啦", "（摇尾巴）", "唔……", "这个好吃吗", "再看我一眼嘛",
    # 梗：影之诗名台词、炉石「打得不错」
    "影之诗真是太有趣了", "影之诗真是太塔诺西啦", "打得不错（敷衍）",
]

# 傲娇性格被戳：嘴硬但不嘲讽（嘲讽只在毒舌性格；用户 2026-10-30）
POKE_LINES_TSUNDERE = [
    "干嘛戳我……",
    "哼，才没有很开心",
    "别戳啦",
    "（摇尾巴）……才没有",
    "唔……",
]

# 毒舌（慎用）：火力全开嘲讽，连"会不会打牌"都嘲（用户 2026-10-30）
POKE_LINES_TOXIC = [
    "三流的决斗者用着四流的牌组",
    "你这牌打得像第一次摸键盘",
    "就这？会不会打牌啊",
    "连这都打不出来，你不如把卡组给我",
    "输给你的人都不玩影之诗了",
    "打得不错（敷衍）",
    "这操作看得我尾巴都僵了",
    "牌是这么打的？笑死",
]


def _bubble_image(w, h):
    """聊天框样式的圆角气泡（左侧小尾巴指向模型）。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([10, 0, w - 1, h - 1], radius=14, fill=BUBBLE_BG)
    cy = max(20, h - 24)          # 小尾巴在左下角
    d.polygon([(0, cy - 8), (12, cy), (0, cy + 8)], fill=BUBBLE_BG)
    return im


def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"size": DEFAULT_SIZE, "locked": False}


def save_cfg(cfg):
    try:
        with open(CONFIG, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


class Renderer:
    """隐藏 OpenGL 上下文里渲染模型，产出 PIL RGBA 帧。"""

    def __init__(self, w, h, model_path=None, fps=DEFAULT_FPS):
        self.w = w
        self.h = h
        # 2x 超采样（GPU 渲染 2 倍 → CPU BILINEAR 缩小显示，边缘更锐利）
        self.render_w = max(w, int(w * 2))
        self.render_h = max(h, int(h * 2))
        self.fps = max(1, int(fps))
        self.model_path = model_path or MODEL
        self.latest = None
        self.lock = threading.Lock()
        self._stop = False
        self.frames = 0
        self.pending = None
        self.groups = []
        self.model = None

    def start(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop = True

    def _loop(self):
        import pygame
        from OpenGL.GL import (glClearColor, glClear, GL_COLOR_BUFFER_BIT,
                               glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE)
        import live2d

        try:
            pygame.init()
            pygame.display.set_mode(
                (self.render_w, self.render_h),
                pygame.OPENGL | pygame.HIDDEN | pygame.DOUBLEBUF)
            live2d.init()
            live2d.glInit()
            model = live2d.Model()
            model.LoadModelJson(self.model_path)
            model.Resize(self.render_w, self.render_h)
            # 注册模型目录下的 motions/*.motion3.json（组名=文件名）
            mdir = os.path.join(os.path.dirname(self.model_path), "motions")
            if os.path.isdir(mdir):
                for fn in sorted(os.listdir(mdir)):
                    if fn.endswith(".motion3.json"):
                        group = fn[:-len(".motion3.json")]
                        try:
                            model.LoadExtraMotion(
                                group, os.path.join(mdir, fn))
                        except Exception:
                            pass
            self.model = model
            # 加载全部原生表情（_l2d/*.exp3.json，约 44 个）
            try:
                import glob as _glob
                _edir = os.path.dirname(self.model_path)
                for _p in _glob.glob(os.path.join(_edir, "*.exp3.json")):
                    _name = os.path.basename(_p)[:-len(".exp3.json")]
                    try:
                        model.LoadExtraExpression(_name, _p)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                self.groups = list(model.GetMotionGroups().keys())
            except Exception:
                self.groups = []
        except Exception as e:
            print("render init err:", e, flush=True)
            return

        w, h = self.render_w, self.render_h
        while not self._stop:
            try:
                pending = None
                with self.lock:
                    if self.pending:
                        pending = self.pending
                        self.pending = None
                if pending:
                    try:
                        model.StartMotion(pending, 0, 3)
                    except Exception:
                        try:
                            model.StartMotion(pending + "_0", 0, 3)
                        except Exception:
                            pass
                expr = None
                with self.lock:
                    expr = getattr(self, "pending_expr", None)
                    self.pending_expr = None
                if expr:
                    try:
                        model.SetExpression(expr)
                    except Exception:
                        pass
                try:
                    model.UpdateExpression()
                except Exception:
                    pass
                glClearColor(1/255.0, 2/255.0, 3/255.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)
                model.Update()
                model.Draw()
                buf = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
                im = Image.frombytes("RGBA", (w, h), buf).transpose(
                    Image.FLIP_TOP_BOTTOM)
                if (w, h) != (self.w, self.h):
                    im = im.resize((self.w, self.h), Image.BILINEAR)
                with self.lock:
                    self.latest = im
                self.frames += 1
            except Exception:
                pass
            # 按 fps 限速（12fps 时约 0.083s）
            time.sleep(max(0.02, 1.0 / max(1, self.fps)))
        # 线程退出：释放 pygame/GL 上下文，否则下次重建会出现双上下文卡死
        try:
            import pygame
            pygame.display.quit()
            pygame.quit()
        except Exception:
            pass


class PetView:
    def __init__(self):
        # 单实例：即使 run_companion 被重复拉起、或有人直接构造 PetView，
        # 也只会有一个宠物窗口（用户 2026-10-29：出现过"两条鱼"）。
        try:
            import singleton
            if not singleton.acquire("SvwbPetView"):
                raise RuntimeError("宠物窗口已在运行")
        except RuntimeError:
            raise
        except Exception:
            pass

        self.cfg = load_cfg()
        self.size = int(self.cfg.get("size") or DEFAULT_SIZE)
        if self.size not in SIZES:
            self.size = DEFAULT_SIZE
        self.fps = int(self.cfg.get("fps") or DEFAULT_FPS)
        if self.fps not in FPS_CHOICES:
            self.fps = DEFAULT_FPS
        self.locked = bool(self.cfg.get("locked"))
        self.model_path = self.cfg.get("model") or MODEL

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        try:
            self.root.attributes("-transparentcolor", KEY)
        except Exception:
            pass

        self.canvas = tk.Canvas(self.root, bg=KEY, bd=0, highlightthickness=0)
        self.canvas.pack()

        self._img_item = None
        self._photo = None
        self._drag = None
        self._hide_after = None
        self._menu = tk.Menu(self.root, tearoff=0)

        self.obs_canvas = None
        import stream_settings
        stream_settings.watch(self.root, self._stream_mode)
        self._build(self.size)
        self.root.bind("<Button-1>", self._press)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Button-3>", self._menu_popup)
        self.root.bind("<Key>", self._on_key)
        self.root.focus_force()
        self._tick()
        self._topmost_loop()
        self._register_hotkeys()
        self._watch_cmd()
        self._auto_expression()
        # 监听 pet_config.json：AI 设置里改「模型大小」保存后立即生效（用户 2026-10-29 要求）
        self._cfg_mtime = 0
        self._watch_config()

    def _auto_expression(self):
        """空闲时自动换一个原生表情，避免长时间同一张脸。

        第一次调用只启动计时器；之后每 30~45 秒检查一次：
        气泡正在说话（AI 刚回复或用户在戳）时跳过，避免抢戏。
        """
        try:
            if getattr(self, "_auto_expr_started", False):
                now = time.time()
                try:
                    bubble_on = (self.canvas.itemcget(self._bubble_text, "state")
                                 == "normal")
                except Exception:
                    bubble_on = False
                if (not bubble_on
                        and now - getattr(self, "_play_at", 0.0) > 12.0):
                    names = self._expr_names()
                    if names:
                        import random
                        with self.renderer.lock:
                            self.renderer.pending_expr = random.choice(names)
            self._auto_expr_started = True
            import random
            self.root.after(30000 + random.randint(0, 15000),
                            self._auto_expression)
        except Exception:
            pass

    def _force_topmost(self):
        """Tk 的 -topmost 有时会被全屏游戏盖住，这里用 Win32 强制置顶。"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                hwnd, ctypes.wintypes.HWND(-1), 0, 0, 0, 0, 0x0003)
        except Exception:
            pass

    def _topmost_loop(self):
        try:
            self._force_topmost()
        except Exception:
            pass
        try:
            self.root.after(2000, self._topmost_loop)
        except Exception:
            pass

    def _build(self, size):
        self.size = size
        self.w = size
        # 模型 canvas 是 1:1（4068x4068）；窗口也用正方形，否则左右（尾巴）会被裁掉
        self.h = size
        self.total_w = self.w + BUBBLE_W + 8
        self.canvas.config(width=self.total_w, height=self.h)
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            # 位置钳制在屏幕内：否则窗口可能一半在屏幕外，看起来像"打不开"
            w = min(self.total_w, sw)
            h = min(self.h, sh)
            x = max(0, min(sw - w - 30, sw - w))
            y = max(0, min(sh - h - 90, sh - h))
            self.root.geometry("+%d+%d" % (x, y))
        except Exception:
            pass
        # 重建前清掉旧 canvas item，避免旧图/气泡残留在新尺寸里
        try:
            self.canvas.delete("all")
        except Exception:
            pass
        self._img_item = self.canvas.create_image(0, 0, anchor="nw")
        self._bub_photo = ImageTk.PhotoImage(
            _bubble_image(BUBBLE_W, self.h - 32))
        self._bubble_bg = self.canvas.create_image(
            self.w + 6, 16, anchor="nw", image=self._bub_photo,
            state="hidden")
        self._bubble_text = self.canvas.create_text(
            self.w + 18, 30, anchor="nw", text="", fill=TEXT_FG,
            font=("Microsoft YaHei UI", 11), width=BUBBLE_W - 28,
            justify="left", state="hidden")
        # 表情包（被戳时偶尔甩一张，本地图不耗 API）
        self._emote_photo = None
        self._emote_item = self.canvas.create_image(
            self.w + 8, 16, anchor="nw", state="hidden")
        self.renderer = Renderer(self.w, self.h, self.model_path,
                                 fps=self.fps)
        self.renderer.start()

    def _watch_cmd(self):
        """轮询 logs/_pet_cmd.json：AI/外部请求播放一个动作（跨进程联动）。"""
        try:
            p = os.path.join(HERE, "logs", "_pet_cmd.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    d = json.load(fh)
                at = float(d.get("at") or 0)
                if at > getattr(self, "_cmd_at", 0):
                    self._cmd_at = at
                    line = (d.get("say") or "").strip()
                    if line:
                        self.show(line, 8000)
                    name = (d.get("action") or "").strip()
                    if name:
                        self._play_expression(name)
        except Exception:
            pass
        try:
            self.root.after(500, self._watch_cmd)
        except Exception:
            pass

    def _watch_config(self):
        """每 2 秒看一次 pet_config.json：大小改了立即应用（保存即生效）。"""
        try:
            mtime = os.path.getmtime(CONFIG)
        except Exception:
            mtime = 0
        if mtime != getattr(self, "_cfg_mtime", None):
            self._cfg_mtime = mtime
            try:
                cfg = load_cfg()
                new_size = int(cfg.get("size") or DEFAULT_SIZE)
                if new_size in SIZES and new_size != self.size:
                    self._set_size(new_size)
                new_fps = int(cfg.get("fps") or DEFAULT_FPS)
                if new_fps in FPS_CHOICES and new_fps != self.fps:
                    self.fps = new_fps
                    try:
                        self.renderer.fps = new_fps
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            self.root.after(2000, self._watch_config)
        except Exception:
            pass

    def _tick(self):
        try:
            with self.renderer.lock:
                im = self.renderer.latest
            if im is not None:
                self._photo = ImageTk.PhotoImage(im)
                self.canvas.itemconfig(self._img_item, image=self._photo)
            if self.obs_canvas is not None:
                self._sync_obs_canvas()
        except Exception:
            pass
        try:
            self.root.after(max(40, int(1000 / max(1, self.fps))), self._tick)
        except Exception:
            pass

    # ---------------- 气泡 ----------------
    def show(self, text, hide_after=12000):
        text = (text or "").strip()
        if not text:
            return

        def _u():
            try:
                txt = text[:220]
                # 只保留一个气泡：显示文字气泡时收起表情包，避免叠在一起
                try:
                    self.canvas.itemconfig(self._emote_item, state="hidden")
                except Exception:
                    pass
                self.canvas.itemconfig(self._bubble_text, text=txt,
                                       state="normal")
                self.canvas.update_idletasks()
                bb = self.canvas.bbox(self._bubble_text)
                if bb and (bb[3] - bb[1]) > 4:
                    th = bb[3] - bb[1]
                else:
                    # 兜底：按字数估算（每行 ~13 个中文字，行高 20）
                    lines = max(1, (len(txt) + 12) // 13)
                    th = lines * 20 + 6
                bw = BUBBLE_W
                bh = max(40, th + 28)
                bx = self.w + 8
                by = max(6, (self.h - bh) // 2)
                self._bub_photo = ImageTk.PhotoImage(
                    _bubble_image(bw, bh))
                self.canvas.itemconfig(self._bubble_bg, image=self._bub_photo,
                                       state="normal")
                self.canvas.coords(self._bubble_bg, bx, by)
                self.canvas.coords(self._bubble_text, bx + 24, by + 14)
                if self._hide_after:
                    try:
                        self.root.after_cancel(self._hide_after)
                    except Exception:
                        pass
                if hide_after:
                    self._hide_after = self.root.after(hide_after,
                                                       self.hide_bubble)
            except Exception:
                pass

        try:
            self.root.after(0, _u)
        except Exception:
            pass

    def hide_bubble(self):
        try:
            self.canvas.itemconfig(self._bubble_bg, state="hidden")
            self.canvas.itemconfig(self._bubble_text, state="hidden", text="")
        except Exception:
            pass
        self._hide_after = None

    # ---------------- 拖动 / 点击 ----------------
    def _press(self, ev):
        self._press_at = (ev.x_root, ev.y_root)
        if self.locked:
            return
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        if self.locked or not self._drag:
            return
        self.root.geometry("+%d+%d" % (ev.x_root - self._drag[0],
                                       ev.y_root - self._drag[1]))

    def _release(self, ev):
        pa = getattr(self, "_press_at", None)
        self._drag = None
        self._press_at = None
        if pa and abs(ev.x_root - pa[0]) < 4 and abs(ev.y_root - pa[1]) < 4:
            self._on_poke()

    def _on_poke(self):
        """点模型：随机播一个动作 + 本地气泡回应。"""
        try:
            import random
            groups = getattr(self.renderer, "groups", []) or []
            if groups:
                with self.renderer.lock:
                    self.renderer.pending = random.choice(groups)
        except Exception:
            pass
        try:
            import random
            try:
                import companion
                pers = companion.load_config().get("personality") or "gentle"
            except Exception:
                pers = "gentle"
            if pers == "toxic":
                lines = POKE_LINES_TOXIC
            elif pers == "tsundere":
                lines = POKE_LINES_TSUNDERE
            else:
                lines = POKE_LINES
            self.show(random.choice(lines), 2500)
        except Exception:
            pass

    def _show_emote(self):
        """显示一张表情包（assets/emotes/*.png），3 秒后收起。"""
        try:
            import glob
            import random
            from PIL import Image, ImageTk
            paths = glob.glob(os.path.join(HERE, "assets", "emotes", "*.png"))
            if not paths:
                return False
            im = Image.open(random.choice(paths)).convert("RGBA")
            if im.width > BUBBLE_W:
                im = im.resize((BUBBLE_W,
                                int(im.height * BUBBLE_W / im.width)))
            self._emote_photo = ImageTk.PhotoImage(im)
            self.canvas.itemconfig(self._bubble_bg, state="hidden")
            self.canvas.itemconfig(self._bubble_text, state="hidden")
            self.canvas.itemconfig(self._emote_item,
                                   image=self._emote_photo, state="normal")
            by = max(6, (self.h - im.height) // 2)
            self.canvas.coords(self._emote_item, self.w + 8, by)
            if self._hide_after:
                try:
                    self.root.after_cancel(self._hide_after)
                except Exception:
                    pass
            self._hide_after = self.root.after(3000, self._hide_emote)
            return True
        except Exception:
            return False

    def _hide_emote(self):
        try:
            self.canvas.itemconfig(self._emote_item, state="hidden")
        except Exception:
            pass

    # ---------------- 右键菜单 ----------------
    def _stream_mode(self, enabled):
        if enabled:
            self._open_obs_window()
        elif self.obs_canvas is not None:
            self._close_obs_window()

    def _open_obs_window(self):
        if self.obs_canvas is not None:
            self.obs_canvas.master.deiconify()
            self.obs_canvas.master.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("影之诗 · 宠物和气泡")
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close_obs_window)
        canvas = tk.Canvas(win, bg=KEY, bd=0, highlightthickness=0)
        canvas.pack()
        self.obs_canvas = canvas
        self.obs_items = (
            canvas.create_image(0, 0, anchor="nw"),
            canvas.create_image(0, 0, anchor="nw"),
            canvas.create_text(0, 0, anchor="nw", fill=TEXT_FG,
                               font=("Microsoft YaHei UI", 11), width=BUBBLE_W - 28,
                               justify="left"),
            canvas.create_image(0, 0, anchor="nw"),
        )
        self._sync_obs_canvas()

    def _sync_obs_canvas(self):
        canvas = self.obs_canvas
        canvas.configure(width=self.total_w, height=self.h)
        for src, dest, option in zip(
                (self._img_item, self._bubble_bg, self._bubble_text, self._emote_item),
                self.obs_items, ("image", "image", "text", "image")):
            canvas.coords(dest, *self.canvas.coords(src))
            canvas.itemconfigure(dest, state=self.canvas.itemcget(src, "state"),
                                 **{option: self.canvas.itemcget(src, option)})

    def _close_obs_window(self):
        win = self.obs_canvas.master
        self.obs_canvas = None
        win.destroy()

    def _menu_popup(self, ev):
        m = self._menu
        m.delete(0, "end")
        m.add_command(
            label=("解锁位置" if self.locked else "锁定位置"),
            command=self._toggle_lock)
        m.add_command(label="显示 OBS 采集窗口", command=self._open_obs_window)
        m.add_command(label="打开控制台", command=self._open_console)
        m.add_separator()
        m.add_command(label="关闭", command=self.close)
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        except Exception:
            pass

    def _toggle_lock(self):
        self.locked = not self.locked
        self.cfg["locked"] = self.locked
        save_cfg(self.cfg)

    def _expr_names(self):
        """_l2d/*.exp3.json 的全部表情名。"""
        try:
            import glob
            return sorted(os.path.basename(p)[:-len(".exp3.json")]
                          for p in glob.glob(os.path.join(HERE, "_l2d", "*.exp3.json")))
        except Exception:
            return []

    def _play_expression(self, name):
        """统一播放入口：原生表情或动作，共用 2.5 秒冷却，避免重叠。"""
        try:
            if time.time() - getattr(self, "_play_at", 0) < 2.5:
                return False
        except Exception:
            pass
        self._play_at = time.time()
        if name in self._expr_names():
            try:
                with self.renderer.lock:
                    self.renderer.pending_expr = name
            except Exception:
                return False
            return True
        self._trigger(name)
        return True

    def _trigger(self, name):
        self._play_expression(name)

    def _register_hotkeys(self):
        """全局热键（窗口没焦点也能触发）：Alt+键 / Ctrl+键。"""
        if _keyboard is None:
            return
        keys = self.cfg.get("keys") or {}
        for k, name in keys.items():
            for mod in ("alt", "ctrl"):
                try:
                    _keyboard.add_hotkey(
                        "%s+%s" % (mod, k),
                        lambda n=name: self._play_expression(n))
                except Exception:
                    pass
        # 原生表情快捷键（AI 设置里「模型表情快捷键…」写 expression_keys）
        expr_keys = self.cfg.get("expression_keys") or {}
        if not expr_keys:
            _chars = list("1234567890abcdefghijklmnopqrstuvwxyz")
            _chars += ["f%d" % i for i in range(1, 9)]
            _names = self._expr_names()
            expr_keys = dict(zip(_chars[:len(_names)], _names))
        for k, name in expr_keys.items():
            try:
                _keyboard.add_hotkey(
                    "alt+%s" % k,
                    lambda n=name: self._play_expression(n))
            except Exception:
                pass

    def _on_key(self, ev):
        """模型动作：Alt+键 或 Ctrl+键 触发（不用单键，避免和游戏冲突）。

        pet_config.json 的 keys：按键 -> 动作组名（如 {"q": "喷水"}）。
        """
        # Alt = 0x20000, Ctrl = 0x4（Windows Tk）
        if not ((ev.state & 0x20000) or (ev.state & 0x4)):
            return
        keys = self.cfg.get("keys") or {}
        name = keys.get(str(ev.keysym).lower())
        if not name:
            return
        self._play_expression(name)

    def _open_console(self):
        """打开控制台：已在跑就把已有窗口提到前台，否则启动新的。"""
        try:
            import parent_guard
            if parent_guard.running():
                import ctypes
                u = ctypes.windll.user32
                for title in ("影之诗助手", "影之幻境 · 控制台"):
                    hwnd = u.FindWindowW(None, title)
                    if hwnd:
                        u.ShowWindow(hwnd, 9)      # SW_RESTORE
                        u.SetForegroundWindow(hwnd)
                        return
        except Exception:
            pass
        try:
            import subprocess
            subprocess.Popen([sys.executable, os.path.join(HERE, "app.py")],
                             cwd=HERE)
        except Exception:
            pass

    def _import_model(self):
        """导入其它 Live2D 模型（选 .model3.json），保存后立刻换掉。"""
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=self.root,
                title="选择 Live2D 模型（.model3.json）",
                filetypes=[("Live2D 模型", "*.model3.json"),
                           ("所有文件", "*.*")])
        except Exception:
            return
        if not path:
            return
        try:
            self.cfg["model"] = path
            save_cfg(self.cfg)
            self.model_path = path
            try:
                self.renderer.stop()
            except Exception:
                pass
            self.renderer = Renderer(self.w, self.h, path)
            self.renderer.start()
            self.show("模型已替换～", 4000)
        except Exception as e:
            print("导入模型失败：%s" % e)
            # pythonw 下 print 看不到，写日志 + 气泡提示
            try:
                os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
                with open(os.path.join(HERE, "logs", "pet-err.txt"), "a",
                          encoding="utf-8") as fh:
                    fh.write("%s 导入模型失败：%s\n"
                             % (time.strftime("%Y-%m-%d %H:%M:%S"), e))
            except Exception:
                pass
            try:
                self.show("导入失败～换一个模型试试", 4000)
            except Exception:
                pass

    def _set_size(self, n):
        if n == self.size:
            return
        # 先停旧渲染线程并等它退出：两个 OpenGL 上下文同时跑会把进程卡死
        old = self.renderer
        try:
            old.stop()
            t = getattr(old, "_t", None)
            if t is not None:
                t.join(timeout=3.0)
        except Exception:
            pass
        # 给旧 GL 上下文一点释放时间，再建新的
        try:
            time.sleep(0.3)
        except Exception:
            pass
        self.cfg["size"] = n
        save_cfg(self.cfg)
        self._build(n)   # _build 内部会创建并 start 新 renderer（这里不要再 start）

    def close(self):
        try:
            self.renderer.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    import singleton
    if not singleton.acquire("SvwbPet"):
        print("宠物窗口已经在运行")
        raise SystemExit(0)
    # 只能从控制台启动：控制台没开就拒绝
    try:
        import parent_guard
        # 用 running()：控制台正常退出会删 _app.pid，orphaned() 判断不出来
        if not parent_guard.running():
            print("请先从控制台启动 AI 陪玩")
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        pass
    PetView().run()
