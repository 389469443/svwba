#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行读取器并把输出**同时**显示到屏幕和写入日志文件。

为什么不直接在 .bat 里重定向：
  · `--watch` 是常驻循环，重定向到文件后屏幕上什么都看不到
  · 用 PowerShell 的 Tee-Object 做批处理嵌套引号极易出错（实测日志没生成）
所以改成这个 Python 包装器：逐行读子进程输出 → 打印到屏幕 + 追加到日志。

用法（一般由 读对局.bat 调用）：
  python run_and_log.py
  python run_and_log.py --once        # 各跑一次就退出（不常驻）
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOGDIR = os.path.join(HERE, "logs")

# 默认只跑 read_player.py：它带**阶段门控**（运营阶段/大厅自动跳过），
# 且能读双方等级 + 场上随从的卡名/星级/攻血。
# 首选 read_world.py：它走 Sv2WarchessWorldData，
# 直接拿到 <Player>/<Opponent> 两个引用，**不需要 OCR、不需要猜敌我**。
STEPS = [
    ("读取对局（WorldData 路线：我方/对方由字段位置直接确定）",
     "read_world.py"),
]
# 需要五源交叉验证/原始 dump 时，加 --full 走另一套
STEPS_FULL = [
    ("五源交叉验证（多布局 + 硬校验 + dump）",
     "shadow_warchess_reader.py"),
] + STEPS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一次，不常驻刷新")
    ap.add_argument("--full", action="store_true", help="额外跑五源交叉验证与原始 dump")
    args = ap.parse_args()

    os.makedirs(LOGDIR, exist_ok=True)
    log_path = os.path.join(LOGDIR, time.strftime("运行-%Y%m%d-%H%M%S.txt"))
    logf = open(log_path, "w", encoding="utf-8-sig")   # BOM：记事本打开不乱码

    def emit(line):
        s = line.rstrip("\r\n")
        if s:
            print(s)
        logf.write(s + "\n")
        logf.flush()

    emit("=== 影之幻境 · 只读读取 ===")
    emit("时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    emit("日志: " + log_path)

    # 先报一次进程状态，方便判断"是不是游戏没开"
    try:
        sys.path.insert(0, HERE)
        from shadow_warchess_reader import find_game_pid
        pid, path = find_game_pid()
        emit(f"游戏进程: {pid if pid else '未找到（请先启动游戏，并进入战斗）'}")
    except Exception as e:
        emit(f"游戏进程探测失败: {e}")

    steps = STEPS_FULL if args.full else STEPS
    for title, script in steps:
        emit("")
        emit("=" * 60)
        emit(title)
        emit("=" * 60)
        cmd = [PY, os.path.join(HERE, script)]
        if not args.once:
            cmd += ["--watch", "2"]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 bufsize=1, universal_newlines=True, encoding="utf-8",
                                 errors="replace")
            for line in p.stdout:
                emit(line)
            p.wait()
        except KeyboardInterrupt:
            emit("")
            emit("（已中断）")
            try:
                p.kill()
            except Exception:
                pass
            break
        except Exception as e:
            emit(f"启动失败: {type(e).__name__}: {e}")

    emit("")
    emit("=== 结束 ===")
    logf.close()
    print("")
    print("=" * 60)
    print("结果已保存到：")
    print("  " + log_path)
    print("跟助手说一句「好了」即可，它会自己去读这个文件。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
