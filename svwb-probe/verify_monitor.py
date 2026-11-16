#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三方同时验证监视器（2026-10-18）

同时记录三路证据，方便事后对齐找问题：
  ① **画面**：定时截图（画面识别 / 人眼复核都靠它）
  ② **内存**：read_world 的实时读数（血量 / 等级 / 场上随从面板）
  ③ **判定输出**：overlay.json（阶段 / 胜率 / 光环 / 进化数 / 掉血预测）

输出（默认 svwb-probe/logs/verify/）：
  frames/        截图（jpg，只留最近 N 张）
  verify.csv     每次采样的三路数据
  events.csv     阶段切换 / 掉血实测 vs 预测（一行一个事件）
  monitor.log    自己的日志

用法（后台跑，不弹窗）：
  pythonw verify_monitor.py --interval 1.5 --shot-every 3
  python  verify_monitor.py --once      # 只采一次，测试用
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GAME = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing")
OVERLAY = os.path.join(GAME, "shots", "live", "overlay.json")
OUT = os.path.join(HERE, "logs", "verify")
FRAMES = os.path.join(OUT, "frames")
KEEP_FRAMES = 300


def _log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with io.open(os.path.join(OUT, "monitor.log"), "a",
                     encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def read_overlay():
    for _ in range(3):
        try:
            with io.open(OVERLAY, "rb") as fh:
                return json.loads(fh.read().decode("utf-8-sig"))
        except Exception:
            time.sleep(0.05)
    return None


def overlay_summary(d):
    if not isinstance(d, dict):
        return {}
    out = {k: d.get(k) for k in ("phase", "win", "phaseLabel")}
    for k in ("damage", "damageDist", "drop", "predictedDamage", "postHp",
              "ourHp", "oppHp", "evolveCount", "updatedAt"):
        if k in d:
            out[k] = d.get(k)
    le = d.get("leaderEffects")
    out["leaderEffects"] = len(le) if isinstance(le, list) else 0
    se = d.get("specialEffects")
    out["specialEffects"] = len(se) if isinstance(se, list) else 0
    return out


# ── 内存一路：缓存句柄只开一次 ─────────────────────────────────────────────
class Mem:
    def __init__(self):
        self.h = None
        self.cards = None
        self.last_err = ""

    def _open(self):
        import shadow_warchess_reader as RW
        pid, _path = RW.find_game_pid()      # 返回 (pid, 路径)
        if not pid:
            return False
        self.h = RW.open_proc(pid)
        self.pid = pid
        from shadow_warchess_reader import load_cards
        self.cards = load_cards()
        return True

    def snapshot(self, allow_cold=False):
        """返回 (我方血量, 等级, 场上随从列表, 错误)"""
        try:
            import read_world as RW2
        except Exception as exc:
            return None, None, [], "import 失败：%s" % exc
        if self.h is None:
            try:
                if not self._open():
                    return None, None, [], "游戏未运行"
            except Exception as exc:
                return None, None, [], "打开进程失败：%s" % exc
        try:
            snap = RW2.snapshot(self.h, self.cards,
                                force_cold=(True if allow_cold else False))
        except Exception as exc:
            return None, None, [], "snapshot 异常：%s" % exc
        if not isinstance(snap, dict) or "error" in snap:
            err = (snap or {}).get("error") if isinstance(snap, dict) else "?"
            return None, None, [], str(err)
        me = next((p for p in snap.get("players") or []
                   if p.get("side") == "我方"), None)
        if not me:
            return None, None, [], "只读到一方"
        board = []
        for c in me.get("cards") or []:
            try:
                board.append("%s*%s %s/%s" % (c.get("name"), c.get("star"),
                                              c.get("atk"), c.get("life")))
            except Exception:
                pass
        return me.get("life"), me.get("grade"), board, ""


def shot(tag=""):
    try:
        from PIL import ImageGrab
        import ctypes
        try:
            u = ctypes.windll.user32
            w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        except Exception:
            w, h = 1920, 1080
        img = ImageGrab.grab(bbox=(0, 0, w, h), all_screens=True)
        img = img.resize((int(img.width * 0.5), int(img.height * 0.5)))
        os.makedirs(FRAMES, exist_ok=True)
        name = time.strftime("%H%M%S") + ("_%s" % tag if tag else "") + ".jpg"
        path = os.path.join(FRAMES, name)
        img.convert("RGB").save(path, quality=62)
        _prune()
        return path
    except Exception as exc:
        _log("截图失败：%s" % exc)
        return None


def _prune():
    try:
        files = sorted((os.path.join(FRAMES, f) for f in os.listdir(FRAMES)),
                       key=os.path.getmtime)
        for p in files[:-KEEP_FRAMES]:
            os.remove(p)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=1.5)
    ap.add_argument("--shot-every", type=float, default=3.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    os.makedirs(FRAMES, exist_ok=True)
    vpath = os.path.join(OUT, "verify.csv")
    epath = os.path.join(OUT, "events.csv")
    new_v = not os.path.isfile(vpath)
    new_e = not os.path.isfile(epath)
    vf = io.open(vpath, "a", newline="", encoding="utf-8")
    ef = io.open(epath, "a", newline="", encoding="utf-8")
    vw = csv.writer(vf)
    ew = csv.writer(ef)
    if new_v:
        vw.writerow(["time", "phase", "win", "hp", "level", "board",
                     "leaderEffects", "specialEffects", "evolve", "note"])
    if new_e:
        ew.writerow(["time", "event", "phase", "detail"])

    mem = Mem()
    last_cold = 0.0
    last_phase = None
    last_hp = None
    last_shot = 0.0
    err_prev = ""
    miss = 0
    _log("监视开始：interval=%.1fs shot=%.1fs out=%s"
         % (args.interval, args.shot_every, OUT))

    while True:
        try:
            ts = time.strftime("%H:%M:%S")
            ov = overlay_summary(read_overlay())
            cold = (err_prev == "cache-miss"
                    and time.time() - last_cold > 60)
            hp, lv, board, err = mem.snapshot(allow_cold=cold)
            if cold:
                last_cold = time.time()
                if err:
                    _log("冷扫描也没读到：%s" % err)
                else:
                    _log("冷扫描完成：hp=%s 等级=%s 随从 %d"
                         % (hp, lv, len(board)))
            err_prev = err
            phase = ov.get("phase") or ("battle" if board else "other")

            # 阶段切换：存图 + 记事件
            if phase != last_phase:
                p = shot(phase)
                ew.writerow([ts, "phase", phase,
                             "prev=%s shot=%s" % (last_phase, os.path.basename(p) if p else "-")])
                ef.flush()
                _log("阶段：%s → %s（截图 %s）" % (last_phase, phase, p))
                last_phase = phase

            # 掉血实测 vs 预测（换阶段时血量下降 = 这一场掉的血）
            if hp is not None and last_hp is not None and hp < last_hp:
                drop = last_hp - hp
                pred = ov.get("damage") or ov.get("predictedDamage") or ov.get("drop")
                ew.writerow([ts, "damage", phase,
                             "实测掉血=%s 预测=%s 明细=%s"
                             % (drop, pred, json.dumps(ov, ensure_ascii=False))])
                ef.flush()
                _log("掉血：实测 %s / 预测 %s" % (drop, pred))
            if hp is not None:
                last_hp = hp

            vw.writerow([ts, phase, ov.get("win"), hp, lv, "|".join(board),
                         ov.get("leaderEffects"), ov.get("specialEffects"),
                         ov.get("evolveCount"), err])
            vf.flush()

            if args.once:
                _log("--once 采样完成")
                break

            now = time.time()
            if now - last_shot >= args.shot_every:
                last_shot = now
                shot("")

            if err:
                miss += 1
                if miss % 40 == 1:
                    _log("内存读取失败 %d 次：%s" % (miss, err))
            else:
                miss = 0

            time.sleep(max(0.5, args.interval))
        except KeyboardInterrupt:
            break
        except Exception:
            _log("循环异常（继续）：\\n" + traceback.format_exc())
            time.sleep(2)

    vf.close()
    ef.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
