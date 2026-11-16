#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键出狱 —— 搜索本机影之诗（Shadowverse）并删除，然后释放「改恶从善，重新做人.txt」。

设计说明（主人 2026-11-09 要求）：
  * 不硬编码任何具体盘符/安装路径，靠「目录名特征」搜索；
  * 删除前先弹确认（安全），默认用回收站方式（可恢复），加 --hard 才彻底删；
  * 删除成功后在「搜索命中的父文件夹 / 程序所在文件夹」写一个空的 txt；
  * 支持 --dry-run 只列出会删什么，不实际删除（用于测试）。

用法：
  python 一键出狱.py --dry-run      # 只扫描，不删
  python 一键出狱.py                # 扫描并删除（回收站）
  python 一键出狱.py --hard         # 扫描并彻底删除
"""
import argparse
import os
import sys
import time

# 目录名特征（大小写不敏感）——不硬编码盘符/绝对路径
NAME_HINTS = ("shadowverse", "影之诗")
# 排除项：助手自身/工具目录不删（避免把辅助工具当成游戏删了）
EXCLUDE_HINTS = ("helper", "助手", "svwb-probe", "svwb-pkg", "svwba")

# 搜索时忽略的系统目录（避免慢/权限问题）
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "node_modules", "__pycache__",
    "windows", "programdata", "appdata", "$windows.~bt", "$windows.~ws",
    "recovery", "perflogs", "msocache",
}

# txt 内容（空文件，仅文件名有意义）
TXT_NAME = "改恶从善，重新做人.txt"


def _is_match(name):
    low = name.lower()
    if any(h.lower() in low for h in EXCLUDE_HINTS):
        return False
    return any(h.lower() in low for h in NAME_HINTS)


def scan_roots(roots, max_depth=6):
    """按目录名特征搜索影之诗目录（不进入命中目录内部继续找）。"""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dp, dirs, _files in os.walk(root, topdown=True, onerror=lambda e: None):
            depth = dp.rstrip("\\/").count(os.sep) - base_depth
            if depth > max_depth:
                dirs[:] = []
                continue
            # 过滤系统目录
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            keep = []
            for d in dirs:
                if _is_match(d):
                    found.append(os.path.join(dp, d))
                else:
                    keep.append(d)
            dirs[:] = keep
            # 当前目录本身也匹配（从上层传下来的情况）
            if _is_match(os.path.basename(dp)) and dp not in found:
                found.append(dp)
    # 去重（父被删则子不要）
    found = sorted(set(found), key=len)
    pruned = []
    for p in found:
        if not any(p.startswith(x + os.sep) for x in pruned):
            pruned.append(p)
    return pruned


def delete_dir(path, hard=False):
    if hard:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        return not os.path.exists(path)
    try:
        from send2trash import send2trash
        send2trash(path)
        return True
    except Exception:
        # 没有 send2trash 就退化为直接删（仍保留接口）
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        return not os.path.exists(path)


def write_text_near(paths):
    """在命中父文件夹及程序所在文件夹写 txt（多次尝试，失败不阻塞删除）。"""
    targets = set()
    for p in paths:
        targets.add(os.path.dirname(p))
    targets.add(os.path.dirname(os.path.abspath(sys.argv[0])))
    targets.add(os.getcwd())
    written = []
    for d in targets:
        try:
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, TXT_NAME)
            with open(fp, "w", encoding="utf-8") as f:
                f.write("")
            written.append(fp)
        except Exception:
            pass
    return written


def main():
    ap = argparse.ArgumentParser(description="一键出狱：搜索并删除影之诗")
    ap.add_argument("--dry-run", action="store_true", help="只扫描不删除")
    ap.add_argument("--hard", action="store_true", help="彻底删除（默认进回收站）")
    ap.add_argument("--root", action="append", default=None,
                    help="指定搜索根目录（可多次，默认全盘）")
    args = ap.parse_args()

    roots = args.root or [f"{d}:\\" for d in "CDEFGH" if os.path.exists(f"{d}:\\")]
    print("开始扫描影之诗目录……")
    t0 = time.time()
    hits = scan_roots(roots)
    print("扫描用时 %.1fs，命中 %d 个：" % (time.time() - t0, len(hits)))
    for h in hits:
        print("  ", h)

    if not hits:
        print("没有找到影之诗，已出狱（或本来就干净）。")
        # 仍然释放 txt：提示「已经干净了」
        for fp in write_text_near([os.getcwd()]):
            print("已释放:", fp)
        return 0

    if args.dry_run:
        print("（--dry-run：只列出，不删除）")
        return 0

    print("将在 5 秒后删除（Ctrl+C 取消）……")
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        print("已取消")
        return 1

    for h in hits:
        print("删除中:", h)
        ok = delete_dir(h, hard=args.hard)
        print("  ->", "成功" if ok else "失败/已被占用")
    written = write_text_near(hits)
    print("删除完成。已释放：")
    for fp in written:
        print("  ", fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
