#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 §22 交接信息追加到 HANDOFF.md（结论化，不贴大段代码/日志）。"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, "HANDOFF.md")

TEXT = """

## §22 影之诗记牌器 / AI 陪玩（2026-10-27 交接）

### 22.1 目标与验收标准

- 目标：插件从「影之幻境专用」扩成「影之诗通用」；影之诗模块提供炉石 Firestone 式记牌器 + AI 陪玩（透明 Live2D 宠物）。
- 验收：
  1. 对局中显示手牌、牌库剩余、已打出/已破坏（卡名 + 数量）；
  2. 「牌库中」按炉石样式：费用方块（职业色、黑字）、卡名（金色）、种类、数量框（金边）、稀有度边框；排序 费用→稀有度→职业→卡名；
  3. AI 陪玩：用户填自己的 Key，检测抽牌/出牌/掉血说话；换牌阶段静默、每回合最多一次；
  4. 宠物：透明、置顶、可拖、点模型有回应、右键锁定/打开控制台；
  5. 单实例：控制台/记牌器/宠物各只一个；控制台退出子进程自动退出。

### 22.2 已完成

- 卡表：`gamedata/sv_card_meta.json`（5632 条：name/cost/rarity/atk/life/type/class/skill），由 `gamedata/build_card_meta.py` 从 `mastermemory.bytes` 生成。
- 实时读取：`frida_live.py` hook `BattlePlayerBase.get_HandCardList`，读 `BattleStatePlayerMpo` 字段（HandCards +0x10、FieldCards +0x18、DeckCount +0x20、Life +0x24、Turn +0x38、IsEndMulligan +0x5C、PlayedCardIds +0x70、DestroyedCardIds +0x78），写 `logs/deck_live.json`，500ms 节流。
- 记牌器：`deck_tracker.py`（无边框、半透明、标题区拖动、高度按实际行数自适应、手牌一张一行、炉石列表、无滚轮）；`deck_reader.py`（snapshot 优先读 JSON；卡组扫描失败时只显示手牌）。
- AI 陪玩：`companion.py`（8 家 Provider 预设、角色设定「鲸鲸」、每回合一次 + 15/60 秒降频、换牌静默、Edge TTS 只留女声、配置存 `%LOCALAPPDATA%\\影之诗助手\\companion.json`）；`ai_settings.py`（提供商下拉 + 自动填 base_url/model）；`pet_view.py`（离屏 OpenGL → glReadPixels → Tk 透明窗口；右键锁定/打开控制台/关闭；点模型随机动作 + 本地气泡；全局热键 Alt/Ctrl + 键触发动作）。
- 进程管理：`singleton.py`（命名互斥量 SvwbConsole / SvwbDeckTracker / SvwbPet）；`parent_guard.py` 新增 `running()`；`frida_live` / `run_companion` / `pet_view` 在控制台退出后自动停；宠物只能从控制台启动。
- 控制台：`app.py` 影之诗页新增「记牌器 / AI 陪玩（AI 设置 + 启动宠物）」；`open_deck_tracker` 自动拉起 `frida_live`。

### 22.3 关键决策及原因

- 用 Frida + IL2CPP 类名/字段 hook，而不是纯内存扫描：对象地址每局变，hook 稳定。
- 卡组明细读不到 → 降级：宁可「牌库中」空着，也不显示错误卡组（阈值：与手牌/已打出重合 ≥ max(2, 一半)）。
- 配置外置到用户目录：打包分发时不带 API Key。
- 宠物用「离屏 OpenGL → 读像素 → Tk 透明窗」：OpenGL 窗口无法用 GDI 色键透明。
- 语音用 Edge TTS（云端、0 显存）；不打包 GPT-SoVITS（6.8GB）。

### 22.4 改过的文件与位置

- 新增：`companion.py`、`ai_settings.py`、`pet_view.py`、`run_companion.py`、`frida_live.py`、`singleton.py`、`sim_plays.py`、`gamedata/sv_card_meta.json`、`assets/whale.png`、`_l2d/`（Live2D 模型）
- 修改：`app.py`（影之诗页/AI 入口/`_ensure_frida`/单实例）、`deck_tracker.py`（炉石列表/高度/手牌/无滚轮/无边框）、`deck_reader.py`（snapshot/卡组阈值）、`parent_guard.py`（`running()`）
- 以上均同步到 `D:\\download\\影之幻境助手-v0.1.0\\svwb-probe\\`。

### 22.5 未完成 TODO 与下一步

1. **卡组明细读取（未通）**：`DeckData._cardIdList` 不落内存明文；已验证卡组界面 38 种（无重复）、持有量数组、List<int> / 8 字节 / 含重复三种扫描均失败。下一步：手动导入 `gamedata/user_deck.json`，或抓包/OCR（有风险，未做）。
2. 记牌器「牌库中」依赖卡组；卡组未导入时只显示手牌。
3. AI 陪玩需要用户自己的有效 Key（当前配置里的 Key 无效，401）。
4. `使用说明.txt` 的「模型替换教程」已写，但「导入模型」按钮已删除，需同步修改说明。
5. `frida_live` 强杀会崩游戏（agent 在游戏进程内）；应改为 `script.unload` 优雅退出。

### 22.6 验证命令

- 编译：`python -m py_compile app.py deck_tracker.py deck_reader.py companion.py ai_settings.py pet_view.py run_companion.py frida_live.py singleton.py`
- 记牌器快照：`python -c "import deck_reader; r=deck_reader.snapshot(); print(r.get('in_battle'), len(r.get('rows') or []), len(r.get('hand') or []))"`
- 陪玩 API：`python -c "import companion; print(companion.Companion().test_llm())"`
- 模拟 200 局：`python sim_plays.py`（异常 0，平均 19.3 事件/局）
- 打包：`cd D:\\download && tar -a -c -f 影之幻境助手-v0.1.0.zip 影之幻境助手-v0.1.0`

### 22.7 坑

- Frida 外部线程遍历 GC 会 `Fatal error in GC: Collecting from unknown thread`；必须 `il2cpp_thread_attach`，且不要遍历 GC 堆。
- `il2cpp_class_from_name` 找不到的类（如 `BattleStatePlayerMpo`），用 hook 触发时读 `objClass` 类名。
- `DeckData` 的方法名被别的类共享（`GetCardIdList` 出现在 `MeshAttachment` / `CriAtomExOutputAnalyzer` 等），hook 必须过滤 `self` 类名。
- OpenGL 窗口的色键透明无效；Tk `-transparentcolor` + 离屏渲染可行。
- 杀 `frida_live` 会崩游戏（agent 在游戏内），退出要 unload。
- `_app.pid` 正常退出会被删；`orphaned()` 在文件不存在时返回 False，判断「控制台是否在跑」要用 `running()`。
- 发布包 `logs/` 与工作区 `logs/` 是两份；排查「数据不对」先确认窗口读的是哪份。
- Git Bash 路径 `/d/download/...` 传给 Windows Python 会 `Errno 2`；用 `D:\\...`。
"""

with io.open(P, "a", encoding="utf-8") as fh:
    fh.write(TEXT)
print("handoff appended", os.path.getsize(P))
