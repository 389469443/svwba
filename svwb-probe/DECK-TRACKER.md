# 影之诗模块 · 记牌器设计（v1 草案）

> 状态：数据结构已从游戏 AOT 源码确认；**对象定位需要实机探针**。
> 日期：2026-10-21。

## 1. 目标

把插件从「影之幻境（自走棋）」扩展成「影之诗」通用插件：

- 控制台分两个模块：**影之幻境** / **影之诗**。
- 影之幻境：现有全部功能（胜率 / 战绩 / 悬浮窗 / 光环 / 统计）。
- 影之诗：**记牌器**（Firestone 式）——记录你的卡组，显示哪些牌还没抽到。

## 2. 数据来源（已确认）

游戏 `Wizard1` 程序集（IL2CPP AOT，`il2cppOutput/Wizard1__*.cpp`）里有：

```
BattlePlayerBase
  ├─ HandCardList              List<BattleCardBase>   手牌
  ├─ DeckCardList              List<BattleCardBase>   牌库里**还没抽**的牌
  ├─ BattleStartDeckCardList   List<BattleCardBase>   开局牌库（= 你的卡组）
  ├─ CemeteryList              List<BattleCardBase>   墓场（已用/已死）
  ├─ BanishList                List<BattleCardBase>   放逐
  └─ GameDrawCards             List<BattleCardBase>   全局抽牌记录

BattleCardBase
  └─ _baseParameter            CardParameter*
       └─ CardId（int，字段名 U3CCardIdU3Ek__BackingField）

List<T>（il2cpp 运行时布局）
  ├─ _items      T[] 指针（元素是对象指针）
  ├─ _size       int
  └─ _version    int
```

对应 C++ 源码（备份目录，换版本后按同名字段重新确认）：
- `Wizard1__106.cpp`：`BattlePlayerBase` / `BattleCardBase`
- `Wizard1__148.cpp`：`DeckData` / `BattlePlayerPair` / `List<T>`

## 3. 记牌口径

设 `D = BattleStartDeckCardList`（开局卡组，按 card_id 计数），
`R = DeckCardList`（当前剩余未抽）。

- **还没抽到** = `D - R`（按 card_id 多重集做差）。这就是 Firestone 里"牌库剩余"那一栏。
- 手牌 = `HandCardList`（按 card_id 计数）。
- 已打出/已死 = `D - R - Hand`（由 `CemeteryList` / `BanishList` 交叉验证，读不到就按差值推断）。
- 显示：卡组每张牌一行，右侧 `剩余 x / 共 n`；`x == 0` 置灰并加删除线（已抽到）。

**对手**：只显示对手**已打出/已公开**的牌（`CemeteryList` + 已打出的 `BattleCardBase` 在场上），
绝不读对手手牌和牌库 —— 那是隐藏信息（§1 安全边界）。

## 4. 对象定位（待实机）

`BattlePlayer` 继承 `BattlePlayerBase`，不是 Unity 对象（`RuntimeObject`），
所以不能按 GameObject 找。两条路：

1. **静态单例**：`BattleManagerBase.BattlePlayer` 字段。如果能找到 `BattleManager` 单例地址
   （Wizard1 的 `GameMgr`/`BattleMgr`），就能一路读到玩家。
2. **扫描堆**：找 `BattlePlayerBase` 的实例（il2cpp 对象头 = class 指针 + monitor）。
   候选特征：对象里 `BattleStartDeckCardList` 的 `_size` 在 30~60 之间，
   且 `_items` 指向的对象里有有效的 `_baseParameter`。

建议先做 `probe_deck.py`（只读）：
- 找游戏进程（复用 `shadow_warchess_reader.find_game_pid`）。
- 扫描候选堆区，按上面特征打印命中。
- 进一局影之诗对战，看能否在 1~2 秒内定位。

## 5. UI（Firestone 式）

- 独立小窗口，默认贴着游戏窗口右侧（可拖动、可锁定，和现有面板一致）。
- 列表按费用升序：`费用 | 卡名 | 剩余/总`。
- 未抽到 = 正常色；已抽到 = 灰色 + 划线。
- 顶部：`我方牌库 剩余 N 张 · 手牌 M 张`。
- 未检测到对战时显示「未在对战中」，不弹错误。

## 6. 分期

| 期 | 内容 | 依赖 |
|---|---|---|
| P0 | 控制台模块化（影之幻境 / 影之诗 两个页签） | 无 |
| P1 | `deck_reader.py` 读取层 + 假内存自测 | 无 |
| P2 | `probe_deck.py` 实机定位（需要一局影之诗对战） | 游戏 |
| P3 | `deck_tracker.py` 窗口接入真实数据 | P2 |
| P4 | 对手已公开牌追踪（可选） | P2 |

## 7. 风险

1. **版本漂移**：字段偏移随热更变化 → 所有偏移集中在一个常量表，换版本重跑探针。
2. **对象存活**：对战结束对象销毁 → 轮询时判空并显示「未在对战中」。
3. **卡牌 id 映射**：卡组里可能是 `CardParameter.CardId`（资源 id），卡名要查卡库；
   查不到的显示 `#id`，不猜。
4. **合规**：只读内存、不注入、不读对手隐藏信息；这一点和幻境模块一致。
