#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读**最终名次**（第几名）。

数据源（两条，互为备份）：
  1. `Sv2Warchess.Sv2WarchessRankPlayerInfo`
        long Uid  +0x10
        int  HeroId +0x18
        int  Life   +0x1c
        bool IsPriviliged +0x20
        int  BoarderId +0x24
        int  Rank   +0x28     ← 名次
        int  PrevRank +0x2c
        int  MaxLife  +0x30
     每个玩家一个实例，用**我方 uid** 匹配。

  2. `Netease.GameNet.Sv2WarChessBattlePlayerInfo`
        long Uid +0x10 / string Name +0x18 / int HeroId +0x20 ...
        int Rank +0x40

用法：
  python read_rank.py            # 读一次
  python read_rank.py --watch 5

⚠️ 同一进程里可能同时存在**多张名次表**（服务端每推一次快照就新分配一批
   RankPlayerInfo 实例，旧实例不立刻释放）。`mine[-1]` 不一定是最终结算
   那张表 —— 必须按「8 个 uid 互不重复 + rank 恰好 1..8」筛出完整表，
   再取最新一张（我方 life 最低；相同则取出现顺序靠后的）。
   详见 `_split_tables()` / `_pick_latest_table()`。
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shadow_warchess_reader import (   # noqa: E402
    find_game_pid, open_proc, read, load_cards,
    scan_qwords, find_class, cstr,
)
import read_world as RW                # noqa: E402

HEADER = 0x10
RANK_MAX = 8          # 一局固定 8 个玩家，名次恰好 1..8


def klass_of(h, addr):
    d = read(h, addr, 8)
    return struct.unpack("<Q", d)[0] if d and len(d) == 8 else 0


def our_uid(h, cards_db):
    snap = RW.snapshot(h, cards_db)
    for p in snap.get("players") or []:
        if p.get("side") == "我方":
            return p.get("uid")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 多张名次表的拆分与挑选（2026-10-18 修「第4名被记成第3名」）
#
# 为什么会有多张表：服务端每推一次名次快照，游戏就可能新分配一整批
# RankPlayerInfo 实例，旧实例不会立刻释放。所以同一个 uid 在 rows 里会出现
# 多次（rank 1~8 各有一轮），`mine[-1]` 取到的不一定是**最终结算**那张表。
#
# 判据（与用户实机核对的口径一致）：
#   完整表 = 恰好 8 行、8 个 uid 互不重复、rank 恰好 1..8；
#   最新表 = 该表里我方 life 最低（受伤/出局只会让 life 变低）；
#            life 相同时优先「life 与 rank 自洽」的表，再按出现顺序取靠后的。
#   结算条件：我方 life <= 0（已出局）时，最新完整表的 rank 才是最终名次。
# ─────────────────────────────────────────────────────────────────────────────
def _complete_table(rows):
    """这张表是否**一致且完整**：8 行、uid 不重复、rank 恰好 1..8。"""
    if len(rows) != RANK_MAX:
        return False
    uids = [r.get("uid") for r in rows]
    ranks = sorted(int(r.get("rank") or 0) for r in rows)
    return len(set(uids)) == RANK_MAX and ranks == list(range(1, RANK_MAX + 1))


def _life_monotonic(rows):
    """
    表内 life 与 rank 是否自洽：名次越靠前（rank 越小），life 不应更低。

    名次是按出局顺序排的：先出局的人 life 最低、名次最差。
    所有人 life 相同的初始表也满足；不满足的多半是「旧表 + 新表」拼出来的。
    """
    try:
        ordered = sorted(rows, key=lambda r: int(r.get("rank") or 0))
        prev = None
        for r in ordered:
            life = int(r.get("life") or 0)
            if prev is not None and life > prev:
                return False
            prev = life
        return True
    except Exception:
        return False


def _split_tables(rows):
    """
    把扫描到的行拆成候选名次表，只返回**完整表**：[(出现序号, 8 行), ...]。

    scan_qwords 按内存地址升序返回，同一张表的实例通常连续分配 → 先按
    「连续 8 行」切。如果表之间是交错分配的（切出来 uid 重复 / rank 不全），
    再按「同一个 uid 的第 k 次出现」配对 —— 同一局 8 个玩家在每张表里各出现一次。
    """
    n = len(rows)
    tables = []
    # ① 连续分组
    for i in range(0, n - RANK_MAX + 1, RANK_MAX):
        group = rows[i:i + RANK_MAX]
        if _complete_table(group):
            tables.append((i // RANK_MAX, group))
    if tables:
        return tables
    # ② 交错分组：每个 uid 的第 k 次出现配成第 k 张表
    by_uid = {}
    for r in rows:
        by_uid.setdefault(r.get("uid"), []).append(r)
    if len(by_uid) < RANK_MAX:
        return []
    top = sorted(by_uid.items(), key=lambda kv: -len(kv[1]))[:RANK_MAX]
    k_max = min(len(v) for _, v in top)
    for k in range(k_max):
        group = [lst[k] for _, lst in top]
        if _complete_table(group):
            tables.append((k, group))
    return tables


def _pick_latest_table(tables, uid):
    """
    从多张完整表里挑**最新一张**，返回 (table, mine, 候选表数)。

    最新判据：
      ① 我方 life 最低（受伤/出局只会降低 life）；
      ② life 相同 → 优先 life 与 rank 自洽的表；
      ③ 再相同 → 取出现顺序靠后的（地址更高 ≈ 分配更晚）。
    """
    cands = []
    for idx, t in tables:
        mine = next((r for r in t if r.get("uid") == uid), None)
        if mine is None:
            continue
        cands.append((int(mine.get("life") or 0),
                      1 if _life_monotonic(t) else 0,
                      idx, t, mine))
    if not cands:
        return None, None, 0
    # key 越小越优先：life 最低 → 自洽 → 出现最晚
    cands.sort(key=lambda x: (x[0], -x[1], -x[2]))
    _life, _mono, _idx, table, mine = cands[0]
    return table, mine, len(cands)


def from_rank_info(h, uid):
    """Sv2WarchessRankPlayerInfo：Uid@0x10, Rank@0x28"""
    cb = find_class(h, "Sv2WarchessRankPlayerInfo", "Sv2Warchess")
    if isinstance(cb, tuple):
        cb = cb[0]
    if not cb:
        return None, "找不到 RankPlayerInfo 类"
    sites = scan_qwords(h, {cb})
    rows = []
    for a in sites:
        if klass_of(h, a) != cb:
            continue
        d = read(h, a + HEADER, 0x30)
        if not d or len(d) < 0x28:
            continue
        u = struct.unpack_from("<q", d, 0x00)[0]
        hero = struct.unpack_from("<i", d, 0x08)[0]
        life = struct.unpack_from("<i", d, 0x0C)[0]
        rank = struct.unpack_from("<i", d, 0x18)[0]
        if not (1 <= rank <= 8):
            continue
        rows.append({"uid": u, "hero": hero, "life": life, "rank": rank})
    if not rows:
        return None, f"没有有效实例（引用 {len(sites)}）"
    table, mine, n_tables = _pick_latest_table(_split_tables(rows), uid)
    if not table:
        return None, f"没有一致完整的名次表（有效行 {len(rows)}，需 8 个 uid/rank 1..8）"
    # 只返回**选中的那一张完整表**：上层拿到的 rows/mine 一定自洽。
    return {"rows": table, "mine": mine, "count": len(table),
            "tables": n_tables, "all_rows": len(rows)}, None


def from_battle_player_info(h, uid):
    """Sv2WarChessBattlePlayerInfo：Uid@0x10, Name@0x18, Rank@0x40"""
    cb = find_class(h, "Sv2WarChessBattlePlayerInfo", "Netease.GameNet")
    if isinstance(cb, tuple):
        cb = cb[0]
    if not cb:
        return None, "找不到 BattlePlayerInfo 类"
    sites = scan_qwords(h, {cb})
    rows = []
    for a in sites:
        if klass_of(h, a) != cb:
            continue
        d = read(h, a + HEADER, 0x48)
        if not d or len(d) < 0x44:
            continue
        u = struct.unpack_from("<q", d, 0x00)[0]
        nm_ptr = struct.unpack_from("<Q", d, 0x08)[0]
        life = struct.unpack_from("<i", d, 0x28)[0]
        rank = struct.unpack_from("<i", d, 0x30)[0]
        if not (1 <= rank <= 8):
            continue
        nm = cstr(h, nm_ptr) if 0x10000 < nm_ptr < 0x7FFFFFFFFFFF else None
        rows.append({"uid": u, "name": nm, "life": life, "rank": rank})
    if not rows:
        return None, f"没有有效实例（引用 {len(sites)}）"
    table, mine, n_tables = _pick_latest_table(_split_tables(rows), uid)
    if not table:
        return None, f"没有一致完整的名次表（有效行 {len(rows)}，需 8 个 uid/rank 1..8）"
    return {"rows": table, "mine": mine, "count": len(table),
            "tables": n_tables, "all_rows": len(rows)}, None


def snapshot(h, cards_db):
    uid = our_uid(h, cards_db)
    out = {"our_uid": uid}
    if not uid:
        out["error"] = "读不到我方 uid（可能不在对局）"
        return out
    r1, e1 = from_rank_info(h, uid)
    if r1:
        out.update({"source": "RankPlayerInfo", **r1})
        return out
    r2, e2 = from_battle_player_info(h, uid)
    if r2:
        out.update({"source": "BattlePlayerInfo", **r2})
        return out
    out["error"] = f"{e1} / {e2}"
    return out


def render(res):
    if res.get("error") and not res.get("rows"):
        print(f"[!] {res['error']}  uid={res.get('our_uid')}")
        return
    mine = res.get("mine")
    _extra = ""
    if res.get("tables"):
        _extra = ("  完整表 %d 张 / 共扫 %d 行"
                  % (res.get("tables"), res.get("all_rows")))
    print(f"来源 {res.get('source')}  实例 {res.get('count')} 个{_extra}"
          f"  我方 uid={res.get('our_uid')}")
    for r in sorted(res.get("rows") or [], key=lambda x: x["rank"]):
        mark = "  ← 我" if mine and r["uid"] == mine["uid"] else ""
        nm = f" {r.get('name')}" if r.get("name") else ""
        print(f"  第{r['rank']}名  uid={r['uid']}{nm}{mark}")
    if mine:
        print(f"\n★ 我方名次：第 {mine['rank']} 名")
    else:
        print("\n[!] 列表里没有我方 uid")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--watch", type=float, default=0)
    args = ap.parse_args()
    pid = args.pid or find_game_pid()[0]
    if not pid:
        print("找不到游戏进程")
        return 1
    h = open_proc(pid)
    RW.WD_PID[0] = pid
    cards_db = load_cards()
    while True:
        t0 = time.time()
        try:
            res = snapshot(h, cards_db)
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"}
        render(res)
        print(f"   [{time.time()-t0:.1f}s]", file=sys.stderr)
        if not args.watch:
            break
        time.sleep(args.watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
