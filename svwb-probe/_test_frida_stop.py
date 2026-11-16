#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frida_live 优雅停止 / 参数解析 / 单实例的离线自测。

只读代码 + 写临时停止标记，**不 attach 游戏**，可随时重跑。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def main():
    import frida_live as F

    print("== 1. 参数解析 ==")
    check("无参数 → PID 0 / 默认 WAIT",
          F._parse_args(["x"]) == (0, F.DEFAULT_WAIT))
    check("PID 与 WAIT 正常解析",
          F._parse_args(["x", "1234", "99"]) == (1234, 99))
    check("非法 PID 不崩、回 0",
          F._parse_args(["x", "abc"]) == (0, F.DEFAULT_WAIT))
    check("非法 WAIT 回默认",
          F._parse_args(["x", "1234", "z"]) == (1234, F.DEFAULT_WAIT))
    check("默认等待 >= 1 小时", F.DEFAULT_WAIT >= 3600)

    print("== 2. 停止标记 ==")
    try:
        os.remove(F.STOP_FILE)
    except Exception:
        pass
    check("初始没有停止标记", F._stop_requested() is False)
    with open(F.STOP_FILE, "w"):
        pass
    check("写入标记后请求为 True", F._stop_requested() is True)
    F.clear_stop()
    check("clear_stop 后回到 False", F._stop_requested() is False)
    F.clear_stop()
    check("重复 clear_stop 不报错", True)

    print("== 3. 单实例 ==")
    import singleton
    check("测试锁第一次拿到", singleton.acquire("SvwbFridaSelfTest") is True)
    check("测试锁第二次被拒", singleton.acquire("SvwbFridaSelfTest") is False)

    print("== 4. app.py 停机接线 ==")
    with open(os.path.join(HERE, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    check("_kill 对 frida 有特判", 'if key == "frida"' in src)
    check("_ensure_frida 登记句柄", 'self.procs["frida"]' in src)
    check("stop_all 会优雅停 frida", 'self._kill("frida")' in src)
    check("app 与 frida_live 使用同一停止文件",
          os.path.join(HERE, "logs", "_frida.stop") == F.STOP_FILE)

    print("== 5. 宠物菜单 ==")
    with open(os.path.join(HERE, "pet_view.py"), encoding="utf-8") as fh:
        pv = fh.read()
    check("右键菜单恢复「导入模型…」", '"导入模型…"' in pv)
    check("导入失败写日志", 'pet-err.txt' in pv)

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
