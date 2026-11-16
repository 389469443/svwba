#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PhaseTracker 单元测试。

覆盖三种真实会遇到的采样序列：
  1. 正常开战
  2. 开战瞬间抖出一个过渡态（旧逻辑会算两场）
  3. 过渡态持续几个采样点（旧逻辑会漏掉整场）

用法：python test_phase.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "watch_mem.py")


def load_tracker():
    """只从 watch_mem.py 里取出 PhaseTracker 类来执行，不跑主程序"""
    src = open(WATCH, encoding="utf-8").read()
    tree = ast.parse(src)
    node = next((n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "PhaseTracker"),
                None)
    if node is None:
        raise SystemExit("watch_mem.py 里没有 PhaseTracker")
    mod = ast.Module(body=[node], type_ignores=[])
    ns = {}
    exec(compile(mod, WATCH, "exec"), ns)
    return ns["PhaseTracker"]


def run(PT, seq, confirm=2, min_dwell=0.8, step=0.2):
    tr = PT(confirm=confirm, min_dwell=min_dwell)
    t = 1000.0
    edges = []
    for ph in seq:
        t += step
        e, _cur = tr.update(ph, t)
        if e:
            edges.append(e)
    return edges


def main():
    PT = load_tracker()
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {name}")
        else:
            failed += 1
            print(f"  ✗ {name} {detail}")

    print("=== PhaseTracker 测试 ===")

    e = run(PT, ["other", "operate", "operate", "battle", "battle",
                 "battle", "battle", "battle", "battle",
                 "operate", "operate", "operate"])
    print("  场景1 正常开战        →", e)
    check("正常开战只产生 1 个 rising", e.count("rising") == 1, e)
    check("结束后有 falling", e.count("falling") == 1, e)

    e = run(PT, ["operate", "battle", "other", "battle", "battle",
                 "battle", "operate"])
    print("  场景2 过渡态抖一下    →", e)
    check("抖动不会重复计一场", e.count("rising") == 1, e)

    e = run(PT, ["operate", "other", "other", "other", "battle", "battle",
                 "battle", "operate"])
    print("  场景3 过渡态较长      →", e)
    check("过渡态较长仍能捕捉开战", e.count("rising") == 1, e)

    e = run(PT, ["battle", "battle", "battle", "battle"])
    print("  场景4 一开始就在战斗  →", e)
    check("从 None 进入战斗也算 rising", e.count("rising") == 1, e)

    e = run(PT, ["operate"] * 10)
    print("  场景5 一直运营        →", e)
    check("一直运营不产生 rising", e.count("rising") == 0, e)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
