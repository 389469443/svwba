#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对手「获得纹章」表 —— 从卡表技能原文自动生成（数据层，暂不接界面）。

和 `foe_grants.py`（加手牌）是**两张独立的表**，互不影响：
那张记「哪张牌给了我一张牌」，这张记「哪张牌给了我一枚纹章」。

为什么单独一张表：纹章（type 5/6）压根不在记牌器读的那四个列表里
（手牌 / 战场 / 打出 / 墓地），所以现在**读不到**；这张表只是先在数据层
把对应关系备好，等以后要显示（或要读纹章本身）时直接用。

解析的四种时机：
    【入场曲】 / 【进化时】 / 【超进化时】 / 【谢幕曲】
    + 使自己获得『纹章：XX』（谢幕曲是用户 2026-11-11 追加的）

不认的写法（先不猜）：攻击时、自己的回合结束时……以及
「使自己的主战者获得『…』」（12 处，措辞不同）。
以后要扩就加一条正则分支，别把范围悄悄放大。

    python foe_marks.py            看汇总 + 几个样例
    python foe_marks.py 10114110   看某张牌在哪几个时机给了哪枚纹章
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "gamedata", "sv_card_meta.json")
NAMES = os.path.join(HERE, "gamedata", "sv_card_names.json")

TIMING_TXT = {"play": "入场曲", "evolve": "进化时", "super": "超进化时",
              "last": "谢幕曲"}

# 【时机】之后、下一个【之前，出现「使自己获得『纹章名』」才算。
# 允许中间夹别的字（“并…”），但不许跨过下一个【（那就换了技能段）。
_PAT = re.compile(r"【(超进化时|谢幕曲|入场曲|进化时)】((?:(?!【).)*?)"
                  r"使自己获得『([^』]+)』", re.S)

_CACHE = {"key": None, "table": {}, "names": {}}


def _strip(s):
    return re.sub(r"<[^>]+>", "", s or "")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _pick_id(cands):
    """同名纹章有多个 id（异画/镜像）。挑“本体”：末尾 0 → 10 开头 → 数字最小。"""
    def key(c):
        return (0 if str(c).endswith("0") else 1,
                0 if str(c).startswith("10") else 1,
                str(c))
    return sorted(cands, key=key)[0] if cands else None


def _build():
    meta = _load(META)
    names = _load(NAMES)
    by_name = {}
    for cid, nm in names.items():
        by_name.setdefault(nm, []).append(cid)
    table = {}
    for cid, m in meta.items():
        text = _strip((m or {}).get("skill"))
        if not text or "获得『" not in text:
            continue
        got = {}
        for hit in _PAT.finditer(text):
            when = {"入场曲": "play", "进化时": "evolve",
                    "超进化时": "super", "谢幕曲": "last"}[hit.group(1)]
            target = hit.group(3).strip()
            mid = _pick_id(by_name.get(target) or [])
            if not mid:
                continue
            mid = int(mid)                 # 和快照里的卡 id 统一成 int
            lst = got.setdefault(when, [])
            if mid not in lst:
                lst.append(mid)
        if got:
            table[str(cid)] = got
    _CACHE["table"] = table
    _CACHE["names"] = names
    return table


def table():
    """{打出的卡 id: {"play": [纹章 id], "evolve": [...], "super": [...]}}

    卡表文件改了（跑过 update_cardmeta.py）会自动重建。
    """
    try:
        key = (os.path.getmtime(META), os.path.getmtime(NAMES))
    except OSError:
        key = None
    if _CACHE["key"] != key or not _CACHE["table"]:
        _CACHE["key"] = key
        _build()
    return _CACHE["table"]


def marks(cid, timing="play"):
    """这张卡在这个时机给了哪几枚纹章 → [纹章 id]，没有就是空列表。"""
    try:
        ent = table().get(str(int(cid))) or {}
    except Exception:
        return []
    return list(ent.get(timing) or [])


def on_play(cid):
    return marks(cid, "play")


def on_evolve(cid):
    return marks(cid, "evolve") + marks(cid, "super")


def on_death(cid):
    """这张卡被破坏（进墓地）时给的纹章。"""
    return marks(cid, "last")


def name_of(cid):
    if not _CACHE.get("names"):
        table()                    # 名字表跟着 table 一起建，别让调用顺序影响结果
    return (_CACHE.get("names") or {}).get(str(cid)) or ""


def summary():
    t = table()
    out = {txt: sum(1 for v in t.values() if v.get(k))
           for k, txt in TIMING_TXT.items()}
    out["卡牌总数"] = len(t)
    return out


def _main(argv):
    if len(argv) > 1:
        cid = argv[1]
        print("%s（%s）" % (cid, name_of(int(cid)) or "查不到"))
        hit = table().get(str(int(cid))) or {}
        if not hit:
            print("  没有「获得纹章」的入场曲/进化时/超进化时效果")
            return 0
        for k, txt in TIMING_TXT.items():
            for mid in hit.get(k) or []:
                print("  【%s】获得 %s（%s）" % (txt, name_of(mid), mid))
        return 0
    print("对手「获得纹章」表：", summary())
    n = 0
    for cid, v in table().items():
        for k, txt in TIMING_TXT.items():
            for mid in v.get(k) or []:
                print("  %s %-22s 【%s】→ %s"
                      % (cid, name_of(int(cid)), txt, name_of(mid)))
                n += 1
                if n >= 6:
                    return 0
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
