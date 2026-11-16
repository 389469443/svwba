#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
光环认领瞬间探针（2026-10-18）

用户抓不住认领瞬间，所以这里**高频轮询**（0.4 秒）玩家 SpecialCardList：
  · 一旦出现新的光环卡号（或条目变多），立刻 dump 那条特殊卡的参数对象；
  · 同时记录我们推算的光环条目（desc/atk/hp）做对照。
只读、走缓存；输出 logs/claim_probe.txt
"""
import io
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "claim_probe.txt")
OV = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing",
                  "shots", "live", "overlay.json")


def main():
    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    import read_world as W
    import watch_mem as WM

    emit("=" * 60)
    emit("光环认领探针启动 %s" % time.strftime("%H:%M:%S"))
    h = None
    cards = None
    prev = []
    while True:
        try:
            if h is None:
                pid, _p = RW.find_game_pid()
                if pid:
                    h = RW.open_proc(pid)
                    cards = RW.load_cards()
                else:
                    time.sleep(1.0)
                    continue
            snap = W.snapshot(h, cards, force_cold=False)
            if snap.get("error"):
                time.sleep(0.4)
                continue
            me = next((p for p in snap.get("players") or []
                       if p.get("side") == "我方"), None)
            if not me:
                time.sleep(0.4)
                continue
            # 读 SpecialCardList 的**对象地址**，不只卡号
            try:
                ptr = struct.unpack("<Q", RW.read(h, int(me["obj"], 16) + 0x78, 8))[0]
                items = W.read_list_objects(h, ptr, maxn=64) or []
            except Exception:
                items = []
            ids = [W.read_card_id(h, it) for it in items]
            if ids != prev and len(items) >= len(prev):
                new_items = items[len(prev):] if len(items) > len(prev) else []
                emit("-" * 50)
                emit("[%s] 条目 %d → %d  ids=%s" % (
                    time.strftime("%H:%M:%S"), len(prev), len(items), ids))
                # 我们的推算值
                try:
                    ov = json.load(io.open(OV, encoding="utf-8-sig"))
                    emit("  面板光环: %s" % [
                        (e.get("desc"), e.get("atk"), e.get("hp"))
                        for e in (ov.get("leaderEffects") or [])])
                except Exception:
                    pass
                for it in (new_items or items[-2:]):
                    emit("  新特殊卡 obj=%#x" % it)
                    raw = RW.read(h, it, 0x80)
                    if raw:
                        ints = [struct.unpack_from("<i", raw, k)[0]
                                for k in range(0, 0x60, 4)]
                        emit("     ints: %s" % ints)
                    for off in range(0x10, 0x60, 8):
                        d = RW.read(h, it + off, 8)
                        if not d:
                            continue
                        q = struct.unpack("<Q", d)[0]
                        if not (0x10000 < q < 0x7FFFFFFFFFFF):
                            continue
                        blob = RW.read(h, q, 0x80)
                        if not blob:
                            continue
                        bi = [struct.unpack_from("<i", blob, k)[0]
                              for k in range(0, 0x40, 4)]
                        emit("     ptr +0x%02x=%#x ints=%s" % (off, q, bi))
                if not new_items:
                    emit("  （条目数变多，但没抓到新增对象）")
            prev = ids if ids else prev
            time.sleep(0.4)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            emit("异常：%s" % exc)
            time.sleep(1.0)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
