# 影之诗助手（svwba）技术说明文档

> 用途：交给 ChatGPT 帮忙排查/改进。内容是本项目的程序结构、Frida hook 细节、内存探测结果、已知问题。
> 项目：影之诗（Shadowverse）对局辅助工具，运行在 Windows，游戏跑在 MuMu 模拟器里（游戏进程名 `MuMu模拟器x影之诗高清版.o`，Unity + il2cpp）。

---

## 一、整体架构

```
启动.bat
  └─ app.py                控制台（Tkinter GUI，主进程）
       ├─ deck_tracker.py  记牌器窗口（Toplevel，跑在控制台进程里）
       ├─ frida_live.py    实时读取（独立 pythonw 子进程，Frida attach 游戏）
       ├─ run_companion.py AI 陪玩（独立进程：宠物 Live2D 窗口 + 大模型对话）
       │    ├─ pet_view.py   Live2D 渲染（pygame + live2d-py）
       │    └─ companion.py  对局状态 → 提示词 → 调用 LLM
       ├─ shadow_warchess_reader.py  找游戏进程
       ├─ battle_stats.py  胜负统计
       └─ upgrade_helper.py 旧版本目录迁移/清理

数据流：
frida_live.py（hook 游戏） ──写──> logs/deck_live.json ──读──> deck_reader.py
     ──> deck_tracker.py（界面）/ companion.py（AI 提示词）/ ai_settings.py
```

### 进程间通信
- **frida_live → 其他**：写 `logs/deck_live.json`（约 250ms 一次），字段见下。
- **非 frida → 宠物**：写 `logs/_pet_cmd.json`（`{action, say, at}`），宠物轮询执行表情/说话。
- **控制台 → 子进程**：`subprocess` 启动 pythonw；`logs/_frida.stop` 文件用于优雅停止 frida。

---

## 二、各程序描述

### 1. app.py（控制台）
- Tkinter 主窗口，管理所有子功能开关：影之幻境（overlay/panel/leader/judge）、影之诗（deck/companion/frida）。
- 读写 `logs/console-settings.json`（`running` 状态、`scale`、`sv_live` 直播透明等）。
- 内置告警/直播模式、托盘隐藏、AI 设置对话框。
- 看门狗 `_idle_watchdog`：frida 在跑、游戏在、但 `deck_live` 连续 60 秒 `idle` → 自动重启 frida。

### 2. deck_tracker.py（记牌器）
- 控制台内 Toplevel 窗口，标题「影之诗 · 记牌器」。
- 每 250ms 读 `deck_live.json`，渲染：胜率行、牌库剩余/手牌数、牌库中（按费用排序）、手牌中、爆牌中。
- 胜负判定 `_record_result`：我方 life<=0 → 负；对手 life<=0 → 胜；双方都活着时看 `retire` 字段（`IsPlayerRetire`）判断谁投降。
- 手牌显隐：`mulliganDone=True` **或** `turn>=1` 才显示（换牌阶段隐藏）。

### 3. deck_reader.py（读取封装）
- `snapshot()`：读 `deck_live.json`，校验新鲜度（>30 秒视为过期），返回 dict。
- `card_meta(cid)` / `card_name(cid)`：查 `gamedata/sv_card_meta.json` 卡表（mtime 缓存）。
- 透传字段：`deck, life, hand, field, played, destroyed, enemy, turn, pp, maxpp, mulliganDone, retire, addr, overdraw, idle`。

### 4. frida_live.py（实时读取，核心）
- 独立进程 `pythonw -u frida_live.py <pid> <seconds>`，用 Frida attach 游戏进程。
- 注入 JS（见第三节），hook il2cpp 方法、每 250ms 采样 Mpo 对象，写 `deck_live.json`。
- 心跳：无数据时每 2 秒写 `{"idle": true, "at": ...}`。
- 停止：轮询 `logs/_frida.stop` 文件；`_pid_alive(pid)` 检测游戏退出后自行退出。
- 兜底扫描 `scanForMpo()`：hook 拿不到对象时，按记录的 vtable 主动扫内存（分块 32MB 异步 `Memory.scan`），找回 Mpo。

### 5. companion.py（AI 陪玩）
- 读 `deck_live.json`，检测事件（抽牌/出牌/生命变化/牌库变化），生成提示词调 LLM。
- 配置文件 `%LOCALAPPDATA%\影之诗助手\companion.json`（API key/base_url/model/personality/use_llm）。
- 当前配置：DeepSeek 官方 API（`https://api.deepseek.com/v1`，`deepseek-chat`），单次约 1 秒。
- 节流：普通 6 秒 / 重要事件 18 秒；`use_llm=false` 时走本地模板（0 费用）。
- 终局说话：数据过期 10 秒 → 按胜负说一句（`_end_prompt`）。
- TTS：可选 edge-tts 语音。

### 6. pet_view.py（宠物窗口）
- pygame + live2d-py 渲染 Live2D 模型（`_l2d/` 下模型 + 44 个 `.exp3.json` 表情）。
- 2x 超采样（render 2 倍尺寸后 BILINEAR 缩回），340 尺寸约 10.7fps。
- 热键：Alt+数字/字母 表情（窗口内 Tk 事件）；`keyboard` 全局钩子**已禁用**（会吞键）。

### 7. shadow_warchess_reader.py
- `find_game_pid()`：遍历进程，按名字匹配游戏，排除 MuMu 服务进程；用 `OpenProcess(QUERY_INFORMATION|VM_READ)` 预检可读性。

### 8. battle_stats.py / ai_settings.py / upgrade_helper.py / collect_diag.py / setup_env.ps1 / deps_check.py
- battle_stats：`logs/battle_result.json` 胜负计数（原子写）。
- ai_settings：AI 设置对话框（API 测试、性格、表情快捷键）。
- upgrade_helper：旧版本目录检测/迁移/删除（仅删含 `svwb-probe/app.py` 的目录）。
- collect_diag：生成诊断 zip（含版本、日志、配置脱敏）。
- setup_env.ps1：装依赖（pillow/numpy/frida/pygame/PyOpenGL/live2d-py/requests/edge-tts）；deps_check 校验。

---

## 三、Frida hook 详情

### 3.1 il2cpp 导出函数（从 `GameAssembly.dll` 取）
```javascript
il2cpp_domain_get
il2cpp_thread_attach
il2cpp_domain_get_assemblies
il2cpp_assembly_get_image
il2cpp_class_from_name
il2cpp_class_get_methods
il2cpp_method_get_name
il2cpp_object_get_class
il2cpp_class_get_name
```

### 3.2 找类/方法（无硬编码地址）
```javascript
// 遍历所有 assembly，按命名空间+类名找
var klass = classFromName(image, "" , "BattlePlayerBase");
// 遍历方法表，按方法名取函数指针
while ((m = classGetMethods(klass, iter)) !== null) {
  if (methodGetName(m) === "get_HandCardList") fn = m.readPointer();
}
// 运行时函数地址 = fn（每次启动由 il2cpp 解析，不固定）
```

### 3.3 hook 点
| 方法 | 作用 | 触发 | 备注 |
|---|---|---|---|
| `BattlePlayerBase.get_HandCardList` | 取我方手牌列表 | 抽牌/刷新时 | **只我方**，用来确定 `selfRef`（我方 Mpo） |
| `BattlePlayerBase.get_DeckCardList` | 取牌库列表 | 双方开局/洗牌 | 双方都会调用，用来确定 `enemyRef` |
| `BattleManagerBase.get_IsPlayerRetire` | 是否我方投降 | 投降/结算 | 返回 bool，写 `retire` 字段 |
| `BattleManagerBase.get_IsBattleEnd` / `JudgeBattleResult` | 对局是否结束/结算 | 终局 | 已 hook，但探测中很少触发，暂未依赖 |

### 3.4 hook 内容（核心 JS 逻辑）
```javascript
Interceptor.attach(fn, { onEnter: function (args) {
    var p = args[0];                       // this（Mpo 对象指针）
    // 校验对象类名
    if (className(objClass(p)).readUtf8String() !== "BattleStatePlayerMpo") return;
    // get_HandCardList 只在我方调用：调用者变化 = 新对局 → 直接换绑
    if (!isPlayerState(selfRef) || p.toString() !== selfRef.toString()) {
        selfRef = p; enemyRef = null;
    }
    rememberVtable(selfRef);               // 记 vtable，供兜底扫描
}});
```

### 3.5 采样（每 250ms）
```javascript
function sample() {
  if (!isPlayerState(selfRef)) { /* idle 心跳 + 4 秒兜底扫描 scanForMpo */ return; }
  var me = selfRef;
  var info = {
    addr: me.toString(),
    deck: me.add(32).readS32(),            // +0x20
    life: me.add(36).readS32(),            // +0x24
    mulliganDone: me.add(0x5C).readS8() != 0,
    turn: me.add(0x38).readS32(),
    pp: me.add(0x34).readS32(),            // 当前费用（2026-11-08 改）
    maxpp: me.add(0x38).readS32(),         // 回合/最大费
    hand: readArr(me.add(16).readPointer(), "hand"),    // +0x10
    field: readArr(me.add(24).readPointer(), "field"),  // +0x18
    played: readArr(me.add(112).readPointer(), "int"),  // +0x70
    destroyed: readArr(me.add(120).readPointer(), "int"),// +0x78
    enemy: enemyRef ? { life: enemyRef.add(36).readS32(), deck: enemyRef.add(32).readS32(),
                        field: readArr(enemyRef.add(24).readPointer(), "field") } : null,
    retire: playerRetire,
    at: Date.now()
  };
  // 合理性校验：卡 id 必须 8 位（10000000~99999999），否则丢 selfRef 触发扫描
  for (var hi = 0; hi < info.hand.length; hi++) {
    var hv = info.hand[hi];
    if (!(hv === 0 || (hv >= 10000000 && hv <= 99999999))) { selfRef = null; return; }
  }
  if (info.deck < 0 || info.deck > 45 || info.life < 0 || info.life > 30 || info.hand.length > 10) return;
  send(info);
}
```

### 3.6 数组读取（il2cpp List/数组）
```javascript
// hand/field 是 List<BattleCardBase>：+0x18 是 Count，+0x10 是 数组指针
function readArr(ptr, kind) {
  var arr = []; if (!ptr || ptr.isNull()) return arr;
  var itemsPtr = ptr.add(0x10).readPointer();   // 元素指针
  var count = ptr.add(0x18).readS32();          // 数量
  if (count < 0 || count > 60) return arr;
  var stride = Process.pointerSize;
  for (var i = 0; i < count; i++) {
    var item = itemsPtr.add(i * stride).readPointer();
    if (!item || item.isNull()) continue;
    if (kind === "hand")  arr.push(item.add(0x10).readS32());   // 卡 id
    else if (kind === "field") arr.push({cardId: item.add(0x10).readS32(),
                                          atk: item.add(0x1C).readS32(),
                                          life: item.add(0x20).readS32()});
    else arr.push(item.readS32());
  }
  return arr;
}
```

---

## 四、内存偏移（Mpo = `BattleStatePlayerMpo`）

| 偏移 | 字段 | 说明 |
|---|---|---|
| +0x10 | hand 指针 | List<BattleCardBase>（我方手牌） |
| +0x18 | field 指针 | List<BattleCardBase>（我方场面） |
| +0x20 | deck | 牌库剩余张数（int） |
| +0x24 | life | 主战者生命（int） |
| +0x34 | pp | 当前费用（疑似；和 +0x38 同值，待用费后探测确认） |
| +0x38 | turn | 回合数（探测确认：turn4 → 4） |
| +0x5C | mulliganDone | 换牌完成标记（0/1，s8） |
| +0x70 | played | 已打出牌列表指针 |
| +0x78 | destroyed | 已摧毁牌列表指针 |

**探测实例**（129 个字段，取关键）：
```json
{"+0x30":0, "+0x34":4, "+0x38":4, "+0x3c":2, "+0x40":2, "+0x44":2, "+0x48":2,
 "+0x4c":0, "+0x50":0, "+0x54":0, "+0x58":4, "+0x5c":1, "+0x60":2, "+0x64":5,
 "+0x68":7, "+0x6c":-1, "+0x90":3, "+0x94":0, "+0x98":1}
```
（+0x5c=1 换牌完成；+0x38=4 回合；+0x34=4 费用）

### 卡 id 映射（卡表 `gamedata/sv_card_meta.json`）
| cardId | 名称 | cost | type |
|---|---|---|---|
| 90021320 | 黄金之杯 | 1 | 4（法术，回复主战者 2 生命） |
| 90021310 | 黄金短剑 | 1 | 4（法术，1 点伤害） |
| 11024110 | 表里侦探·诺·莱芙 | 9 | 1（随从） |
| 10624110 | 惨烈的剑王·罗德诺艾尔四世 | 6 | 1（随从） |

---

## 五、查询到的已知问题（需要 ChatGPT 帮忙分析）

1. **手牌显示滞后/消失**：`mulliganDone` 靠 hook 更新，换牌后 hook 可能不触发 → 一直 False → 手牌不显示。已加 `turn>=1` 兜底，但仍偶尔出现。
2. **对象失效读垃圾**：旧对局 Mpo 对象释放后类名仍有效（`isPlayerState` 为 True），读到 7 位假卡 id。已加卡 id 8 位校验 + vtable 扫描找回，但扫描要 4~10 秒。
3. **费用偏移**：`+100`（0x64）曾经读成费用（错误），实际是别的字段；目前用 `+0x34`/`+0x38`，但两者在 turn 4 时都是 4，无法完全区分「当前费」和「回合」。需要游戏内用费后再探测确认。
4. **hook 触发不稳定**：`get_HandCardList` 只在抽牌/刷新时调用，换牌/出牌可能不触发 → 手牌列表滞后。采样是主动读 Mpo 指针，不依赖 hook，但对象换新要重新绑定。
5. **不同步**：`+0x18` field 读出的 atk/life 偏移（0x1C/0x20）是否准确待验证（护符没有面板，读到 0/0）。

---

## 六、给 ChatGPT 的问题清单

1. 有没有更稳的方式判断「换牌结束」（不走 hook、不依赖 mulliganDone）？
2. `pp` 和 `turn` 在 Mpo 上能否精确区分？影之诗费用规则（先手 1 费起、后手 +1、上限 10）能否用来交叉验证？
3. 对象释放后如何更可靠地判断（除了类名 + 卡 id 校验）？比如检查 GC handle 或 vtable 有效性？
4. 能否用 il2cpp 的 `class_get_fields` 按字段名读（比硬编码偏移稳）？frida 里怎么做？
5. 手牌列表除了 `get_HandCardList`，还有没有其他更频繁触发的读取入口？
