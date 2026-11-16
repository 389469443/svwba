#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proto 对象探针（2026-10-18）

目标：在**不注入、不 hook、不解密**的前提下，把服务器下发的、客户端已经解好的
      proto 对象在内存里定位出来：
        · S_THISGAME_CARD_REC（本局统计：被破坏的卡 / 打出的卡 …）
        · NotifyEventEvolve（进化事件，含 EvolvedUnits）
      先用 Sv2WarchessBattlePlayerBase 验证方法（它的实例地址我们已知）。

做法：find_class 找到类元数据 → scan_qwords 在私有内存里搜"值等于 klass"的位置
      → 这些位置就是对象头（对象 +0x0 = klass）。只做一次，之后走缓存。

输出：logs/proto_probe.txt
"""
import io
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = os.path.join(HERE, "logs", "proto_probe.txt")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fh = io.open(OUT, "w", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW

    pid, path = RW.find_game_pid()
    if not pid:
        emit("游戏没开")
        fh.close()
        return 1
    emit("游戏 PID=%s %s" % (pid, path))
    h = RW.open_proc(pid)

    # 已知实例地址（用于验证扫描方法）：从 snapshot 拿两个玩家对象
    known = []
    try:
        import read_world as W
        snap = W.snapshot(h, RW.load_cards(), force_cold=False)
        for p in snap.get("players") or []:
            known.append(int(p["obj"], 16))
        emit("已知玩家对象：%s" % [hex(x) for x in known])
    except Exception as exc:
        emit("（拿不到已知玩家对象：%s）" % exc)

    targets = [
        ("Sv2WarchessBattlePlayerBase", "Sv2Warchess", True),
        ("S_THISGAME_CARD_REC", "Netease.GameNet.MOS_PROTO", False),
        ("NotifyEventEvolve", "Sv2Warchess", False),
    ]

    for tname, ns, is_check in targets:
        emit("\n" + "=" * 60)
        emit("类：%s （命名空间 %s）" % (tname, ns))
        t0 = time.time()
        try:
            klass = RW.find_class(h, tname, ns)
        except Exception as exc:
            emit("  find_class 失败：%s" % exc)
            continue
        emit("  klass = %s（%.1fs）" % (hex(klass) if klass else None, time.time() - t0))
        if not klass:
            continue
        t1 = time.time()
        try:
            hits = RW.scan_qwords(h, {klass}, private_only=True)
        except Exception as exc:
            emit("  scan 失败：%s" % exc)
            continue
        emit("  命中 %d 个对象（扫描 %.1fs）" % (len(hits), time.time() - t1))
        if is_check and known:
            ok = [x for x in hits if x in known]
            emit("  验证：已知地址命中 %d/%d %s"
                 % (len(ok), len(known), [hex(x) for x in ok]))
        # dump 前几个对象的字段
        for addr in hits[:6]:
            raw = RW.read(h, addr, 0x60)
            if not raw:
                continue
            ints = [struct.unpack_from("<i", raw, i)[0]
                    for i in range(0, len(raw) - 3, 4)]
            ptrs = [struct.unpack_from("<Q", raw, i)[0]
                    for i in range(0, len(raw) - 7, 8)]
            emit("  obj %#x" % addr)
            emit("    ints: " + " ".join(str(v) for v in ints))
            emit("    ptrs: " + " ".join("%#x" % v for v in ptrs
                                         if 0x10000 < v < 0x7FFFFFFFFFFF))
    emit("\n完成，写入 %s" % OUT)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
