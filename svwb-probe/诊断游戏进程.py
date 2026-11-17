# -*- coding: utf-8 -*-
"""进程诊断：找出「为什么没读到游戏」。

在发布包根目录双击 `诊断游戏进程.bat` 即可，结果写到
`svwb-probe\\logs\\进程诊断.txt`，把它发回来就行。

它不读游戏内存、不注入、不改任何东西，只做三件事：
  1. 列出宿主上工作集最大的那些进程，带路径、说明为什么没被选中；
  2. 打印「程序实际会选哪一个」，选错还是没选到一眼就能看出来；
  3. 顺带看控制台的页签 / 开关状态（停在「影之幻境」页时读取不会启动）。

2026-11-13 改：判断规则不再在这里照抄一份，而是**直接用**
`shadow_warchess_reader` 里的那套（以前两边各抄一份，抄着抄着就走样了）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "logs", "进程诊断.txt")

lines = []


def say(s=""):
    lines.append(s)
    print(s, flush=True)


def main():
    import platform
    say("=" * 74)
    say("影之诗助手 · 进程诊断")
    say("时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    say("系统：%s" % platform.platform())
    say("Python：%s（%d 位）" % (sys.version.split()[0],
                             64 if sys.maxsize > 2 ** 32 else 32))
    say("包目录：%s" % os.path.dirname(HERE))
    say("=" * 74)

    try:
        import shadow_warchess_reader as RW
    except Exception as e:
        say("!! 无法 import shadow_warchess_reader：%s" % e)
        say("   （是不是没把本脚本放进 svwb-probe 目录？）")
        return 1

    keys = tuple(getattr(RW, "GAME_KEYS", ()))
    skip = tuple(getattr(RW, "SKIP", ()))
    min_ws = int(getattr(RW, "MIN_WS", 200 * 1048576))
    say("[规则] 名字命中 %s" % "/".join(keys))
    say("       排除 %s" % "/".join(skip))
    say("       工作集 ≥ %dMB，多个候选取最大的" % (min_ws // 1048576))
    say()

    # [0] 控制台自己的状态：停在「影之幻境」页时，实时读取不会自动启动
    say("[0] 控制台状态")
    cs_path = os.path.join(HERE, "logs", "console-settings.json")
    if os.path.isfile(cs_path):
        try:
            import json
            with open(cs_path, encoding="utf-8") as fh:
                cs = json.load(fh)
            tab = str(cs.get("tab") or "?")
            run = cs.get("running") or {}
            say("  当前页签：%s%s" % (tab, "（影之诗）" if tab == "sv"
                                    else "（影之幻境）"))
            say("  实时读取开关：%s" % ("开" if run.get("frida") else "关"))
            say("  记牌器开关：%s" % ("开" if run.get("deck") else "关"))
            if tab != "sv":
                say("  ⚠ 控制台不在「影之诗」页 —— 这时只有记牌器窗口开着，")
                say("    实时读取才会被自动拉起；否则它根本不会启动。")
            if not run.get("frida"):
                say("  ⚠ 实时读取开关是关的 —— 开着记牌器也不会去读游戏。")
        except Exception as e:
            say("  读取 console-settings.json 失败：%s" % e)
    else:
        say("  没有 logs\\console-settings.json（控制台还没跑过？）")
    say()

    # [1] 实际会选谁 —— 这是最关键的一段
    say("[1] 程序实际会选谁")
    try:
        cands = RW._scan_candidates()
    except Exception as e:
        cands = []
        say("  _scan_candidates() 抛异常：%s" % e)
    if cands:
        ws, pid, path, why = cands[0]
        say("  选中：pid=%s（%dMB，%s）" % (pid, ws // 1048576, why))
        say("        %s" % path)
        if len(cands) > 1:
            say("  其他候选（次选）：")
            for ws, pid, path, why in cands[1:6]:
                say("        %-8s %6dMB %s" % (pid, ws // 1048576, path))
    else:
        say("  选中：**无** —— 没找到游戏进程，所以实时读取不会启动。")
    say()

    # [2] 宿主上工作集最大的进程（看清模拟器到底叫什么）
    say("[2] 工作集最大的进程（前 30 个）")
    rows = []
    for pid in RW.enum_pids():
        if not pid:
            continue
        p = RW.pid_image(pid)
        if not p:
            continue
        rows.append((RW._workingset(pid), pid, p))
    rows.sort(reverse=True)
    for ws, pid, p in rows[:30]:
        base = os.path.basename(p).lower()
        verdict = "候选"
        try:
            ok, why = RW._verdict(p, base)
            verdict = "候选" if ok else why
        except Exception:
            pass
        say("  %-8d %7dMB %-12s %s" % (pid, ws // 1048576, verdict, p))
    say()

    # [3] 结论
    say("[3] 结论提示")
    if not cands:
        say("  · 没有任何进程命中关键词。基本可以确定：这个模拟器版本的进程名")
        say("    不在上面那串关键词里（或者关键词只出现在很深的路径里）。")
        say("    把本文件发回来，看着 [2] 里那个几个 GB 的进程就能补上。")
    else:
        say("  · 已经选中了上面那个进程。如果界面还是「读取未启动」，")
        say("    那就不是进程识别的问题，而是注入 / 版本兼容 —— 需要看")
        say("    svwb-probe\\logs\\frida-out.txt、frida_live.log。")

    say()
    say("诊断结束。把本文件（logs\\进程诊断.txt）发回即可。")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append("")
        lines.append("!! 诊断脚本自己崩了：")
        lines.extend(traceback.format_exc().splitlines())
        rc = 1
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("\n已保存：%s" % OUT)
    except Exception as e:
        print("保存失败：%s" % e)
    if "--pause" in sys.argv:
        try:
            input("\n按回车关闭...")
        except Exception:
            pass
    raise SystemExit(rc)
