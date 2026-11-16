#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内存 → 模拟器 → 胜率 + 扣血概率（完整闭环）。

数据源：Sv2WarchessWorldData 里的 <Player>/<Opponent> 两个引用
        （由 read_world.py 读取，已在真机验证：我方血量与画面完全一致）

流程：
  1. 从内存读双方：等级 / 血量 / 场上随从（卡名·星级·攻·血）
  2. **空场规则**先判：一方空场 = 那方直接输；双方都空 = 平局（不需要模拟）
  3. 否则调 lib/index.js 跑蒙特卡洛，得出胜率与伤害分布
  4. 结果同时写进悬浮层 feed（tools/overlay_feed.py），并打印到终端

用法：
  python sim_from_memory.py              # 读一次并算
  python sim_from_memory.py --sims 50000
  python sim_from_memory.py --no-phase   # 跳过阶段判别（调试）
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "dsh-shadowverse-huanjing", "tools")
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from shadow_warchess_reader import find_game_pid, open_proc, load_cards  # noqa: E402
import read_world as RW                                                # noqa: E402


def apply_empty_rule(player, opponent):
    """
    用户确认的规则：一方空场 → 那方直接输；双方空 → 平局。

    ⚠️ **只有确认读到过列表时才敢用这条规则**。
    如果某一方是"读取失败"（cards_unknown），它的 board 也会是空，
    但那是我们没读到，不是真的空场 —— 此时若套用规则会判出
    "对方空场 → 必胜"这种完全错误的结论（实测踩过）。
    """
    if player.get("cards_unknown") or opponent.get("cards_unknown"):
        return None                     # 有读取失败 → 不适用空场规则
    mb = player.get("board") or []
    ob = opponent.get("board") or []

    def dmg(side):
        lv = int(side.get("level") or 1)
        return lv + sum(int(c.get("star") or 1) for c in (side.get("board") or []))

    if mb and ob:
        return None
    if not mb and not ob:
        return {"win": 0, "loss": 0, "draw": 100, "detail": "双方空场 · 平局（规则判定）",
                "damageDist": {}, "rule": "both"}
    if not ob:
        d = dmg(player)
        return {"win": 100, "loss": 0, "draw": 0,
                "detail": f"对方空场 · 必胜（规则判定）  对方掉血 {d} 点",
                "damageDist": {"toOpponent": [{"damage": d, "pPercent": 100.0}]},
                "rule": "opp_empty"}
    d = dmg(opponent)
    return {"win": 0, "loss": 100, "draw": 0,
            "detail": f"我方空场 · 必败（规则判定）  我方掉血 {d} 点",
            "damageDist": {"toPlayer": [{"damage": d, "pPercent": 100.0}]},
            "rule": "me_empty"}


def build_battle(players):
    """把内存读数转成引擎输入"""
    me = next((p for p in players if p.get("side") == "我方"), None)
    op = next((p for p in players if p.get("side") == "对方"), None)
    if not me or not op:
        return None

    def side(p):
        board = []
        for c in p.get("cards") or []:
            board.append({
                "name": c.get("name") or f"card{c.get('card_id')}",
                "attack": max(0, int(c.get("atk") or 0)),
                "health": max(1, int(c.get("life") or 1)),
                "star": max(1, int(c.get("star") or 1)),
            })
        # 主战者光环：给引擎用（召唤出来的随从要按条件吃加成）
        # **双方都要**：对方的光环同样影响战局（用户要求算进胜率）
        _auras = []
        _side_key = "player" if p.get("side") == "我方" else "opponent"
        try:
            import watch_mem as _W
            for e in (_W._LEADER_EFFECTS or []):
                if (e.get("side") or "player") != _side_key:
                    continue
                c = e.get("cond")
                if not c:
                    c = _W._aura_condition(e.get("desc") or "")
                    # desc 已经不含数值了，数值要用条目自己的字段
                    c = dict(c, atk=int(e.get("atk") or 0), hp=int(e.get("hp") or 0))
                _auras.append({"tribe": c.get("tribe"),
                               "maxStar": c.get("maxStar"),
                               # 按卡名的光环（僵尸光环）必须带 name，
                               # 否则引擎会当成"所有随从 +N/+N"
                               "name": c.get("name"),
                               "atk": int(c.get("atk") or 0),
                               "hp": int(c.get("hp") or 0)})
        except Exception:
            _auras = []

        return {"auras": _auras,
                "hp": max(1, int(p.get("life") or 1)),
                "level": max(1, int(p.get("grade") or 1)),
                # 主战者技能要用：加能力的效果读不到面板，只能靠它补
                "hero": int(p.get("hero_id") or 0),
                "board": board,
                "cards_unknown": bool(p.get("cards_unknown"))}

    return {"phase": "battle", "player": side(me), "opponent": side(op)}


def dist_rows(damage_obj):
    if not damage_obj or not damage_obj.get("entries"):
        return []
    tot = sum(e["count"] for e in damage_obj["entries"]) or 1
    rows = [{"damage": e["value"], "count": e["count"],
             "pPercent": round(e["count"] / tot * 100, 1)}
            for e in damage_obj["entries"]]
    rows.sort(key=lambda r: r["damage"])
    return rows


def render(battle, res):
    me, op = battle["player"], battle["opponent"]
    print("")
    print(f"我方  等级{me['level']}  血量{me['hp']}  随从{len(me['board'])}张")
    for c in me["board"]:
        print(f"    {c['star']}★ {c['attack']}/{c['health']}  {c['name']}")
    print(f"对方  等级{op['level']}  血量{op['hp']}  随从{len(op['board'])}张")
    for c in op["board"]:
        print(f"    {c['star']}★ {c['attack']}/{c['health']}  {c['name']}")
    print("")
    print(f"★ 胜率 {res['win']}%   败率 {res['loss']}%   平率 {res['draw']}%")
    if res.get("detail"):
        print(f"  {res['detail']}")
    for key, label in (("toOpponent", "对方掉血"), ("toPlayer", "我方掉血")):
        rows = (res.get("damageDist") or {}).get(key) or []
        if not rows:
            continue
        seg = "  ".join(f"{r['damage']}点 {r['pPercent']:.0f}%" for r in rows[:10])
        lo, hi = rows[0]["damage"], rows[-1]["damage"]
        print(f"  {label}：{seg}")
        if lo != hi:
            print(f"              （范围 {lo}~{hi} 点）")


def feed_overlay(res):
    try:
        import overlay_feed as FEED
        FEED.write({
            "win": res["win"], "loss": res["loss"], "draw": res["draw"],
            "damageDist": res.get("damageDist") or {},
            "detail": res.get("detail") or "",
            "note": "✓ 内存读取" if not res.get("rule") else "空场规则",
        })
    except Exception as e:
        print(f"（写悬浮层失败: {e}）", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--no-phase", action="store_true")
    ap.add_argument("--no-feed", action="store_true")
    args = ap.parse_args()

    pid = args.pid or find_game_pid()[0]
    if not pid:
        print("找不到游戏进程")
        return 1
    h = open_proc(pid)
    RW.WD_PID[0] = pid
    cards_db = load_cards()

    if not args.no_phase:
        try:
            import grab as GRAB
            img = GRAB.grab_screen()
            if img is not None:
                import phase as PHASE
                ph = PHASE.phase_of(img)
                if ph != "battle":
                    print(f"【{'运营阶段' if ph == 'operate' else '不在对局'}】跳过")
                    return 0
        except Exception as e:
            print(f"（阶段判别失败，继续: {e}）", file=sys.stderr)

    t0 = time.time()
    snap = RW.snapshot(h, cards_db)
    if "error" in snap:
        print(f"[!] {snap['error']}")
        return 2
    battle = build_battle(snap.get("players") or [])
    if not battle:
        print("[!] 没读到双方玩家")
        return 2
    print(f"# 内存读取耗时 {time.time()-t0:.1f}s")

    # ---- 空场规则优先 ----
    empty = apply_empty_rule(battle["player"], battle["opponent"])
    if empty is not None:
        render(battle, empty)
        if not args.no_feed:
            feed_overlay(empty)
        return 0

    # ---- 跑蒙特卡洛 ----
    import damage_dist as DD
    t1 = time.time()
    raw = DD.run_sim(battle, sims=args.sims, seed="mem")
    # ⚠️ 引擎的 winRate 是**字典**：{"player": 胜%, "opponent": 败%, "draw": 平%}
    #    之前当成数字用，直接崩（TypeError: dict doesn't define __round__）
    wr = raw.get("winRate") or {}
    if isinstance(wr, dict):
        win = int(round(wr.get("player") or 0))
        loss = int(round(wr.get("opponent") or 0))
        draw = int(round(wr.get("draw") or 0))
    else:
        win = int(round(wr or 0))
        loss, draw = 100 - win, 0
    res = {"win": win, "loss": loss, "draw": draw,
           "damageDist": {}, "detail": ""}
    dmg = raw.get("damage") or {}
    for key, label in (("toOpponent", "toOpponent"), ("toPlayer", "toPlayer")):
        dobj = dmg.get(key) if isinstance(dmg, dict) else None
        rows = dist_rows(dobj)
        if rows:
            res["damageDist"][key] = rows
    render(battle, res)
    print(f"  （模拟 {args.sims} 次，耗时 {time.time()-t1:.1f}s）")
    if not args.no_feed:
        feed_overlay(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
