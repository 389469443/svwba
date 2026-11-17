#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键收集诊断信息 —— 给帮忙测 bug 的人用。

用法（一般由「收集诊断信息.bat」调用，也可以手动）：
    pythonw collect_diag.py            # 收集 → 诊断包/HJ诊断-<时间>.zip
    python  collect_diag.py --no-open  # 收集但不弹资源管理器

收进来什么
----------
  说明.txt            生成时间 / 版本 / 让测试者补充的问题模板
  日志/               svwb-probe\\logs 下最近 30 个 .txt/.log（每个最多留尾部 2MB）
  数据/               overlay.json / history.json / 各类 *settings*.json / 位置文件
  对照证据/           最近的「预测对照-*.png + .txt」（**定位读数/规则错误最有用**）
  游戏日志/           游戏自己的 Unity Player.log 尾部 4000 行（有就带，没有就跳过）
  环境/               .env-ready.json / python-path.txt / node-path.txt / 系统与进程摘要

设计原则
--------
  · 只读，不改任何配置、不碰游戏进程；
  · 体积可控（默认单个文件最多留 2MB 尾部，zip 一般 1~5MB）；
  · 不给测试者增加负担：双击一个 bat 就出一个 zip。
"""
import argparse
import glob
import io
import json
import os
import platform
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # 包根
GAME = os.path.join(ROOT, "dsh-shadowverse-huanjing")
LIVE = os.path.join(GAME, "shots", "live")
LOGS = os.path.join(HERE, "logs")
LIMIT = 2 * 1024 * 1024                            # 单文件最多 2MB
VERSION = "v0.1.0"


def _tail_text(path, max_bytes=LIMIT):
    """读文件尾部（大文件只留最后 max_bytes），尽量按 UTF-8 解码。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                head = "\r\n[……前面内容太多，已截断……]\r\n"
            else:
                head = ""
            raw = fh.read()
        for enc in ("utf-8-sig", "gbk", "latin-1"):
            try:
                return head + raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return head + raw.decode("utf-8", "replace")
    except OSError as exc:
        return "[读取失败] %s" % exc


def _recent(paths, n=30):
    items = [p for p in paths if os.path.isfile(p)]
    items.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return items[:n]


def _game_player_log():
    """Unity 的 Player.log：%LOCALAPPDATA%Low\\netease\\<游戏目录>\\Player.log"""
    base = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "netease")
    if not os.path.isdir(base):
        return None
    cands = []
    for d in os.listdir(base):
        p = os.path.join(base, d, "Player.log")
        if os.path.isfile(p):
            cands.append(p)
    if not cands:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


def _screen_size():
    try:
        import ctypes
        u = ctypes.windll.user32
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return None


def _processes():
    """只列跟本工具/游戏有关的进程（不进 zip 的其它隐私信息）。"""
    keys = ("python", "pythonw", "node", "mumu", "shadow", "影之诗")
    rows = []
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, timeout=15)
        text = out.stdout.decode("gbk", "replace") if out.stdout else ""
        for line in text.splitlines():
            low = line.lower()
            if any(k in low for k in keys):
                rows.append(line.strip())
    except Exception as exc:
        rows.append("[tasklist 失败] %s" % exc)
    return rows[:40]


def _env_summary():
    lines = ["时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "包版本：%s" % VERSION,
             "系统：%s" % platform.platform(),
             "Python（跑收集脚本的这个）：%s" % sys.version.split()[0]]
    marker = os.path.join(HERE, ".env-ready.json")
    if os.path.isfile(marker):
        lines.append("环境标记（.env-ready.json）：\r\n" + _tail_text(marker, 4096))
    else:
        lines.append("环境标记：不存在（还没跑过首次启动自检？）")
    scr = _screen_size()
    if scr:
        lines.append("屏幕分辨率（物理像素）：%dx%d" % scr)
    try:
        import ctypes
        dpi = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
        lines.append("系统缩放：%d%%" % int(dpi * 100))
    except Exception:
        pass
    lines.append("")
    lines.append("相关进程（tasklist 过滤）：")
    lines.extend("  " + r for r in _processes())
    return "\r\n".join(lines)


def _readme(ts):
    return "\r\n".join([
        "影之幻境助手 · 诊断包",
        "====================",
        "",
        "生成时间：%s" % ts,
        "包版本：%s" % VERSION,
        "",
        "【请补充这几句（很重要）】",
        "  1. 什么时候出的问题（大概几点、哪一局）",
        "  2. 你看到的现象是什么（比如：胜率明显不对 / 面板不显示 / 闪退 / 掉血对不上）",
        "  3. 你期望它是什么样",
        "  4. 能不能稳定复现（每次都这样 / 偶尔一次）",
        "  5. 当时游戏在干什么（开战瞬间 / 运营阶段 / 刚开一局 / 结算画面）",
        "",
        "【包里都有什么】",
        "  说明.txt        本文件",
        "  日志/           判定器、面板、控制台自己的日志（含 光环解析失败.log、环境检查.log）",
        "  数据/           overlay.json、战绩、各种设置、窗口位置",
        "  对照证据/       每场战斗自动存的「画面截图 + 当时的内存读数」（找读数/规则错误最有用的）",
        "  游戏日志/       游戏自己的 Unity 日志尾部（Player.log），有才带",
        "  环境/           Python/Node 版本、屏幕与缩放、相关进程",
        "",
        "【怎么发】",
        "  把这个 zip 直接发给作者即可，不需要其它东西。",
        "  注意：zip 里可能包含游戏画面截图，转发前自己看一眼。",
        "",
    ])


def collect(open_folder=True):
    ts = time.strftime("%Y%m%d-%H%M%S")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    outdir = os.path.join(ROOT, "诊断包")
    os.makedirs(outdir, exist_ok=True)
    zpath = os.path.join(outdir, "HJ诊断-%s.zip" % ts)
    added = 0

    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        def put(arc, text):
            nonlocal added
            z.writestr(arc, text)
            added += 1

        def put_file(arc, path):
            nonlocal added
            if not os.path.isfile(path):
                return False
            z.writestr(arc, _tail_text(path))
            added += 1
            return True

        put("说明.txt", _readme(stamp))

        # ① 本工具日志
        log_files = []
        for pat in ("*.txt", "*.log"):
            log_files.extend(glob.glob(os.path.join(LOGS, pat)))
        for p in _recent(log_files, 30):
            put_file("日志/" + os.path.basename(p), p)

        # ② 数据（覆盖判定器输出 + 战绩 + 设置 + 位置）
        data_cands = [
            (os.path.join(LIVE, "overlay.json"), "数据/overlay.json"),
            (os.path.join(LIVE, "overlay-settings.json"), "数据/overlay-settings.json"),
            (os.path.join(LIVE, "overlay-pos.json"), "数据/overlay-pos.json"),
            (os.path.join(LOGS, "history.json"), "数据/history.json"),
            (os.path.join(LOGS, "_leader_effects.json"), "数据/_leader_effects.json"),
            (os.path.join(LOGS, "panel-settings.json"), "数据/panel-settings.json"),
            (os.path.join(LOGS, "leader-settings.json"), "数据/leader-settings.json"),
            (os.path.join(LOGS, "console-settings.json"), "数据/console-settings.json"),
            (os.path.join(LOGS, "judge-settings.json"), "数据/judge-settings.json"),
            (os.path.join(LOGS, "panel-pos.json"), "数据/panel-pos.json"),
            (os.path.join(LOGS, "_last_game_record.json"), "数据/_last_game_record.json"),
            # 2026-11-13：找不到游戏进程时探针自己留的两个现场（谁的问题一眼看出来）
            (os.path.join(LOGS, "reader_status.json"), "数据/reader_status.json"),
            (os.path.join(LOGS, "进程识别.log"), "数据/进程识别.log"),
        ]
        for path, arc in data_cands:
            put_file(arc, path)

        # ③ 对照证据（截图 + 读数），最新的几对
        for pat in ("预测对照-*.txt", "预测对照-*.png"):
            for p in _recent(glob.glob(os.path.join(LOGS, pat)), 6):
                if os.path.isfile(p):
                    z.write(p, "对照证据/" + os.path.basename(p))
                    added += 1

        # ④ 游戏自己的 Unity 日志（有就带，只留尾部）
        glog = _game_player_log()
        if glog:
            put_file("游戏日志/" + os.path.basename(glog), glog)

        # ⑤ 环境
        put("环境/系统信息.txt", _env_summary())
        put_file("环境/python-path.txt", os.path.join(HERE, "python-path.txt"))
        put_file("环境/node-path.txt", os.path.join(HERE, "node-path.txt"))
        put_file("环境/.env-ready.json", os.path.join(HERE, ".env-ready.json"))

    size = os.path.getsize(zpath)
    print("诊断包已生成：")
    print("  %s" % zpath)
    print("  条目 %d 个，%.1f MB" % (added, size / 1048576.0))
    print("  把整个 zip 发给作者即可；里面可能含游戏截图，转发前看一眼。")
    if open_folder:
        try:
            subprocess.Popen(["explorer", "/select,", zpath])
        except Exception:
            pass
    return zpath


def main():
    ap = argparse.ArgumentParser(description="收集诊断信息并打包")
    ap.add_argument("--no-open", action="store_true", help="生成后不弹资源管理器")
    args = ap.parse_args()
    try:
        collect(open_folder=not args.no_open)
        return 0
    except Exception as exc:
        print("收集失败：%s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
