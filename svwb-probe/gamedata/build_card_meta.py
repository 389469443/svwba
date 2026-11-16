#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 gamedata/sv_card_meta.json：卡 id -> {name,cost,rarity,atk,life,skill,type,class}
（2026-10-21 扩展：悬停详情用）
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import parse_master_cards as PM

OUT = os.path.join(HERE, "sv_card_meta.json")


def main():
    tables, kinds, raw = PM.parse_tables(PM.DEFAULT_FILE)
    names = {}
    try:
        names = json.load(io.open(os.path.join(HERE, "sv_card_names.json"),
                                  encoding="utf-8"))
    except Exception:
        pass

    mtl = {}
    for r in tables.get("MasterTextLabel", []):
        if isinstance(r, list) and len(r) >= 2:
            mtl[r[0]] = r[1]

    ctext = {}
    for r in tables.get("CardText", []):
        if isinstance(r, list) and len(r) >= 3:
            ctext[int(r[0])] = r

    base = {}
    for r in tables.get("BaseCardMaster", []):
        if isinstance(r, list) and len(r) >= 9:
            base[int(r[0])] = r

    def tr(key):
        if not key:
            return ""
        return mtl.get(key, key)

    out = {}
    for r in tables.get("CardMaster", []):
        if not (isinstance(r, list) and len(r) >= 5):
            continue
        cid = int(r[0])
        bid = int(r[1])
        tid = int(r[4])
        b = base.get(bid, [])
        t = ctext.get(tid, [])
        skill_key = t[2] if len(t) > 2 else None
        out[str(cid)] = {
            "name": names.get(str(cid)),
            "cost": b[4] if len(b) > 4 else None,
            "rarity": b[8] if len(b) > 8 else None,
            "atk": b[5] if len(b) > 5 else None,
            "life": b[6] if len(b) > 6 else None,
            "type": b[1] if len(b) > 1 else None,
            "class": b[2] if len(b) > 2 else None,
            "skill": tr(skill_key),
        }

    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("cards:", len(out), "->", OUT)
    for cid in ("10501110", "10001110"):
        print(cid, out.get(cid))


if __name__ == "__main__":
    main()
