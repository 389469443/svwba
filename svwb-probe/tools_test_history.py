#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
战绩面板 + history.py 自测脚本（可重复运行，全程只用临时文件）。

    python tools_test_history.py            # 全部场景（含 Tk 界面逻辑）
    python tools_test_history.py --no-ui    # 只测 history.py（无显示环境也能跑）

覆盖：
  history.py
    · 真实 logs/history.json 的副本可解析
    · 空战绩
    · 选主战者先建一条 → 出局回填，**始终只有一条**
    · 主战者 id 与记录不一致时出局 → 仍回填同一条，不新增
    · 多条"未结算"孤儿行自动收敛（已经输了的局不会永远显示进行中）
    · 没有未结算记录时才新建
    · 备注字段 / update_game / 原子写（不留 .tmp）
  panel_ui.py
    · 空战绩 → 「尚未结算」
    · 一条进行中 → 「本局进行中 / 已完成 0 局」
    · 一条已结算 + 一条进行中 → 「已完成 1 局」
    · 名次在主战者名字后面、时间在行末且可关、不显示血量数值
    · ↻ = 重置页面：清空后等新数据才恢复
    · 锁定 ↔ panel-settings.json 双向同步
    · 锁定后光标悬停在锁按钮上 → 临时取消穿透（可以再点开）
"""
import argparse
import ctypes
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import history as HIS     # noqa: E402

TMP = tempfile.mkdtemp(prefix="svwb_panel_test_")
HIST = os.path.join(TMP, "history.json")
REAL_HIST = os.path.join(HERE, "logs", "history.json")

RESULTS = []
HERO_A = 1105
HERO_B = 1200


# ---------------------------------------------------------------- 工具
def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    tag = "[PASS]" if ok else "[FAIL]"
    line = f"  {tag} {name}"
    if detail and not ok:
        line += f"   ← {detail}"
    print(line, flush=True)
    return ok


def section(title):
    print("\n== " + title + " ==", flush=True)


def write_hist(data, path=None):
    with open(path or HIST, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


def read_hist(path=None):
    with open(path or HIST, encoding="utf-8") as fh:
        return json.load(fh)


def game(t="15:15:07", hero=HERO_A, hp=None, rank=None, board=None, note=""):
    return {"t": t, "hero": hero, "hp": hp, "rank": rank,
            "board": board or [], "note": note,
            "note_line1": "", "note_line2": ""}


def fixture(games=(), records=(), session="2026-10-18 15:15:07"):
    write_hist({"session": session, "records": list(records),
                "games": list(games), "placement": None})


# ---------------------------------------------------------------- history.py
def test_history():
    section("history.py · 一局只有一条")
    HIS.HIST = HIST

    # 真实战绩文件副本（证明格式兼容，且不改动原文件）
    if os.path.isfile(REAL_HIST):
        write_hist(read_hist(REAL_HIST))          # 读真实 + 写临时副本
        check("真实 history.json 的副本可读写", isinstance(read_hist(), dict))
    else:
        fixture()
        check("（没有真实 history.json，直接用空战绩）", True)

    fixture()
    idx, created = HIS.ensure_game(HERO_A)
    d = read_hist()
    check("选主战者 → 新建 1 条，且只有编号 + 主战者",
          created and idx == 0 and len(d["games"]) == 1
          and d["games"][0]["hero"] == HERO_A
          and d["games"][0]["hp"] is None and d["games"][0]["rank"] is None,
          str(d.get("games")))

    idx, created = HIS.ensure_game(HERO_A)
    check("同一局再次 ensure_game → 不新增",
          (idx, created) == (0, False) and len(read_hist()["games"]) == 1)

    idx, created = HIS.ensure_game(HERO_B)      # 主战者读错/换皮
    d = read_hist()
    check("主战者 id 变了也不新增（复用未结算那条）",
          len(d["games"]) == 1 and created is False and d["games"][0]["hero"] == HERO_A,
          str(d.get("games")))

    idx = HIS.finish_game(999, 5, -3, [])       # 出局时 id 又读错
    d = read_hist()
    check("出局时 id 对不上 → 回填同一条、不新增",
          len(d["games"]) == 1 and idx == 0, str(d.get("games")))
    check("回填后名次/血量正确，主战者名字不被读错覆盖",
          d["games"][0]["rank"] == 5 and d["games"][0]["hp"] == -3
          and d["games"][0]["hero"] == HERO_A, str(d["games"][0]))

    # 下一局：上一局已结算 → 正常新增
    HIS.ensure_game(HERO_B)
    d = read_hist()
    check("上一局已结算 → 下一局新增一条",
          len(d["games"]) == 2 and d["games"][-1]["hp"] is None, str(d.get("games")))
    HIS.finish_game(HERO_A, 2, -7, [])
    d = read_hist()
    check("第二局出局回填 → 仍然 2 条（没有第三条）",
          len(d["games"]) == 2 and d["games"][-1]["rank"] == 2
          and d["games"][-1]["hp"] == -7, str(d.get("games")))

    section("history.py · 已输的局不能卡在「进行中」")
    fixture(games=[game(t="15:01:00", hero=HERO_A, hp=None),
                   game(t="15:02:00", hero=HERO_B, hp=None)])
    idx = HIS.finish_game(HERO_A, 4, -6, [])
    d = read_hist()
    check("两条未结算孤儿 → 结算时收敛成 1 条",
          len(d["games"]) == 1 and d["games"][0]["rank"] == 4
          and d["games"][0]["hp"] == -6, str(d.get("games")))

    fixture(games=[game(t="15:01:00", hero=HERO_A, hp=None),
                   game(t="15:02:00", hero=HERO_B, hp=None)])
    HIS.ensure_game(HERO_A)
    d = read_hist()
    check("新一局开始时 → 孤儿未结算行自动清掉，只剩当前这条",
          len(d["games"]) == 1 and d["games"][0]["hp"] is None, str(d.get("games")))

    section("history.py · 没记录时才新建 / 备注 / 原子写")
    fixture(games=[game(hero=HERO_A, hp=-3, rank=5)])
    idx = HIS.finish_game(HERO_A, 2, -7, [])
    d = read_hist()
    check("没有未结算记录 → 才 append 一条",
          idx == 1 and len(d["games"]) == 2 and d["games"][-1]["rank"] == 2,
          str(d.get("games")))

    fixture(games=[game(hero=HERO_A, hp=-3, rank=None)])
    idx = HIS.finish_game(HERO_A, 3, -3, [])
    d = read_hist()
    check("只缺名次的已结算记录 → 只补名次，不新增",
          idx == 0 and len(d["games"]) == 1 and d["games"][0]["rank"] == 3,
          str(d.get("games")))

    fixture()
    HIS.add_game(3, HERO_A, -4, [])
    d = read_hist()
    check("add_game 写入已结算记录并带备注字段",
          d["games"][0]["rank"] == 3 and d["games"][0]["hp"] == -4
          and "note_line1" in d["games"][0] and "note_line2" in d["games"][0],
          str(d.get("games")))
    check("update_game 能写回两行备注",
          HIS.update_game(0, note_line1="精灵", note_line2="卡手")
          and read_hist()["games"][0]["note_line1"] == "精灵")
    check("update_game 越界返回 False", HIS.update_game(99, note_line1="x") is False)
    leftovers = [f for f in os.listdir(os.path.dirname(HIST))
                 if f.startswith(os.path.basename(HIST) + ".tmp")]
    check("_save 是原子写：不残留 history.json.tmp", not leftovers, str(leftovers))


# ---------------------------------------------------------------- panel_ui.py
def test_ui():
    import tkinter as tk
    import panel_ui as PUI

    section("panel_ui.py · 渲染（临时 history / 设置文件）")
    PUI.HIST = HIST
    PUI.POSF = os.path.join(TMP, "panel-pos.json")
    PUI.PANEL_SET = os.path.join(TMP, "panel-settings.json")
    with open(PUI.PANEL_SET, "w", encoding="utf-8") as fh:
        json.dump({"show_time": True, "alpha": 0.8, "text_alpha": 0.95,
                   "locked": False, "scale": 1.0, "font_size": 9,
                   "hero_display": "both", "force_topmost": True}, fh)

    root = tk.Tk()
    try:
        p = PUI.Panel(root)
        root.update_idletasks()
        # 测试里固定"光标在屏幕外"，避免真实鼠标位置干扰锁定判定
        p._cursor_pos = lambda: (-10000, -10000)

        def body_text():
            out = []

            def walk(w):
                try:
                    if isinstance(w, tk.Label):
                        out.append(str(w.cget("text")))
                    elif isinstance(w, tk.Entry):
                        out.append(str(w.get()))
                except Exception:
                    pass
                for c in w.winfo_children():
                    walk(c)

            walk(p.body)
            return " | ".join(out)

        def show(data):
            write_hist(data)
            p.last_sig = None
            p._page_cleared = False
            p.refresh()
            return body_text()

        def ex_transparent():
            ex = ctypes.windll.user32.GetWindowLongW(p._hwnd(), PUI.GWL_EXSTYLE)
            return bool(ex & PUI.WS_EX_TRANSPARENT)

        def first_hero_row():
            frames = [w for w in p.body.winfo_children()
                      if isinstance(w, tk.Frame)]
            if not frames:
                return None
            subs = [w for w in frames[0].winfo_children()
                    if isinstance(w, tk.Frame)]
            return subs[0] if subs else None

        # --- 空战绩 ---
        txt = show({"session": "2026-10-18 15:15:07", "records": [],
                    "games": [], "placement": None})
        info = p.info.cget("text")
        check("空战绩 → 头部「尚未结算」", "尚未结算" in info, info)
        check("空战绩 → 正文提示「还没有战绩」", "还没有战绩" in txt, txt)
        check("空战绩 → 头部不谎称「进行中」", "进行中" not in info, info)

        # 会话时间读不到（history.json 刚被清掉/还没建）也不能显示「开局 ?」
        info = p.info.cget("text")
        show({"session": None, "records": [], "games": [], "placement": None})
        info = p.info.cget("text")
        check("没有会话时间 → 头部只有「尚未结算」，不出现「开局 ?」",
              "尚未结算" in info and "?" not in info, info)

        # --- 一条进行中 ---
        txt = show({"session": "2026-10-18 15:15:07", "records": [],
                    "games": [game(hp=None)], "placement": None})
        info = p.info.cget("text")
        hero_name = HIS.hero_name(HERO_A)[0]
        check("一条进行中 → 头部「本局进行中」", "本局进行中" in info, info)
        check("一条进行中 → 头部「已完成 0 局」", "已完成 0 局" in info, info)
        check("进行中的记录也显示主战者名字", hero_name in txt, txt)
        check("进行中的行里没有「进行中/已出局」状态文字",
              "进行中" not in txt and "已出局" not in txt, txt)

        # --- 一条已结算 + 一条进行中 ---
        settled = game(t="15:15:07", hero=HERO_A, hp=-5, rank=3)
        pending = game(t="15:21:00", hero=HERO_B, hp=None)
        txt = show({"session": "2026-10-18 15:15:07", "records": [],
                    "games": [settled, pending], "placement": None})
        info = p.info.cget("text")
        check("已结算 + 进行中 → 头部「已完成 1 局」", "已完成 1 局" in info, info)
        check("已结算 + 进行中 → 头部「本局进行中」", "本局进行中" in info, info)
        check("名次「第3名」出现在面板上", "第3名" in txt, txt)
        check("不显示血量数值",
              "-5" not in txt and "血" not in txt, txt)

        # 名次在名字后面、时间在行末
        hero_row = first_hero_row()
        labels = [str(c.cget("text")) for c in hero_row.winfo_children()
                  if isinstance(c, tk.Label)]
        check("行内顺序：序号 → 主战者 → 名次",
              len(labels) >= 3 and labels[1] == hero_name
              and labels[2] == "第3名", str(labels))
        check("时间在行末（同一行最后一个标签）",
              labels and "15:15:07" in labels[-1], str(labels))

        # 设置里关时间 → 立即不显示
        p._apply_live_settings({"show_time": False, "locked": False})
        txt = body_text()
        check("设置里关「显示时间」→ 立即不显示时间",
              "15:15:07" not in txt, txt)
        p._apply_live_settings({"show_time": True, "locked": False})
        check("重新打开「显示时间」→ 时间回来",
              "15:15:07" in body_text())

        # --- ↻ 手动重置 ---
        p.force_refresh()
        check("点 ↻ → 头部「已重置 · 等待新战绩」",
              p.info.cget("text") == "已重置 · 等待新战绩",
              p.info.cget("text"))
        check("点 ↻ → 页面清空", len(p.body.winfo_children()) == 0,
              str(len(p.body.winfo_children())))
        p.refresh()
        check("↻ 之后数据没变 → 仍然保持重置状态",
              p.info.cget("text") == "已重置 · 等待新战绩"
              and len(p.body.winfo_children()) == 0)
        d = read_hist()
        d["games"].append(game(t="15:30:00", hero=HERO_A, hp=-9, rank=1))
        write_hist(d)
        p.refresh()
        check("新战绩写入 → 自动恢复显示",
              "已完成 2 局" in p.info.cget("text")
              and len(p.body.winfo_children()) > 0, p.info.cget("text"))

        # --- 已输的局不出现在「进行中」 ---
        write_hist({"session": "2026-10-18 15:15:07", "records": [],
                    "games": [game(t="15:40:00", hero=HERO_B, hp=None)],
                    "placement": None})
        HIS.finish_game(999, 4, -8, [])           # id 读错，仍要回填
        p.last_sig = None
        p._page_cleared = False
        p.refresh()
        info = p.info.cget("text")
        txt = body_text()
        check("出局后（id 读错）→ 头部不再显示进行中",
              "进行中" not in info and "本局第4名" in info, info)
        check("出局后 → 「已完成 1 局」且只有一条记录",
              "已完成 1 局" in info
              and len([w for w in p.body.winfo_children()
                       if isinstance(w, tk.Frame)]) == 1, info)

        section("panel_ui.py · 锁定 ↔ 设置文件 / 悬停解锁")
        # 面板 → 文件
        p.locked = False
        p._hover_lock = False
        p._ct_on = None
        p._sync_clickthrough(force=True)
        p._toggle_lock()
        st = json.load(open(PUI.PANEL_SET, encoding="utf-8"))
        check("面板点锁定 → panel-settings.json.locked=True",
              st.get("locked") is True, str(st))
        check("锁定时窗口真的点击穿透（WS_EX_TRANSPARENT）", ex_transparent())

        # 文件 → 面板（设置对话框那条路）
        p._apply_live_settings({"show_time": True, "locked": False})
        check("设置里解锁 → 面板跟着解锁",
              p.locked is False and not ex_transparent())
        p._apply_live_settings({"show_time": True, "locked": True})
        check("设置里锁定 → 面板跟着锁定",
              p.locked is True and ex_transparent())

        # 缺 locked 键不能强行解锁
        p._apply_live_settings({"show_time": True, "alpha": 0.8})
        check("设置文件缺 locked 键 → 不改锁定状态",
              p.locked is True and ex_transparent())

        # 悬停锁按钮 → 临时取消穿透
        rect = p._widget_rect_physical(p.lock_btn)
        check("锁按钮能算出有效的屏幕物理矩形",
              bool(rect) and rect[2] > rect[0] and rect[3] > rect[1],
              str(rect))
        p._cursor_pos = lambda: (-10000, -10000)
        p._poll_cursor()
        check("锁定 + 光标在按钮外 → 保持点击穿透", ex_transparent())
        cx = int((rect[0] + rect[2]) // 2)
        cy = int((rect[1] + rect[3]) // 2)
        p._cursor_pos = lambda: (cx, cy)
        p._poll_cursor()
        check("锁定 + 光标停在锁按钮上 → 临时取消穿透（能点）",
              (not ex_transparent()) and p._hover_lock)
        p.lock_btn.invoke()
        check("此时点锁按钮 → 真的解开了",
              p.locked is False and not ex_transparent())
        st = json.load(open(PUI.PANEL_SET, encoding="utf-8"))
        check("解锁后设置文件同步 locked=False", st.get("locked") is False,
              str(st))
        p._cursor_pos = lambda: (cx, cy)
        p._poll_cursor()
        check("解锁状态下光标在按钮上也不影响交互（不穿透）",
              not ex_transparent())
        p._cursor_pos = lambda: (-10000, -10000)
        p._poll_cursor()

        # 锁定后再用悬停解开（完整走一遍用户操作）
        p._toggle_lock()
        p._cursor_pos = lambda: (-10000, -10000)
        p._poll_cursor()
        check("再次锁定 → 穿透", p.locked is True and ex_transparent())
        p._cursor_pos = lambda: (cx, cy)
        p._poll_cursor()
        p.lock_btn.invoke()
        check("锁定 → 悬停 → 点一下 → 再次解开",
              p.locked is False and not ex_transparent())

        # 拖动钳制：不能把面板拖出屏幕（旧坐标/换分辨率后也不会消失）
        class _E:
            x_root = 0
            y_root = 0

        p.pos = [0, 0]
        p._drag = (0, 0, 0, 0)
        e = _E()
        e.x_root, e.y_root = 100000, 100000
        p._on_drag(e)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        check("拖动不会把面板拖出屏幕（位置始终在屏内）",
              0 <= p.pos[0] <= max(0, sw - PUI.WIN_W)
              and 0 <= p.pos[1] <= max(0, sh - PUI.WIN_H), str(p.pos))
        p._drag = None

        # 设置文件不存在时（全新安装）写回不能只剩 locked 一个键
        try:
            os.remove(PUI.PANEL_SET)
        except OSError:
            pass
        p._write_settings_locked(True)
        st = json.load(open(PUI.PANEL_SET, encoding="utf-8"))
        check("全新安装（没有设置文件）→ 写回 locked 时保留其它设置",
              st.get("locked") is True and "alpha" in st
              and "show_time" in st and "font_size" in st, str(st))
        p._write_settings_locked(False)
        root.update_idletasks()
    finally:
        try:
            root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ui", action="store_true", help="只测 history.py")
    args = ap.parse_args()

    print(f"临时目录：{TMP}")
    print(f"只读参考：{REAL_HIST}")
    try:
        test_history()
        if args.no_ui:
            print("\n（--no-ui：跳过 panel_ui 测试）")
        else:
            try:
                test_ui()
            except Exception:
                traceback.print_exc()
                check("panel_ui 测试整体跑通", False, "见上面的异常栈")
    finally:
        failed = [r for r in RESULTS if not r[1]]
        print("\n================ 结果 ================")
        print(f"通过 {len(RESULTS) - len(failed)} / {len(RESULTS)}")
        if failed:
            print("失败项：")
            for name, _ok, detail in failed:
                print(f"  · {name}" + (f"  ← {detail}" if detail else ""))
        else:
            # 全部通过才清理临时目录；失败时保留方便排查
            shutil.rmtree(TMP, ignore_errors=True)
        print("=====================================")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
