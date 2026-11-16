#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内存字段探针（2026-10-18）

用途：游戏运行时把"可能放着本局统计的地方"全部 dump 出来，方便校准
  ① 本局进化次数（已用进化点 / 进化列表）
  ② 战斗阶段被破坏的守护随从数
  ③ 光环（claim_aura）的持久化位置
而不是靠轮询去猜。

用法（游戏开着的时候）：
    python -u probe_stats.py
输出：logs/stats_probe.txt

说明：只读，不改任何东西。
"""
import io
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = os.path.join(HERE, "logs", "stats_probe.txt")


def dump(emit, label, data, base=0):
    if not data:
        emit("%s: <读不到>" % label)
        return
    ints = [struct.unpack_from("<i", data, i)[0] for i in range(0, len(data) - 3, 4)]
    pts = [struct.unpack_from("<Q", data, i)[0] for i in range(0, len(data) - 7, 8)]
    emit("%s (base=+0x%x, %d 字节)" % (label, base, len(data)))
    for i in range(0, len(ints), 4):
        emit("  +0x%02x: %s" % (base + i * 4,
                                " ".join("%d" % v for v in ints[i:i + 4])))
    emit("  指针候选：%s" % ", ".join("+0x%02x=%#x" % (base + i * 8, v)
                                     for i, v in enumerate(pts)
                                     if 0x10000 < v < 0x7FFFFFFFFFFF))


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fh = io.open(OUT, "w", encoding="utf-8")

    def emit(s=""):
        print(s)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    import read_world as W

    pid, path = RW.find_game_pid()
    if not pid:
        emit("游戏没开（find_game_pid 返回 None）")
        fh.close()
        return 1
    emit("游戏 PID=%s  %s" % (pid, path))
    h = RW.open_proc(pid)
    cards = RW.load_cards()

    snap = W.snapshot(h, cards, force_cold=True)
    if snap.get("error"):
        emit("snapshot 失败：%s" % snap["error"])
        fh.close()
        return 1

    for p in snap.get("players") or []:
        emit("=" * 60)
        emit("玩家 %s  obj=%s" % (p.get("side"), p.get("obj")))
        emit("  已知：uid=%s hero=%s life=%s grade=%s evo=%s"
             % (p.get("uid"), p.get("hero_id"), p.get("life"),
                p.get("grade"), p.get("evolve_point")))
        emit("  进化点：used=%s max=%s super=%s max_super=%s"
             % (p.get("evolve_point"), p.get("max_evolve_point"),
                p.get("super_evolve_point"), p.get("max_super_evolve_point")))
        addr = int(p["obj"], 16)
        # 0x10..0x60：玩家本体（含进化点字段，验证用）
        dump(emit, "  player +0x10", RW.read(h, addr + 0x10, 0x50), 0x10)
        # 0x50..0x90：List<BattleCardBase> 指针群（Shop/Hand/Battle/HeroSkill/Skill/Special/LocalEventMgr）
        raw = RW.read(h, addr + 0x50, 0x40)
        if raw:
            for i in range(0, 0x40, 8):
                ptr = struct.unpack_from("<Q", raw, i)[0]
                if 0x10000 < ptr < 0x7FFFFFFFFFFF:
                    lst = W.read_list_objects(h, ptr, maxn=24)
                    tag = {
                        0x50: "ShopCardList", 0x58: "HandCards",
                        0x60: "BattleCards", 0x68: "HeroSkillCardList",
                        0x70: "SkillCards", 0x78: "SpecialCardList",
                        0x80: "LocalEventMgr",
                    }.get(0x50 + i, "+0x%02x" % (0x50 + i))
                    if lst:
                        names = []
                        for ptr_card in lst:
                            if not isinstance(ptr_card, int):
                                continue
                            cd = W.read_card(h, ptr_card) or {}
                            cid = cd.get("card_id")
                            names.append(str(cid) if cid else "?")
                        emit("  %-16s ptr=%#x  size=%d  card_ids=%s"
                             % (tag, ptr, len(lst), ",".join(names[:12])))
                    else:
                        emit("  %-16s ptr=%#x  (不是 List / 读不到)" % (tag, ptr))

    emit("\n已写入 %s" % OUT)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
