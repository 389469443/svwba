#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记牌器顶栏渲染自测：牌库剩余 / 手牌 / 爆牌 + 洗回红框。"""
import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deck_tracker  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def main():
    root = tk.Tk()
    root.withdraw()
    dt = deck_tracker.DeckTracker(root)
    try:
        snap = {
            "in_battle": True, "deckCount": 25,
            "hand": [11032310, 10731120, 10543310],
            "played": [], "destroyed": [],
            "rows": [(11032310, 3, 2, 1), (10731120, 3, 3, 0)],
            "total": 40, "overdraw": 2, "returned": True,
        }
        dt.render(snap)
        root.update()
        txt = dt.status_text.cget("text")
        mark = dt.status_mark.cget("text")
        check("顶栏只显示剩余/手牌（不显示爆牌数量）",
              txt == "牌库剩余 25 · 手牌 3", txt)
        check("洗回显示红框", mark == "【洗回】", mark)

        # 爆牌区：有 overdraw_cards 时显示在「手牌中」下方
        snap2 = dict(snap)
        snap2["overdraw_cards"] = [10734110, 10734110, 11032310]
        dt.render(snap2)
        root.update()
        heads = [w.cget("text") for w in dt.inner.winfo_children()
                 if isinstance(w, tk.Label)]
        check("爆牌区显示在手牌下方", "爆牌中" in heads, heads)

        # 手牌区：应能看到「手牌中」标题和 3 行手牌
        headers = [w.cget("text") for w in dt.inner.winfo_children()
                   if isinstance(w, tk.Label)]
        check("有「手牌中」标题",
              any("手牌中" in str(t) for t in headers), headers)

        # 相同快照再渲染一次：不应重建（防卡名闪烁）——放在爆牌区渲染之前
        dt.render(dict(snap))
        root.update()
        before = [str(w) for w in dt.inner.winfo_children()]
        dt.render(dict(snap))
        root.update()
        after = [str(w) for w in dt.inner.winfo_children()]
        check("相同数据不重建（不闪烁）", before == after,
              (len(before), len(after)))

        snap["returned"] = False
        dt.render(snap)
        root.update()
        check("无洗回时红框清空", dt.status_mark.cget("text") == "")

        snap2 = {"in_battle": False, "reason": "未在对战中"}
        dt.render(snap2)
        root.update()
        check("非对局时显示当前卡组",
              "当前卡组" in dt.status_text.cget("text"),
              dt.status_text.cget("text"))
    finally:
        try:
            dt.close()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
