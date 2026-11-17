#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对手「把某张牌加入手牌」的推测表 —— 从卡表技能原文自动生成。

为什么要这个：记牌器能读到对手的**手牌张数**，读不到牌面（这是刻意的：
不碰对手手牌）。所以「入场曲把1张『解析的创造物』加入手牌」这类效果，
在界面上只能看到“手牌 +1”。但如果技能原文里写死了要给哪张牌，
我们就可以在对手打出那张卡时，替他补一行「获得 · 解析的创造物」。

解析的四种时机：
    【入场曲】  打出即结算  → 高置信，打出就记
    【进化时】  这张随从进化时结算 → 要看到它确实进化了才记
    【超进化时】同上，超进化时结算 → 同上
    【谢幕曲】  这张随从被破坏时结算 → 看到它进墓地才记（用户 2026-11-11 追加）

匹配不到的（随机、同名、非公开、条件型）一律不记 —— 本来就不该猜。
典型例子是【器械操纵者·吉尔克】：技能写的是“随机1张与…同名的卡牌
以非公开形式加入手牌”，没有『卡名』，所以解析不到，也不会显示。

数据来源：gamedata/sv_card_meta.json 的 skill 字段（带颜色标记的原文，
需要先剥掉 <color=...> 之类的标签）。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "gamedata", "sv_card_meta.json")
NAMES = os.path.join(HERE, "gamedata", "sv_card_names.json")

# 时机 → 中文标记（界面/日志里用中文）
TIMING_TXT = {"play": "入场曲", "evolve": "进化时", "super": "超进化时",
              "last": "谢幕曲"}
TAG = "推测"                        # 这类行在记牌器里的标记

# 【时机】之后、下一个【之前，出现「将N张『卡名』加入手牌」才算。
# 允许中间夹别的文字（「，使其费用变为0」之类），但不许跨过下一个【。
_PAT = re.compile(r"【(超进化时|谢幕曲|入场曲|进化时)】((?:(?!【).)*?)"
                  r"将(\d+)张『([^』]+)』加入手牌", re.S)
# 引用型技能（2026-11-17）：【进化时】发动与【入场曲】相同的能力。
# 悠然的滑手就是这样写的 —— 不认这种写法，超进化/进化时就一条给牌都记不到。
_REF = re.compile(r"【(超进化时|谢幕曲|进化时)】[^【]{0,12}?"
                  r"与【(入场曲|进化时|超进化时|谢幕曲)】相同", re.S)
_WHEN = {"入场曲": "play", "进化时": "evolve",
         "超进化时": "super", "谢幕曲": "last"}

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
    """同名卡可能有多个 id（异画、token 的 90/95 镜像）。挑一个最像“本体”的：
    末位 0 优先，其次 10 开头，再次 90 开头。"""
    def key(c):
        return (0 if str(c).endswith("0") else 1,
                0 if str(c).startswith("10") else
                1 if str(c).startswith("90") else 2,
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
        if not text or "加入手牌" not in text:
            continue
        got = {}
        for hit in _PAT.finditer(text):
            when = _WHEN[hit.group(1)]
            n = int(hit.group(3))
            target = hit.group(4).strip()
            tid = _pick_id(by_name.get(target) or [])
            if not tid or n <= 0:
                continue
            # 统一成 int：下游要拿它和快照里的卡 id（int）比较，
            # 字符串和 int 混着放会出现「明明已经打出来了还记着推测」。
            tid = int(tid)
            lst = got.setdefault(when, [])
            if (tid, n) not in lst:
                lst.append((tid, n))
        # 「与【入场曲】相同」这类引用：照着被引用时机的结果补一份。
        # 引用的时机可能自己也还没解析出来，所以最多补两轮。
        todo = []
        for hit in _REF.finditer(text):
            when, src = _WHEN[hit.group(1)], _WHEN[hit.group(2)]
            if when != src:
                todo.append((when, src))
        for _ in range(2):
            rest = []
            for when, src in todo:
                if src in got:
                    if when not in got:
                        got[when] = list(got[src])
                else:
                    rest.append((when, src))
            todo = rest
        if got:
            table[str(cid)] = got
    _CACHE["table"] = table
    _CACHE["names"] = names
    return table


def table():
    """{打出/进化的卡 id: {"play": [(目标卡 id, 张数)], "evolve": [...], "super": [...]}}

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


def grants(cid, timing="play"):
    """这张卡在这个时机给了哪些牌 → [(目标 id, 张数)]，没有就是空列表。"""
    try:
        ent = table().get(str(int(cid))) or {}
    except Exception:
        return []
    return list(ent.get(timing) or [])


def on_play(cid):
    return grants(cid, "play")


def on_evolve(cid, ev=2, prev=0):
    """这张卡进化 / 超进化时给的牌。

    规则（用户 2026-11-17 确认）：**超进化会同时触发「进化时」和「超进化时」**，
    普通进化只触发「进化时」。
    prev 是这张牌之前记录过的状态（1 = 已进化），用于「1→2 升级」时只补差值，
    避免把「进化时」那部分重复记一遍。
    """
    ev = int(ev or 2)
    prev = int(prev or 0)
    out = []
    if prev < 1:
        out += grants(cid, "evolve")
    if ev >= 2 and prev < 2:
        out += grants(cid, "super")
    return out


def on_death(cid):
    """这张卡被破坏（进墓地）时给的牌。"""
    return grants(cid, "last")


def name_of(cid):
    return (_CACHE.get("names") or {}).get(str(cid)) or ""


def summary():
    """自检/文档用：每个时机各覆盖多少张卡。"""
    t = table()
    out = {}
    for key, txt in TIMING_TXT.items():
        out[txt] = sum(1 for v in t.values() if v.get(key))
    out["卡牌总数"] = len(t)
    return out
