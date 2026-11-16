#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
悬浮窗回归（2026-10-18 精简版）

用户已删除「来源 / 回合 / 详情窗」功能，所以这里只验证现在的行为：
  1. 启动就是悬浮球，点开是面板
  2. 效果行 = **效果 + 数值**，没有来源行、没有「点开看来源」
  3. 进化次数在最上面
  4. 空数据也显示（球），离开对局只清空不隐藏
  5. 面板「×」= 收回球，不关闭
  6. 展开后收回，球回到原位置
用法： python -u _verify_leader_panel.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import leader_panel as LP  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


def labels(root):
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


def payload(effects, evolve=None, phase="battle"):
    d = {"phase": phase,
         "leaderEffects": [
             {"side": "player", "desc": desc, "source": src,
              "atk": a, "hp": h, "round": 3}
             for desc, src, a, h in effects]}
    if evolve is not None:
        d["evolveCount"] = evolve
    d["specialEffects"] = [
        {"desc": "本局战斗梦魇随从进场数", "value": "3",
         "source": "傍死的安纳提玛·徒姬（超进化）"}]
    return d


def main():
    tmp = tempfile.mkdtemp(prefix="svwb-vlp-")
    ov = os.path.join(tmp, "overlay.json")
    st = os.path.join(tmp, "leader-settings.json")
    io.open(st, "w", encoding="utf-8").write(
        json.dumps({"alpha": 1.0, "font_size": 9}))

    def write(d):
        io.open(ov, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))

    write(payload([("精灵·随从进场", "森林骑士道·辛西亚", 1, 0),
                   ("幻境等级3或以下的随从进场", "商队猛犸象", 2, 2)], evolve=4))
    root = tk.Tk()
    p = LP.LeaderPanel(root, ov, x=200, y=200, w=320, h=160,
                       interval=60.0, settings_path=st,
                       title_text="验证 · 悬浮窗")
    root.update()

    print("[1] 启动即显示悬浮球")
    check("visible=True", p.visible is True, p.visible)
    check("ball_mode=True", p.ball_mode is True)
    check("窗口 56x56", root.winfo_width() == LP.BALL_SIZE, root.winfo_width())
    check("角标 = 光环2 + 计数1", p._ball_badge == 3, p._ball_badge)

    print("[2] 点开面板：只有效果 + 数值，没有来源/回合")
    p._expand()
    p._check_overlay()
    root.update()
    texts = labels(root)
    check("展开宽度 = 面板宽", root.winfo_width() == p.w, root.winfo_width())
    check("有精灵光环（效果名 + 数值分两列）",
          any("精灵·随从进场" in t for t in texts)
          and any(t.strip() == "+1/+0" for t in texts), texts[:8])
    check("有进化行（最上面）", any(t.strip() == "本局进化" for t in texts)
          and any(t.strip() == "4 次" for t in texts), texts[:6])
    check("有带任务的计数行", any("本局战斗梦魇随从进场数" in t for t in texts), texts)
    check("没有任何来源行（←）",
          not any(t.strip().startswith("←") for t in texts), texts)
    check("没有「点开看来源」页脚",
          not any("点开看" in t for t in texts), texts)
    check("没有来源牌名（辛西亚）", not any("辛西亚" in t for t in texts), texts)

    print("[3] × = 收回球（不关闭）")
    p._enter_ball()
    root.update()
    check("回到 56x56", root.winfo_width() == LP.BALL_SIZE, root.winfo_width())
    check("进程仍在（窗口存在）", bool(root.winfo_exists()))

    print("[4] 展开后收回，球回原位")
    before = (p._place_x, p._place_y)
    p._expand()
    p._clamp_to_screen()
    root.update()
    p._enter_ball()
    root.update()
    check("球回到展开前的位置", (p._place_x, p._place_y) == before,
          (before, p._place_x, p._place_y))

    print("[5] 空数据仍显示 / 离局只清空")
    write({"phase": "battle", "leaderEffects": [], "specialEffects": []})
    p.acc = []
    p._special = []
    p.evolve_count = None
    p._acc_sig = None
    p._check_overlay()
    root.update()
    check("无光环也显示", p.visible is True)
    write({"phase": "other", "leaderEffects": []})
    p._check_overlay()
    root.update()
    check("离开对局仍显示", p.visible is True)
    check("离开对局内容清零（acc/special/进化）",
          p.acc == [] and p._special == [] and p.evolve_count is None,
          (p.acc, p._special, p.evolve_count))

    try:
        p.close()
        root.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    ok = sum(1 for _n, c in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
