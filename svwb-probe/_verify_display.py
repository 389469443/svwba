#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
显示层自检（不碰线上 feed / 不改 history.json）

覆盖：
  A. overlay.build_dist_text 的健壮性（空 / 坏结构 / 字符串数字 / 超长分布）
  B. Overlay.render 的各种输入：完整战果、0% 战果、win=null、坏 damageDist
     —— 断言每一帧都有可见文字，绝不空白
  C. panel_ui.Panel 渲染 games（有名次 / 名次未知 / 空阵容 / 长备注）

用法：
  <python> -u _verify_display.py
  <python> -u _verify_display.py --real-history   # 额外用真实 history.json 渲染一遍
"""
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing", "tools")
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

os.environ["PANEL_OPAQUE"] = "1"      # 测试用不透明窗口，避免抠色影响

import overlay as OV                  # noqa: E402
import panel_ui as PU                 # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  | " + extra) if extra else ""))
    if not cond:
        FAILS.append(name + (" | " + extra if extra else ""))


def all_text(widget):
    """递归收集控件文字（Entry 的 cget('text') 会命中 textvariable，跳过）"""
    out = []
    try:
        if widget.winfo_class() != "Entry":
            out.append(str(widget.cget("text")))
    except Exception:
        pass
    for c in widget.winfo_children():
        out.extend(all_text(c))
    return out


# ---------------------------------------------------------------- A
def test_dist():
    print("\n[A] build_dist_text")
    check("空 dict → 空串", OV.build_dist_text({}) == "")
    check("空 damageDist + detail → 用 detail",
          OV.build_dist_text({"damageDist": {}, "detail": "双方空场 · 平局"}) == "双方空场 · 平局")
    check("坏 damageDist（字符串）不抛异常",
          OV.build_dist_text({"damageDist": "oops"}) == "")

    payload = {"damageDist": {
        "toPlayer": [{"damage": d, "pPercent": p} for d, p in
                     [(6, 25.0), (7, 32.0), (8, 22.0), (9, 10.5), (10, 6.0), (11, 4.5)]],
        "toOpponent": [{"damage": 3, "pPercent": 100.0}]}}
    txt = OV.build_dist_text(payload)
    print("      " + txt.replace("\n", "\n      "))
    check("三个百分比都在", all(x in txt for x in ("6点 25%", "7点 32%", "8点 22%")))
    check("范围 6~11 在", "（范围 6~11 点）" in txt)
    check("我方在敌方之前", txt.index("我方掉血") < txt.index("敌方掉血"))
    check("单一伤害不写范围", "范围" not in OV.build_dist_text(
        {"damageDist": {"toPlayer": [{"damage": 7, "pPercent": 100}]}}))

    # 字符串数字 / 元组 / 缺 pPercent
    t2 = OV.build_dist_text({"damageDist": {"toPlayer": [
        {"damage": "9", "pPercent": "12.5"}, [7, 30], {"value": 8}]}})
    print("      " + t2.replace("\n", "\n      "))
    check("字符串/元组/缺字段都能渲染",
          "7点 30%" in t2 and "8点 0%" in t2 and "9点 12.5%" in t2)

    # 超长分布：截断但保留首尾，且给出范围
    rows = [{"damage": i, "pPercent": 100.0 / (abs(i - 12) + 1)} for i in range(1, 31)]
    t3 = OV.build_dist_text({"damageDist": {"toPlayer": rows}}, limit=8)
    print("      " + t3.replace("\n", "\n      "))
    check("超长分布保留最小值 1 点", "1点" in t3)
    check("超长分布保留最大值 30 点", "30点" in t3)
    check("超长分布仍给范围", "（范围 1~30 点）" in t3)
    check("超长分布被截断（<=8 项 + 省略号）", t3.count("点 ") <= 8 and "…" in t3)


# ---------------------------------------------------------------- B
def test_overlay_render():
    print("\n[B] Overlay.render（离屏窗口）")
    tmpdir = tempfile.mkdtemp(prefix="ov_test_")
    state = os.path.join(tmpdir, "overlay.json")
    OV.STATE = state
    ov = None
    try:
        ov = OV.Overlay(pos=(-3200, -3200), demo=False)
        ov.root.withdraw()

        def render_and_get(payload, write=True):
            if write:
                with open(state, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
            ov._last_mtime = 0
            ov._tick()
            return ov.line.cget("text"), ov.detail.cget("text")

        # 完整战果（含 0 值：0 不能被当成"没有结果"）
        line, detail = render_and_get({
            "phase": "battle", "win": 0, "loss": 100, "draw": 0,
            "damageDist": {"toPlayer": [{"damage": 26, "pPercent": 23.0},
                                        {"damage": 28, "pPercent": 13.0},
                                        {"damage": 30, "pPercent": 64.0}]}})
        print("      line=%r" % line)
        print("      detail=%r" % detail)
        check("0% 胜率照常显示（三率齐全）",
              all(x in line for x in ("胜 0%", "败 100%", "平 0%")))
        check("伤害分布 + 范围都在",
              "26点 23%" in detail and "（范围 26~30 点）" in detail)

        # 0 值是合法结果：不能变成"读取中"
        line, _ = render_and_get({"phase": "battle", "win": 0, "loss": 0, "draw": 100})
        check("0/0/100 也当结果", "胜 0%" in line and "平 100%" in line)

        # 完整战果但没有 damageDist
        line, detail = render_and_get({"phase": "battle", "win": 62, "loss": 35, "draw": 3})
        check("无分布时有可见占位", bool(detail.strip()))
        check("无分布不影响胜率行", "胜 62%" in line)

        # 坏 damageDist：不能抛异常、不能空白
        line, detail = render_and_get({"phase": "battle", "win": 42, "loss": 50, "draw": 8,
                                       "damageDist": {"toPlayer": [{"x": 1}]}})
        check("坏 damageDist 仍有文字", "胜 42%" in line and bool(detail.strip()))

        # 运营阶段
        line, detail = render_and_get({"phase": "operate", "win": None})
        check("运营阶段提示文案", "运营" in line and bool(detail.strip()) is False)

        # 战斗阶段但结果未出（win=null）
        line, _ = render_and_get({"phase": "battle", "win": None})
        check("battle+win=null 显示读取中", "读取中" in line)

        # 静态 feed（判定器不再写入）也不能永远卡在"读取中"：
        # 把等待起点拨回 16 秒，再走一次 _tick（会周期性重绘同一份数据）
        ov._wait_since = time.time() - 16
        ov._last_render_at = 0
        ov._tick()
        line = ov.line.cget("text")
        check("15s 后仍显示读取中并提示可能失败",
              "读取中" in line and "15 秒" in line, line)

        # 其它阶段 → 默认文案（"一场"，不是"一把"）
        line, _ = render_and_get({"phase": "other", "win": None})
        check("其它阶段显示默认文案", line == "快来一场紧张刺激的影之幻境喵", line)
        check("默认文案是『一场』不是『一把』", "一把" not in line)

        # 空文件 / 数据不是 dict → 默认文案
        with open(state, "w", encoding="utf-8") as fh:
            fh.write("null")
        ov._last_mtime = 0
        ov._tick()
        check("feed 为 null → 默认文案",
              ov.line.cget("text") == "快来一场紧张刺激的影之幻境喵",
              ov.line.cget("text"))

        # feed 文件不存在 → 默认文案
        os.remove(state)
        ov._last_mtime = 0
        ov._rendered_once = False
        ov._tick()
        check("feed 不存在 → 默认文案",
              ov.line.cget("text") == "快来一场紧张刺激的影之幻境喵",
              ov.line.cget("text"))
        print("      最后一行=%r" % ov.line.cget("text"))
    finally:
        if ov is not None:
            try:
                ov.root.destroy()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- D
CARD = {
    "龙族": ["飞跃的银白幼龙", "烈焰火蜥蜴", "侮蔑的肯定者", "育龙少女", "炽炎幼龙"],
    "精灵": ["纯真的水之妖精", "妖精", "妖精剑的后继者"],
    "中立": ["挥毫的怪物", "亲爱的搭档·碧", "叮当天使·莉亚"],
    "皇家护卫": ["勇猛的鲁米那斯枪士", "骑士", "统率的鲁米那斯骑士"],
    "梦魇": ["魅惑的魅魔·莉莉姆", "蝙蝠"],
}


def board(*names):
    return [{"name": n, "star": 1, "attack": 1, "health": 1} for n in names]


def test_autofill():
    print("\n[D] note_line1 自动填充（种族规则）")
    m1 = PU.load_tribe_map()
    m2 = PU.load_tribe_map()
    check("cards.json 缓存生效（同一对象，不重复读盘）", m1 is m2)
    check("cards.json 已加载（>=80 张）", len(m1) >= 80, str(len(m1)))

    cases = [
        ("3龙+1中 → 龙族",
         board(*CARD["龙族"][:3], CARD["中立"][0]), "龙族"),
        ("2龙+2精+3中 → 混合随从",
         board(*CARD["龙族"][:2], *CARD["精灵"][:2], *CARD["中立"][:3]), "混合随从"),
        ("3精+2龙 → 精灵",
         board(*CARD["精灵"][:3], *CARD["龙族"][:2]), "精灵"),
        ("3精+3龙 同数量 → 精灵（优先级）",
         board(*CARD["精灵"][:3], *CARD["龙族"][:3]), "精灵"),
        ("全部卡名查不到 → 留空", board("不存在的随从A", "不存在的随从B"), ""),
        ("空阵容 → 留空", [], ""),
    ]
    for name, bd, want in cases:
        got = PU.auto_line1_from_board(bd)
        check(name, got == want, repr(got))

    # 面板实际写回：只在 note_line1 为空、且其它字段不动
    tmpdir = tempfile.mkdtemp(prefix="pn_auto_")
    hist = os.path.join(tmpdir, "history.json")
    games = [
        {"t": "a", "rank": 3, "hero": 1104, "hp": 0,
         "board": board(*CARD["龙族"][:3], CARD["中立"][0]),
         "note": "n1", "note_line1": "", "note_line2": ""},
        {"t": "b", "rank": None, "hero": 1104, "hp": 0,
         "board": board(*CARD["龙族"][:2], *CARD["精灵"][:2], *CARD["中立"][:3]),
         "note": "n2", "note_line1": "", "note_line2": ""},
        {"t": "c", "rank": None, "hero": 1104, "hp": 0,
         "board": board(*CARD["精灵"][:3], *CARD["龙族"][:2]),
         "note": "n3", "note_line1": "", "note_line2": ""},
        {"t": "d", "rank": 1, "hero": 1104, "hp": 20,
         "board": board(*CARD["龙族"][:3], CARD["中立"][0]),
         "note": "n4", "note_line1": "手写的阵容不要动", "note_line2": "手写原因"},
        {"t": "e", "rank": None, "hero": 1104, "hp": 0,
         "board": board("未知甲", "未知乙"), "note": "n5",
         "note_line1": "", "note_line2": ""},
    ]
    with open(hist, "w", encoding="utf-8") as fh:
        json.dump({"session": "x", "records": [], "games": games}, fh,
                  ensure_ascii=False, indent=1)
    PU.HIST = hist
    import history as HIS
    HIS.HIST = hist
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    panel = None
    try:
        panel = PU.Panel(root)
        root.update_idletasks()
        with open(hist, encoding="utf-8") as fh:
            saved = json.load(fh)["games"]
        print("      面板打开后的 note_line1（应保持全空）：",
              [g.get("note_line1") for g in saved])
        # 新版：自动阵容只显示，不写 history.json
        check("3龙+1中 → 自动阵容只显示、不写盘", saved[0].get("note_line1") == "")
        check("2龙+2精+3中 → 不写盘", saved[1].get("note_line1") == "")
        check("3精+2龙 → 不写盘", saved[2].get("note_line1") == "")
        check("用户手写流派保持不动",
              saved[3].get("note_line1") == "手写的阵容不要动")
        check("手写记录的失败原因也没被改",
              saved[3].get("note_line2") == "手写原因")
        check("全查不到 → 仍留空", saved[4].get("note_line1") == "")
        joined = "\n".join(all_text(panel.body))
        check("标签显示『流派』", "流派" in joined)
        check("标签显示『备注』", "备注" in joined)
        check("主战者行显示自动阵容『龙族』", "龙族" in joined)
        check("自动阵容也能显示『混合随从』", "混合随从" in joined)
        check("自动阵容也能显示『精灵』", "精灵" in joined)
        check("主战者名字后没拼种族（无『罗文·』）", "罗文·" not in joined)

        # 手写流派后强制重建：内容不被自动值覆盖
        if panel._note_pairs:
            pair = panel._note_pairs[0]          # (idx0, entry1, entry2, g)
            e1 = pair[1]
            e1.delete(0, "end")
            e1.insert(0, "精灵龙体系")
            panel._save_game_notes(*pair)
            panel.last_sig = None                # 强制重建
            panel.refresh()
            with open(hist, encoding="utf-8") as fh:
                saved2 = json.load(fh)["games"]
            check("手写流派后写回 history.json",
                  saved2[0].get("note_line1") == "精灵龙体系")
            check("重建后输入框仍显示手写流派",
                  panel._note_entries[0].get() == "精灵龙体系")

    finally:
        if panel is not None:
            try:
                root.destroy()
            except Exception:
                pass
    with open(hist, "w", encoding="utf-8") as fh:
        json.dump({"session": "x", "records": [], "games": [
            {"t": "z", "rank": None, "hero": 1104, "hp": 0,
             "board": board("未知甲", "未知乙"), "note": "",
             "note_line1": "", "note_line2": ""}]}, fh, ensure_ascii=False)
    root2 = tk.Tk()
    root2.withdraw()
    try:
        p2 = PU.Panel(root2)
        root2.update_idletasks()
        joined2 = "\n".join(all_text(p2.body))
        check("查不到时【不显示】自动阵容（无『混合随从』占位）",
              "混合随从" not in joined2 and "龙族" not in joined2
              and "精灵" not in joined2)
    finally:
        try:
            root2.destroy()
        except Exception:
            pass
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- C
def test_panel(real_history=False):
    print("\n[C] panel_ui.Panel 渲染 games（离屏窗口）")
    tmpdir = tempfile.mkdtemp(prefix="pn_test_")
    hist = os.path.join(tmpdir, "history.json")
    data = {
        "session": "2026-10-17 23:18:30",
        "records": [{"t": "23:18:30", "me_hero": 1104, "op_hero": 1007,
                     "me_lv": 2, "me_hp": 40, "me_board": [],
                     "op_lv": 2, "op_hp": 40, "op_board": [],
                     "win": 0, "loss": 0, "draw": 100}],
        "games": [
            {"t": "23:25:44", "rank": 3, "hero": 100000, "hp": 0,
             "board": [{"star": 4, "attack": 5, "health": 4, "name": "煌击战士·贝鲁"}],
             "note": "血量归零",
             "note_line1": "罗文 6级 5血 精灵龙体系",
             "note_line2": "对面 3 个 6 星，等级落后"},
            {"t": "23:32:30", "rank": None, "hero": 1104, "hp": 0,
             "board": [{"star": 5, "attack": 13, "health": 10, "name": "未知随从甲"},
                       {"star": 6, "attack": 18, "health": 13, "name": "未知随从乙"}],
             "note": "来源 内存判定-20261017-232839.txt：5血，预测掉血26~30点；名次无法确认",
             "note_line1": "",
             "note_line2": ""},
        ],
    }
    with open(hist, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    PU.HIST = hist
    import history as HIS
    HIS.HIST = hist          # 面板写回用的是 history 模块的 HIST
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    panel = None
    try:
        panel = PU.Panel(root)
        root.update_idletasks()
        texts = [t for t in all_text(panel.body) if t.strip()]
        print("      控件文字：")
        for t in texts:
            print("        " + t.replace("\n", "\\n"))
        joined = "\n".join(texts)
        check("显示第 3 名（rank=3）", "第3名" in joined)
        check("名次未知显示『名次未记录』", "名次未记录" in joined)
        check("显示英雄名（英雄表查询）", "龙杯娘" in joined and "罗文" in joined)
        check("主战者后面不拼种族（无『·中立』『·龙族』后缀）",
              "·中立" not in joined and "·龙族" not in joined)
        check("主战者行显示自动阵容（混合随从）", "混合随从" in joined)
        check("显示血量", "血量0" in joined)
        check("不再显示带星级的阵容明细（只留种族名）",
              "★13/10" not in joined and "★15/10" not in joined)
        check("长备注不显示在面板里（用户要求）",
              "内存判定" not in joined and "名次无法确认" not in joined)
        check("数据里的 note 仍然保留（供排查/后续使用）",
              any((g.get("note") or "").strip() for g in data["games"]))
        check("头部『已完成 2 局』", "已完成 2 局" in panel.info.cget("text"))
        check("头部不把名次未知说成『本局进行中』",
              "本局进行中" not in panel.info.cget("text"))

        # ---- 两行可输入备注 ----
        ents = panel._note_entries
        check("标签是『流派』和『备注』",
              "流派" in joined and "备注" in joined)
        check("两局共 4 个备注输入框", len(ents) == 4, str(len(ents)))
        check("第1局『流派』显示已存内容",
              len(ents) >= 2 and ents[0].get() == "罗文 6级 5血 精灵龙体系")
        check("第1局『备注』显示已存内容",
              len(ents) >= 2 and ents[1].get() == "对面 3 个 6 星，等级落后")
        check("第2局空字段 → 空输入框",
              len(ents) >= 4 and ents[2].get() == "" and ents[3].get() == "")
        check("未锁定时输入框可编辑", len(ents) >= 1 and ents[0].cget("state") == "normal")
        panel.locked = True
        panel._apply_note_state()
        check("锁定时输入框只读", len(ents) >= 1 and ents[0].cget("state") == "readonly")
        panel.locked = False
        panel._apply_note_state()
        check("解锁后恢复可编辑", len(ents) >= 1 and ents[0].cget("state") == "normal")

        # 写回验证：改内容 → 调保存 → history.json 真的更新
        if len(ents) >= 4:
            ents[0].delete(0, "end")
            ents[0].insert(0, "跨海 7级 龙族 5张")
            ents[1].delete(0, "end")
            ents[1].insert(0, "对面两个三星六费")
            panel._save_game_notes(0, ents[0], ents[1], data["games"][0])
            with open(hist, encoding="utf-8") as fh:
                saved = json.load(fh)
            check("写回 history.json（流派）",
                  saved["games"][0].get("note_line1") == "跨海 7级 龙族 5张")
            check("写回 history.json（失败原因）",
                  saved["games"][0].get("note_line2") == "对面两个三星六费")
            check("写回后旧 note 字段仍在",
                  bool(saved["games"][0].get("note")))

        # 运行中新增一局：sig 必须变化 → 不重启也能刷出来
        data["games"].append({"t": "23:40:00", "rank": 1, "hero": 1104, "hp": 12,
                              "board": [{"star": 6, "attack": 20, "health": 20,
                                         "name": "测试冠军"}],
                              "note": "新增对局刷新测试"})
        with open(hist, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        panel.refresh()
        root.update_idletasks()
        texts2 = "\n".join(all_text(panel.body))
        check("新增一局后自动刷新（无需重启）",
              "第1名" in texts2 and "已完成 3 局" in panel.info.cget("text"))
        check("新增后头部局数变成 3", "已完成 3 局" in panel.info.cget("text"))
    finally:
        if panel is not None:
            try:
                root.destroy()
            except Exception:
                pass

    # 空 games / 无数据
    with open(hist, "w", encoding="utf-8") as fh:
        json.dump({"session": None, "records": []}, fh)
    root2 = tk.Tk()
    root2.withdraw()
    try:
        p2 = PU.Panel(root2)
        root2.update_idletasks()
        t2 = "\n".join(all_text(p2.body))
        print("      空数据文字：%r" % t2.strip())
        check("空数据也有可见提示", bool(t2.strip()))
    finally:
        try:
            root2.destroy()
        except Exception:
            pass

    # 真实 history.json（只读拷贝到临时目录，避免改动）
    if real_history:
        real = os.path.join(HERE, "logs", "history.json")
        if os.path.isfile(real):
            shutil.copyfile(real, hist)
            root3 = tk.Tk()
            root3.withdraw()
            try:
                p3 = PU.Panel(root3)
                root3.update_idletasks()
                t3 = "\n".join(all_text(p3.body))
                print("      真实 history 渲染 OK，%d 字" % len(t3))
                check("真实 history.json 渲染无异常", bool(t3.strip()))
                if len(p3._note_entries) >= 2:
                    with open(hist, encoding="utf-8") as fh:
                        real_data = json.load(fh)
                    g0 = real_data["games"][0]
                    e1, e2 = p3._note_entries[0], p3._note_entries[1]
                    e1.delete(0, "end")
                    e1.insert(0, "真实数据写回测试")
                    e2.delete(0, "end")
                    e2.insert(0, "测试失败原因")
                    p3._save_game_notes(0, e1, e2, g0)
                    with open(hist, encoding="utf-8") as fh:
                        back = json.load(fh)
                    check("真实 history 副本：写回『阵容』",
                          back["games"][0].get("note_line1") == "真实数据写回测试")
                    check("真实 history 副本：写回『失败原因』",
                          back["games"][0].get("note_line2") == "测试失败原因")
                    check("真实 history 副本：旧 note 未丢",
                          bool(back["games"][0].get("note")))
            finally:
                try:
                    root3.destroy()
                except Exception:
                    pass

    shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=== 显示层自检 ===")
    try:
        test_dist()
        test_overlay_render()
        test_autofill()
        test_panel(real_history=("--real-history" in sys.argv))
    except Exception:
        traceback.print_exc()
        FAILS.append("未捕获异常: " + traceback.format_exc().splitlines()[-1])
    print("\n=== 结果：%d 项失败 ===" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
