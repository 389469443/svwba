#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终阵容统计（本地聚合骨架，2026-10-19）

配套 META-SCHEMA.md。**只做本地聚合，不联网**：服务器就绪后，
把 `load_jsonl()` / `aggregate()` 直接接到上报/下发上即可。

数据形状（一局一条）：
  {"final_board": [{"card_id":10501110,"star":2,"evolved":true}, ...],
   "rank": 1, "ver": "11.9.10", "tier": 15}

用法：
  python -u meta_stats.py --history logs/history.json     # 用本地战绩算
  python -u meta_stats.py --jsonl logs/final_boards.jsonl # 用 JSONL 算
  python -u meta_stats.py --min-samples 30
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(HERE, "..", "dsh-shadowverse-huanjing", "lib",
                     "knowledge", "cards.json")
BOARDS_LOG = os.path.join(HERE, "logs", "final_boards.jsonl")


def _cid(item):
    """归一化一张牌：兼容 card_id / name 两种来源。"""
    if not isinstance(item, dict):
        return None
    cid = item.get("card_id") or item.get("cardId") or item.get("baseCardId")
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        cid = 0
    star = int(item.get("star") or 0)
    return {"card_id": cid, "star": max(0, star),
            "evolved": bool(item.get("evolved") or item.get("isEvolved"))}


def normalize_board(board):
    """把任意来源的 board 规整成 [{card_id, star, evolved}]，丢掉无效项。"""
    out = []
    for item in board or []:
        c = _cid(item)
        if c and c["card_id"] > 0:
            out.append(c)
    return out


def signature(board):
    """阵容主 key：**忽略星级/进化** 的 card_id 多重集（升序）。"""
    ids = sorted(c["card_id"] for c in normalize_board(board))
    return "|".join(str(i) for i in ids)


def signature_full(board):
    """完整 key：含星级与进化（细粒度，用于"同一套牌不同强度"的对比）。"""
    rows = sorted((c["card_id"], c["star"], 1 if c["evolved"] else 0)
                  for c in normalize_board(board))
    return "|".join("%d:%d:%d" % r for r in rows)


def cards_of(board):
    return [c["card_id"] for c in normalize_board(board)]


def load_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def aggregate(rows, min_samples=30):
    """
    按「阵容主 key」聚合，返回：
      {
        "total": 有效局数,
        "teams": [{key, samples, firsts, top4, avg_rank, first_rate,
                   top4_rate, enough, cards}...],
        "cards": {card_id: {appearances, in_first, champion_rate}},
        "enough_min": min_samples,
      }
    """
    teams = {}
    cards = {}
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        board = row.get("final_board") or row.get("board") or []
        key = signature(board)
        if not key:
            continue
        try:
            rank = int(row.get("rank") or 0)
        except (TypeError, ValueError):
            rank = 0
        if not (1 <= rank <= 8):
            continue
        total += 1
        t = teams.setdefault(key, {
            "key": key, "samples": 0, "firsts": 0, "top4": 0, "rank_sum": 0,
            "cards": cards_of(board)})
        t["samples"] += 1
        t["rank_sum"] += rank
        if rank == 1:
            t["firsts"] += 1
        if rank <= 4:
            t["top4"] += 1
        for cid in cards_of(board):
            c = cards.setdefault(cid, {"appearances": 0, "in_first": 0})
            c["appearances"] += 1
            if rank == 1:
                c["in_first"] += 1

    out_teams = []
    for t in teams.values():
        n = t["samples"]
        out_teams.append({
            "key": t["key"],
            "samples": n,
            "firsts": t["firsts"],
            "top4": t["top4"],
            "avg_rank": round(t["rank_sum"] / float(n), 2),
            "first_rate": round(t["firsts"] / float(n), 4),
            "top4_rate": round(t["top4"] / float(n), 4),
            "enough": n >= min_samples,
            "cards": t["cards"],
        })
    out_teams.sort(key=lambda t: (-t["first_rate"], -t["samples"]))
    for cid, c in cards.items():
        c["champion_rate"] = round(c["in_first"] / float(c["appearances"]), 4) \
            if c["appearances"] else 0.0
    return {"total": total, "teams": out_teams, "cards": cards,
            "enough_min": min_samples}


def load_history_games(path):
    """从 history.json 的 games 读（board 里要有 card_id 才有意义）。"""
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = []
    for g in d.get("games") or []:
        if not isinstance(g, dict):
            continue
        rows.append({
            "final_board": g.get("final_board") or g.get("board") or [],
            "rank": g.get("rank"),
            "ver": d.get("ver"),
            "tier": g.get("tier"),
        })
    return rows


def load_cards_db(path=CARDS):
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for cid, card in (d.get("cards") or {}).items():
        try:
            out[int(cid)] = card.get("name") or str(cid)
        except (TypeError, ValueError):
            continue
    return out


def describe_team(team, names=None):
    names = names or {}
    parts = []
    for cid in sorted(set(team["cards"])):
        n = team["cards"].count(cid)
        nm = names.get(cid) or ("#%d" % cid)
        parts.append(nm if n == 1 else "%s×%d" % (nm, n))
    return " / ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--history", default="")
    ap.add_argument("--jsonl", default="")
    ap.add_argument("--min-samples", type=int, default=30)
    args = ap.parse_args(argv)

    if args.jsonl:
        rows = load_jsonl(args.jsonl)
    elif args.history:
        rows = load_history_games(args.history)
    else:
        rows = load_jsonl(BOARDS_LOG)
        if not rows:
            print("没有输入：用 --history 或 --jsonl 指定数据源")
            return 2

    res = aggregate(rows, min_samples=args.min_samples)
    names = load_cards_db()
    print("有效对局：%d 局；样本门槛：%d" % (res["total"], res["enough_min"]))
    if not res["teams"]:
        print("（还没有可统计的最终阵容）")
        return 0
    print("\n%-6s %6s %6s %8s  %s" % ("样本", "吃鸡率", "前四率", "平均名次", "阵容"))
    for t in res["teams"]:
        flag = "" if t["enough"] else "  [样本不足]"
        print("%-6d %5.1f%% %5.1f%% %8.2f  %s%s"
              % (t["samples"], t["first_rate"] * 100, t["top4_rate"] * 100,
                 t["avg_rank"], describe_team(t, names), flag))
    print("\n单卡冠军占比（出现在第一名阵容 / 总出现）：")
    rows_c = sorted(res["cards"].items(),
                    key=lambda kv: (-kv[1]["champion_rate"], -kv[1]["appearances"]))
    for cid, c in rows_c[:20]:
        print("  %-22s %3d/%3d  %.2f"
              % (names.get(cid, "#%d" % cid), c["in_first"], c["appearances"],
                 c["champion_rate"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
