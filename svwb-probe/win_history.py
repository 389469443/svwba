#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史胜率归档：卡组 × 北京时间「一天」。

记牌器顶栏的胜率（battle_stats）只是「从上一次清零到现在」的一段流水，
关掉就忘、下次清零就没了。想回答「这套牌这几天打得怎么样」，
就得在清零之前把它按天固下来。

规则（用户 2026-11-09 要求）：
- 主键 = 卡组名（控制台「卡组」里的当前使用卡组，gamedata/user_decks.json 的 active）；
- 索引 = 北京时间的一天（东八区，**不看机器时区**）；
- 触发点两个，动作完全一样：① 用户手动切换卡组；② 北京时间 00:00；
  两次都把 battle_stats 现在的数字记到（卡组, 当天）名下，然后清零 battle_stats。
- 0 胜 0 负不写记录、也不动 battle_stats（没有对局就没什么可归档的）。

按天拆桶：
  归档那一刻其实不知道这些胜负有几天前打的（控制台可能整夜没开、一觉睡到
  第二天下午才连上）。battle_stats.history 里每局都带 ``at`` 时间戳，
  条数与 wins+losses 对得上时按时间戳分到各天；对不上（历史被截到 100 条）
  就整块算到游标 ``day``（那个时段所属的日期）上，宁可粗一点也别乱分。

文件：logs/win_history.json
    {
      "cur": "宇宙鱼",              # 当前这段数字属于哪个卡组
      "day": "2026-11-09",          # 以及它开始的日期（拆桶失败时的兜底）
      "decks": {
        "宇宙鱼": {"days": {"2026-11-09": {"wins": 3, "losses": 1}},
                    "last": "2026-11-09 02:31:00"}
      }
    }
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import battle_stats

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "logs", "win_history.json")
DECKS_PATH = os.path.join(HERE, "gamedata", "user_decks.json")
TZ = timezone(timedelta(hours=8))       # 北京时间；显式指定，不受系统时区影响
MAX_DAYS = 400                          # 单个卡组最多留这么多天，防止文件无限长
_LOCK = threading.RLock()


# ---------------- 时间 ----------------
def bj_now(ts=None):
    if ts is None:
        return datetime.now(TZ)
    return datetime.fromtimestamp(float(ts), TZ)


def bj_day(ts=None):
    return bj_now(ts).strftime("%Y-%m-%d")


def bj_hms(ts=None):
    return bj_now(ts).strftime("%Y-%m-%d %H:%M:%S")


def _day_of_at(at):
    """battle_stats 里的 'YYYY-mm-dd HH:MM:SS' 是**本机时间**，换算成北京日期。"""
    try:
        t = time.mktime(time.strptime(str(at)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return ""
    return bj_day(t)


def seconds_to_midnight(ts=None):
    """离北京时间下一个 00:00 还有多少秒（给后台线程/测试看）。"""
    now = bj_now(ts)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                            microsecond=0)
    return max(0.0, (nxt - now).total_seconds())


# ---------------- 存取 ----------------
def _load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("decks"), dict):
        d["decks"] = {}
    d.setdefault("cur", "")
    d.setdefault("day", "")
    return d


def _save(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp.%s.%s" % (os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)


def active_deck():
    """控制台「卡组」里当前使用的那套；没有就返回空串。"""
    try:
        with open(DECKS_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        decks = d.get("decks") or []
        act = d.get("active") or ""
        if act:
            return act
        return (decks[0].get("name") or "") if decks else ""
    except Exception:
        return ""


def deck_size(name):
    """卡组张数（界面上当副标签用），查不到返回 0。"""
    try:
        with open(DECKS_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        for x in (d.get("decks") or []):
            if (x.get("name") or "") == name:
                return len(x.get("cards") or [])
    except Exception:
        pass
    return 0


# ---------------- 归档 ----------------
def _split(st, fallback_day):
    """把当前这批胜负按每局时间戳分到各天 → [(day, wins, losses)]。"""
    wins = int(st.get("wins") or 0)
    losses = int(st.get("losses") or 0)
    total = wins + losses
    if not total:
        return []
    hist = [h for h in (st.get("history") or []) if isinstance(h, dict)]
    if len(hist) != total:
        # 历史条数和统计对不上（多半是被截到 100 条），整套算兜底那天，
        # 至少总数是对的；硬拆会把胜负拆错天。
        return [(fallback_day, wins, losses)]
    buckets = {}
    for h in hist:
        day = _day_of_at(h.get("at")) or fallback_day
        b = buckets.setdefault(day, [0, 0])
        b[0 if h.get("win") else 1] += 1
    # 再验一次总数：万一 history 被人工改过 / 改判只动了一边，
    # 按天拆出来的胜负数就和统计对不上，那就宁可不拆。
    hw = sum(v[0] for v in buckets.values())
    hl = sum(v[1] for v in buckets.values())
    if hw != wins or hl != losses:
        return [(fallback_day, wins, losses)]
    return [(k, v[0], v[1]) for k, v in sorted(buckets.items())]


def _bump(d, deck, day, wins, losses, now=None):
    if not deck:
        deck = "未命名卡组"
    ent = d["decks"].setdefault(deck, {})
    days = ent.setdefault("days", {})
    rec = days.setdefault(day, {"wins": 0, "losses": 0})
    rec["wins"] = int(rec.get("wins") or 0) + int(wins)
    rec["losses"] = int(rec.get("losses") or 0) + int(losses)
    ent["last"] = bj_hms(now)
    if len(days) > MAX_DAYS:                      # 太老的先扔，文件别无限长
        for k in sorted(days)[:len(days) - MAX_DAYS]:
            days.pop(k, None)


def _stamp(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# 上一次「确定什么都没变」时的 (日期, 卡组文件时间戳, 账本时间戳)。
# 控制台每 15 秒问一次，绝大多数时候答案是「什么都没变」——
# 那就只做两次 stat + 一次元组比较，连 JSON 都不解析。
_FAST = None


def sync(active=None, now=None):
    """检查是否需要归档；需要就「记账 → 清零」。

    返回事件 dict（没触发归档也返回 None）：
        {"deck": 归档到哪个卡组, "days": [{"day","wins","losses"}...],
         "wins":, "losses":, "switched": 是否因为切卡组,
         "rolled": 是否因为跨天, "cleared": 是否真的清了 battle_stats}

    只有调用方没指定 ``active``（也就是定时检查）时才走时间戳短路：
    切卡组/改名这类显式调用一定要真读一遍文件，不能被缓存挡掉。
    """
    global _FAST
    now = time.time() if now is None else float(now)
    key = None
    if active is None:
        key = (bj_day(now), _stamp(DECKS_PATH), _stamp(PATH))
        if _FAST == key:
            return None
    ev = _sync_slow(active, now)
    # 慢路径可能刚写过账本（首次建账 / 归档），那时进去算的 key 已经过期，
    # 重新取一次再缓存，免得下一拍白跑一趟。
    _FAST = (bj_day(now), _stamp(DECKS_PATH), _stamp(PATH)) \
        if (key is not None and ev is None) else None
    return ev


def _sync_slow(active=None, now=None):
    now = time.time() if now is None else float(now)
    today = bj_day(now)
    disk = active_deck()
    act = (disk if active is None else str(active)).strip()
    with _LOCK:
        d = _load()
        has_state = bool(d.get("cur") and d.get("day"))
        # 这段数字属于谁：① 状态里记着的 > ② 磁盘上的当前卡组 > ③ 调用方给的。
        # 第 ② 条是为「第一次跑、状态文件还不存在」准备的 —— 切卡组时调用方
        # 会先来归档、再写 active，所以那时磁盘上还是旧卡组，正好是对的。
        cur = d.get("cur") or disk or act
        day = d.get("day") or today
        switched = bool(d.get("cur")) and bool(act) and act != d["cur"]
        rolled = bool(d.get("day")) and d["day"] != today
        # 第一次跑（还没有状态文件）：只把游标立起来，**不归档也不清零**。
        # 否则升级后的第一次启动会把记牌器里正挂着的胜率吞掉，看起来像 bug。
        # 例外：第一次跑的同时正在切卡组 —— 那这段数字是切走前那套牌的，
        # 按切换处理才不丢。
        first_switch = (not has_state) and bool(disk) and bool(act) \
            and act != disk
        if not has_state and not first_switch:
            d["cur"] = cur
            d["day"] = today
            d["at"] = bj_hms(now)
            _save(d)
            return None
        switched = switched or first_switch
        if not (switched or rolled):
            return None

        st = battle_stats.stats()
        wins = int(st.get("wins") or 0)
        losses = int(st.get("losses") or 0)
        days, cleared = [], False
        if wins + losses:
            for dy, w, l in _split(st, day):
                _bump(d, cur, dy, w, l, now)
                days.append({"day": dy, "wins": w, "losses": l})
            cleared = True
        d["cur"] = act or cur
        d["day"] = today
        d["at"] = bj_hms(now)
        _save(d)                    # 先落盘再清空：宁可重复也不丢
        if cleared:
            battle_stats.reset()
    return {"deck": cur or act, "days": days, "wins": wins, "losses": losses,
            "switched": switched, "rolled": rolled, "cleared": cleared}


def pending(now=None):
    """记牌器里还没归档的数字（面板上那行「记牌器当前」）。"""
    st = battle_stats.stats()
    with _LOCK:
        d = _load()
    deck = d.get("cur") or active_deck()
    wins = int(st.get("wins") or 0)
    losses = int(st.get("losses") or 0)
    return {"deck": deck, "wins": wins, "losses": losses,
            "total": wins + losses, "rate": _rate(wins, losses),
            "day": d.get("day") or bj_day(now)}


# ---------------- 查询 ----------------
def _rate(w, l):
    t = w + l
    return (w / float(t) * 100.0) if t else 0.0


def decks():
    """面板用的卡组列表（当前使用排最前，其余按场次降序）。"""
    d = _load()
    act = active_deck()
    out = []
    for name, ent in (d.get("decks") or {}).items():
        ent = ent if isinstance(ent, dict) else {}
        rows = []
        for day, rec in sorted((ent.get("days") or {}).items(), reverse=True):
            rec = rec if isinstance(rec, dict) else {}
            w = int(rec.get("wins") or 0)
            l = int(rec.get("losses") or 0)
            if w + l <= 0:
                continue
            rows.append({"day": day, "wins": w, "losses": l,
                         "total": w + l, "rate": _rate(w, l)})
        tw = sum(r["wins"] for r in rows)
        tl = sum(r["losses"] for r in rows)
        if not rows and name != act:
            continue                     # 空记录不占地方
        out.append({"name": name, "wins": tw, "losses": tl,
                    "total": tw + tl, "rate": _rate(tw, tl),
                    "days": rows, "last": ent.get("last") or "",
                    "size": deck_size(name), "active": name == act})
    if act and not any(x["name"] == act for x in out):
        out.append({"name": act, "wins": 0, "losses": 0, "total": 0,
                    "rate": 0.0, "days": [], "last": "",
                    "size": deck_size(act), "active": True})
    out.sort(key=lambda x: (not x["active"], -x["total"], x["name"]))
    return out


def summary():
    rows = decks()
    wins = sum(x["wins"] for x in rows)
    losses = sum(x["losses"] for x in rows)
    return {"wins": wins, "losses": losses, "total": wins + losses,
            "rate": _rate(wins, losses), "decks": len(rows),
            "days": sum(len(x["days"]) for x in rows)}


# ------------------------------------------------------------ 按职业统计 ---
# 2026-11-16 用户要求：面板上可以切「按卡组统计 / 按职业统计」。
# 卡组名是用户自己起的（中速梦、宇宙鱼……），职业只能从卡表反推：
# 每张卡在 sv_card_meta.json 里都有 class（0=中立 1..7=职业），
# 取「非中立卡的众数」就足够稳（实测 33:7、38:2 这种比例）。
CLASS_NAME = {0: "中立", 1: "精灵", 2: "皇家护卫", 3: "巫师",
              4: "龙族", 5: "梦魇", 6: "主教", 7: "复仇者"}
MODE_DECK, MODE_CLASS = "deck", "class"


def _compute_class(name):
    """卡组名 → 职业名（按卡组构成的职业众数）；算不出来返回 ""。"""
    try:
        import deck_reader
        decks = deck_reader._decks_from_file()
        item = next((x for x in decks if x.get("name") == name), None)
        if not item:
            return ""
        meta = deck_reader.load_meta()
        from collections import Counter
        c = Counter()
        for cid in item.get("cards") or []:
            v = meta.get(str(cid)) or {}
            cls = v.get("class")
            if isinstance(cls, int) and cls > 0:    # 0=中立，不投票
                c[cls] += 1
        if not c:
            return ""
        return CLASS_NAME.get(c.most_common(1)[0][0], "")
    except Exception:
        return ""


def set_deck_class(name, cls):
    """把算出来的职业记进账本 —— 卡组以后被删了，历史还能按职业归类。"""
    if not name or not cls:
        return
    with _LOCK:
        d = _load()
        ent = (d.get("decks") or {}).get(name)
        if not isinstance(ent, dict) or ent.get("class") == cls:
            return
        ent["class"] = cls
        _save(d)


def deck_class(name):
    """卡组名 → 职业名：账本里存过就直接用，否则现算（算出来会存下来）。"""
    if not name:
        return ""
    d = _load()
    ent = (d.get("decks") or {}).get(name)
    cls = ent.get("class") if isinstance(ent, dict) else ""
    if isinstance(cls, str) and cls:
        return cls
    cls = _compute_class(name)
    if cls:
        set_deck_class(name, cls)
    return cls or ""


def by_class():
    """按职业聚合：同一职业下所有卡组的胜负相加（结构同 decks()）。"""
    buckets = {}
    for row in decks():
        cls = deck_class(row["name"]) or "未知职业"
        b = buckets.get(cls)
        if b is None:
            b = buckets[cls] = {"name": cls, "wins": 0, "losses": 0,
                                "days": {}, "names": [], "n": 0,
                                "active": False, "size": 0, "last": ""}
        b["wins"] += row["wins"]
        b["losses"] += row["losses"]
        b["names"].append(row["name"])
        if row["total"]:
            b["n"] += 1
        if row["active"]:
            b["active"] = True
            b["size"] = row["size"]
        for dd in row["days"]:
            rec = b["days"].setdefault(dd["day"], {"wins": 0, "losses": 0})
            rec["wins"] += dd["wins"]
            rec["losses"] += dd["losses"]
        if str(row["last"]) > b["last"]:
            b["last"] = str(row["last"])
    out = []
    for b in buckets.values():
        days = []
        for day, rec in sorted(b["days"].items(), reverse=True):
            w, l = int(rec["wins"]), int(rec["losses"])
            if w + l <= 0:
                continue
            days.append({"day": day, "wins": w, "losses": l,
                         "total": w + l, "rate": _rate(w, l)})
        w, l = b["wins"], b["losses"]
        out.append({"name": b["name"], "wins": w, "losses": l,
                    "total": w + l, "rate": _rate(w, l), "days": days,
                    "last": b["last"], "size": b["size"],
                    "active": b["active"], "decks": b["names"],
                    "n_decks": b["n"]})
    out.sort(key=lambda x: (not x["active"], -x["total"], x["name"]))
    return out


def get_mode():
    """面板的统计方式：默认「按卡组统计」。"""
    m = _load().get("ui_mode")
    return MODE_CLASS if m == MODE_CLASS else MODE_DECK


def set_mode(mode):
    mode = MODE_CLASS if mode == MODE_CLASS else MODE_DECK
    with _LOCK:
        d = _load()
        if d.get("ui_mode") == mode:
            return mode
        d["ui_mode"] = mode
        _save(d)
    return mode


def clear_deck(name):
    """删掉一套卡组的历史记录（不动卡组本身）。"""
    with _LOCK:
        d = _load()
        d.get("decks", {}).pop(name, None)
        _save(d)
    return True


def rename_deck(old, new):
    """卡组改名：历史记录跟着换钥匙（新名字已有记录就合并）。"""
    if not old or not new or old == new:
        return False
    with _LOCK:
        d = _load()
        decks = d.get("decks") or {}
        ent = decks.pop(old, None)
        if ent is not None:
            tgt = decks.get(new)
            if isinstance(tgt, dict):
                days = tgt.setdefault("days", {})
                for day, rec in (ent.get("days") or {}).items():
                    rec = rec if isinstance(rec, dict) else {}
                    cur = days.setdefault(day, {"wins": 0, "losses": 0})
                    cur["wins"] = int(cur.get("wins") or 0) + int(
                        rec.get("wins") or 0)
                    cur["losses"] = int(cur.get("losses") or 0) + int(
                        rec.get("losses") or 0)
                if (ent.get("last") or "") > (tgt.get("last") or ""):
                    tgt["last"] = ent.get("last")
            else:
                decks[new] = ent
            d["decks"] = decks
        if d.get("cur") == old:
            d["cur"] = new
        _save(d)
    return True


def clear_all():
    with _LOCK:
        d = _load()
        d["decks"] = {}
        _save(d)
    return True
