# 给 ChatGPT 的修复任务：恢复影之诗助手控制台的工具按钮

## 背景

这个项目是「影之诗助手」（GitHub: `nanamihoshiochi/svwba`，本地路径 `svwba-probe/`），
用 Tkinter 写的控制台主界面在 `app.py`。

**你之前的改动把控制台的工具按钮删掉了**，现在用户重新装回去的按钮**点了没有反应**（没有绑定回调/功能）。

## 你的任务

1. **恢复所有工具按钮**（按下面清单），按钮文字、位置、样式与原来一致；
2. **每个按钮必须绑定真实回调**，点击后真的执行对应功能（不是空函数、不是 `pass`）；
3. 不要改动其他逻辑（frida 读取、记牌器、AI 陪玩）。

## 按钮清单（app.py 里应有）

### 1. 标题栏按钮（`_build` 头部 `head` 里）
| 文字 | 回调 | 功能 |
|---|---|---|
| `—` | `self._minimize_to_taskbar` | 最小化到任务栏 |
| `✕` | `self.on_close` | 关闭（按设置最小化/退出） |
| `托盘` | `self._hide_or_fallback` | 隐藏到托盘 |
| `🔓`/`🔒` | `self._toggle_lock` | 锁定/解锁窗口位置 |
| `⚙ 设置` | `self.open_console_settings` | 打开控制台设置 |
| `重启` | `self.restart_console` | 重启控制台 |

### 2. 顶部标签页（`tabrow`）
- 用 `tk.Button` 动态生成，命令切换 `tab`（影之幻境 / 影之诗），样式有选中态。

### 3. 影之幻境模块的 4 个开关按钮（`_add_toggle` / `row`）
| key | 文字 | 回调 | 功能 |
|---|---|---|---|
| `overlay` | 战场叠加 | `self.start_one("overlay", pause=0.0)` | 启动 overlay.py |
| `panel` | 战绩面板 | `self.start_one("panel", pause=1.0)` | 启动 panel_ui.py |
| `leader` | 主战者面板 | `self.start_one("leader", pause=0.3)` | 启动 leader_panel.py |
| `judge` | 判定器 | `self.start_one("judge", pause=0.5)` | 启动 judge.py |

每个按钮右侧还有「设置」`settings_cmd` 和「重启」`self.restart_one(key)`。

### 4. 影之诗模块按钮
| 文字 | 回调 | 功能 |
|---|---|---|
| `打开`（记牌器） | `self.open_deck_tracker` | 启动 frida + 打开记牌器窗口 |
| `AI 设置` | `self.open_ai_settings` | 打开 AI 陪玩设置对话框 |
| `启动宠物` | `self.open_companion` | 启动 run_companion.py（Live2D 宠物） |
| `导入卡组码` | `self.open_deck_import` | 打开卡组码导入对话框 |
| `查看已导入` | `self.open_deck_list` | 查看已导入卡组 |

### 5. 元数据 / 其他
| 文字 | 回调 | 功能 |
|---|---|---|
| `设置` | `settings_cmd` | 打开对应设置 |
| `重启` | `self.restart_one("meta")` | 重启元数据服务 |
| `打开` | `self.open_meta_page` | 打开网页 |
| `服务器` | `self.open_meta_settings` | 服务器设置 |
| `打开 AI 设置（API Key / 语音）` | `self.open_ai_settings` | 同 AI 设置 |

### 6. 对话框里的按钮（不能删）
- 卡组码导入：`解析`(解析卡组码)、`解析并保存`、`关闭`
- 卡组列表：`重命名`、`删除`、`设为当前`、`关闭`
- 设置保存：`应用`（`on_apply`）、`取消`

## 关键实现要求

1. 按钮用 `tk.Button(parent, text=..., command=回调, bg=..., fg=..., ...)` 创建后**单独 `.pack()`**，
   不要写成 `btn = tk.Button(...).pack()`（会返回 None，后面 `.config()` 会 AttributeError）。
2. 回调方法必须真实存在（`start_one`、`open_deck_tracker`、`open_companion` 等），
   不能指向不存在的方法或 `lambda: None`。
3. 启动子进程用 `subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "xxx.py")], cwd=HERE, ...)`，
   进程句柄存到 `self.procs[key]`，方便重启/停止。
4. 参考完整实现：仓库 `svwba` 里的 `svwb-probe/app.py`（有全部 27 个按钮的原版实现），
   按它恢复即可；**不要自己重新设计布局**。

## 验收标准

- 控制台打开后，所有按钮**可见**（标题栏 6 个、标签页 2 个、幻境 4 个开关、影之诗 5 个、元数据 4 个）；
- 每个按钮**点击都有反应**：启动子进程/打开窗口/切换标签/保存设置；
- 控制台日志（`logs/console-app.log`）里能看到对应动作输出；
- 没有 `AttributeError: 'NoneType' object has no attribute 'config'` 之类报错。
