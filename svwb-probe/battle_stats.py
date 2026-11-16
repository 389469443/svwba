#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对局胜负统计（本地 logs/battle_result.json）。

读取器按游戏结算画面的结果记账。
胜率 = 胜 / (胜+负)。
"""
import json
import os
import time
import threading
import msvcrt
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "logs", "battle_result.json")
_LOCK = threading.Lock()


@contextmanager
def _write_lock():
    # 重置在窗口进程，结算在读取器进程。锁文件独立于原子替换的 JSON。
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with _LOCK, open(PATH + ".lock", "a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def stats():
    d = _load()
    wins = int(d.get("wins") or 0)
    losses = int(d.get("losses") or 0)
    total = wins + losses
    rate = (wins / total * 100.0) if total else 0.0
    return {"wins": wins, "losses": losses, "total": total,
            "rate": rate, "history": d.get("history") or []}


def deck_key(item):
    return item.get("stats_id") or ("name:" + item["name"])


def active_deck():
    path = os.path.join(HERE, "gamedata", "user_decks.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return next((deck_key(x) for x in data.get("decks", []) if x.get("name")
                     and x["name"] == data.get("active")), None)
    except (OSError, ValueError):
        return None


def deck_counts():
    return _load().get("deck_counts", {})


def reset_deck(key):
    with _write_lock():
        data = _load()
        data.get("deck_counts", {}).pop(key, None)
        _save(data)


def record(win, event_id=None, deck=None):
    """记录一局结果，返回最新 stats()；加锁避免多个窗口同时写丢记录。"""
    with _write_lock():
        d = _load()
        if event_id and any(e.get("id") == event_id for e in d.get("history", []) if isinstance(e, dict)):
            return stats()
        key = "wins" if win else "losses"
        if deck:
            counts = d.setdefault("deck_counts", {}).setdefault(deck, {})
            counts[key] = counts.get(key, 0) + 1
        d[key] = int(d.get(key) or 0) + 1
        hist = list(d.get("history") or [])
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {"win": bool(win), "at": now}
        if event_id:
            entry["id"] = event_id
        hist.insert(0, entry)
        d["history"] = hist[:100]
        d["at"] = now
        _save(d)
    return stats()


def _save(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp.%s.%s" % (os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)


def reset():
    with _write_lock():
        data = _load()
        _save({"deck_counts": data.get("deck_counts", {})})
    return stats()
