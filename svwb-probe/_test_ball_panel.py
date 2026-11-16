#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
悬浮球 + 面板分区 自测（2026-10-18）

覆盖：
  1. 有数据时默认收起成悬浮球（56×56），角标 = 光环数 + 其他效果数
  2. 点球展开：显示「光环行」「其他随从效果」「本局进化：N 次」
  3. 再点收起按钮回球
  4. 只读临时 overlay.json，不碰真实文件、不起别的进程

用法： python -u _test_ball_panel.py
"""
import ctypes
import io
import json
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import leader_panel as LP  # noqa: E402

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

try:
    SCALE = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
except Exception:
    SCALE = 1.0

LOGS = os.path.join(HERE, "logs")
SHOT = os.path.join(LOGS, "验证_悬浮球_")
CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond), extra))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


def labels_text(root):
    out = []

    def walk(w):
        try:
            if isinstance(w, tk.Label):
                out.append(str(w.cget("text")))
        except Exception:
            pass
        for c in w.winfo_children():
            walk(c)

    walk(root)
    return out


def shot(root, name):
    if not ImageGrab:
        return
    try:
        try:
            root.attributes("-topmost", True)   # 只是为了截图：截图后恢复
            root.lift()
        except Exception:
            pass
        for _ in range(3):
            root.update()
            time.sleep(0.06)
        root.update_idletasks()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        bbox = (int(x * SCALE), int(y * SCALE),
                int((x + w) * SCALE), int((y + h) * SCALE))
        print("    [shot] pos=(%d,%d) size=(%dx%d) scale=%.2f" % (x, y, w, h, SCALE))
        ImageGrab.grab(bbox=bbox).save(SHOT + name + ".png")
        try:
            root.attributes("-topmost", False)
        except Exception:
            pass
        print("  截图 logs/验证_悬浮球_%s.png" % name)
    except Exception as exc:
        print("  截图失败：%s" % exc)


def payload():
    return {
        "phase": "battle",
        "leaderEffects": [
            {"side": "player", "desc": "精灵·随从进场", "source": "森林骑士道·辛西亚",
             "auraName": "精灵光环", "round": 3, "atk": 1, "hp": 0},
            {"side": "player", "desc": "幻境等级3或以下的随从进场", "source": "商队猛犸象",
             "auraName": "谭涛光环", "round": 4, "atk": 2, "hp": 2},
        ],
        "specialEffects": [
            {"desc": "本局战斗梦魇随从进场数", "value": "3",
             "source": "傍死的安纳提玛·徒姬（超进化）"},
            {"desc": "本局游戏被破坏的守护随从数量", "value": "1",
             "source": "呜咽的圣骑士·维尔伯特（超进化）"},
        ],
        "evolveCount": 4,
    }


def main():
    tmp = tempfile.mkdtemp(prefix="svwb-ball-")
    path = os.path.join(tmp, "overlay.json")
    io.open(path, "w", encoding="utf-8").write(json.dumps(payload(), ensure_ascii=False))
    os.makedirs(LOGS, exist_ok=True)

    # 用独立的设置文件：不透明度固定 1.0，避免抓到背景透出来的东西（真实日志里的 0.55 会透）
    st_path = os.path.join(tmp, "leader-settings.json")
    io.open(st_path, "w", encoding="utf-8").write(
        json.dumps({"alpha": 1.0, "font_size": 9}))
    root = tk.Tk()
    panel = LP.LeaderPanel(root, path, x=120, y=120, w=320, h=160,
                           interval=0.3, log_path=os.path.join(tmp, "ball.log"),
                           settings_path=st_path)
    root.update()

    print("== 1. 默认收起成悬浮球 ==")
    panel._check_overlay()
    root.update()
    check("ball_mode 默认开启", panel.ball_mode is True)
    check("窗口是 56×56", root.winfo_width() == LP.BALL_SIZE
          and root.winfo_height() == LP.BALL_SIZE,
          (root.winfo_width(), root.winfo_height()))
    check("角标 = 光环 2 + 其他效果 2", panel._ball_badge == 4, panel._ball_badge)
    shot(root, "01_收起")

    print("== 1.5 球外观 / 拖拽 / 没有 ○ 按钮 ==")
    check("没有 ○ 按钮（已删除）", not hasattr(panel, "ball_btn"))
    # 拖动球：直接调手势处理器（模拟真实拖动）
    x0, y0 = panel._place_x, panel._place_y
    class _E:
        def __init__(self, xr):
            self.x_root = xr
            self.y_root = 0
    e1 = _E(1000); e1.y_root = 500
    e2 = _E(1120); e2.y_root = 560
    panel._ball_press(e1)
    panel._ball_motion(e2)
    panel._ball_release(e2)
    root.update()
    check("拖动悬浮球 → 窗口跟着移动",
          (panel._place_x, panel._place_y) != (x0, y0),
          (x0, y0, panel._place_x, panel._place_y))
    check("拖动后没有展开面板", panel.ball_mode is True, panel.ball_mode)
    # × 号 = 收回球（不是关闭）
    panel._expand()
    root.update()
    panel._enter_ball()
    root.update()
    check("×/收起 → 回到悬浮球", panel.ball_mode is True)

    print("== 1.6 展开为了完整显示可以挪，收回要回原位 ==")
    panel._place(1000, 700)          # 靠近右下角：面板展开时会被夹进屏幕
    root.update()
    before = (panel._place_x, panel._place_y)
    panel._expand()
    root.update()
    panel._clamp_to_screen()          # 手动触发"挪进屏幕"（真实场景由布局/夹屏逻辑做）
    root.update()
    moved_when_expanded = (panel._place_x, panel._place_y) != before
    panel._enter_ball()
    root.update()
    check("收回后球回到展开前的位置",
          (panel._place_x, panel._place_y) == before,
          (before, panel._place_x, panel._place_y))
    print("   （展开时是否被夹进屏幕：%s）" % moved_when_expanded)

    print("== 2. 点球展开 ==")
    panel._expand()
    root.update()
    check("展开后宽度回到面板宽", root.winfo_width() == panel.w, root.winfo_width())
    texts = labels_text(root)
    joined = " | ".join(texts)
    check("不再显示「其他随从效果」标题",
          not any(t.strip() == "其他随从效果" for t in texts), texts[:16])
    check("徒姬：条件单独一行（本局战斗梦魇随从进场数）",
          any(t.strip() == "本局战斗梦魇随从进场数" for t in texts), texts[:14])
    check("徒姬：数值单独靠右（3）", any(t.strip() == "3" for t in texts), texts[:14])
    check("维尔伯特：条件单独一行（本局游戏被破坏的守护随从数量）",
          any(t.strip() == "本局游戏被破坏的守护随从数量" for t in texts), texts[:14])
    check("维尔伯特：数值单独靠右（1）", any(t.strip() == "1" for t in texts), texts[:14])
    check("这两条不显示来源（没有 ← 徒姬/维尔伯特）",
          not any(t.startswith("← 傍死的安纳提玛") or t.startswith("← 呜咽的圣骑士")
                  for t in texts), [t for t in texts if t.startswith("←")])
    check("进化次数在最上面且是效果行样式",
          any(t.strip() == "本局进化" for t in texts)
          and any(t.strip() == "4 次" for t in texts), texts[:10])
    _idx_evo = next((i for i, t in enumerate(texts) if t.strip() == "本局进化"), 999)
    _idx_aura = next((i for i, t in enumerate(texts) if "精灵·随从进场" in t), 999)
    check("进化行排在光环行之前", _idx_evo < _idx_aura, (_idx_evo, _idx_aura))
    check("光环行仍在", any("精灵·随从进场" in t for t in texts), joined[:200])
    shot(root, "02_展开")

    print("== 3. 再收起 ==")
    panel._enter_ball()
    root.update()
    check("又回到 56×56", root.winfo_width() == LP.BALL_SIZE, root.winfo_width())
    shot(root, "03_再收起")

    print("== 3.5 局内但没有光环/效果 → 仍然显示（用户要求）==")
    # 模拟"刚开一局、还没拿到任何光环"：清掉本局累积
    panel.acc = []
    panel._special = []
    panel.evolve_count = None
    panel._acc_sig = None
    io.open(path, "w", encoding="utf-8").write(json.dumps(
        {"phase": "battle", "leaderEffects": [], "specialEffects": []},
        ensure_ascii=False))
    panel._check_overlay()
    root.update()
    check("无光环也显示（ball_mode 可见）", panel.visible is True and panel.ball_mode is True,
          (panel.visible, panel.ball_mode))
    check("角标为 0（圆球显示 ○）", panel._ball_badge == 0, panel._ball_badge)

    print("== 4. 离开对局 → 仍显示（只清空）==")
    io.open(path, "w", encoding="utf-8").write(json.dumps(
        {"phase": "other", "leaderEffects": [], "specialEffects": [], "evolveCount": 4},
        ensure_ascii=False))
    panel._check_overlay()
    root.update()
    check("phase=other 时仍显示（悬浮球常驻）", panel.visible is True, panel.visible)

    try:
        panel.close()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

    ok = sum(1 for _n, c, _e in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
