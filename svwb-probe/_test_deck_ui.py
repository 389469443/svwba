#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡组 UI 逻辑离线自测：切换当前 / 重命名 / 选套回退（不弹窗）。"""
import json
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DECK_FILE = os.path.join(HERE, "gamedata", "user_decks.json")

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
    live = os.path.join(tempfile.gettempdir(), "svwb_deckui_test.json")
    os.environ["SVWB_LIVE"] = live
    import deck_reader
    import app as appmod

    bak = None
    if os.path.exists(DECK_FILE):
        with open(DECK_FILE, "rb") as fh:
            bak = fh.read()
    try:
        with open(DECK_FILE, "w", encoding="utf-8") as fh:
            json.dump({"decks": [
                {"name": "A", "cards": [11032310] * 40},
                {"name": "B", "cards": [10731120] * 40},
            ], "active": "A"}, fh, ensure_ascii=False)

        class Fake:
            pass

        f = Fake()
        f._decks_path = types.MethodType(appmod.App._decks_path, f)
        f._refresh_deck_status = types.MethodType(lambda self: None, f)
        f._save_active_deck = types.MethodType(
            appmod.App._save_active_deck, f)
        f._rename_deck = types.MethodType(appmod.App._rename_deck, f)

        f._save_active_deck("B")
        d = json.load(open(DECK_FILE, encoding="utf-8"))
        check("切换当前 = B", d.get("active") == "B", d.get("active"))

        f._rename_deck("B", "B-改名")
        d = json.load(open(DECK_FILE, encoding="utf-8"))
        names = [x["name"] for x in d["decks"]]
        check("重命名生效", "B-改名" in names, names)
        check("active 跟随重命名", d.get("active") == "B-改名",
              d.get("active"))
        check("deck_reader 读到 active", deck_reader._active_name() == "B-改名",
              deck_reader._active_name())

        def write_live(hand):
            obj = {"addr": "0x0", "deck": 30, "life": 20, "hand": list(hand),
                   "played": [], "destroyed": [],
                   "at": int(time.time() * 1000)}
            with open(live, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False)

        deck_reader._CACHE["deck_ids"] = []
        write_live([11032310] * 3)
        r = deck_reader.snapshot()
        ids = {c for c, _, _, _ in (r.get("rows") or [])}
        check("当前卡组对不上 → 回退到匹配的 A",
              r.get("total") == 40 and 11032310 in ids, r.get("total"))

        deck_reader._CACHE["deck_ids"] = []
        write_live([10731120] * 3)
        r = deck_reader.snapshot()
        ids = {c for c, _, _, _ in (r.get("rows") or [])}
        check("当前卡组匹配 → 选 B-改名",
              r.get("total") == 40 and 10731120 in ids, r.get("total"))
    finally:
        if bak is None:
            try:
                os.remove(DECK_FILE)
            except OSError:
                pass
        else:
            with open(DECK_FILE, "wb") as fh:
                fh.write(bak)
        try:
            os.remove(live)
        except OSError:
            pass

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
