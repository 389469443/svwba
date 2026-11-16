#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X 字段探针（2026-10-18）
等 维尔伯特 / 徒姬 / 贝鲁 上场时，把它们的卡对象字段 dump 出来，
和我们的计数（guard_dead / nightmare_in / evolved）对照，定位 X 在哪个偏移。
只读、低频（每 4 秒一次缓存读）。
"""
import io
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "xfield_probe.txt")
OV = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing",
                  "shots", "live", "overlay.json")
TARGETS = ("维尔伯特", "徒姬", "贝鲁")


def counts():
    try:
        d = json.load(io.open(OV, encoding="utf-8-sig"))
        return {"evolve": d.get("evolveCount"),
                "special": d.get("specialEffects")
                or d.get("specialEffects") or [],
                "phase": d.get("phase")}
    except Exception:
        return {}


def main():
    fh = io.open(OUT, "w", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    import read_world as W

    emit("X 字段探针启动")
    h = None
    cards = None
    seen = set()
    for _ in range(150):                       # 最多 ~10 分钟
        try:
            if h is None:
                pid, _p = RW.find_game_pid()
                if pid:
                    h = RW.open_proc(pid)
                    cards = RW.load_cards()
                else:
                    time.sleep(4)
                    continue
            snap = W.snapshot(h, cards, force_cold=False)
            if snap.get("error"):
                time.sleep(4)
                continue
            me = next((p for p in snap.get("players") or []
                       if p.get("side") == "我方"), None)
            if not me:
                time.sleep(4)
                continue
            hit = []
            for c in me.get("cards") or []:
                name = str(c.get("name") or "")
                if any(t in name for t in TARGETS):
                    hit.append(c)
            if hit:
                info = counts()
                emit("=" * 50)
                emit("时间 %s  phase=%s evolve=%s"
                     % (time.strftime("%H:%M:%S"), info.get("phase"),
                        info.get("evolve")))
                for x in info.get("special") or []:
                    emit("   计数行: %s = %s"
                         % (x.get("desc"), x.get("value")))
                for c in hit:
                    obj = int(c["obj"], 16)
                    raw = RW.read(h, obj + 0x10, 0x40)
                    ints = ([struct.unpack_from("<i", raw, i)[0]
                             for i in range(0, 0x40, 4)] if raw else [])
                    emit("   %s  %s/%s  obj=%s" % (c.get("name"),
                                                   c.get("atk"), c.get("life"),
                                                   c.get("obj")))
                    emit("     +0x10..0x4c: %s" % ints)
                    for off in (0x10, 0x18, 0x20, 0x28):
                        d = RW.read(h, obj + off, 8)
                        if not d:
                            continue
                        q = struct.unpack("<Q", d)[0]
                        if 0x10000 < q < 0x7FFFFFFFFFFF:
                            blob = RW.read(h, q, 0x40)
                            if blob:
                                bi = [struct.unpack_from("<i", blob, k)[0]
                                      for k in range(0, 0x30, 4)]
                                emit("     ptr +0x%02x=%#x ints=%s"
                                     % (off, q, bi))
                    seen.add(c.get("name"))
            if len(seen) >= 2:
                emit("已看到 %s，探针结束" % sorted(seen))
                break
            time.sleep(4)
        except Exception as exc:
            emit("异常：%s" % exc)
            time.sleep(4)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
