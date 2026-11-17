#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对局胜负统计（本地 logs/battle_result.json）。

记牌器在检测到「一局结束」时调用 record(win)：
- 我方最后生命 <= 0 → 输；
- 对手生命 <= 0 → 赢；
- 双方都还活着就结束了（认输 / 掉线）→ 记负，但打上 guess 标记：
  这种情况判不出「谁投的降」，只能按「我方认输」推定，
  界面上会留一个「改判」按钮（battle_stats.flip_last），
  对局其实没结束（长考）时 tracker 会自己撤销（battle_stats.revoke_last）。
胜率 = 胜 / (胜+负)。
"""
import json
import os
import time
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "logs", "battle_result.json")
_LOCK = threading.Lock()


def _load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp.%s.%s" % (os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PATH)


def _bump(d, win, n=1):
    key = "wins" if win else "losses"
    d[key] = max(0, int(d.get(key) or 0) + n)


def stats():
    d = _load()
    wins = int(d.get("wins") or 0)
    losses = int(d.get("losses") or 0)
    total = wins + losses
    rate = (wins / total * 100.0) if total else 0.0
    return {"wins": wins, "losses": losses, "total": total,
            "rate": rate, "history": d.get("history") or []}


def record(win, guess=False):
    """记录一局结果，返回最新 stats()；加锁避免多个窗口同时写丢记录。

    guess=True 表示「判不出谁死，按认输推定」的那一局。历史里会带标记，
    界面据此显示「改判」按钮。
    """
    with _LOCK:
        d = _load()
        _bump(d, bool(win))
        hist = list(d.get("history") or [])
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        item = {"win": bool(win), "at": now}
        if guess:
            item["guess"] = True
        hist.insert(0, item)
        d["history"] = hist[:100]
        d["at"] = now
        _save(d)
    return stats()


def revoke_last():
    """撤掉最近一条记录（推定错了的时候用）。

    返回 (是否撤销成功, 被撤掉的那条是不是「推定」, stats)。
    """
    with _LOCK:
        d = _load()
        hist = list(d.get("history") or [])
        if not hist:
            return False, False, stats()
        item = hist.pop(0)
        _bump(d, bool(item.get("win")), -1)
        d["history"] = hist
        d["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(d)
        st = stats()
    return True, bool(item.get("guess")), st


def flip_last():
    """把最近一条记录改判（胜 ↔ 负）。返回 (是否成功, stats)。"""
    with _LOCK:
        d = _load()
        hist = list(d.get("history") or [])
        if not hist:
            return False, stats()
        item = hist[0]
        old = bool(item.get("win"))
        _bump(d, old, -1)
        _bump(d, not old)
        item["win"] = not old
        item.pop("guess", None)          # 人工确认过的就不再是推定了
        item["flip"] = True
        hist[0] = item
        d["history"] = hist
        d["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(d)
    return True, stats()


def reset():
    """清空统计（刷新）。"""
    try:
        os.remove(PATH)
    except Exception:
        pass
    return stats()
