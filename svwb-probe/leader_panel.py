#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主战者效果面板 V1.0（独立小面板，只负责显示）

用途
----
战斗中出现「给予主战者（Leader）的光环效果」时，在屏幕右下角弹出一个小窗，
列出**我方**主战者身上的效果（效果 + 合计数值 + 来源牌）。

交互（用户 2026-10-18 要求）
---------------------------
1. **只显示我方**：`leaderEffects` 里只渲染 `side == "player"` 的条目。
2. **折叠态**：每种效果一行（`精灵·随从进场 +2/+0`），下一行暗色小字写来源
   （`← 森林骑士道·辛西亚 / 虫风花·魅禄`）；同一张牌触发多次写 `×N`。
   最多显示 3 种，其余点开详情看。
3. **点击效果行 → 二层详情窗**：按「回合 → 同类型效果 → 来源」三层，
   点效果行展开/收起来源；**再点主面板效果行关闭**（× / Esc / 关闭按钮也可以）。
   详情窗可拖动、内容超高可滚动，并且始终夹在屏幕内（1080p/150% 也不出屏）。
4. **位置锁定**：默认固定在右下角，不能拖动；但**单击仍然有效**
   （锁定只是不移动窗口，不是点击穿透）。
5. **显隐**：
   - 没进游戏（overlay 不存在 / phase=other 等）、没有我方光环 → 隐藏；
   - 单场战斗结束（phase 回到 operate）→ 保留内容，不清空；
   - 整局结束（gameOver / phase=gameover / ourHp<=0）或离开对局 → 清空并隐藏；
   - 拿到新光环 → 自动出现。
6. 字号（7~14）、不透明度（0.3~1.0）每轮热读 `logs/leader-settings.json`。

数据（判定器写，本面板只读）
----------------------------
`shots/live/overlay.json`：

    {"phase": "battle", "gameOver": false,
     "leaderEffects": [{"side": "player", "source": "森林骑士道·辛西亚",
                        "desc": "精灵·随从进场", "auraName": "精灵光环",
                        "round": 4, "atk": 1, "hp": 0,
                        "cond": {"tribe": "精灵", "maxStar": null,
                                 "name": null, "atk": 1, "hp": 0}}]}

· 同一效果可能来自多张牌（逐来源条目），面板自己按 desc 累加数值；
· 同一 (desc, source) 在同一次快照里出现多次 = 同一张牌触发多次 → ×N，
  重复轮询同一份快照不会虚增（合并是覆盖不是追加）。

设计约束
--------
1. **不改任何现有文件**：overlay.py / panel_ui.py / app.py 都不动，
   本文件单独用 pythonw 启动。
2. **按内容轮询**（默认 450ms）：先读字节再比较，内容没变就什么都不做。
3. **隐藏用 withdraw() 而不是销毁**：下一局能立刻重新显示，不必重启进程。
4. **不设 DPI 感知**：本机 175% 缩放下，Tk 进程不声明 DPI 感知时坐标是
   「逻辑像素」，正好和战绩面板 panel_ui.py（同样不声明）同一套。
5. 文件不存在 / JSON 损坏 / 字段类型不对 → 一律不崩：
   不存在 → 隐藏（已累积内容保留，文件恢复后还在）；
   损坏或类型不对 → 已累积为空时显示「数据读取失败」，否则保留现有列表。
"""
import argparse
import ctypes
import io
import json
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))

DEF_OVERLAY = os.path.normpath(os.path.join(
    HERE, "..", "dsh-shadowverse-huanjing", "shots", "live", "overlay.json"))
DEF_POS = os.path.join(HERE, "logs", "panel-pos.json")
DEF_SETTINGS = os.path.join(HERE, "logs", "panel-settings.json")
DEF_LEADER_SET = os.path.join(HERE, "logs", "leader-settings.json")

TITLE_TEXT = "影之幻境 · 悬浮窗"
FONT_FAMILY = "Microsoft YaHei UI"

# 主战者效果面板设置默认值（logs/leader-settings.json）
ALPHA_DEF = 0.90                 # 不透明度，和战绩面板（0.88）接近
ALPHA_MIN, ALPHA_MAX = 0.30, 1.0
FONT_DEF = 9                     # 基准字号（标题/效果正文）
FONT_MIN, FONT_MAX = 7, 14

# 配色与战绩面板 / 悬浮层保持一致（深底 + 金系）
BG = "#12161d"
BG2 = "#1a2029"                  # 折叠态效果行底色（比面板底稍亮）
HOVER = "#232c39"                # 鼠标悬停色
BORDER = "#3a3a44"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"                # 暗金：回合 / 页脚
FG = "#efe9dc"
DIM = "#9aa7a0"
RULE = "#2a3038"
GREEN = "#8fbf8a"
RED = "#c07b6b"

WIN_W, WIN_H = 320, 160          # 默认尺寸（逻辑像素）
_SCALE = 1.0                     # DPI 缩放（启动时按系统 DPI 设置）


def enable_dpi_awareness():
    """
    让 Tk 按**真实分辨率**渲染（用户要求：悬浮窗高清一点）。
    不开的话，Windows 会把整个窗口位图拉伸（175% 缩放下字发虚）。
    返回缩放系数：窗口/坐标都要乘它，保持和以前一样的观感大小。
    """
    global _SCALE
    import os as _os
    if _os.environ.get("SVWB_NO_DPI") == "1":
        _SCALE = 1.0
        return _SCALE
    try:
        import ctypes
        try:
            # 2 = per-monitor DPI aware（Win8.1+）
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            _SCALE = float(ctypes.windll.user32.GetDpiForSystem()) / 96.0
        except Exception:
            _SCALE = 1.0
    except Exception:
        _SCALE = 1.0
    if not (1.0 <= _SCALE <= 3.0):
        _SCALE = 1.0
    return _SCALE
BALL_SIZE = 56                   # 悬浮球尺寸（逻辑像素）
BALL_CHROMA = "#010203"          # 悬浮球的抠色键（球外的方角透明掉）
POLL_SEC = 0.25                  # 默认轮询间隔（用户要求"1 秒内看到变化"→ 250ms）
PANEL_H = 340                    # 战绩面板默认高度（panel_ui.py 的 WIN_H）
FOLLOW_GAP = 8                   # 跟随模式：与战绩面板底边的间距

COLLAPSED_MAX = 3                # 折叠态最多显示几条不同效果（完整列表在详情窗）
DETAIL_W = 500                   # 详情窗宽度（逻辑像素）
DETAIL_BODY_MAX = 520            # 详情窗列表最大高度，超出滚动
DETAIL_GAP = 10                  # 详情窗与主面板的间距

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

SIDE_PLAYER = ("player", "self", "own", "me", "mine", "我方")
SIDE_OPP = ("opponent", "enemy", "opp", "对方")
SIDE_UNKNOWN_TEXT = "未标注"

# 这些 phase 表示「不在对局中」：面板隐藏。
# 注意 operate / battle 都算在局内（单场战斗结束不清空，整局结束才清）。
OUT_OF_GAME_PHASES = ("other", "gameover", "menu", "lobby", "idle", "none")
LONG_SRC_MAX = 240              # 来源名过长时先截到这么长，避免 measure 被拖慢


# ── 小工具 ────────────────────────────────────────────────────
def _text(v, limit=240):
    """字段转干净字符串：非字符串/None 都不抛异常，压缩空白并截断"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if not isinstance(v, str):
        return ""
    s = " ".join(v.split())
    return s[:limit]


def _read_bytes(path):
    """读文件原始字节；不存在 / 无权限 → None（不抛异常）"""
    try:
        with io.open(path, "rb") as fh:
            return fh.read(1 << 20)       # 1MB 足够，防止异常大的文件拖死面板
    except OSError:
        return None


def _clamp_alpha(v):
    try:
        return max(ALPHA_MIN, min(ALPHA_MAX, float(v)))
    except (TypeError, ValueError):
        return ALPHA_DEF


def _clamp_font(v):
    try:
        return max(FONT_MIN, min(FONT_MAX, int(round(float(v)))))
    except (TypeError, ValueError):
        return FONT_DEF


def load_leader_settings(path=DEF_LEADER_SET):
    """读取 logs/leader-settings.json；坏文件/缺字段一律回默认值"""
    out = {"alpha": ALPHA_DEF, "font_size": FONT_DEF}
    raw = _read_bytes(path)
    if raw is None:
        return out
    try:
        d = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return out
    if not isinstance(d, dict):
        return out
    out["alpha"] = _clamp_alpha(d.get("alpha", ALPHA_DEF))
    out["font_size"] = _clamp_font(d.get("font_size", FONT_DEF))
    return out


def side_of(raw):
    """判定阵营：返回 player / opponent / unknown（大小写、中英文都认）"""
    s = _text(raw).lower()
    if s in SIDE_PLAYER:
        return "player"
    if s in SIDE_OPP:
        return "opponent"
    return "unknown"


def ellipsize(text, font, max_px):
    """按实测像素宽截断并加省略号，避免长效果名把整行挤爆"""
    text = text or ""
    if max_px <= 0:
        return ""
    try:
        if font.measure(text) <= max_px:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + "…") <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo] + "…") if lo else "…"
    except tk.TclError:
        return text


def compact_sources(srcs, font, max_px):
    """
    折叠态来源行：`← 牌A ×2 / 牌B / 牌C`。

    关键信息是**来源牌名和触发次数 ×N**，所以超宽时：
      · 只截断牌名本身；
      · 每张牌后面的 `×N` 一定保留；
      · 保持一行，不把面板撑高、也不让行溢出。
    srcs : {牌名: 次数}
    """
    items = sorted(srcs.items(), key=lambda kv: (-kv[1], kv[0]))
    if not items:
        return ""

    def build(widths=None):
        parts = []
        for i, (name, cnt) in enumerate(items):
            n = name if widths is None else ellipsize(name, font, widths[i])
            if cnt > 1:
                n += " ×%d" % cnt
            parts.append(n)
        return "← " + " / ".join(parts)

    full = build()
    try:
        if font.measure(full) <= max_px:
            return full
    except tk.TclError:
        return full

    # 先扣掉「← 」、分隔符、以及所有 ×N 的宽度，剩下的均分给牌名
    try:
        fixed = font.measure("← " + " / " * (len(items) - 1))
        fixed += sum(font.measure(" ×%d" % c) for _n, c in items if c > 1)
    except tk.TclError:
        return ellipsize(full, font, max_px)
    avail = max(40, max_px - fixed)
    widths = [max(12, int(avail / len(items)) - 4) for _ in items]
    out = build(widths)
    for _ in range(30):
        try:
            if font.measure(out) <= max_px or max(widths) <= 10:
                break
        except tk.TclError:
            break
        widths = [max(10, int(w * 0.85)) for w in widths]
        out = build(widths)
    return out


# ── 数据解析 ──────────────────────────────────────────────────
def _truthy(v):
    """JSON 里的真假值：true / "true" / 1 / "yes" 都算真"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on", "gameover")
    if isinstance(v, (int, float)):
        return v != 0
    return False


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def item_key(it):
    """
    累积去重用的身份：**效果描述 + 来源牌**。

    为什么要带来源：同一种效果可能由不同主战者/随从提供（例：
    「森林骑士道·辛西亚」和「虫风花·魅禄」都给「精灵·随从进场 +1/+0」），
    详情窗要按来源分别显示，所以不能只按 desc 合并。
    折叠态再按展示文案（text）去重，所以用户看到的仍然只有一行。
    """
    desc = (it.get("desc") or "").strip() or (it.get("text") or "").strip()
    source = (it.get("source") or "").strip()
    return ("desc", desc, source)


def parse_snapshot(raw):
    """
    bytes → 快照 dict

        kind        : missing（读不到）/ error（JSON 坏、类型不对）/ ok
        phase       : 小写阶段名
        items       : leaderEffects 里解析成功的**全部**条目（含 side）
        error       : 解析失败原因
        game_over   : 整局结束信号列表（非空 = 已结束）
        game_over_false : 数据里显式写了 gameOver:false
        new_game    : 数据里写了 newGame:true
        players / others : 我方 / 非我方条目数（日志用）
    """
    base = {"kind": "missing", "phase": "", "items": [], "error": "",
            "game_over": [], "game_over_false": False, "new_game": False,
            "players": 0, "others": 0, "special": [], "evolve": None,
            "updated_at": ""}
    if raw is None:
        return base
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        base.update(kind="error", error="不是 UTF-8 文本：%s" % e)
        return base
    try:
        doc = json.loads(text)
    except ValueError as e:
        base.update(kind="error", error="JSON 解析失败：%s" % e)
        return base
    if not isinstance(doc, dict):
        base.update(kind="error",
                    error="JSON 顶层不是对象（%s）" % type(doc).__name__)
        return base

    phase = _text(doc.get("phase")).lower()

    # ---- 整局结束信号（任何一条命中即算结束）----
    game_over = []
    if _truthy(doc.get("gameOver")):
        game_over.append("gameOver=true")
    if phase == "gameover":
        game_over.append("phase=gameover")
    hp = doc.get("ourHp")
    if isinstance(hp, str):
        try:
            hp = float(hp.strip())
        except ValueError:
            hp = None
    if _is_num(hp) and hp <= 0:
        game_over.append("ourHp=%s<=0" % hp)
    go_false = ("gameOver" in doc) and not _truthy(doc.get("gameOver"))

    base.update(kind="ok", phase=phase, game_over=game_over,
                game_over_false=go_false, new_game=_truthy(doc.get("newGame")))

    # ── 其他随从效果（徒姬 / 维尔伯特 这类）——只在"我方真的出现"时由判定器写 ──
    raw_spec = doc.get("specialEffects")
    if isinstance(raw_spec, list):
        for rec in raw_spec:
            if not isinstance(rec, dict):
                continue
            d = _text(rec.get("desc"))
            if not d:
                continue
            base["special"].append({
                "desc": d,
                "value": _text(rec.get("value")),
                "atk": int(rec.get("atk") or 0),
                "hp": int(rec.get("hp") or 0),
                "source": _text(rec.get("source")),
                "note": _text(rec.get("note")),
            })
    base["updated_at"] = str(doc.get("updatedAt") or "")
    ev = doc.get("evolveCount")
    if isinstance(ev, dict):
        ev = ev.get("total")
    if _is_num(ev):
        base["evolve"] = int(ev)

    # 字段不存在 = 没有新效果，这是正常情况，不是错误
    if "leaderEffects" not in doc:
        return base

    raw_eff = doc.get("leaderEffects")
    if not isinstance(raw_eff, list):
        base.update(kind="error",
                    error="leaderEffects 不是数组（实际 %s）"
                          % type(raw_eff).__name__)
        return base

    items = []
    players = others = 0
    for rec in raw_eff:
        if not isinstance(rec, dict):
            continue                      # 脏条目跳过，不影响其它效果
        name = _text(rec.get("name"))
        source = _text(rec.get("source"))
        card = _text(rec.get("cardId"))
        desc = _text(rec.get("desc"))     # 判定器新版：稳定的「效果描述」
        text = _text(rec.get("text"))     # 判定器新版：带份数/叠加数值的展示文案
        aura = _text(rec.get("auraName"))  # 光环名（僵尸光环/精灵光环…），仅兜底用
        if not name and not source and not card and not desc and not text and not aura:
            continue                      # 完全空白的记录没有展示价值
        side = side_of(rec.get("side"))
        if side == "player":
            players += 1
        else:
            others += 1
        # 回合：判定器用「我方幻境等级」代理，可能是数字、数字字符串或缺失
        rnd = rec.get("round")
        if isinstance(rnd, bool) or rnd is None:
            rnd = None
        elif isinstance(rnd, (int, float)):
            rnd = int(rnd)
        elif isinstance(rnd, str):
            s = rnd.strip()
            rnd = int(s) if s.isdigit() else None
        else:
            rnd = None
        if rnd is not None and rnd <= 0:
            rnd = None                    # 0 / 负数表示「还没读到」，不显示成第0回合
        display = text or desc or aura or name or source or "（未命名效果）"

        # 数值：折叠态和详情窗都要**自己按来源累加**，所以必须保留 atk/hp。
        # 之前漏了这两个字段，界面上就全变成 +0/+0（实测踩到）。
        def _num(v):
            if isinstance(v, bool) or v is None:
                return 0
            try:
                return int(float(v))
            except Exception:
                return 0

        items.append({
            "side": side,
            # 折叠态与详情窗都直接用判定器给的 text（含「×2 → +2/+0」这类叠加信息）
            "text": display,
            "name": display,              # 兼容旧字段名
            "desc": desc or aura or text or name,  # 去重/分组用的稳定身份
            "auraName": aura,
            "source": source,
            "cardId": card,
            "round": rnd,
            "atk": _num(rec.get("atk")),
            "hp": _num(rec.get("hp")),
            "count": max(1, _num(rec.get("count"))),
        })
    base.update(items=items, players=players, others=others)
    return base


def _read_snapshot(path, tries=3, delay=0.03):
    """
    读 + 解析 overlay.json；解析失败（读到写入瞬间的半截 JSON）就重试几次。

    判定器现在是原子替换写入（tmp + os.replace），理论上读不到半截；
    但 Windows 上"读者正开着目标文件"仍可能让某一次读失败，
    一次失败就报「数据读取失败」会让面板闪一下（用户以为程序坏了）。
    重试后仍坏 → 照原样返回 error 快照，由调用方决定怎么显示。
    """
    tries = max(1, int(tries))
    raw = None
    snap = parse_snapshot(None)
    for i in range(tries):
        raw = _read_bytes(path)
        snap = parse_snapshot(raw)
        if snap.get("kind") != "error":
            return raw, snap
        if i + 1 < tries:
            time.sleep(delay)
    return raw, snap


# ── 面板 ──────────────────────────────────────────────────────

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


def _hwnd_of(widget):
    """Tk 窗口 → 顶层 Win32 句柄（取 GetParent，失败退回 winfo_id）"""
    try:
        widget.update_idletasks()
    except Exception:
        pass
    try:
        user32 = ctypes.windll.user32
        h = user32.GetParent(widget.winfo_id())
        return h or widget.winfo_id()
    except Exception:
        try:
            return widget.winfo_id()
        except Exception:
            return 0



def _apply_zorder(root):
    """更新 Z 序：贴着游戏，但不进置顶层（别的窗口能盖住面板）"""
    try:
        import game_win as _GW
        _GW.sync_zorder(_GW.hwnd_of(root))
    except Exception:
        pass


class LeaderPanel:
    """主战者效果面板：轮询 + 显示/隐藏，自身不写任何数据文件"""

    def __init__(self, root, path, x=40, y=300, w=WIN_W, h=WIN_H,
                 follow=False, pos_path=DEF_POS, gap=FOLLOW_GAP,
                 interval=POLL_SEC, log_path=None, title_text=None,
                 settings_path=DEF_LEADER_SET, locked=False):
        self.root = root
        self.title_text = title_text or TITLE_TEXT
        self.path = os.path.abspath(path)
        self.pos_path = os.path.abspath(pos_path)
        self.follow = bool(follow)
        self.gap = int(gap)
        self.interval = max(0.15, min(2.0, float(interval)))
        self.log_path = os.path.abspath(log_path) if log_path else None
        self.phys = ""                # --phys 的原始字符串（main 里填）
        self.w = max(200, int(w))
        self.h = max(90, int(h))
        self.base_w, self.base_h = self.w, self.h
        # 位置锁定：**不能拖动，但仍然可以点击**（点效果行开详情窗）。
        # 默认锁定（用户要求：平时固定放右下角）。
        self.pos_locked = bool(locked)

        # 设置（不透明度 / 字号）由 logs/leader-settings.json 热读
        self.settings_path = os.path.abspath(settings_path)
        _st = load_leader_settings(self.settings_path)
        self._alpha = _clamp_alpha(_st["alpha"])
        self._font_size = _clamp_font(_st["font_size"])
        self._set_raw = _read_bytes(self.settings_path)
        # 详情窗宽度 / 换行宽度：必须在这里就按默认字号算好，
        # 否则字号没变过（默认 9）时 wrap 还是写死的 200/160，效果名会被
        # 无谓折行（实测「幻境等级3或以下的随从进场」在 500 宽的窗里折成两行）。
        try:
            _sw = self.root.winfo_screenwidth()
        except Exception:
            _sw = 1280
        self._detail_w = min(max(300, _sw - 40), DETAIL_W)
        self._effect_wrap = max(160, self._detail_w - 90)
        self._source_wrap = max(140, self._detail_w - 120)

        self.visible = None           # None=还没决定过
        self.acc = []                 # 本局累积的我方效果（整局结束才清）
        self.items = []               # 当前渲染用的累积列表（详情窗读它）
        self._acc_sig = None          # 累积列表内容签名（决定要不要重绘）
        self._game_over = False       # 上一轮是否已判定「整局结束」
        # 悬浮球：默认收成球，点一下才展开面板（用户 2026-10-18 要求）
        self.ball_mode = True
        self._special = []            # 其他随从效果（徒姬/维尔伯特…）
        self.evolve_count = None      # 本局进化次数（常显）
        self._ball_badge = 0
        self._last_raw = object()      # 哨兵：保证第一次一定处理
        self._last_log_sig = None      # 日志去重（同一状态只记一次）
        self._drag = None             # 主面板拖动：按下时的 (屏幕x, 屏幕y, 窗x, 窗y)
        self._moved = False           # 主面板这次按下是否已经算拖动（区分点击）
        self._pos_sig = None
        self._last_topmost = 0.0
        self._place_x, self._place_y = int(x), int(y)

        # 二层详情窗
        self._detail = None
        self._detail_canvas = None
        self._detail_inner = None
        self._detail_head_count = None
        self._detail_scroll = None
        self._detail_outer = None
        self._collapsed_groups = set()  # 详情窗里已展开来源的效果 key
        self._ddrag = None            # 详情窗拖动起点
        self._dmoved = False
        self._press = None            # 主面板按下位置（锁定态也要能判断"点击"）
        self._after_id = None          # 轮询定时器（关闭时取消，避免 Tcl after 报错）

        self._build()
        # 默认是“收起成球”的状态：把面板控件先收起来、只留球
        if getattr(self, "ball_mode", False):
            for w in (self.head, self.rule, self.body, self.foot):
                try:
                    w.pack_forget()
                except tk.TclError:
                    pass
            try:
                self.ball.pack(fill="both", expand=True)
            except tk.TclError:
                pass
            self._draw_ball()
        self.collapsed = True
        if getattr(self, "ball_mode", False):
            self.root.geometry("%dx%d" % (self.ball_px, self.ball_px))
        else:
            self.root.geometry("%dx%d" % (self.w, self.h))
        self.root.update_idletasks()
        self._style_window()
        self._apply_alpha()
        self._resize_for_fonts()
        if self.follow:
            self._apply_follow(force=True)
        else:
            self._place(self._place_x, self._place_y)
        # 用户 2026-10-18 21:30：一启动就显示（悬浮球），不再"先藏起来等读盘"
        self._set_visible(True)
        try:
            self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        except tk.TclError:
            pass
        self._log("启动 file=%s size=%dx%d start=(%d,%d) follow=%s"
                  % (self.path, self.w, self.h, x, y, self.follow))
        self._tick()

    def _cancel_tick(self):
        aid, self._after_id = self._after_id, None
        if aid is None:
            return
        try:
            self.root.after_cancel(aid)
        except Exception:
            pass

    def _on_root_destroy(self, event):
        if event.widget is self.root:
            self._cancel_tick()

    # ---------- 设置热读（不透明度 / 字号） ----------
    def _apply_alpha(self):
        try:
            self.root.attributes("-alpha", float(self._alpha))
        except Exception:
            pass
        top = getattr(self, "_detail", None)
        if top is not None:
            try:
                if top.winfo_exists():
                    top.attributes("-alpha", float(self._alpha))
            except Exception:
                pass

    def _font_sizes(self):
        fs = _clamp_font(self._font_size)
        return fs, max(6, fs - 1)

    def _resize_for_fonts(self):
        """
        按**实际渲染出来的内容**调窗口高度。

        折叠态每行现在是「效果 + 来源小字」两行，固定 160 高在 3 条效果时
        会裁掉第三条的来源行（实测），所以这里量 body 的 reqheight，
        再夹到屏幕内。默认仍是 320x160（只有一两条时不会变大）。
        """
        if getattr(self, "ball_mode", False):
            return                      # 球形态固定 56x56，不按内容调高度
        try:
            self.root.update_idletasks()
            need = self._chrome_h() + self.body.winfo_reqheight() + 8
            sh = self.root.winfo_screenheight()
            self.h = int(max(self.base_h, min(max(90, sh - 40), need)))
            self.root.geometry("%dx%d" % (self.w, self.h))
        except Exception:
            self.h = self.base_h

    def _apply_font_sizes(self):
        fs, small = self._font_sizes()
        for f in (self.f_title, self.f_name, self.f_s):
            try:
                f.configure(size=fs)
            except Exception:
                pass
        try:
            self.f_ball.configure(size=max(10, fs + 4))
            self.f_ball_small.configure(size=max(6, small - 1))
        except Exception:
            pass
        for f in (self.f_src, self.f_badge, self.f_round):
            try:
                f.configure(size=small)
            except Exception:
                pass
        extra = max(0, fs - FONT_DEF)
        self._detail_w = min(
            max(300, self.root.winfo_screenwidth() - 40),
            DETAIL_W + extra * 18)
        # 详情窗的换行宽度跟着窗宽走（之前写死 200/160，效果名被无谓折成
        # 两三行、来源名每行只剩几个字，详情窗看着很挤）。
        self._effect_wrap = max(160, self._detail_w - 90)
        self._source_wrap = max(140, self._detail_w - 120)
        if getattr(self, "ball_mode", False):
            self._draw_ball()
            return
        self._resize_for_fonts()
        if self.items:
            self._render_items(self.items)
        elif self._detail_is_open():
            self._refresh_detail()

    def _poll_leader_settings(self):
        """每轮读 leader-settings.json；只在不透明度/字号变化时动界面"""
        raw = _read_bytes(self.settings_path)
        if raw == self._set_raw:
            return
        self._set_raw = raw
        st = load_leader_settings(self.settings_path)
        alpha = _clamp_alpha(st["alpha"])
        fs = _clamp_font(st["font_size"])
        alpha_changed = abs(alpha - self._alpha) > 1e-4
        font_changed = fs != self._font_size
        self._alpha, self._font_size = alpha, fs
        if alpha_changed:
            self._apply_alpha()
            self._log("不透明度 → %.2f" % alpha)
        if font_changed:
            self._apply_font_sizes()
            self._log("字号 → %d" % fs)

    # ---------- 界面 ----------
    def _build(self):
        root = self.root
        root.title(self.title_text)
        root.overrideredirect(True)    # 无边框
        root.configure(bg=BG)
        try:
            root.attributes("-topmost", False)
        except tk.TclError:
            pass

        self.f_title = tkfont.Font(root=root, family=FONT_FAMILY,
                                   size=self._font_size, weight="bold")
        self.f_name = tkfont.Font(root=root, family=FONT_FAMILY,
                                  size=self._font_size)
        self.f_src = tkfont.Font(root=root, family=FONT_FAMILY,
                                 size=max(6, self._font_size - 1))
        self.f_badge = tkfont.Font(root=root, family=FONT_FAMILY,
                                   size=max(6, self._font_size - 1),
                                   weight="bold")
        # f_s 是历史字段名（旧代码里多处用过），和效果正文同字号
        self.f_s = tkfont.Font(root=root, family=FONT_FAMILY,
                               size=self._font_size)
        self.f_round = tkfont.Font(root=root, family=FONT_FAMILY,
                                   size=max(6, self._font_size - 1),
                                   weight="bold")
        # 悬浮球专用（比面板字号大一点，球心数字才好看）
        self.f_ball = tkfont.Font(root=root, family=FONT_FAMILY,
                                  size=max(10, self._font_size + 4),
                                  weight="bold")
        self.f_ball_small = tkfont.Font(root=root, family=FONT_FAMILY,
                                        size=max(6, self._font_size - 2))

        self.outer = tk.Frame(root, bg=BG, bd=0,
                              highlightbackground=BORDER, highlightthickness=1)
        self.outer.pack(fill="both", expand=True)

        # 悬浮球（收起态）：圆形按钮 + 效果条数角标。球外的方角用抠色透明掉。
        self.ball_px = max(28, int(round(BALL_SIZE * _SCALE)))
        self.ball = tk.Canvas(self.outer, width=self.ball_px, height=self.ball_px,
                              bg=BALL_CHROMA, highlightthickness=0, bd=0,
                              cursor="hand2")
        self.ball._lp_dragged = True        # 主面板的拖动绑定不要套到球上
        # 球自己处理"拖动移动 / 单击展开"
        self.ball.bind("<Button-1>", self._ball_press)
        self.ball.bind("<B1-Motion>", self._ball_motion)
        self.ball.bind("<ButtonRelease-1>", self._ball_release)
        self.ball.bind("<Enter>", lambda _e: self._ball_hover(True))
        self.ball.bind("<Leave>", lambda _e: self._ball_hover(False))

        head = tk.Frame(self.outer, bg=BG)
        head.pack(fill="x", padx=8, pady=(4, 0))
        self.title = tk.Label(head, text=self.title_text, bg=BG, fg=GOLD,
                              font=self.f_title, anchor="w", cursor="fleur",
                              bd=0, padx=0, pady=0)
        self.title.pack(side="left")
        self.close_btn = tk.Label(head, text="×", bg=BG, fg=GOLD2,
                                  font=self.f_src, cursor="hand2",
                                  bd=0, padx=2, pady=0)
        self.close_btn.pack(side="right")
        # 用户要求：悬浮窗不能关，× 只是"收回成悬浮球"
        self.close_btn.bind("<Button-1>", lambda _e: self._enter_ball())
        self.head = head

        self.rule = tk.Frame(self.outer, bg=RULE, height=1)
        self.rule.pack(fill="x", padx=6, pady=(3, 2))

        self.body = tk.Frame(self.outer, bg=BG)
        self.body.pack(fill="both", expand=True)

        self.foot = tk.Label(self.outer, text="", bg=BG, fg=GOLD2,
                             font=self.f_src, anchor="w", bd=0, padx=0, pady=0)
        self.foot.pack(fill="x", padx=8, pady=(0, 3))


        self._bind_drag(self.outer)
        root.bind("<Escape>", self._on_escape)

    def _on_escape(self, _e=None):
        if self._detail_is_open():
            self._close_detail()
        else:
            self._enter_ball()          # 悬浮窗不能关，Esc 只是收回球

    def _mark_clickable(self, widget, callback):
        """给控件挂一个「点击（不是拖动）时执行」的回调，并显示手型光标"""
        widget._lp_click = callback
        try:
            widget.configure(cursor="hand2")
        except tk.TclError:
            pass

    def _bind_drag(self, widget):
        """拖动绑到每个子控件上（× 按钮除外），否则点到文字就拖不动"""
        if widget is self.close_btn or getattr(widget, "_lp_dragged", False):
            return                      # 已绑过：重复绑会让一次拖动触发 N 次
        widget._lp_dragged = True
        widget.bind("<Button-1>", self._on_press, add="+")
        widget.bind("<B1-Motion>", self._on_drag, add="+")
        widget.bind("<ButtonRelease-1>", self._on_release, add="+")
        for child in widget.winfo_children():
            self._bind_drag(child)

    def _on_press(self, e):
        # 无论是否锁定，都记录按下位置：锁定态只是不移动窗口，
        # 单击仍然要能打开/关闭详情窗（之前这里直接 return，锁定后永远点不开）。
        self._press = (e.x_root, e.y_root)
        self._moved = False
        if self.pos_locked:
            self._drag = None
            return
        self._drag = (e.x_root, e.y_root, self._place_x, self._place_y)

    def _on_drag(self, e):
        if not self._press:
            return
        dx = e.x_root - self._press[0]
        dy = e.y_root - self._press[1]
        if self.pos_locked:
            # 锁定：不移动，但用户明显在拖时不要把松手当成点击
            if abs(dx) > 6 or abs(dy) > 6:
                self._moved = True
            return
        if not self._drag:
            return
        if not self._moved and abs(dx) <= 3 and abs(dy) <= 3:
            return                      # 手抖不算拖动，避免点击时窗口乱跑
        self._moved = True
        x0, y0, px, py = self._drag
        nx, ny = px + dx, py + dy
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # 夹进屏幕：必须先换算成**物理**尺寸（窗口尺寸是逻辑像素，175% 缩放下要乘 1.75），
        # 否则算出来的 y 合法但窗口下半截会伸到屏幕外（实测跑到 1495，高度 280 → 出界）
        try:
            _dpi = float(self.root.winfo_fpixels("1i")) / 96.0
        except Exception:
            _dpi = 1.0
        sw_l = int(sw / _dpi)
        sh_l = int(sh / _dpi)
        nx = max(0, min(max(0, sw_l - self.w - 8), nx))
        ny = max(0, min(max(0, sh_l - self.h - 8), ny))
        self._place(nx, ny)

    def _on_release(self, e):
        moved = self._moved
        if moved:
            # 用户主动拖了面板 → 收回时就用面板当前的位置（不再回到展开前）
            self._pos_before_expand = None
        self._drag = None
        self._moved = False
        self._press = None
        if moved:
            return                      # 拖动结束：不触发点击
        # 单击：从事件控件往上找最近的可点击区域（效果行 / 页脚）
        w = e.widget
        for _ in range(10):
            if w is None:
                return
            cb = getattr(w, "_lp_click", None)
            if cb:
                try:
                    cb()
                except Exception as ex:
                    self._log("点击处理失败（已忽略）：%s" % ex)
                return
            try:
                w = w.master
            except Exception:
                return

    # ---------- Win32 ----------
    def _hwnd(self):
        return _hwnd_of(self.root)

    def _style_window(self, topmost=None):
        """置顶 + 不在任务栏/Alt-Tab 里露脸（工具窗）；只在游戏前台时置顶"""
        if topmost is None:
            topmost = _above_game()
        try:
            user32 = ctypes.windll.user32
            hwnd = self._hwnd()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_TOPMOST
            if topmost:
                ex |= WS_EX_TOPMOST
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST if topmost else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            self._log("Win32 置顶设置失败（忽略）：%s" % e)

    def _style_detail(self, top, topmost=None):
        """详情窗和主面板同一套置顶策略 + 不占任务栏"""
        if topmost is None:
            topmost = _above_game()
        try:
            user32 = ctypes.windll.user32
            hwnd = _hwnd_of(top)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_TOPMOST
            if topmost:
                ex |= WS_EX_TOPMOST
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST if topmost else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            self._log("详情窗置顶设置失败（忽略）：%s" % e)

    def _place(self, x, y):
        """用 SetWindowPos 定位：Tk 的 geometry 把 -x 当成「距右边」，会飞掉"""
        self._place_x, self._place_y = int(x), int(y)
        try:
            ctypes.windll.user32.SetWindowPos(
                self._hwnd(), None, self._place_x, self._place_y, 0, 0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        except Exception:
            try:
                self.root.geometry("+%d+%d" % (max(0, x), max(0, y)))
            except tk.TclError:
                pass


    def _clamp_to_screen(self):
        """
        用 Win32 把自己的窗口夹进屏幕内（物理像素）。

        为什么不用 Tk 的坐标：Tk 是逻辑像素，175% 缩放下和物理像素差 1.75 倍，
        算出来的 y 看着合法，窗口下半截其实已经在屏幕外面了（实测 y=1495、高 280、屏高 1600）。

        注意必须用**整窗矩形**（right/bottom）判断：只夹左上角会让高窗口的底边出界。
        """
        if not self.visible:
            return False
        try:
            import ctypes, ctypes.wintypes as wt
            u = ctypes.windll.user32
            hwnd = _hwnd_of(self.root)
            if not hwnd:
                return False
            r = wt.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            w, h = r.right - r.left, r.bottom - r.top
            if w <= 0 or h <= 0:
                return False
            nx = max(0, min(max(0, sw - w - 8), r.left))
            ny = max(0, min(max(0, sh - h - 8), r.top))
            if (nx, ny) != (r.left, r.top):
                u.SetWindowPos(hwnd, 0, nx, ny, 0, 0, 0x0001 | 0x0004)
                self._place_x, self._place_y = nx, ny
                self._log("夹进屏幕 (%d,%d) -> (%d,%d) 窗口 %dx%d 屏 %dx%d"
                          % (r.left, r.top, nx, ny, w, h, sw, sh))
            return True
        except Exception as e:
            self._log("夹取失败: %s" % e)
            return False

    def _force_phys(self):
        """用 Win32 直接按**物理像素**摆位，绕开 Tk 的逻辑像素换算。"""
        spec = str(getattr(self, "phys", "") or "").strip()
        if not spec:
            return False
        try:
            x, y = [int(v) for v in spec.split(",")[:2]]
            import ctypes
            u = ctypes.windll.user32
            hwnd = _hwnd_of(self.root)
            u.SetWindowPos(hwnd, 0, x, y, 0, 0,
                           0x0001 | 0x0004)   # SWP_NOSIZE | SWP_NOZORDER
            self._place_x, self._place_y = x, y
            self._log("phys 定位 -> (%d,%d)" % (x, y))
            return True
        except Exception as e:
            self._log("phys 定位失败: %s" % e)
            return False

    def _main_topmost(self):
        """主面板当前实际的置顶状态（详情窗要与它保持一致）"""
        try:
            return bool(int(float(self.root.attributes("-topmost"))))
        except Exception:
            return _above_game()

    def _sync_detail_topmost(self, foreground=None):
        top = getattr(self, "_detail", None)
        if top is None:
            return
        try:
            if not top.winfo_exists():
                return
        except tk.TclError:
            return
        if foreground is None:
            foreground = _above_game()
        try:
            top.attributes("-topmost", foreground)
        except tk.TclError:
            pass
        self._style_detail(top, foreground)

    def _reassert_topmost(self):
        """
        每 1 秒同步一次置顶状态（主面板 + 详情窗）。

        策略（用户要求）：游戏在前台才置顶；切到浏览器等 → 取消置顶，
        让面板被其它窗口盖住，而不是一直压在所有窗口最上面。
        """
        now = time.time()
        if now - self._last_topmost < 1.0:
            return
        self._last_topmost = now
        foreground = _above_game()
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass
        if self.visible:
            # 贴到游戏正上方（普通窗口层），别的窗口能遮挡它
            _apply_zorder(self.root)
        self._sync_detail_topmost(foreground)

    # ---------- 显隐 ----------
    # ---------- 悬浮球 ----------
    @staticmethod
    def _mix(c1, c2, t):
        """两个 #rrggbb 之间插值（t=0 → c1）"""
        def _rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        a, b = _rgb(c1), _rgb(c2)
        return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t))
                                       for i in range(3))

    def _ball_hover(self, on):
        if getattr(self, "_ball_on", None) == on:
            return
        self._ball_on = on
        self._draw_ball()

    def _draw_ball(self):
        """
        圆形悬浮球：暗底径向渐变 + 金色圆环 + 内圈高光 + 球心数字。
        （用 Canvas 画而不是贴图：Canvas 坐标会跟着 DPI 缩放，贴图不会，175% 下会变小。）
        """
        try:
            c = self.ball
            c.delete("all")
            r = self.ball_px
            cx = cy = r / 2.0
            hover = bool(getattr(self, "_ball_on", False))
            pad = 2.0
            # ① 外发光（两圈很淡的光晕）
            for k, col in ((1.0, "#20283a"), (0.7, "#2a3448")):
                rad = (cx - pad) * k
                c.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                              outline=col, width=1)
            # ② 径向渐变底（同心圆近似）
            rings = 18
            for i in range(rings, 0, -1):
                t = i / float(rings)
                col = self._mix("#303c52", "#0b0f16", t)
                rad = (cx - pad - 1.0) * t
                c.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                              outline="", fill=col)
            # ③ 左上柔光（很淡，别糊成一坨）
            g = 1.0 if hover else 0.6
            hl = cx * 0.52
            c.create_oval(cx - hl, cy - hl * 1.10, cx + hl * 0.30, cy + hl * 0.05,
                          outline="", fill=self._mix("#303c52", "#4f6288", 0.07 * g))
            # ④ 金色圆环：整圈 + 上方亮弧
            c.create_oval(pad, pad, r - pad, r - pad,
                          outline="#e6c377" if hover else "#cfae66", width=2)
            c.create_arc(pad + 0.5, pad + 0.5, r - pad - 0.5, r - pad - 0.5,
                         start=35, extent=110, style="arc",
                         outline="#fff0c0" if hover else "#f4dfa4", width=1.6)
            # ⑤ 内圈暗线，让边缘更立体
            c.create_oval(pad + 2.6, pad + 2.6, r - pad - 2.6, r - pad - 2.6,
                          outline="#0a0d13", width=1)
            # ⑥ 球心：数字 + 单位
            n = int(getattr(self, "_ball_badge", 0) or 0)
            if n > 0:
                c.create_text(cx, cy - 4, text=str(n), fill="#f8e9bd",
                              font=self.f_ball)
                c.create_text(cx, cy + 11, text="条", fill="#93a0b2",
                              font=self.f_ball_small)
            else:
                c.create_oval(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5,
                              outline="#93a0b2", width=1)
        except tk.TclError:
            pass

    def _ball_press(self, e):
        self._bdrag = (e.x_root, e.y_root, self._place_x, self._place_y)
        self._bmoved = False

    def _ball_motion(self, e):
        if not getattr(self, "_bdrag", None):
            return
        dx = e.x_root - self._bdrag[0]
        dy = e.y_root - self._bdrag[1]
        if not self._bmoved and abs(dx) <= 3 and abs(dy) <= 3:
            return                      # 手抖不算拖动
        self._bmoved = True
        self._place(self._bdrag[2] + dx, self._bdrag[3] + dy)

    def _ball_release(self, e):
        moved = bool(getattr(self, "_bmoved", False))
        self._bdrag = None
        self._bmoved = False
        if moved:
            self._clamp_to_screen()
            self._save_pos()
            return
        self._expand()                  # 单击 → 展开面板

    def _save_pos(self):
        """把悬浮球/面板的位置记进 leader-settings.json（下次启动接着用）"""
        try:
            raw = _read_bytes(self.settings_path)
            data = json.loads(raw.decode("utf-8-sig")) if raw else {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data["x"] = int(self._place_x)
        data["y"] = int(self._place_y)
        try:
            d = os.path.dirname(self.settings_path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.settings_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.settings_path)
        except Exception:
            pass

    def _apply_ball_shape(self):
        """球形态：56x56 + 抠色（方角透明）"""
        try:
            self.outer.configure(highlightthickness=0, bg=BALL_CHROMA)
            self.root.attributes("-transparentcolor", BALL_CHROMA)
        except tk.TclError:
            pass
        try:
            self.root.geometry("%dx%d+%d+%d"
                               % (self.ball_px, self.ball_px,
                                  self._place_x, self._place_y))
        except tk.TclError:
            pass
        self._apply_alpha()

    def _enter_ball(self, _e=None):
        """收起成悬浮球（点一下球再展开）"""
        if self.ball_mode:
            return
        self.ball_mode = True
        self._close_detail(silent=True)
        for w in (self.head, self.rule, self.body, self.foot):
            try:
                w.pack_forget()
            except tk.TclError:
                pass
        try:
            self.ball.pack(fill="both", expand=True)
        except tk.TclError:
            pass
        self._draw_ball()
        self._apply_ball_shape()
        # 回到展开前的位置（面板刚才是为了完整显示才被夹进屏幕的）
        pos = getattr(self, "_pos_before_expand", None)
        if pos:
            self._place(pos[0], pos[1])
            self._pos_before_expand = None
        self._log("收起为悬浮球（%d 个效果）" % self._ball_badge)

    def _expand(self, _e=None):
        """点悬浮球 → 展开成面板"""
        if not self.ball_mode:
            return
        # 用户要求：展开面板时可以让面板"挪进屏幕"（哪怕有遮挡也要完整显示），
        # 但**收回成球时要回到展开前的位置**，球不能跑。
        self._pos_before_expand = (self._place_x, self._place_y)
        self.ball_mode = False
        try:
            self.ball.pack_forget()
            self.outer.configure(highlightthickness=1, bg=BG)
            self.root.attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        self.head.pack(fill="x", padx=8, pady=(4, 0))
        self.rule.pack(fill="x", padx=6, pady=(3, 2))
        self.body.pack(fill="both", expand=True)
        self.foot.pack(fill="x", padx=8, pady=(0, 3))
        try:
            self.root.geometry("%dx%d+%d+%d"
                               % (self.w, self.h, self._place_x, self._place_y))
        except tk.TclError:
            pass
        self._apply_alpha()
        self._render_items(self.acc)
        self._log("展开面板（%d 个效果）" % self._ball_badge)

    def _section(self, title):
        """小节标题（光环 / 其他随从效果 / 进化次数）"""
        try:
            tk.Label(self.body, text=title, bg=BG, fg=GOLD2,
                     font=self.f_src, anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        except tk.TclError:
            pass

    def _set_visible(self, visible):
        if visible == self.visible:
            return
        self.visible = visible
        try:
            if visible:
                self.root.deiconify()
                foreground = _above_game()
                self.root.attributes("-topmost", False)
                self._style_window(foreground)
                _apply_zorder(self.root)
                self._apply_alpha()
                if getattr(self, "ball_mode", False):
                    self._apply_ball_shape()
            else:
                self._close_detail(silent=True)   # 主面板藏起来 → 详情窗也收起
                self.root.withdraw()
        except tk.TclError as e:
            self._log("窗口显隐失败：%s" % e)

    # ---------- 跟随模式 ----------
    def _apply_follow(self, force=False):
        """把面板贴到 panel-pos.json 描述的战绩面板下方（y 加上面板高度）"""
        raw = _read_bytes(self.pos_path)
        if raw is None:
            if force:
                self._log("跟随模式：读不到 %s，改用 --x/--y" % self.pos_path)
            return
        if raw == self._pos_sig and not force:
            return
        self._pos_sig = raw
        try:
            d = json.loads(raw.decode("utf-8-sig"))
            px, py = int(d["x"]), int(d["y"])
        except Exception as e:
            self._log("跟随模式：panel-pos.json 不可用（%s），位置保持不变" % e)
            return
        # 战绩面板高度随它的 scale 变，读一次设置；读不到按 1.0 算
        scale = 1.0
        st_raw = _read_bytes(DEF_SETTINGS)
        if st_raw is not None:
            try:
                st = json.loads(st_raw.decode("utf-8-sig"))
                scale = max(0.6, min(2.0, float(st.get("scale", 1.0))))
            except Exception:
                pass
        off = int(round(PANEL_H * scale)) + self.gap
        nx, ny = px, py + off
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # 整个面板都要留在屏幕内：只留 60px 会让下半截出界（实测 y=854 高 160 屏 914）
        nx = max(0, min(max(0, sw - self.w - 8), nx))
        ny = max(0, min(max(0, sh - self.h - 8), ny))
        self._place(nx, ny)
        self._log("跟随：战绩面板 (%d,%d) scale=%.2f → 本面板 (%d,%d)"
                  % (px, py, scale, nx, ny))

    # ---------- 渲染 ----------
    def _chrome_h(self):
        """标题 + 分隔线 + 页脚占掉的高度（逻辑像素），用来算能放几行"""
        try:
            return (self.f_title.metrics("linespace") + 4
                    + 1 + 3 + 2          # 分隔线上下留白
                    + self.f_src.metrics("linespace") + 3 + 2)
        except tk.TclError:
            return 48

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        try:
            self.foot.config(text="")
        except tk.TclError:
            pass



    # ---------- 二层详情窗 ----------
    def _detail_is_open(self):
        top = getattr(self, "_detail", None)
        if top is None:
            return False
        try:
            return bool(top.winfo_exists())
        except tk.TclError:
            return False

    def _close_detail(self, silent=False):
        """关闭详情窗并清掉引用（再点效果行会重新打开）"""
        top = getattr(self, "_detail", None)
        self._detail = None
        self._detail_canvas = None
        self._detail_inner = None
        self._detail_head_count = None
        self._detail_scroll = None
        self._detail_outer = None
        self._ddrag = None
        self._dmoved = False
        if top is None:
            return
        try:
            top.destroy()
        except Exception:
            pass
        if not silent:
            self._log("关闭详情窗")

    def _toggle_detail(self, *_a, **_k):
        """用户 2026-10-18：来源/回合详情功能已删除（浪费算力）"""
        return

    def _toggle_detail_unused(self):
        """点效果行 / 页脚：关→开，开→关"""
        if self._detail_is_open():
            self._close_detail()
        else:
            self._open_detail()

    def _detail_count_text(self):
        """标题：X 种效果 / Y 个来源（来源次数按 ×N 累加）"""
        descs = set()
        srcs = 0
        for it in self.items:
            d = str(it.get("desc") or it.get("text") or "").strip()
            if d:
                descs.add(d)
            srcs += int(it.get("count") or 1)
        if not self.items:
            return "主战者效果 · 本局还没有"
        return "主战者效果 · %d 种 / %d 个来源" % (len(descs), srcs)

    def _open_detail(self):
        """
        弹出二层详情窗：每条显示「第N回合  效果  ← 来源」。

        细节：
          · 无边框 + 自绘标题栏（× 可关，整窗可拖）
          · 置顶策略与主面板一致（游戏前台才置顶，后台自动取消）
          · 打开时自动夹进屏幕；列表过高时内部滚动，窗口不会跑出屏幕
        """
        if self._detail_is_open():
            self._close_detail()
            return
        top = None
        try:
            top = tk.Toplevel(self.root)
            self._detail = top
            top.withdraw()                    # 先摆好尺寸/位置再显示，避免闪一下
            top.title(self.title_text + " · 详情")
            top.configure(bg=BG)
            top.overrideredirect(True)

            outer = tk.Frame(top, bg=BG, bd=0,
                             highlightbackground=BORDER, highlightthickness=1)
            outer.pack(fill="both", expand=True)
            self._detail_outer = outer

            head = tk.Frame(outer, bg=BG)
            head.pack(fill="x", padx=12, pady=(8, 0))
            self._detail_head_count = tk.Label(
                head, text=self._detail_count_text(), bg=BG, fg=GOLD,
                font=self.f_title, anchor="w", bd=0, padx=0, pady=0)
            self._detail_head_count.pack(side="left")
            close_top = tk.Label(head, text="×", bg=BG, fg=GOLD2,
                                 font=self.f_src, cursor="hand2",
                                 bd=0, padx=4, pady=0)
            close_top.pack(side="right")
            close_top._lp_nodrag = True
            close_top.bind("<Button-1>", lambda _e: self._close_detail())

            tk.Frame(outer, bg=RULE, height=1).pack(fill="x", padx=10,
                                                   pady=(6, 4))

            holder = tk.Frame(outer, bg=BG)
            holder.pack(fill="both", expand=True, padx=12, pady=(0, 0))
            canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
            canvas.pack(side="left", fill="both", expand=True)
            inner = tk.Frame(canvas, bg=BG)
            win = canvas.create_window((0, 0), window=inner, anchor="nw")
            self._detail_canvas = canvas
            self._detail_inner = inner
            self._detail_win = win
            canvas.bind("<Configure>",
                        lambda e: canvas.itemconfigure(win, width=e.width))
            inner.bind("<Configure>", lambda _e: self._sync_detail_scroll())
            self._populate_detail(inner)

            foot = tk.Frame(outer, bg=BG)
            foot.pack(fill="x", padx=12, pady=(6, 8))
            tk.Label(foot, text="暗金=回合 · 主色=效果 · 灰=来源 · Esc 关闭",
                     bg=BG, fg=GOLD2, font=self.f_src, anchor="w",
                     bd=0, padx=0, pady=0).pack(side="left")
            btn = tk.Button(foot, text="关闭", command=self._close_detail,
                            bg=BG2, fg=GOLD, activebackground=HOVER,
                            activeforeground=GOLD, relief="flat", bd=0,
                            padx=12, pady=3, font=self.f_src,
                            cursor="hand2", highlightthickness=0)
            btn.pack(side="right")
            btn._lp_nodrag = True

            self._bind_detail_drag(outer)
            top.bind("<Escape>", lambda _e: self._close_detail())
            top.bind("<MouseWheel>", self._detail_wheel)

            foreground = self._main_topmost()   # 与主面板当前状态一致
            try:
                top.attributes("-topmost", foreground)
            except tk.TclError:
                pass
            self._style_detail(top, foreground)
            self._show_detail()
            self._log("打开详情窗：%d 条" % len(self.items))
        except Exception as e:
            self._log("详情窗打开失败：%s" % e)
            self._close_detail(silent=True)

    def _show_detail(self):
        """首次显示详情窗：按内容算尺寸，放到主面板旁边（夹进屏幕）"""
        self._layout_detail(keep_pos=False)
        top = self._detail
        if top is None:
            return
        try:
            top.deiconify()
            top.lift()
            foreground = self._main_topmost()
            top.attributes("-topmost", foreground)
            self._style_detail(top, foreground)
            top.attributes("-alpha", float(self._alpha))
        except tk.TclError:
            pass

    def _detail_layout(self):
        """
        算详情窗尺寸并夹进屏幕。

        关键点（用户要求 1080p / 150% 缩放下也不能出屏）：
          · 窗口宽度不超过屏幕宽 - 2*gap；
          · 列表高度不超过「屏幕高 - 边框/标题/页脚 - 2*gap」，
            超出就在窗内滚动，而不是让窗口顶出去；
          · 位置先试主面板右侧，右侧不够翻左侧，最后强制夹回屏幕。
        """
        top, outer = self._detail, self._detail_outer
        canvas, inner = self._detail_canvas, self._detail_inner
        if top is None or outer is None or canvas is None or inner is None:
            return None
        try:
            top.update_idletasks()
            content_h = max(40, inner.winfo_reqheight())

            # 先量「非列表部分」的高度：把 canvas 压到 1px 再量外框
            canvas.configure(height=1)
            top.update_idletasks()
            chrome = max(60, outer.winfo_reqheight() - 1)

            sw = max(320, self.root.winfo_screenwidth())
            sh = max(240, self.root.winfo_screenheight())
            gap = DETAIL_GAP
            max_body = max(60, sh - 2 * gap - chrome)
            body_h = int(min(content_h, DETAIL_BODY_MAX, max_body))
            canvas.configure(height=body_h)
            self._sync_detail_scroll()
            top.update_idletasks()

            w = max(self._detail_w, outer.winfo_reqwidth())
            w = int(min(max(260, w), max(200, sw - 2 * gap)))
            h = max(80, outer.winfo_reqheight())
            h = int(min(h, max(80, sh - 2 * gap)))
            return w, h, sw, sh, gap
        except Exception as e:
            self._log("详情窗尺寸计算失败：%s" % e)
            return None

    def _layout_detail(self, keep_pos=False):
        """应用尺寸；首次打开时自动选位置，刷新时保留用户拖动过的位置。"""
        metrics = self._detail_layout()
        if not metrics:
            return
        w, h, sw, sh, gap = metrics
        top = self._detail
        if top is None:
            return
        if keep_pos:
            try:
                x, y = top.winfo_x(), top.winfo_y()
            except tk.TclError:
                x, y = gap, gap
        else:
            # 默认放主面板右侧；右边不够就翻到左侧；再不够就贴屏幕右侧
            x = self._place_x + self.w + gap
            if x + w > sw - gap:
                x = self._place_x - w - gap
            if x < gap:
                x = max(gap, sw - w - gap)
            y = self._place_y
            if y + h > sh - gap:
                y = sh - h - gap
        x = max(gap, min(max(gap, sw - w - gap), x))
        y = max(gap, min(max(gap, sh - h - gap), y))
        try:
            top.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except tk.TclError:
            pass

    def _refresh_detail(self):
        """主面板内容更新时，详情窗若开着就原地刷新（不关窗、不抢焦点）"""
        if not self._detail_is_open():
            return
        try:
            if self._detail_head_count is not None:
                self._detail_head_count.config(text=self._detail_count_text())
            self._populate_detail(self._detail_inner)
            self._detail.update_idletasks()
            self._layout_detail(keep_pos=True)
        except Exception as e:
            self._log("详情窗刷新失败（已忽略）：%s" % e)

    def _grouped(self):
        """
        把逐来源条目整理成「回合 → 效果 → 来源」三层。

        返回 [(round, [(desc, atk, hp, [(source, count), ...]), ...]), ...]
        · round 升序（早的回合在前，回合未记录的放最后）
        · 同回合内按效果首次出现的顺序
        · source count 来自数据里的重复条目（同一张牌触发多次 = ×N）
        """
        buckets = {}
        order = []
        for it in self.items:
            rnd = it.get("round")
            try:
                rnd = int(rnd) if rnd not in (None, "") else 0
            except Exception:
                rnd = 0
            desc = str(it.get("desc") or it.get("text") or "").strip() or "（未命名效果）"
            src = str(it.get("source") or "").strip() or "来源未标注"
            src = src[:LONG_SRC_MAX]
            atk = int(it.get("atk") or 0)
            hp = int(it.get("hp") or 0)
            cnt = max(1, int(it.get("count") or 1))
            rk = (rnd, desc)
            if rk not in buckets:
                buckets[rk] = {"atk": 0, "hp": 0, "srcs": {}, "seq": len(order)}
                order.append(rk)
            b = buckets[rk]
            b["atk"] += atk
            b["hp"] += hp
            b["srcs"][src] = b["srcs"].get(src, 0) + cnt

        by_round = {}
        for rk in order:
            b = buckets[rk]
            by_round.setdefault(rk[0], []).append(
                (rk[1], b["atk"], b["hp"],
                 sorted(b["srcs"].items(), key=lambda kv: (-kv[1], kv[0]))))
        return sorted(by_round.items(),
                      key=lambda kv: (kv[0] <= 0, kv[0] if kv[0] > 0 else 0))

    def _populate_detail(self, inner):
        """
        分组渲染详情：回合标题 → 效果行（可点开）→ 来源明细。

        点击效果行展开/收起来源；整窗可拖，关闭按钮不拖。
        排序：先回合，后效果类型；同一效果的多张来源牌合并成一条。
        """
        if inner is None:
            return
        for child in inner.winfo_children():
            child.destroy()
        if not self.items:
            tk.Label(inner, text="本局还没有主战者光环", bg=BG, fg=DIM,
                     font=self.f_s, anchor="w").pack(anchor="w", pady=6)
            return

        groups = self._grouped()
        if not hasattr(self, "_collapsed_groups") or self._collapsed_groups is None:
            self._collapsed_groups = set()

        for gi, (rnd, effects) in enumerate(groups):
            # ① 回合
            rtag = ("第%d回合" % rnd) if rnd else "回合未记录"
            rh = tk.Frame(inner, bg=BG)
            rh.pack(fill="x", pady=(8 if gi else 2, 2))
            tk.Label(rh, text=rtag, bg=BG, fg=GOLD2, font=self.f_round,
                     anchor="w", bd=0, padx=0, pady=0).pack(side="left")

            # ② 同类型效果（同一回合内按 desc 合并）
            for desc, atk, hp, srcs in effects:
                key = (rnd, desc)
                expanded = key in self._collapsed_groups
                head = tk.Frame(inner, bg=BG, cursor="hand2")
                head.pack(fill="x", padx=(8, 0), pady=1)
                head._lp_group = key
                arrow = "▾" if expanded else "▸"
                lab = tk.Label(
                    head,
                    text="%s  %s +%d/+%d" % (arrow, desc, atk, hp),
                    bg=BG, fg=FG, font=self.f_name, anchor="w",
                    justify="left", bd=0, padx=3, pady=2,
                    wraplength=self._effect_wrap, cursor="hand2")
                lab.pack(side="left", fill="x", expand=True)
                for wdg in (head, lab):
                    self._bind_detail_hover(wdg, head, lab)

                # ③ 来源（点开才显示；哪张牌、几次）
                if expanded:
                    for sname, cnt in srcs:
                        srow = tk.Frame(inner, bg=BG)
                        srow.pack(fill="x", padx=(30, 0), pady=0)
                        txt = "· %s" % sname
                        if cnt > 1:
                            txt += "  ×%d" % cnt
                        tk.Label(srow, text=txt, bg=BG, fg=DIM,
                                 font=self.f_src, anchor="w",
                                 justify="left", bd=0, padx=2, pady=1,
                                 wraplength=self._source_wrap).pack(
                            side="left", fill="x", expand=True)

        self._bind_detail_drag(inner)

    # 详情窗效果行悬停高亮
    def _bind_detail_hover(self, widget, head, lab):
        widget.bind("<Enter>",
                    lambda _e: self._paint_detail_row(head, lab, HOVER),
                    add="+")
        widget.bind("<Leave>",
                    lambda e: self._leave_detail_row(head, lab, e),
                    add="+")

    def _leave_detail_row(self, head, lab, event):
        """指针在效果行内部移动时不算离开，避免悬停色闪烁"""
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        for _ in range(8):
            if w is None:
                break
            if w is head:
                return
            try:
                w = w.master
            except Exception:
                break
        self._paint_detail_row(head, lab, BG)

    def _paint_detail_row(self, head, lab, color):
        for w in (head, lab):
            try:
                w.configure(bg=color)
            except tk.TclError:
                pass

    def _toggle_group(self, key):
        """点一下展开来源，再点收起"""
        try:
            if key in self._collapsed_groups:
                self._collapsed_groups.discard(key)
            else:
                self._collapsed_groups.add(key)
            self._refresh_detail()
        except Exception as e:
            self._log("展开来源失败：%s" % e)

    def _sync_detail_scroll(self):
        """内容超高才显示滚动条；同时更新滚动范围"""
        canvas, inner = self._detail_canvas, self._detail_inner
        if canvas is None or inner is None:
            return
        try:
            content_h = max(1, inner.winfo_reqheight())
            content_w = max(1, inner.winfo_reqwidth())
            canvas.configure(scrollregion=(0, 0, content_w, content_h))
            try:
                want_h = int(float(canvas.cget("height")))
            except Exception:
                want_h = canvas.winfo_height()
            need = content_h > want_h + 1
            if need and self._detail_scroll is None:
                holder = canvas.master
                sb = tk.Scrollbar(holder, orient="vertical",
                                  command=canvas.yview)
                sb.pack(side="right", fill="y")
                canvas.configure(yscrollcommand=sb.set)
                self._detail_scroll = sb
            elif not need and self._detail_scroll is not None:
                try:
                    self._detail_scroll.destroy()
                except Exception:
                    pass
                self._detail_scroll = None
                canvas.configure(yscrollcommand="")
                canvas.yview_moveto(0)
        except Exception:
            pass

    def _refresh_detail(self):
        """主面板内容更新时，详情窗若开着就原地刷新（不关窗、不抢焦点）"""
        if not self._detail_is_open():
            return
        try:
            if self._detail_head_count is not None:
                self._detail_head_count.config(text=self._detail_count_text())
            self._populate_detail(self._detail_inner)
            self._detail.update_idletasks()
            content_h = max(44, self._detail_inner.winfo_reqheight())
            body_h = min(content_h, DETAIL_BODY_MAX)
            self._detail_canvas.configure(height=body_h)
            self._sync_detail_scroll()
            self._detail.update_idletasks()
            w = max(self._detail_w, self._detail_outer.winfo_reqwidth())
            h = max(80, self._detail_outer.winfo_reqheight())
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            gap = DETAIL_GAP
            x = max(gap, min(self._detail.winfo_x(), max(gap, sw - w - gap)))
            y = max(gap, min(self._detail.winfo_y(), max(gap, sh - h - gap)))
            self._detail.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception as e:
            self._log("详情窗刷新失败（已忽略）：%s" % e)

    # ---------- 详情窗拖动 ----------
    def _bind_detail_drag(self, widget):
        """整窗可拖；被标 _lp_nodrag 的关闭按钮/按钮不参与拖动。

        递归始终向下走：刷新时旧行已销毁、新行是新的控件，需要补绑定。
        """
        if getattr(widget, "_lp_nodrag", False):
            return
        if not getattr(widget, "_lp_ddragged", False):
            widget._lp_ddragged = True
            widget.bind("<Button-1>", self._detail_press, add="+")
            widget.bind("<B1-Motion>", self._detail_motion, add="+")
            widget.bind("<ButtonRelease-1>", self._detail_release, add="+")
        for child in widget.winfo_children():
            self._bind_detail_drag(child)

    def _detail_press(self, e):
        top = self._detail
        if top is None:
            return
        try:
            self._ddrag = (e.x_root, e.y_root,
                           top.winfo_x(), top.winfo_y())
        except tk.TclError:
            self._ddrag = None
        self._dmoved = False

    def _detail_motion(self, e):
        top = self._detail
        if top is None or not self._ddrag:
            return
        x0, y0, px, py = self._ddrag
        dx, dy = e.x_root - x0, e.y_root - y0
        if not self._dmoved and abs(dx) <= 3 and abs(dy) <= 3:
            return
        self._dmoved = True
        try:
            dw, dh = top.winfo_width(), top.winfo_height()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            nx = max(0, min(max(0, sw - dw), px + dx))
            ny = max(0, min(max(0, sh - dh), py + dy))
            top.geometry("+%d+%d" % (nx, ny))
        except tk.TclError:
            pass

    def _detail_release(self, e):
        moved = self._dmoved
        self._ddrag = None
        self._dmoved = False
        if moved:
            return
        # 单击（不是拖动）才展开/收起来源；从效果行拖窗口不会误触发展开
        w = getattr(e, "widget", None)
        for _ in range(12):
            if w is None:
                return
            key = getattr(w, "_lp_group", None)
            if key is not None:
                try:
                    # 回调里会销毁当前控件，放到 idle 更安全
                    self.root.after_idle(lambda k=key: self._toggle_group(k))
                except tk.TclError:
                    pass
                return
            try:
                w = w.master
            except Exception:
                return

    def _detail_wheel(self, e):
        canvas = self._detail_canvas
        if canvas is None:
            return None
        try:
            step = -1 if getattr(e, "delta", 0) > 0 else 1
            canvas.yview_scroll(step * 2, "units")
        except tk.TclError:
            pass
        return "break"

    # ---------- 折叠态渲染 ----------
    def _render_items(self, items):
        self.items = list(items)
        self._clear()

        # 折叠态：**直接写效果**（用户要求：不要写「光环N」，要写效果本身）。
        # 同一种效果只显示一行，数值是**各来源累加**后的合计；
        # 来源写在下一行暗色小字里（同一张牌多次 → ×N）。
        agg = {}
        for it in items:
            d = str(it.get("desc") or it.get("text") or "").strip()
            if not d:
                continue
            a = agg.setdefault(d, {"atk": 0, "hp": 0, "srcs": {}})
            a["atk"] += int(it.get("atk") or 0)
            a["hp"] += int(it.get("hp") or 0)
            _src = str(it.get("source") or "").strip()
            if _src:
                _src = _src[:LONG_SRC_MAX]
                a["srcs"][_src] = a["srcs"].get(_src, 0) + max(
                    1, int(it.get("count") or 1))
        rows = [(d, v["atk"], v["hp"], v["srcs"]) for d, v in agg.items()]

        # 其他随从效果（徒姬 / 维尔伯特…）：判定器**只在我方真的出现该效果时才写**
        special = list(getattr(self, "_special", None) or [])
        evolve = getattr(self, "evolve_count", None)
        self._ball_badge = len(rows) + len(special)
        if getattr(self, "ball_mode", False):
            self._draw_ball()

        if not rows and not special and evolve is None:
            tk.Label(self.body, text="（暂无主战者效果）", bg=BG, fg=DIM,
                     font=self.f_s).pack(anchor="w", padx=10, pady=10)
            self.foot.config(text="")
            self._resize_for_fonts()
            self._bind_new_children()
            self._refresh_detail()
            return

        max_px = max(80, self.w - 36)
        # 本局进化放在**最上面**，样式和其它效果行一致（左文字 / 右数值）
        if evolve is not None:
            self._add_special_row("本局进化", "%d 次" % int(evolve), {}, max_px)
        for _desc, _atk, _hp, _srcs in rows[:COLLAPSED_MAX]:
            self._add_collapsed_row(_desc, _atk, _hp, _srcs, max_px)
        # 其他随从效果：单独的区块（徒姬的梦魇 +6/+6、维尔伯特的守护 +X/+2X…）
        if special:
            for sp in special[:COLLAPSED_MAX]:
                srcs = {}
                if sp.get("source"):
                    srcs[sp["source"][:LONG_SRC_MAX]] = 1
                if sp.get("value"):
                    self._add_special_row(sp.get("desc") or "", sp.get("value"),
                                          srcs, max_px)
                else:
                    self._add_collapsed_row(sp.get("desc") or "",
                                            int(sp.get("atk") or 0),
                                            int(sp.get("hp") or 0), srcs, max_px)
        extra = len(rows) - COLLAPSED_MAX
        self.foot.config(text=("共 %d 种效果" % len(rows)) if extra > 0 else "")
        self._resize_for_fonts()
        self._bind_new_children()

    def _add_special_row(self, desc, value, srcs, max_px):
        """
        「其他随从效果」的一行：**条件 + X 的值**（用户 2026-10-18 要求）。

            本局战斗阶段进场的梦魇数        3
            ← 傍死的安纳提玛·徒姬（超进化）

        不是 "+X/+X"：这类是"带任务的计数"，直接给值更好读。
        """
        row = tk.Frame(self.body, bg=BG2, cursor="hand2")
        row.pack(fill="x", padx=8, pady=2)
        line = tk.Frame(row, bg=BG2, cursor="hand2")
        line.pack(fill="x")

        # 用户 2026-10-18 21:10：这类"带任务的 X"**不写来源**，也**不要拼成一整句**——
        #   左边写条件、右边单独放数值（不相关的能力各自一行）。
        #   本局战斗梦魇随从进场数        3
        val_txt = str(value or "")
        try:
            val_w = self.f_badge.measure(val_txt) + 14
        except tk.TclError:
            val_w = 40
        val = tk.Label(line, text=val_txt, bg=BG2, fg=GOLD,
                       font=self.f_badge, anchor="e", bd=0,
                       padx=0, pady=0)
        val.pack(side="right", padx=(4, 8), pady=(5, 3))
        shown = ellipsize(str(desc or ""), self.f_name, max(24, max_px - val_w))
        lab = tk.Label(line, text=shown, bg=BG2, fg=FG, font=self.f_name,
                       anchor="w", justify="left", bd=0, padx=8, pady=0)
        lab.pack(side="left", fill="x", expand=True, pady=(5, 3))
        # 这类效果**不显示来源**（用户要求）：没有 ← 那一行，也不做详情入口。
        # 但仍允许拖动面板。
        self._bind_drag(row)

    def _add_collapsed_row(self, desc, atk, hp, srcs, max_px):
        """
        折叠态的一行：

            效果描述            +2/+0
            ← 来源牌A ×2 / 来源牌B

        整行（含来源行）都是打开详情窗的点击区；数值单独靠右，
        描述过长只截描述，**+N/+M 永远完整**。
        """
        row = tk.Frame(self.body, bg=BG2, cursor="hand2")
        row.pack(fill="x", padx=8, pady=2)
        line = tk.Frame(row, bg=BG2, cursor="hand2")
        line.pack(fill="x")

        val_txt = "+%d/+%d" % (atk, hp)
        try:
            val_w = self.f_badge.measure(val_txt) + 14
        except tk.TclError:
            val_w = 60
        val = tk.Label(line, text=val_txt, bg=BG2, fg=GOLD,
                       font=self.f_badge, anchor="e", bd=0,
                       padx=0, pady=0, cursor="hand2")
        val.pack(side="right", padx=(4, 8), pady=(5, 3))

        shown = ellipsize(desc, self.f_name, max(24, max_px - val_w))
        lab = tk.Label(line, text=shown, bg=BG2, fg=FG, font=self.f_name,
                       anchor="w", justify="left", bd=0, padx=8, pady=0,
                       cursor="hand2")
        lab.pack(side="left", fill="x", expand=True, pady=(5, 3))

        # 用户 2026-10-18：**来源/回合功能已删除**（省算力），只保留效果 + 数值。
        slab = None
        self._bind_drag(row)
        for w in [x for x in (row, line, lab, val, slab) if x is not None]:
            w.bind("<Enter>",
                   lambda _e, r=row: self._set_row_bg(r, HOVER),
                   add="+")
            w.bind("<Leave>",
                   lambda e, r=row: self._leave_row(r, e),
                   add="+")

    def _leave_row(self, row, event):
        """指针在行内子控件之间移动时不算离开，避免悬停底色闪烁"""
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        for _ in range(8):
            if w is None:
                break
            if w is row:
                return
            try:
                w = w.master
            except Exception:
                break
        self._set_row_bg(row, BG2)

    def _set_row_bg(self, row, color):
        """整行（含来源小字 / 数值）一起换底色，悬停时不会只亮半行"""
        def walk(w):
            try:
                w.configure(bg=color)
            except tk.TclError:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(row)

    def _bind_new_children(self):
        """新渲染出来的行也要能拖：body 本身已绑过，只补新子控件"""
        for child in self.body.winfo_children():
            self._bind_drag(child)

    def _render_error(self, message):
        self._clear()
        box = tk.Frame(self.body, bg=BG)
        box.pack(fill="both", expand=True, padx=8, pady=(2, 4))
        tk.Label(box, text="数据读取失败", bg=BG, fg=RED,
                 font=self.f_title, anchor="w", bd=0, padx=0,
                 pady=0).pack(fill="x")
        tk.Label(box, text=_text(message, 400), bg=BG, fg=DIM,
                 font=self.f_src, anchor="w", justify="left", bd=0,
                 padx=0, pady=0,
                 wraplength=max(80, self.w - 30)).pack(fill="x", pady=(2, 0))
        self.foot.config(text="仍在轮询，修好后自动恢复")
        self._bind_new_children()

    # ---------- 轮询 ----------
    def _tick(self):
        # 控制台没了就自己退出（避免关插件后残留面板）
        try:
            import parent_guard
            if parent_guard.orphaned():
                self._log("控制台已退出：面板自动关闭")
                self.close()
                return
        except Exception:
            pass
        try:
            self._check_overlay()
        except Exception as e:                 # 兜底：任何异常都不许退出
            self._log("轮询异常（已忽略）：%s" % e)
        try:
            if self.follow:
                self._apply_follow()
            try:
                self._poll_leader_settings()
            except Exception as e:
                self._log("设置轮询异常（已忽略）：%s" % e)
            self._reassert_topmost()
            self._clamp_to_screen()
            if not getattr(self, "_phys_done", False):
                if self._force_phys():
                    self._phys_done = True
        except Exception:
            pass
        try:
            self._after_id = self.root.after(int(self.interval * 1000),
                                             self._tick)
        except tk.TclError:
            self._after_id = None            # 窗口已销毁

    def _check_overlay(self):
        raw, snap = _read_snapshot(self.path)     # 内含重试（见 _read_snapshot）

        # 内容没变：不做任何事。
        # ⚠️ 只有**解析成功**才记「已处理过这份内容」：否则读到坏内容后
        #    同一个 raw 会被永久判定成"没变化"，面板就再也不会重试了。
        if raw == self._last_raw and snap.get("kind") != "error":
            return
        if snap.get("kind") != "error":
            self._last_raw = raw

        # ① 读不到数据：**仍然显示**（用户要求：面板要一直能看到），保留累积内容
        if snap["kind"] == "missing":
            self._set_visible(True)
            self._log_once("missing",
                           "显示（overlay.json 不存在，已累积的 %d 条保留）"
                           % len(self.acc))
            return

        # ② 数据损坏：有内容就继续显示列表，没内容才弹「数据读取失败」
        if snap["kind"] == "error":
            self._set_visible(True)
            if self.acc:
                self._log_once(("error", snap["error"]),
                               "数据读取失败：%s（保留已累积的 %d 条）"
                               % (snap["error"], len(self.acc)))
            else:
                self._render_error(snap["error"])
                self._log_once(("error", snap["error"]),
                               "数据读取失败：%s" % snap["error"])
            return

        # ③ 不在对局中 / 整局结束：清空 + 隐藏（用户要求）
        #    · gameOver=true / phase=gameover / ourHp<=0 → 整局结束；
        #    · phase=other/menu/... → 已经离开对局（判定器在开局和结算后写 other）。
        #    operate / battle 都算在局内：单场战斗结束**不清空**。
        phase = snap["phase"]
        ended = bool(snap["game_over"])
        # 用户 2026-10-18：**没在游戏里时，悬浮窗里的内容要清零**（窗口留着）。
        # 除了显式 out-of-game 阶段，overlay.json 太久没更新（判定器停了/游戏退了）
        # 也按"不在局内"处理，否则会一直显示上一局的旧效果。
        # phase 为空/None = 判定器还没定论（开局、结算瞬间都会短暂出现），
        # **不能**当成"离开对局"去清空，否则对局中球会突然变空（用户实测）。
        in_game = (phase is None) or (phase not in OUT_OF_GAME_PHASES)
        try:
            _upd = str(snap.get("updated_at") or "")
            if _upd:
                _h, _m, _sec = [int(x) for x in _upd.split(":")]
                _now = time.localtime()
                _t = _now.tm_hour * 3600 + _now.tm_min * 60 + _now.tm_sec
                if abs(_t - (_h * 3600 + _m * 60 + _sec)) > 60:
                    in_game = False          # 超过 1 分钟没更新 = 不在局内
        except Exception:
            pass
        out_game = (not ended) and (not in_game)
        if ended or out_game:
            reason = ("、".join(snap["game_over"]) if ended
                      else ("phase=%s" % (phase or "?")))
            n = len(self.acc)
            self.acc = []
            self._acc_sig = None
            self._game_over = ended or phase == "gameover"
            self._special = []
            self.evolve_count = None
            self._render_items([])          # 清空后显示占位，但窗口不藏
            self._set_visible(True)
            self._collapsed_groups.clear()
            self._log_once(("end", reason),
                           "整局结束/离开对局（%s）：清空 %d 条（面板保持显示）"
                           % (reason, n))
            return

        # ④ 新的一局：清空重置（清空后若本轮带了我方效果，下面会重新合并）
        if snap["new_game"]:
            n = len(self.acc)
            self.acc = []
            self._acc_sig = None
            self._game_over = False
            self._special = []
            self.evolve_count = None
            self._render_items([])
            self._set_visible(True)
            self._collapsed_groups.clear()
            self._log("新的一局开始（newGame=true）：清空重置（原有 %d 条）" % n)

        # ⑤ 只合并我方条目；先按 (效果, 来源) 合并同一张牌的重复条目（×N）
        incoming = {}
        order = []
        for it in snap["items"]:
            if it["side"] != "player":
                continue                    # 规则 1：对方光环不显示
            key = item_key(it)
            hit = incoming.get(key)
            if hit is None:
                rec = dict(it)
                rec["count"] = max(1, int(it.get("count") or 1))
                incoming[key] = rec
                order.append(key)
            else:
                # 同一张牌触发多次：次数累加、数值累加（同一次快照内才算，
                # 重复轮询同一份快照不会虚增，因为下面是**覆盖**不是追加）
                hit["count"] += max(1, int(it.get("count") or 1))
                hit["atk"] += int(it.get("atk") or 0)
                hit["hp"] += int(it.get("hp") or 0)
                if hit.get("round") is None and it.get("round") is not None:
                    hit["round"] = it.get("round")

        # 用户 2026-10-18：面板**以判定器的列表为准**整体替换。
        # 之前是只增不减（acc 累积），判定器换了新一局、清空光环后，
        # 面板还留着上一局的条目 —— 就是"上一局状态没重置"的根因。
        try:
            self.acc = [dict(v) for v in incoming.values()] if incoming else []
        except Exception:
            pass
        index = {item_key(it): it for it in self.acc}
        added, updated = [], 0
        for key in order:
            it = incoming[key]
            hit = index.get(key)
            if hit is None:
                new = dict(it)
                self.acc.append(new)
                index[key] = new
                added.append(new)
            elif (hit.get("name"), hit.get("text"), hit.get("source"),
                  hit.get("cardId"), hit.get("round"),
                  hit.get("atk"), hit.get("hp"),
                  int(hit.get("count") or 1)) != (
                    it.get("name"), it.get("text"), it.get("source"),
                    it.get("cardId"), it.get("round"),
                    it.get("atk"), it.get("hp"),
                    int(it.get("count") or 1)):
                # 同一效果的「份数/叠加数值/来源/回合」变了 → 原地更新，不新增一行
                for f in ("name", "text", "desc", "source", "cardId", "round",
                          "atk", "hp", "count"):
                    hit[f] = it.get(f)
                updated += 1

        # ⑥ 刷新显示：有累积就显示（operate/battle 都显示；离开对局上面已隐藏）
        self._special = list(snap.get("special") or [])
        self.evolve_count = snap.get("evolve")
        acc_sig = (
            tuple((it.get("desc"), it.get("text"), it.get("source"),
                   it.get("cardId"), it.get("round"),
                   it.get("atk"), it.get("hp"),
                   int(it.get("count") or 1))
                  for it in self.acc),
            tuple((s.get("desc"), s.get("value"), s.get("atk"),
                   s.get("hp"), s.get("source"))
                  for s in self._special),
            self.evolve_count,
        )
        phase = snap["phase"] or "?"
        # 用户 2026-10-18 21:30：**这个面板要一直显示出来**（哪怕没在局内、没光环）。
        # 进程活着 = 悬浮球一直在；关闭按钮才会让它消失。
        has_any = True
        if acc_sig != self._acc_sig:
            self._acc_sig = acc_sig
            if has_any:
                self._render_items(self.acc)
                self._set_visible(True)
            else:
                self._clear()
                self._set_visible(False)
        elif has_any:
            self._set_visible(True)
        if added:
            skip = ("（跳过 %d 条非我方）" % snap["others"]
                    if snap["others"] else "")
            self._log("新增 %d 条我方效果%s → 本局累计 %d 条（phase=%s）：%s"
                      % (len(added), skip, len(self.acc), phase,
                         "；".join("我方/%s" % i["name"] for i in added)))
        elif updated:
            self._log("更新 %d 条我方效果的显示 → 本局累计 %d 条（phase=%s）"
                      % (updated, len(self.acc), phase))
        elif not self.acc:
            skip = ("，跳过 %d 条非我方" % snap["others"]
                    if snap["others"] else "")
            self._log_once(("empty", phase, snap["others"]),
                           "隐藏：暂无我方主战者效果（phase=%s%s）"
                           % (phase, skip))
        else:
            # 有累积、本轮没有新增：保持现状不动（单场战斗结束也不清空）
            skip = ("，跳过 %d 条非我方" % snap["others"]
                    if snap["others"] else "")
            self._log_once(("keep", phase, acc_sig, snap["others"]),
                           "保持显示：本局累计 %d 条（phase=%s，无新增%s）"
                           % (len(self.acc), phase, skip))

    def _log_once(self, sig, message):
        """同一类状态只记一次，避免 450ms 轮询把日志刷爆"""
        if sig == self._last_log_sig:
            return
        self._last_log_sig = sig
        self._log(message)

    # ---------- 日志 / 退出 ----------
    def _log(self, message):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        try:
            print(line, flush=True)
        except Exception:
            pass
        if not self.log_path:
            return
        try:
            d = os.path.dirname(self.log_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with io.open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def close(self):
        self._log("退出面板")
        self._cancel_tick()
        self._close_detail(silent=True)
        try:
            self.root.destroy()
        except Exception:
            pass


def build_parser():
    ap = argparse.ArgumentParser(
        description="影之幻境 · 主战者效果面板（只读 overlay.json 的 leaderEffects）")
    ap.add_argument("--file", default=DEF_OVERLAY,
                    help="overlay.json 路径（默认：判定器输出的 shots/live/overlay.json）")
    ap.add_argument("--x", type=int, default=40, help="窗口左上角 x（逻辑像素）")
    ap.add_argument("--y", type=int, default=300, help="窗口左上角 y（逻辑像素）")
    ap.add_argument("--w", type=int, default=WIN_W, help="窗口宽（逻辑像素）")
    ap.add_argument("--h", type=int, default=WIN_H, help="窗口高（逻辑像素）")
    ap.add_argument("--phys", default="",
                    help="\u7269\u7406\u50cf\u7d20\u4f4d\u7f6e x,y\uff08\u4f18\u5148\u4e8e\u6240\u6709\u5176\u5b83\u5b9a\u4f4d\u65b9\u5f0f\uff09")
    ap.add_argument("--locked", dest="locked", action="store_true",
                    default=True,
                    help="位置锁定：不能拖动，但仍可点击打开详情窗（默认）")
    ap.add_argument("--unlocked", dest="locked", action="store_false",
                    help="允许拖动窗口（排查/调试用）")
    ap.add_argument("--follow", action="store_true",
                    help="跟随模式：贴在 panel-pos.json 描述的战绩面板下方")
    ap.add_argument("--pos", default=DEF_POS,
                    help="跟随模式读取的位置文件（默认 logs/panel-pos.json）")
    ap.add_argument("--follow-gap", type=int, default=FOLLOW_GAP, dest="follow_gap",
                    help="跟随模式与战绩面板底边的间距（逻辑像素，默认 8）")
    ap.add_argument("--interval", type=float, default=POLL_SEC,
                    help="轮询间隔秒（默认 0.45，限制在 0.15~2.0）")
    ap.add_argument("--log", default=None,
                    help="可选：把状态变化追加写入该文件（排查用）")
    ap.add_argument("--settings", default=DEF_LEADER_SET,
                    help="主战者效果设置文件（默认 logs/leader-settings.json）")
    ap.add_argument("--title", default=TITLE_TEXT,
                    help="窗口标题（默认：%s；排查/多实例验证时可覆盖）"
                         % TITLE_TEXT)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    sc = enable_dpi_awareness()
    if abs(sc - 1.0) > 1e-3:
        args.w = int(round(args.w * sc))
        args.h = int(round(args.h * sc))
        args.x = int(round(args.x * sc))
        args.y = int(round(args.y * sc))

    # 单实例守卫：同名窗口已存在就别再开一个
    # （用实际标题做 key，方便 --title 起的排查实例与正式面板并存）
    try:
        import single_instance as _SI
        if _SI.already_running(args.title):
            print("已有同名面板在运行，退出")
            import sys as _s
            _s.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass
    try:
        root = tk.Tk()
        try:
            import ui_icon
            ui_icon.set_window_icon(root)
        except Exception:
            pass
    except tk.TclError as e:
        # pythonw 下 sys.stderr 是 None，写日志失败也不能再抛
        try:
            sys.stderr.write("无法创建窗口（没有桌面会话？）：%s\n" % e)
        except Exception:
            pass
        return 2
    panel = LeaderPanel(
        root,
        path=args.file,
        x=args.x, y=args.y, w=args.w, h=args.h,
        follow=args.follow,
        pos_path=args.pos,
        gap=args.follow_gap,
        interval=args.interval,
        log_path=args.log,
        title_text=args.title,
        settings_path=args.settings,
        locked=args.locked,
    )
    panel.phys = args.phys
    root.protocol("WM_DELETE_WINDOW", panel.close)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
