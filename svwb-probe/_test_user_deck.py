#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记牌器卡组兜底自测（多套）。

用 SVWB_LIVE 指向临时文件，不碰正在跑的 logs/deck_live.json。
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LIVE = os.path.join(tempfile.gettempdir(), "svwb_live_selftest.json")
os.environ["SVWB_LIVE"] = LIVE


def write_live(hand, played=(), deck=34):
    obj = {"addr": "0x0", "deck": deck, "life": 20, "mulliganDone": True,
           "turn": 2, "hand": list(hand), "field": [], "played": list(played),
           "destroyed": [], "at": int(time.time() * 1000)}
    with open(LIVE, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def main():
    import deck_reader
    ok = [True]

    def check(name, cond, extra=""):
        if not cond:
            ok[0] = False
        print(("  PASS " if cond else "  FAIL ") + name, extra)

    try:
        # 1) 龙族手牌 → 选中龙族套
        write_live([10403120, 10741111, 10741110, 10042310], [10042310])
        r = deck_reader.snapshot()
        rows = {cid: (n, left, hand)
                for cid, n, left, hand in (r.get("rows") or [])}
        check("龙族：牌库基数 40", r.get("total") == 40, r.get("total"))
        check("龙族：rows = 17 种", len(rows) == 17, len(rows))
        check("露莉亚：3 张、手牌 1 → 剩 2",
              rows.get(10403120) == (3, 2, 1), rows.get(10403120))
        check("宣扬的龙人：两个版本 id 都算 → 剩 1",
              rows.get(10741111) == (3, 1, 2), rows.get(10741111))
        check("龙之启示：手牌+已打出 2 → 剩 1",
              rows.get(10042310) == (3, 1, 1), rows.get(10042310))

        # 2) 皇家手牌 → 对不上任何导入套 → 降级
        write_live([10522311, 10524111, 10622310, 10623110])
        r = deck_reader.snapshot()
        check("皇家套未导入：降级到 active 卡组",
              r.get("total") == 40, r.get("total"))
        check("皇家套未导入：rows 非空（用兜底卡组）",
              len(r.get("rows") or []) > 0, len(r.get("rows") or []))
        check("皇家套未导入：手牌仍然返回",
              len(r.get("hand") or []) == 4, len(r.get("hand") or []))

        # 3) 开局无手牌 → 用第一套
        write_live([])
        r = deck_reader.snapshot()
        check("空手牌：回落到第一套（40 张）", r.get("total") == 40,
              r.get("total"))
    finally:
        try:
            os.remove(LIVE)
        except Exception:
            pass

    print("结果：", "全部通过" if ok[0] else "有失败")
    return 0 if ok[0] else 1


if __name__ == "__main__":
    sys.exit(main())
