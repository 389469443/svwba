#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务计数差分探针（2026-10-18）

思路：三个"任务"（进化数 / 被破坏守护数 / 战斗阶段梦魇进场数）一定存在游戏状态里的
某几个 int 上。每 0.8 秒把 WorldData 附近一段内存的 int32 与上次比对，
把 0..80 范围内发生变化的偏移 + 旧值/新值记录下来。
连续几次 +1 都指向同一偏移 → 那就是任务计数。

只读；输出 logs/quest_diff.txt
"""
import io
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "quest_diff.txt")
WINDOW = 8 << 20          # 8MB
STEP = 0.8


def main():
    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW

    emit("=" * 60)
    emit("任务计数差分探针启动 %s（窗口 %.0fMB）" % (time.strftime("%H:%M:%S"),
                                                  WINDOW / 1048576.0))
    h = None
    base = None
    prev = None
    votes = {}
    while True:
        try:
            if h is None:
                pid, _p = RW.find_game_pid()
                if not pid:
                    time.sleep(1.0)
                    continue
                h = RW.open_proc(pid)
                try:
                    cache = json.load(io.open(os.path.join(HERE, "logs",
                                                           "_world_cache.json"),
                                              encoding="utf-8"))
                    wd = int(cache[str(pid)]["wd_instance"], 16)
                    base = wd
                    emit("WorldData = %#x" % base)
                except Exception:
                    base = None
            if base is None:
                time.sleep(1.0)
                continue
            blob = RW.read(h, base, WINDOW)
            if not blob or len(blob) < WINDOW // 2:
                time.sleep(STEP)
                continue
            n = len(blob) // 4
            cur = struct.unpack_from("<%di" % n, blob, 0)
            if prev is not None:
                for i in range(n):
                    a, b = prev[i], cur[i]
                    if a == b or not (0 <= a <= 80 and 0 <= b <= 80):
                        continue
                    if b != a + 1 and b != a + 2 and b != a + 3:
                        continue
                    # 投票：同一个偏移反复 +N 才可能是任务计数
                    rec = votes.setdefault(i, {"n": 0, "last": 0.0,
                                               "vals": []})
                    now2 = time.time()
                    # 同一秒内的重复变化（动画抖动）不算
                    if now2 - rec["last"] < 1.2:
                        continue
                    rec["last"] = now2
                    rec["n"] += 1
                    rec["vals"].append((time.strftime("%H:%M:%S"), a, b))
                    if rec["n"] >= 3:
                        emit("[%s] ★候选 +0x%06x 变化 %d 次: %s"
                             % (time.strftime("%H:%M:%S"), i * 4, rec["n"],
                                rec["vals"][-6:]))
            prev = cur
            time.sleep(STEP)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            emit("异常：%s" % exc)
            time.sleep(2.0)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
