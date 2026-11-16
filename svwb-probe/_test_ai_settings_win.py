#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 设置窗口自测：模型是下拉框 + 窗口半透明。"""
import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ai_settings
import companion

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


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def main():
    root = tk.Tk()
    root.withdraw()
    win = ai_settings.open_settings(root)
    try:
        root.update()
        a = float(win.attributes("-alpha"))
        check("AI 设置窗口透明 0.9", abs(a - 0.9) < 0.02, a)

        cbs = [x for x in walk(win) if x.winfo_class() == "TCombobox"]
        check("模型是下拉框", len(cbs) >= 1, len(cbs))

        # 按选项内容找（顺序可能因历史 API 行而变）
        import pet_view as _pv
        _size_vals = [str(s) for s in _pv.SIZES]
        _fps_vals = [str(f) for f in _pv.FPS_CHOICES]
        _pers_vals = [companion.PERSONALITIES[p][0]
                      for p in companion.PERSONALITIES]
        size_cb = next((c for c in cbs if list(c["values"]) == _size_vals), None)
        fps_cb = next((c for c in cbs if list(c["values"]) == _fps_vals), None)
        pers_cb = next((c for c in cbs if list(c["values"]) == _pers_vals), None)
        check("有模型大小下拉框（200/260/340）", size_cb is not None,
              _size_vals)
        check("有宠物帧率下拉框（8/12/24）", fps_cb is not None, _fps_vals)
        check("有性格下拉框（三种）", pers_cb is not None, _pers_vals)

        import inspect
        src = inspect.getsource(ai_settings.open_settings)
        check("保存会写性格", 'cfg["personality"]' in src)

        # 模型来源标签
        labels = [w.cget("text") for w in walk(win)
                  if w.winfo_class() == "Label"]
        check("底部有模型来源说明",
              any("模型来源" in str(t) and "氵六青" in str(t) for t in labels))

        import inspect
        src = inspect.getsource(ai_settings.open_settings)
        check("保存会写宠物大小", 'pet_cfg["size"]' in src)
        check("保存会写宠物帧率", 'pet_cfg["fps"]' in src)
        check("测试 AI 在后台线程（不卡窗口）",
              "threading.Thread" in src and "win.after(0" in src)

        # 模型下拉应对应当前提供商（配置可能是通义/火山等）
        if cbs:
            vals = list(cbs[0]["values"])
            _cfg = companion.load_config()
            _p = companion.PROVIDERS.get(_cfg.get("provider") or "")
            check("模型下拉对应当前提供商",
                  bool(vals) and (not _p
                                  or all(v in (_p.get("models") or [])
                                         for v in vals)), vals[:5])

        p = companion.PROVIDERS.get("火山方舟") or {}
        check("火山方舟是单个提供商", "火山方舟" in companion.PROVIDERS)
        check("火山方舟带 models 列表", bool(p.get("models")), p.get("models"))
    finally:
        try:
            win.destroy()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
