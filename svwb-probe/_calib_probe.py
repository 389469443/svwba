#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动核对探针：一边打一边存「画面 + 内存读数」的成对证据。

为什么需要：人手去抓「同一瞬间的截图和内存」几乎不可能（战斗只有几秒），
而判断"判必败却赢了"到底是**读数错**还是**规则缺**，只能靠这种成对证据。

工作方式：
  · 每 0.8 秒看一次阶段（屏幕判定，很便宜）
  · 一旦进入 battle，就**同时**抓屏 + 读内存（相差几毫秒），
    存成 logs/核对-HHMMSS.png 和 .txt
  · 每场最多存 3 对（开战瞬间、中段、末段），避免刷爆磁盘
  · 只保留最近 60 对，自动清理
"""
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shadow_warchess_reader import find_game_pid, open_proc, load_cards  # noqa: E402
import read_world as RW       # noqa: E402
import grab                   # noqa: E402
import phase as PH            # noqa: E402

LOGS = os.path.join(HERE, "logs")
KEEP = 60          # 最多保留多少对
PER_BATTLE = 3     # 每场最多存几对


def _cleanup():
    try:
        files = sorted(
            (os.path.join(LOGS, f) for f in os.listdir(LOGS)
             if f.startswith("核对-")),
            key=os.path.getmtime, reverse=True)
        for f in files[KEEP * 2:]:
            os.remove(f)
    except Exception:
        pass


def main():
    os.makedirs(LOGS, exist_ok=True)
    pid = 0
    h = None
    db = None
    last_battle_key = None
    saved = 0
    print("核对探针启动", flush=True)
    while True:
        try:
            cur, _name = find_game_pid()
            if not cur:
                time.sleep(2)
                continue
            if cur != pid:
                pid = cur
                RW.WD_PID[0] = pid
                h = open_proc(pid)
                db = load_cards()
                print("游戏进程 %d" % pid, flush=True)

            img = grab.grab_screen()
            if img is None:
                time.sleep(1)
                continue
            ph = PH.phase_of(img)
            if ph != "battle":
                last_battle_key = None
                saved = 0
                time.sleep(1)
                continue

            if saved >= PER_BATTLE:
                time.sleep(0.5)
                continue

            # 同时抓：先读内存（快，~ms），再存刚才那张屏（≈同一瞬间）
            snap = RW.snapshot(h, db, force_cold=False)
            if snap.get("error"):
                time.sleep(0.5)
                continue

            stamp = time.strftime("%H%M%S")
            png = os.path.join(LOGS, "核对-%s.png" % stamp)
            txt = os.path.join(LOGS, "核对-%s.txt" % stamp)
            try:
                img.save(png)
            except Exception:
                png = None
            lines = ["阶段=battle 时间=%s" % time.strftime("%Y-%m-%d %H:%M:%S"), ""]
            for side, label in (("player", "我方"), ("opponent", "对方")):
                p = next((x for x in (snap.get("players") or [])
                          if x.get("side") == label), None)
                if not p:
                    continue
                lines.append("%s 主战者=%s 等级=%s 血量=%s 随从=%d" % (
                    label, p.get("hero_id"), p.get("grade"), p.get("life"),
                    len(p.get("cards") or [])))
                for c in (p.get("cards") or []):
                    lines.append("    %s★ %s/%s %s" % (
                        c.get("star"), c.get("atk"), c.get("life"), c.get("name")))
                lines.append("")
            with io.open(txt, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            saved += 1
            print("已存 %s%s" % (os.path.basename(txt),
                                 " + 截图" if png else ""), flush=True)
            if saved == 1:
                _cleanup()
            time.sleep(1.2)
        except Exception as e:
            print("探针异常: %s" % e, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
