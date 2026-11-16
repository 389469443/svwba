#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 陪玩设置窗口（独立，可被记牌器/宠物共用）。"""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import companion

BG = "#0f1420"
BG2 = "#1d2433"
FG = "#e8edf7"
GOLD = "#f2c66d"
GOLD2 = "#9aa7bd"
DIM = "#8b95a8"
FONT = ("Microsoft YaHei UI", 9)


def _dark_title(win):
    """Windows 10/11：系统标题栏改成暗色（窗口显示后再设置才能拿到最终 HWND）。"""
    def _apply():
        try:
            import ctypes
            hwnd = (ctypes.windll.user32.GetParent(win.winfo_id())
                    or win.winfo_id())
            v = ctypes.c_int(1)
            for attr in (20, 19):
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
                except Exception:
                    pass
        except Exception:
            pass
    try:
        win.after(150, _apply)
    except Exception:
        pass


def open_settings(parent=None):
    cfg = companion.load_config()
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("AI 陪玩设置")
    win.configure(bg=BG)
    _dark_title(win)
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass
    # 功能窗口统一半透明（用户 2026-10-29 要求）
    try:
        win.attributes("-alpha", 0.9)
    except Exception:
        pass

    pad = tk.Frame(win, bg=BG)
    pad.pack(fill="both", expand=True, padx=12, pady=10)
    vars_ = {}

    # 提供商预设：选中后自动填 base_url / model
    prov_var = tk.StringVar(value=cfg.get("provider") or "自定义")

    def _apply_provider(name):
        p = companion.PROVIDERS.get(name)
        if not p:
            return
        v = vars_.get("base_url")
        if v is not None:
            v.set(p["base_url"])
        models = list(p.get("models") or [])
        if not models and p.get("model"):
            models = [p["model"]]
        try:
            model_cb["values"] = models
        except Exception:
            pass
        model_var.set(p.get("model") or (models[0] if models else ""))
        cfg["provider"] = name
        if "seedance" in (model_var.get() or "").lower():
            try:
                messagebox.showwarning(
                    "提示", "Seedance 是视频生成模型，聊天陪玩用不了；\n"
                    "请选 doubao-seed-evolving 或其它文本模型。",
                    parent=win)
            except Exception:
                pass

    f0 = tk.Frame(pad, bg=BG)
    f0.pack(fill="x", pady=2)
    tk.Label(f0, text="提供商", bg=BG, fg=GOLD2, font=FONT, width=8,
             anchor="w").pack(side="left")
    prov_names = list(companion.PROVIDERS.keys())
    pm = tk.OptionMenu(f0, prov_var, "自定义", *prov_names,
                       command=_apply_provider)
    pm.config(bg=BG2, fg=FG, relief="flat", bd=0, font=FONT,
              highlightthickness=0)
    pm["menu"].config(bg=BG, fg=FG, activebackground=BG2,
                      activeforeground=GOLD, font=FONT)
    pm.pack(side="left", fill="x", expand=True)

    def row(label, key, show=""):
        f = tk.Frame(pad, bg=BG)
        f.pack(fill="x", pady=2)
        tk.Label(f, text=label, bg=BG, fg=GOLD2, font=FONT, width=8,
                 anchor="w").pack(side="left")
        v = tk.StringVar(value=str(cfg.get(key) or ""))
        tk.Entry(f, textvariable=v, bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", font=FONT, show=show).pack(
            side="left", fill="x", expand=True)
        vars_[key] = v

    row("API Key", "api_key", show="*")
    row("Base URL", "base_url")

    # 模型：可下拉选（切换提供商时更新选项），也能手输自定义
    f_model = tk.Frame(pad, bg=BG)
    f_model.pack(fill="x", pady=2)
    tk.Label(f_model, text="模型", bg=BG, fg=GOLD2, font=FONT, width=8,
             anchor="w").pack(side="left")
    model_var = tk.StringVar(value=str(cfg.get("model") or ""))
    # ttk Combobox 默认白底白字看不清 → 配深色样式
    try:
        _style = ttk.Style(win)
        try:
            _style.theme_use("clam")
        except Exception:
            pass
        _style.configure("Dark.TCombobox", fieldbackground=BG2,
                         background=BG2, foreground=FG,
                         selectbackground=BG2, selectforeground=FG,
                         arrowcolor=GOLD2, insertcolor=FG,
                         bordercolor=BG2, lightcolor=BG2, darkcolor=BG2)
        _style.map("Dark.TCombobox",
                   fieldbackground=[("readonly", BG2), ("disabled", BG2)],
                   foreground=[("readonly", FG), ("disabled", GOLD2)])
    except Exception:
        pass
    model_cb = ttk.Combobox(f_model, textvariable=model_var, values=[],
                            font=FONT, style="Dark.TCombobox")
    model_cb.configure(background=BG2, foreground=FG)
    model_cb.pack(side="left", fill="x", expand=True)

    # 历史 API：选一个用过的配置，直接填回 provider/base_url/model/key
    _hist = list(cfg.get("api_history") or [])
    if _hist:
        f_hist = tk.Frame(pad, bg=BG)
        f_hist.pack(fill="x", pady=2)
        tk.Label(f_hist, text="历史 API", bg=BG, fg=GOLD2, font=FONT,
                 width=8, anchor="w").pack(side="left")
        _hist_labels = ["%s / %s" % (h.get("provider") or "?",
                                     h.get("model") or "?") for h in _hist]
        hist_var = tk.StringVar(value="")
        hist_cb = ttk.Combobox(f_hist, textvariable=hist_var,
                               values=_hist_labels, state="readonly",
                               font=FONT, style="Dark.TCombobox")
        hist_cb.pack(side="left", fill="x", expand=True)

        def _pick_hist(*_a):
            label = hist_var.get()
            for h in _hist:
                if ("%s / %s" % (h.get("provider") or "?",
                                 h.get("model") or "?")) == label:
                    prov_var.set(h.get("provider") or "自定义")
                    vars_["base_url"].set(h.get("base_url") or "")
                    vars_["api_key"].set(h.get("api_key") or "")
                    model_var.set(h.get("model") or "")
                    _p = companion.PROVIDERS.get(h.get("provider"))
                    if _p and _p.get("models"):
                        model_cb["values"] = list(_p["models"])
                    break
        hist_var.trace_add("write", _pick_hist)
    # 打开时按当前提供商预填可选模型（不覆盖已保存的 model）。
    # 旧配置的提供商名可能已改（如「火山方舟（Seed-Evolving）」）→ 按 base_url 兼容匹配。
    _prov = prov_var.get()
    _cur = companion.PROVIDERS.get(_prov)
    if _cur is None:
        _cur = next((v for v in companion.PROVIDERS.values()
                     if (v.get("base_url") or "") == (cfg.get("base_url") or "")),
                    None)
        if _cur is not None:
            _prov = next(k for k, v in companion.PROVIDERS.items() if v is _cur)
            prov_var.set(_prov)
    if _cur and _cur.get("models"):
        model_cb["values"] = list(_cur["models"])

    # 模型大小（宠物窗口尺寸，200/260/340）
    f_size = tk.Frame(pad, bg=BG)
    f_size.pack(fill="x", pady=2)
    tk.Label(f_size, text="模型大小", bg=BG, fg=GOLD2, font=FONT, width=8,
             anchor="w").pack(side="left")
    try:
        import pet_view as _pv
        _sizes = list(_pv.SIZES)
        _cur_size = int(_pv.load_cfg().get("size") or _pv.DEFAULT_SIZE)
    except Exception:
        _sizes, _cur_size = list(_pv.SIZES), _pv.DEFAULT_SIZE
    size_var = tk.StringVar(value=str(_cur_size))
    size_cb = ttk.Combobox(f_size, textvariable=size_var,
                           values=[str(s) for s in _sizes], state="readonly",
                           font=FONT, style="Dark.TCombobox")
    size_cb.pack(side="left", fill="x", expand=True)
    tk.Label(pad, text="（保存后宠物立即生效）", bg=BG, fg=DIM,
             font=FONT, anchor="w").pack(fill="x", pady=(0, 2))

    def open_expression_keys():
        import os
        exp_dir = os.path.join(companion.HERE, "_l2d")
        names = sorted(os.path.basename(x)[:-len(".exp3.json")]
                       for x in __import__("glob").glob(os.path.join(exp_dir, "*.exp3.json")))
        try:
            import pet_view as pv
            pcfg = pv.load_cfg()
            current = dict(pcfg.get("expression_keys") or {})
        except Exception:
            pcfg, current = {}, {}
        chars = list("1234567890abcdefghijklmnopqrstuvwxyz") + ["f%d" % i for i in range(1, 9)]
        defaults = {chars[i]: name for i, name in enumerate(names) if i < len(chars)}
        dlg = tk.Toplevel(win)
        dlg.title("模型表情快捷键（Alt+键）")
        dlg.configure(bg=BG)
        dlg.geometry("460x580")
        dlg.attributes("-topmost", True)
        outer = tk.Frame(dlg, bg=BG)
        outer.pack(fill="both", expand=True, padx=10, pady=8)
        tk.Label(outer, text="44 个原生表情。点击输入框后按数字/字母或 F1–F8；游戏中按 Alt+键触发。Delete 清空。",
                 bg=BG, fg=DIM, font=FONT, wraplength=425, justify="left").pack(fill="x", pady=(0, 6))
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=BG)
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win_item = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_item, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        vars_ = {}
        for name in names:
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name, bg=BG, fg=FG, font=FONT,
                     anchor="w").pack(side="left", fill="x", expand=True)
            key = next((k for k, v in current.items() if v == name),
                       next((k for k, v in defaults.items() if v == name), ""))
            var = tk.StringVar(value=key)
            entry = tk.Entry(row, textvariable=var, width=6, justify="center",
                             bg=BG2, fg=FG, insertbackground=FG,
                             relief="flat", font=FONT)
            entry.pack(side="right", padx=3)
            def capture(event, target=var):
                if event.keysym in ("BackSpace", "Delete"):
                    target.set("")
                    return "break"
                keyname = event.keysym.lower()
                if len(keyname) == 1 and keyname.isalnum():
                    target.set(keyname)
                elif keyname.startswith("f") and keyname[1:].isdigit():
                    target.set(keyname)
                return "break"
            entry.bind("<KeyPress>", capture)
            vars_[name] = var

        def save_keys():
            assigned = {}
            for name, var in vars_.items():
                key = var.get().strip().lower()
                if not key:
                    continue
                if key in assigned:
                    messagebox.showerror("快捷键重复",
                        "Alt+%s 同时绑定「%s」和「%s」" % (key, assigned[key], name),
                        parent=dlg)
                    return
                assigned[key] = name
            try:
                pcfg["expression_keys"] = assigned
                pv.save_cfg(pcfg)
                dlg.destroy()
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=dlg)

        def reset_keys():
            for name, var in vars_.items():
                var.set(next((k for k, v in defaults.items() if v == name), ""))
        bar = tk.Frame(outer, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        tk.Button(bar, text="恢复默认", command=reset_keys, bg=BG2, fg=FG,
                  relief="flat", padx=10).pack(side="left")
        tk.Button(bar, text="保存", command=save_keys, bg=GOLD, fg="#111",
                  relief="flat", padx=12).pack(side="right")

    tk.Button(pad, text="模型表情快捷键…", command=open_expression_keys,
              bg=BG2, fg=GOLD, relief="flat", bd=0, padx=10, pady=4,
              font=FONT, cursor="hand2").pack(fill="x", pady=(4,2))

    # 性格（三种，都不嘲讽主人）
    f_pers = tk.Frame(pad, bg=BG)
    f_pers.pack(fill="x", pady=2)
    tk.Label(f_pers, text="性格", bg=BG, fg=GOLD2, font=FONT, width=8,
             anchor="w").pack(side="left")
    _pers_ids = list(companion.PERSONALITIES.keys())
    _pers_labels = [companion.PERSONALITIES[p][0] for p in _pers_ids]
    _cur_p = cfg.get("personality")
    if _cur_p not in companion.PERSONALITIES:
        _cur_p = "gentle"
    pers_var = tk.StringVar(value=companion.PERSONALITIES[_cur_p][0])
    pers_cb = ttk.Combobox(f_pers, textvariable=pers_var,
                           values=_pers_labels, state="readonly",
                           font=FONT, style="Dark.TCombobox")
    pers_cb.pack(side="left", fill="x", expand=True)

    # 宠物帧率：低帧率省 CPU/GPU（打字延迟时调低）
    f_fps = tk.Frame(pad, bg=BG)
    f_fps.pack(fill="x", pady=2)
    tk.Label(f_fps, text="宠物帧率", bg=BG, fg=GOLD2, font=FONT, width=8,
             anchor="w").pack(side="left")
    try:
        import pet_view as _pv2
        _fps_choices = list(_pv2.FPS_CHOICES)
        _cur_fps = int(_pv2.load_cfg().get("fps") or _pv2.DEFAULT_FPS)
    except Exception:
        _fps_choices, _cur_fps = [8, 12, 24], 12
    fps_var = tk.StringVar(value=str(_cur_fps))
    fps_cb = ttk.Combobox(f_fps, textvariable=fps_var,
                          values=[str(f) for f in _fps_choices],
                          state="readonly", font=FONT, style="Dark.TCombobox")
    fps_cb.pack(side="left", fill="x", expand=True)
    tk.Label(pad, text="（卡顿/打字延迟时选低帧率；保存后立即生效）", bg=BG,
             fg=DIM, font=FONT, anchor="w").pack(fill="x", pady=(0, 2))

    ttsv = tk.BooleanVar(value=bool(cfg.get("tts_enabled")))
    f1 = tk.Frame(pad, bg=BG)
    f1.pack(fill="x", pady=3)
    tk.Checkbutton(f1, text="语音（Edge TTS，云端）", variable=ttsv,
                   bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                   font=FONT).pack(side="left")

    f2 = tk.Frame(pad, bg=BG)
    f2.pack(fill="x", pady=2)
    tk.Label(f2, text="音色", bg=BG, fg=GOLD2, font=FONT, width=8,
             anchor="w").pack(side="left")
    voice_names = [n for _, n in companion.VOICES]
    voice_ids = [v for v, _ in companion.VOICES]
    cur = cfg.get("voice") or voice_ids[0]
    vv = tk.StringVar(value=voice_names[voice_ids.index(cur)]
                      if cur in voice_ids else voice_names[0])
    om = tk.OptionMenu(f2, vv, *voice_names)
    om.config(bg=BG2, fg=FG, relief="flat", bd=0, font=FONT,
              highlightthickness=0)
    om["menu"].config(bg=BG, fg=FG, activebackground=BG2,
                      activeforeground=GOLD, font=FONT)
    om.pack(side="left", fill="x", expand=True)

    enabledv = tk.BooleanVar(value=bool(cfg.get("enabled")))
    f3 = tk.Frame(pad, bg=BG)
    f3.pack(fill="x", pady=3)
    tk.Checkbutton(f3, text="启用陪玩（对局中自动说话）", variable=enabledv,
                   bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                   font=FONT).pack(side="left")

    def do_save():
        # 先从 Tk 主线程采集表单值，再把所有文件读写放后台执行。
        try:
            new_cfg = dict(cfg)
            new_cfg["api_key"] = vars_["api_key"].get().strip()
            new_cfg["base_url"] = vars_["base_url"].get().strip() or cfg["base_url"]
            new_cfg["model"] = model_var.get().strip() or cfg["model"]
            new_cfg["tts_enabled"] = bool(ttsv.get())
            new_cfg["enabled"] = bool(enabledv.get())
            new_cfg["provider"] = prov_var.get()
            new_cfg["voice"] = voice_ids[voice_names.index(vv.get())]
            for pid, (label, _text) in companion.PERSONALITIES.items():
                if label == pers_var.get():
                    new_cfg["personality"] = pid
                    break
            new_size, new_fps = int(size_var.get()), int(fps_var.get())
            old_pers = cfg.get("personality")
            for pid, (label, _text) in companion.PERSONALITIES.items():
                if label == pers_var.get():
                    new_cfg["personality"] = pid
                    break
        except Exception as e:
            messagebox.showerror("保存失败", "设置内容无效：%s" % e, parent=win)
            return
        save_btn.config(state="disabled", text="保存中…")

        def _worker():
            error = ""
            try:
                import pet_view as pv
                pet_cfg = pv.load_cfg()
                pet_cfg["size"], pet_cfg["fps"] = new_size, new_fps
                pv.save_cfg(pet_cfg)
                companion.save_config(new_cfg)
                companion.remember_api(new_cfg)
            except Exception as e:
                error = str(e)
            def _finish():
                try:
                    if error:
                        save_btn.config(state="normal", text="保存")
                        messagebox.showerror("保存失败", "写入配置失败：%s" % error,
                                             parent=win)
                    else:
                        # 性格换成功 → 按新性格说一句提醒
                        try:
                            if new_cfg.get("personality") != old_pers:
                                _LINES = {
                                    "gentle": "换好啦～主人今天想听鲸鲸说什么呀？",
                                    "lively": "哇！性格换好啦，元气满满地上吧！",
                                    "tsundere": "哼，才、才不是为你换的呢……",
                                    "toxic": "换了？就这？那我可要好好看看你怎么打。",
                                }
                                line = _LINES.get(new_cfg.get("personality"),
                                                  "换好啦～")
                                import json as _json
                                pcmd = os.path.join(companion.HERE, "logs", "_pet_cmd.json")
                                tmp = pcmd + ".tmp"
                                with open(tmp, "w", encoding="utf-8") as fh:
                                    _json.dump({"say": line, "at": time.time()}, fh,
                                               ensure_ascii=False)
                                os.replace(tmp, pcmd)
                        except Exception:
                            pass
                        win.destroy()
                except Exception:
                    pass
            try:
                win.after(0, _finish)
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def do_test():
        # TTS 也要联网：放后台线程，别卡主窗口
        def _work():
            try:
                cfg2 = companion.load_config()
                c = companion.Companion()
                c.cfg = cfg2
                c.test_tts()
            except Exception:
                pass
        try:
            threading.Thread(target=_work, daemon=True).start()
        except Exception:
            _work()

    def do_test_ai():
        def _work():
            timing = {}
            try:
                c = companion.Companion()
                c.cfg = companion.load_config()
                line = c.test_llm()
                err = getattr(c, "last_error", "") or "未知原因"
                timing = getattr(c, "timing", {}) or {}
            except Exception as e:
                line, err = None, "测试请求异常：%s" % str(e)
                timing = getattr(locals().get("c", None), "timing", {}) or {}
            def _show():
                try:
                    api_first = timing.get("api_first_byte")
                    first = timing.get("first_token")
                    total = timing.get("total")
                    suffix = "\n\n耗时：接口首包 %s，模型首段 %s，总计 %s" % (
                        ("%.2f 秒" % api_first) if api_first is not None else "未收到",
                        ("%.2f 秒" % first) if first is not None else "未收到",
                        ("%.2f 秒" % total) if total is not None else "未记录")
                    if line:
                        messagebox.showinfo("测试 AI", "成功：\n%s%s" % (line, suffix), parent=win)
                    else:
                        messagebox.showerror("测试 AI", "失败：%s%s" % (err, suffix), parent=win)
                except Exception:
                    pass
            try:
                win.after(0, _show)
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    # 模型来源（使用说明里写的那段）
    tk.Label(pad, text="模型来源：鲸鱼娘 Live2D 模型为 CC BY-NC-SA 4.0（非商用），"
                       "作者为 B 站「氵六青」。",
             bg=BG, fg=DIM, font=FONT, justify="left", anchor="w",
             wraplength=380).pack(fill="x", pady=(8, 0))

    bf = tk.Frame(pad, bg=BG)
    bf.pack(fill="x", pady=(6, 0))
    save_btn = None
    for txt, cmd in (("保存", do_save), ("测试 AI", do_test_ai),
                     ("测试语音", do_test), ("关闭", win.destroy)):
        _button = tk.Button(bf, text=txt, command=cmd, bg=BG2, fg=GOLD,
                  relief="flat", bd=0, padx=10, pady=3, font=FONT,
                  activebackground=BG, cursor="hand2")
        _button.pack(side="left", padx=(0, 6))
        if txt == "保存":
            save_btn = _button
    return win


if __name__ == "__main__":
    open_settings().mainloop()
