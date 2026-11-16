#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对局历史记录 —— 每场记下双方阵容、预测、以及**实际掉血**。

设计：
  · 每次开战判定后追加一条记录到 logs/history.json
  · 「实际掉血」不用猜：下一场开战时对比血量，差值就是上一场的结果
  · 会话从「第一次判定」开始，直到进程结束
  · 面板（history_panel.py）读这个文件展示

记录字段：
  t            开战时间
  me_lv/me_hp  我方等级/血量
  me_board     我方阵容 [{star,attack,health,name}]
  op_lv/op_hp  对方等级/血量
  op_board     对方阵容
  win/loss/draw 预测胜率
  pred_opp     预测对方掉血分布 [[伤害, 百分比], ...]
  pred_me      预测我方掉血分布
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "logs", "history.json")


HEROES_JSON = os.path.join(HERE, "..", "dsh-shadowverse-huanjing",
                           "lib", "knowledge", "heroes.json")
_hero_cache = {}


def hero_name(hero_id):
    """hero_id → (名字, 职业)；查不到就返回 ('未知', '')"""
    if not _hero_cache:
        try:
            with open(HEROES_JSON, encoding="utf-8") as fh:
                _hero_cache.update(json.load(fh).get("heroes") or {})
        except Exception:
            pass
    v = _hero_cache.get(str(hero_id))
    if not v:
        return (f"英雄{hero_id}", "")
    prof = v.get("profession") or ""
    if prof.startswith("Class"):
        prof = ""
    return (v.get("name") or f"英雄{hero_id}", prof)


def _load():
    try:
        with open(HIST, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"session": None, "records": []}


def _save(d):
    """
    原子写：先写 .tmp 再 os.replace 覆盖。

    面板每 3 秒重读一次 history.json，直接覆写会让它偶尔读到半截文件
    （解析失败 → 面板瞬间清空再恢复，用户看到"战绩闪一下没了"）。
    os.replace 在同一分区上是原子的，读者只会看到旧内容或新内容。
    """
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    # 文件名带 PID：判定器和面板（写备注）可能同时存盘，
    # 共用同一个 .tmp 会互相截断，结果写进一个半截文件。
    tmp = "%s.tmp.%d" % (HIST, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        os.replace(tmp, HIST)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def start_session(force=False):
    d = _load()
    if force or not d.get("session"):
        d["session"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(d)
    return d["session"]


def new_session():
    """
    开一个**全新会话**：清空所有战绩，重置会话时间。

    控制台启动时调用。用户要求：重启插件后战绩应该是空的，
    「没有对局就不该显示上一次的记录」。
    """
    d = {"session": time.strftime("%Y-%m-%d %H:%M:%S"),
         "records": [], "games": [], "placement": None}
    _save(d)
    return d["session"]


def append(record):
    d = _load()
    if not d.get("session"):
        d["session"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["records"].append(record)
    _save(d)
    return len(d["records"])


def _clean_note(note):
    """
    去掉 note 里跟伤害相关的描述。

    用户要求：「不需要写最后一次受到多少伤害」
    """
    import re as _re
    if not note:
        return ""
    t = str(note)
    if _re.search(r"掉血|受到伤害|预计伤害|实掉", t):
        return ""
    return t


def add_game(rank, hero_id, hp, board, note=""):
    """
    记录**一整局**的结果（结算战绩）。

    用户要的就是这个：血量归零被淘汰、或者活到最后拿第一时的名次。
    和逐场的 `records`（每场的胜率预测）分开存，面板优先展示 games。
    """
    d = _load()
    if "games" not in d or not isinstance(d.get("games"), list):
        d["games"] = []
    d["games"].append({
        "t": time.strftime("%H:%M:%S"),
        "rank": int(rank) if rank else None,
        "hero": int(hero_id or 0),
        "hp": int(hp if hp is not None else 0),
        "board": board or [],
        "note": _clean_note(note),
        # 用户在战绩面板里自己写的两行备注（阵容 / 失败原因）
        "note_line1": "",
        "note_line2": "",
    })
    _save(d)
    return len(d["games"])


def is_settled(g):
    """这条战绩是否已结算（hp 有值）。进行中的记录 hp 是 None。"""
    return isinstance(g, dict) and g.get("hp") is not None


def _prune_orphan_pending(games, keep):
    """
    删掉 `keep` 以外所有「未结算」的孤儿记录，返回 keep 在新列表里的下标。

    只有一局在打，所以同时存在多条 hp=None 时，除了最新的那条，其余都是
    「结束时没回填、永远卡在进行中」的僵尸行。它们不会自己消失，
    面板就会一直挂着几个没名次的名字（用户反馈：明明输了还显示进行中）。
    """
    orphans = [j for j, g in enumerate(games)
               if isinstance(g, dict) and g.get("hp") is None and j != keep]
    for j in reversed(orphans):
        del games[j]
    return keep - sum(1 for j in orphans if j < keep)


def ensure_game(hero_id):
    """
    **在选好主战者时就先建一条记录**（用户要求）。

    只写编号（列表下标）和主战者，名次/血量留空，等整局结束再回填。

    一局**只有一条**：只要末尾还有未结算记录，就复用它，绝不追加。
    主战者 id 可能读错/换皮肤，所以即使对不上也复用（并顺手清掉更早的
    孤儿未结算行），否则一次误读就会多出一条「永远进行中」的记录。

    返回 (下标, 是否新建)。
    """
    d = _load()
    if "games" not in d or not isinstance(d.get("games"), list):
        d["games"] = []
    games = d["games"]
    hid = int(hero_id or 0)
    last_pending = None
    for i in range(len(games) - 1, -1, -1):
        if isinstance(games[i], dict) and games[i].get("hp") is None:
            last_pending = i
            break
    if last_pending is not None:
        changed = False
        n_orphan = sum(1 for j, g in enumerate(games)
                       if isinstance(g, dict) and g.get("hp") is None
                       and j != last_pending)
        idx = _prune_orphan_pending(games, last_pending)
        g = games[idx]
        # 主战者：记录里还没有编号（0/None）才补。已有的保留 ——
        # 选主战者那一刻读到的通常是对的，后面再读错不该把名字改坏。
        if hid and not int(g.get("hero") or 0):
            g["hero"] = hid
            changed = True
        for k, v in (("rank", None), ("hp", None), ("board", []),
                     ("note", ""), ("note_line1", ""), ("note_line2", ""),
                     ("t", time.strftime("%H:%M:%S"))):
            if k not in g:
                g[k] = v
                changed = True
        if changed or n_orphan:
            _save(d)
        return idx, False
    games.append({
        "t": time.strftime("%H:%M:%S"),
        "rank": None,
        "hero": hid,
        "hp": None,
        "board": [],
        "note": "",
        "note_line1": "",
        "note_line2": "",
    })
    _save(d)
    return len(games) - 1, True


def finish_game(hero_id, rank, hp, board, note=""):
    """
    整局结束：把结果回填到那条「未结算」记录上；没有未结算记录才新建一条。

    兜底顺序（用户反馈「已经输了还一直显示进行中」）：
      ① 末尾那条还没结算 → 就是本局，**不看主战者 id**；
      ② 末尾已结算但更早还有未结算孤儿 → 回填最近的那条；
      ③ 已经结算、只是名次还没补上 → 只补名次，不新建；
      ④ 已结算、这次读到的是**同一份结算**（主战者/名次/血量都对得上）→ 重复触发，不新建；
         这次没读到名次（rank=None）→ 同样按重复触发处理（不能靠空值判断新局）；
      ⑤ 以上都没有 → 才新建一条（`ensure_game` 漏了的新局也能补上；写入 t）。
    """
    d = _load()
    games = d.get("games")
    if not isinstance(games, list):
        games = []
        d["games"] = games
    hid = int(hero_id or 0)
    idx = None
    if games and isinstance(games[-1], dict) and games[-1].get("hp") is None:
        idx = len(games) - 1
    if idx is None:
        for i in range(len(games) - 1, -1, -1):
            g = games[i]
            if isinstance(g, dict) and g.get("hp") is None:
                idx = i
                break
    if idx is None and games:
        g = games[-1]
        if isinstance(g, dict) and g.get("hp") is not None:
            # 最后一条**已经结算**：
            #   · 还缺名次 + 这次读到了 → 只补名次，不新建；
            #   · 这次没读到名次（rank=None）→ 无法判断是不是新局，按重复触发处理；
            #   · 主战者/名次/血量都对得上 → 同一份结算，重复触发，不新建；
            #   · 否则 → 确实是新一局（`ensure_game` 漏了），新建一条补记。
            # 旧逻辑在"已结算"时一律 return，会把这局吞掉（用户实测战绩少一条）。
            if g.get("rank") is None and rank:
                idx = len(games) - 1
            elif not rank:
                return len(games) - 1
            elif (int(g.get("rank") or 0) == int(rank)
                  and int(g.get("hp") if g.get("hp") is not None else 0)
                  == int(hp if hp is not None else 0)
                  and int(g.get("hero") or 0) == hid):
                return len(games) - 1
    if idx is None:
        games.append({"t": time.strftime("%H:%M:%S"), "hero": hid,
                      "rank": None, "hp": None, "board": [], "note": "",
                      "note_line1": "", "note_line2": ""})
        idx = len(games) - 1
    idx = _prune_orphan_pending(games, idx)
    g = games[idx]
    # 主战者：只有记录里还没有编号时才用这次读到的补，
    # 避免读错/换皮肤把选人时正确记下的名字覆盖掉。
    if hid and not int(g.get("hero") or 0):
        g["hero"] = hid
    # ⚠️ 以前这里无条件覆盖：重复触发"整局结束"时，第二次 rank 读不到（None）
    # 就把已经写好的名次清空了（用户实测：战绩里名次消失/变 0）。
    # 现在只在**读到名次**时才写。
    if rank:
        g["rank"] = int(rank)
    g["hp"] = int(hp if hp is not None else 0)
    g["board"] = board or []
    g["note"] = _clean_note(note)
    g.setdefault("note_line1", "")
    g.setdefault("note_line2", "")
    if not g.get("t"):
        g["t"] = time.strftime("%H:%M:%S")
    _save(d)
    return idx


def update_game(idx, **fields):
    """
    更新 games[idx] 的字段（面板里两行备注用）。

    按**下标**更新：games 只会在末尾追加，已有条目的下标不会变。
    读-改-写一次完成；失败返回 False（面板只记日志，不弹错）。
    """
    try:
        d = _load()
        games = d.get("games")
        if not isinstance(games, list) or not (0 <= int(idx) < len(games)):
            return False
        if not isinstance(games[idx], dict):
            return False
        games[idx].update(fields)
        _save(d)
        return True
    except Exception:
        return False


def last_game():
    d = _load()
    g = d.get("games") or []
    return g[-1] if g else None


def set_placement(place):
    """结束整局时写入名次"""
    d = _load()
    d["placement"] = place
    _save(d)


def load():
    return _load()


if __name__ == "__main__":
    d = load()
    print(f"会话开始: {d.get('session')}")
    print(f"记录场次: {len(d.get('records') or [])}")
    print(f"名次: {d.get('placement', '未记录')}")
    for i, r in enumerate(d.get("records") or [], 1):
        print(f"\n[{i}] {r['t']}  等级{r['me_lv']} 血{r['me_hp']}")
        for c in r["me_board"]:
            print(f"    我 {c['star']}★ {c['attack']}/{c['health']} {c['name']}")
        print(f"    对方 等级{r['op_lv']} 血{r['op_hp']}")
        for c in r["op_board"]:
            print(f"    敌 {c['star']}★ {c['attack']}/{c['health']} {c['name']}")
        print(f"    预测 胜{r['win']}% 败{r['loss']}% 平{r['draw']}%")
        if r.get("actual_me"):
            print(f"    实际 我方掉血 {r['actual_me']} 点")
        if r.get("actual_opp"):
            print(f"    实际 对方掉血 {r['actual_opp']} 点")
