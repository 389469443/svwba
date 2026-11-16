    def open_ai_settings(self):
        try:
            import ai_settings
            ai_settings.open_settings(self.root)
        except Exception as e:
            print("AI 设置打开失败：%s" % e)

    def open_companion(self):
        try:
            import subprocess
            log = open(os.path.join(HERE, "logs", "companion.log"), "a",
                       encoding="utf-8")
            self.procs["companion"] = subprocess.Popen(
                [sys.executable, "-u",
                 os.path.join(HERE, "run_companion.py")],
                stdout=log, stderr=subprocess.STDOUT, cwd=HERE)
            print("AI 陪玩已启动")
        except Exception as e:
            print("AI 陪玩启动失败：%s" % e)

    def open_deck_import(self):
        """导入卡组码：贴码 → 解析预览 → 保存到 user_decks.json。"""
        import deckcode
        win = self._toplevel("导入卡组码", "560x720")
        tk.Label(win, text="把游戏里生成的卡组码粘贴到下面（整段 #…# 也行）：",
                 bg=BG, fg=FG, font=self.f_s, anchor="w",
                 wraplength=520).pack(fill="x", padx=12, pady=(10, 4))
        txt = tk.Text(win, height=7, bg=BG2, fg=FG, insertbackground=FG,
                      font=self.f_s, wrap="char")
        txt.pack(fill="x", padx=12, pady=4)
        tk.Label(win, text="提示：粘贴后按 Ctrl+Enter 直接导入（也可以点下面的「解析并保存」）。",
                 bg=BG, fg=GOLD2, font=self.f_s, anchor="w",
                 wraplength=520).pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(win, text="解析结果：", bg=BG, fg=GOLD2, font=self.f_s,
                 anchor="w").pack(fill="x", padx=12, pady=(6, 2))
        out = tk.Text(win, height=18, bg=BG2, fg=FG, font=self.f_s,
                      wrap="char", state="disabled")
        out.pack(fill="both", expand=True, padx=12, pady=4)
        state = {"entries": None, "name": ""}

        def _show(text):
            out.config(state="normal")
            out.delete("1.0", "end")
            out.insert("end", text)
            out.config(state="disabled")

        def _parse():
            text = txt.get("1.0", "end")
            try:
                entries = deckcode.decode(text)
            except Exception as e:
                state["entries"] = None
                _show("解析失败：%s\n" % e)
                return
            name = deckcode.parse_name(text)
            state["entries"] = entries
            state["name"] = name
            try:
                import deck_reader
                meta = deck_reader.load_meta()
            except Exception:
                meta = {}
            total = sum(n for _, n in entries)
            lines = ["卡组：%s" % name, "%d 种 / %d 张" % (len(entries), total), ""]
            for cid, n in entries:
                nm = (meta.get(str(cid)) or {}).get("name") or ("#%d" % cid)
                lines.append("%s  ×%d" % (nm, n))
            _show("\n".join(lines))

        def _save():
            if not state["entries"]:
                _parse()
                if not state["entries"]:
                    return
            name = state["name"] or "导入的卡组"
            cards = []
            for cid, n in state["entries"]:
                cards.extend([cid] * n)
            try:
                p = self._decks_path()
                try:
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                except Exception:
                    d = {"decks": []}
                decks = [x for x in (d.get("decks") or [])
                         if x.get("name") != name]
                decks.insert(0, {"name": name, "cards": cards})
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"decks": decks, "active": d.get("active") or "",
                               "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                              fh, ensure_ascii=False, indent=1)
                os.replace(tmp, p)
            except Exception as e:
                out.config(state="normal")
                out.insert("end", "\n保存失败：%s\n" % e)
                out.config(state="disabled")
                return
            self._refresh_deck_status()
            out.config(state="normal")
            out.insert("end", "\n已保存到卡组列表（%s）。\n" % name)
            out.config(state="disabled")

        bar = tk.Frame(win, bg=BG)
        # 按钮固定在窗口底部（place 不占 pack 空间，不会被文本框挤出可见区域）
        bar.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
        tk.Button(bar, text="解析", command=_parse, bg=BG2, fg=FG,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="left")
        tk.Button(bar, text="解析并保存", command=_save, bg=BG2, fg=GOLD,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="left", padx=6)
        tk.Button(bar, text="关闭", command=win.destroy, bg=BG2, fg=DIM,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="right")
        # 导入键：粘贴卡组码后 Ctrl+Enter 直接解析并保存
        txt.bind("<Control-Return>",
                 lambda e: (_save(), "break")[1])
        txt.bind("<Control-KP_Enter>",
                 lambda e: (_save(), "break")[1])
        win.after(50, lambda: txt.focus_set())

    def open_deck_list(self):
        """查看已导入卡组，并可切换「当前使用的卡组」。"""
        import deck_reader
        try:
            with open(self._decks_path(), encoding="utf-8") as fh:
                d = json.load(fh)
            decks = d.get("decks") or []
            active = d.get("active") or ""
        except Exception:
            decks, active = [], ""
        try:
            meta = deck_reader.load_meta()
        except Exception:
            meta = {}

        win = self._toplevel("已导入的卡组", "560x640")
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(win, command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0),
                    pady=8)
        scroll.pack(side="right", fill="y")
        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-e.delta / 120), "units"))

        def _pick(name):
            self._save_active_deck(name)
            win.destroy()
            self.open_deck_list()

        def _rename(name):
            from tkinter import simpledialog
            new = simpledialog.askstring("重命名卡组", "新的卡组名：",
                                         initialvalue=name, parent=win)
            if not new:
                return
            new = new.strip()
            if not new or new == name:
                return
            self._rename_deck(name, new)
            win.destroy()
            self.open_deck_list()

        def _delete(name):
            from tkinter import messagebox
            if not messagebox.askyesno("删除卡组",
                                       "确定删除「%s」？" % name,
                                       parent=win):
                return
            self._delete_deck(name)
            win.destroy()
            self.open_deck_list()

        from collections import Counter
        if not decks:
            tk.Label(inner, text="还没有导入卡组。\n\n点「导入卡组码」粘贴游戏里生成的卡组码即可。",
                     bg=BG, fg=DIM, font=self.f_s, justify="left",
                     anchor="w").pack(fill="x", padx=8, pady=10)
        for x in decks:
            name = x.get("name") or "?"
            cards = x.get("cards") or []
            row = tk.Frame(inner, bg=BG2)
            row.pack(fill="x", padx=8, pady=4)
            top = tk.Frame(row, bg=BG2)
            top.pack(fill="x", padx=8, pady=(6, 2))
            tk.Label(top, text="【%s】%d 张" % (name, len(cards)),
                     bg=BG2, fg=FG, font=self.f_s, anchor="w").pack(
                side="left")
            tk.Button(top, text="重命名", command=lambda n=name: _rename(n),
                      bg=BG, fg=GOLD2, relief="flat", bd=0, padx=8,
                      pady=3, font=self.f_s, cursor="hand2").pack(
                side="right", padx=(0, 4))
            tk.Button(top, text="删除", command=lambda n=name: _delete(n),
                      bg=BG, fg=RED, relief="flat", bd=0, padx=8,
                      pady=3, font=self.f_s, cursor="hand2").pack(
                side="right", padx=(0, 4))
            if name == active:
                tk.Label(top, text="当前使用", bg=BG2, fg=GREEN,
                         font=self.f_s).pack(side="right")
            else:
                tk.Button(top, text="设为当前", command=lambda n=name: _pick(n),
                          bg=BG, fg=GOLD, relief="flat", bd=0, padx=10,
                          pady=3, font=self.f_s, cursor="hand2").pack(
                    side="right")
            detail = "  ".join(
                "%s×%d" % ((meta.get(str(cid)) or {}).get("name")
                           or ("#%d" % cid), n)
                for cid, n in Counter(cards).items())
            tk.Label(row, text=detail, bg=BG2, fg=DIM, font=self.f_s,
                     wraplength=500, justify="left", anchor="w").pack(
                fill="x", padx=8, pady=(0, 6))
        bar = tk.Frame(win, bg=BG)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        tk.Button(bar, text="关闭", command=win.destroy, bg=BG2, fg=DIM,
                  relief="flat", bd=0, padx=14, pady=5,
                  font=self.f_s, cursor="hand2").pack(side="right")

    def restart_console(self):
        """重启整个控制台：先把新进程排上（延时等旧进程释放锁），再后台停子进程并退出。

        停子进程（尤其 frida 要等 unload）不能放在主线程——否则窗口会卡成
        Windows 的「未响应」。
        """
        try:
            self._save_running_state()
        except Exception:
            pass
        try:
            proc = subprocess.Popen(
                [PY, os.path.join(HERE, "app.py"), "--delay", "6"],
                cwd=HERE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print("正在重启控制台（新进程 PID %s）" % proc.pid)
        except Exception as e:
            print("重启控制台失败：%s" % e)
            return
        # 主线程立刻返回，窗口保持响应；子进程（含 frida 优雅停）在后台处理
        def _finish():
            try:
                self.stop_all()
            except Exception:
                pass
            os._exit(0)

        try:
            threading.Thread(target=_finish, daemon=True).start()
        except Exception:
            _finish()

    def restart_one(self, key):
        self._kill(key)
        self.start_one(key)

    def open_deck_tracker(self):
        try:
            import deck_tracker
        except Exception as e:
            print("记牌器模块加载失败：%s" % e)
            return
        self._ensure_frida()
        deck_tracker.open(self.root)

    def open_console_settings(self):
        """控制台自己的设置：不透明度 + 关闭按钮行为（console-settings.json）"""
        cs = load_json(CONSOLE_SET, DEFAULT_CONSOLE)
        ov = load_json(OVERLAY_SET, DEFAULT_OVERLAY)
        dlg = SettingsDialog(self.root, "控制台 · 设置", 480, 440)

        def live_alpha(v):
            try:
                a = max(0.4, min(1.0, float(v)))
                self.root.attributes("-alpha", a)
                self.console_alpha = a
                cs["consoleAlpha"] = round(a, 2)
                save_json(CONSOLE_SET, cs)
            except Exception:
                pass

        def live_font(v):
            try:
                fs = max(8, min(14, int(round(float(v)))))
                self._font_job = getattr(self, "_font_job", None)
                if self._font_job is not None:
                    try:
                        self.root.after_cancel(self._font_job)
                    except Exception:
                        pass

                def _apply():
                    try:
                        self._apply_console_fonts(fs)
                        cs["font_size"] = fs
                        save_json(CONSOLE_SET, cs)
                    except Exception:
                        pass

                self._font_job = self.root.after(250, _apply)
            except Exception:
                pass

        def live_scale(v):
            """面板大小：整体缩放窗口（和字号独立）；拖动时防抖，停下再应用。"""
            try:
                sc = max(0.7, min(2.0, float(v)))
                self._scale_job = getattr(self, "_scale_job", None)
                if self._scale_job is not None:
                    try:
                        self.root.after_cancel(self._scale_job)
                    except Exception:
                        pass

                def _apply():
                    try:
                        self._apply_console_fonts(self.console_font_size, sc)
                        cs["scale"] = round(sc, 2)
                        save_json(CONSOLE_SET, cs)
                    except Exception:
                        pass

                self._scale_job = self.root.after(250, _apply)
            except Exception:
                pass

        dlg.section("外观")
        dlg.slider("不透明度", "console_alpha", 0.4, 1.0, 0.05,
                   self.console_alpha, "{:.2f}", on_change=live_alpha)
        dlg.slider("字号", "console_font", 8, 14, 1,
                   self.console_font_size, "{:.0f}", on_change=live_font)
        dlg.slider("面板大小", "console_scale", 0.7, 2.0, 0.05,
                   getattr(self, "console_scale", 1.0), "{:.2f}",
                   on_change=live_scale)

        dlg.section("AI 陪玩")

        def _open_ai(d):
            try:
                import ai_settings
                ai_settings.open_settings(d.w)
            except Exception:
                pass

        tk.Button(dlg.body, text="打开 AI 设置（API Key / 语音）",
                  command=lambda: _open_ai(dlg), bg=BG2, fg=GOLD,
                  relief="flat", bd=0, padx=10, pady=3, font=dlg.f_s,
                  activebackground=BG, cursor="hand2").pack(anchor="w",
                                                           pady=(2, 0))
        tk.Label(dlg.body, text="没填 API Key 时陪玩不启用、不联网",
                 bg=BG, fg=GOLD2, font=dlg.f_s, anchor="w").pack(fill="x")

        dlg.section("直播设定")
        live_var = dlg.check("直播模式（不透明背景）", "console_live",
                             bool(ov.get("liveMode", False)))

        def live_mode(*_a):
            try:
                ov["liveMode"] = bool(live_var.get())
                save_json(OVERLAY_SET, ov)
                print("直播模式：" + ("开启（OBS 可窗口采集）"
                                      if ov["liveMode"] else "关闭（透明）"))
            except Exception:
                pass

        live_var.trace_add("write", live_mode)
        tk.Label(dlg.body,
                 text="直播模式：OBS 用「窗口采集」选本窗口即可（透明模式抓不到）",
                 bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                 wraplength=430).pack(fill="x", pady=(2, 0))

        dlg.section("阵容统计")
        meta_cfg = meta_client.load_settings()
        dlg.check("启用统计（连接后收集数据）", "console_meta_on",
                  bool(meta_cfg.get("enabled")))
        dlg.entry("服务器地址", "console_meta_server",
                  meta_cfg.get("server") or "", width=30)
        tk.Label(dlg.body,
                 text="未连接服务器时：不收集数据，也不显示统计页。",
                 bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                 wraplength=430).pack(fill="x", pady=(2, 0))

        dlg.section("关闭按钮行为")
        mode_var = dlg.radio_row("点 ✕ 时", "closeAction",
                                 str(cs.get("closeAction") or "minimize"),
                                 (("minimize", "隐藏到托盘"),
                                  ("exit", "退出程序")))

        def live_close_action(*_a):
            v = mode_var.get()
            if v not in ("minimize", "exit"):
                return
            self.close_action = v
            cs["closeAction"] = v
            save_json(CONSOLE_SET, cs)
            self._refresh_close_label()
            print("控制台关闭行为已切换：" +
                  ("隐藏到托盘" if v == "minimize" else "退出程序"))

        mode_var.trace_add("write", live_close_action)
        tk.Label(dlg.body,
                 text="— 最小化：进任务栏（程序继续运行）\n"
                      "✕ 关闭：按上面的选择执行",
                 bg=BG, fg=DIM, font=dlg.f_s, justify="left", anchor="w",
                 wraplength=420).pack(fill="x", pady=(8, 0))

        def apply():
            cs["closeAction"] = dlg.vars["closeAction"].get()
            cs["consoleAlpha"] = round(float(dlg.vars["console_alpha"].get()), 2)
            cs["font_size"] = int(round(dlg.vars["console_font"].get()))
            cs["scale"] = round(float(dlg.vars["console_scale"].get()), 2)
            ov["liveMode"] = bool(dlg.vars["console_live"].get())
            save_json(CONSOLE_SET, cs)
            save_json(OVERLAY_SET, ov)
            # 统计服务器：启用且填了地址才探测；未连接时入口会被撤掉
            meta_cfg["enabled"] = bool(dlg.vars["console_meta_on"].get())
            meta_cfg["server"] = (dlg.vars["console_meta_server"].get() or "").strip()
            if meta_cfg["enabled"] and meta_cfg["server"]:
                meta_client.probe(meta_cfg["server"])
            else:
                meta_cfg["connected"] = False
                meta_cfg["connected_at"] = 0.0
                meta_client.save_settings(meta_cfg)
            self._rebuild_meta_entry()
            self.close_action = cs["closeAction"]
            self.console_alpha = cs["consoleAlpha"]
            self._apply_console_fonts(cs["font_size"], cs["scale"])
            self._refresh_close_label()
            self._preview_console_alpha(self.console_alpha)
            dlg.w.destroy()
            print("控制台关闭行为：" +
                  ("隐藏到托盘" if self.close_action == "minimize" else "退出程序"))

        dlg.buttons(apply, label="应用")