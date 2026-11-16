/**
 * 官方技能 DSL → 引擎钩子的编译器。
 *
 * 官方 `warchess_cards_cn.json` 的 456 条技能里，有相当一部分是**简单可判定**的
 * （给某方随从加属性、造成/受到伤害、获得关键词、多次攻击）。
 * 本模块把能机械解释的部分编译成 `simulateOnce` 的 hooks；解释不了的**明确列出**，
 * 绝不假装已实现 —— 覆盖度报告会如实给出比例。
 *
 * 支持范围（其余一律标注 unsupported）：
 *   powerup     属性增益（数值 / {me.self.offense} 这种自引用）
 *   damage      造成伤害
 *   guard/shield/killer/indestructible/attack_count  关键词授予
 *   evolve      自身进化
 *
 * 标记为不可解释的情况：Condition 含逻辑运算、Option 含 {me.inplay...}/{thisgame...} 等
 * 全局统计表达式、涉及选择/发现/召唤/光环等需要更多上下文的效果。
 */

import { normalizeAbilities } from './rules.mjs'
import { BATTLE_TIMINGS } from './mechanics.mjs'
import { findCardById, cardToFollower } from './knowledge.mjs'

/** 官方 Timing -> 引擎时点 */
export const TIMING_ENGINE = {
  when_play: 'fanfare',
  when_destroy: 'lastWord',
  when_evolve: 'evolve',
  when_super_evolve: 'superEvolve',
  when_attack: 'attack',
  when_damage: 'damage',
  when_fight: 'onFight',
  when_shield_loss: 'onShieldLoss',
  when_summon: 'onSummon',
  when_summon_other: 'onSummonOther',
  when_destroy_other: 'onDestroyOther',
  when_evolve_other: 'onEvolveOther',
  when_play_other: 'onPlayOther',
  when_play_other_before: 'onPlayOtherBefore',
  when_skill_evolve: 'onSkillEvolve',
  when_attack_self_and_other: 'onAttackSelfAndOther',
  self_turn_end: 'prepare',
  self_turn_start: 'turnStart',
  battle_turn_start: 'battleStart',
  permanent: 'static',
  when_buy_card: 'onBuy',
  when_sell_card: 'onSell',
  none: 'none',
}

/**
 * 引擎可以执行的动作。
 *
 * 依据官方数据集（warchess.db 导出的 cards.json）里各动作的参数规格逐条实现：
 *   summon_token   参数 summon_token=<卡号>  或  <卡号>:<卡号>（多选一，取第一个）
 *   claim_aura     参数 base_card_id=<光环卡号>  → 视为常驻能力
 *   attack_count   参数 attack_count=<次数>      → 2 次即「2 连打」
 *   repeat         参数 repeat_count=<次数>      → 效果重复 N 次
 *   invoke_skill   参数 invoke_type=...          → 需要技能表引用，暂不建模
 */
const SIMPLE_ACTIONS = new Set([
  'powerup', 'damage', 'guard', 'shield', 'killer', 'indestructible',
  'attack_count', 'evolve', 'summon_token', 'attack',
  // 以下动作虽然不直接造成伤害，但**必须过白名单**才会进到 switch：
  //   attach_skill / overwrite_skill —— 需要技能注册表，已实现
  //   token_draw / claim_aura / coin_modifier / upgrade_cost_modifier
  //     —— 战斗里确实不结算，标 noop（不是"未建模"）
  'attach_skill', 'overwrite_skill', 'token_draw', 'claim_aura',
  'coin_modifier', 'upgrade_cost_modifier', 'remove_skill', 'random_array',
  // 2026-10-18 补的四个（原来一律"未建模"，覆盖率虚低）：
  //   select / banish / remove_by_banish —— 不改战斗数值，标 noop（理由见编译分支）
  //   invoke_skill —— 真的会重放同卡同时点技能（育龙少女/侮蔑的祈祷者/鲁米那斯枪士）
  'select', 'banish', 'remove_by_banish', 'invoke_skill',
  // repeat 是**放大器**（「此效果将进行 N 次」），本身不产生效果，
  // 但必须先通过白名单才会进到 switch 里被编译。
  'repeat',
])

/**
 * 解析 Option。
 * 注意：lib/knowledge/cards.json 里的 option 已经由构建脚本解析成对象，
 * 而官方原始数据是 "k=v&k2=v2" 字符串 —— 两种都要吃。
 */
export function parseOptions(option) {
  if (option && typeof option === 'object') return { ...option }
  const out = {}
  if (!option) return out
  for (const part of String(option).split('&')) {
    if (!part) continue
    const eq = part.indexOf('=')
    if (eq < 0) out[part] = true
    else out[part.slice(0, eq)] = part.slice(eq + 1)
  }
  return out
}

/** 判断一个表达式是否含引擎无法求值的全局统计 */
function isGlobalExpression(value) {
  if (typeof value !== 'string') return false
  return /\{(me|you|thisgame|us|them)\./.test(value)
}

/**
 * 把数值字段解析成「常量」或「自引用系数」。
 * 支持：纯数字、`2*{...}`（记录需要外部计数）、`{me.self.offense}`（自引用）
 */
function parseAmount(raw, selfStat) {
  if (raw == null) return null
  const s = String(raw).trim()
  if (/^-?\d+(\.\d+)?$/.test(s)) return { kind: 'const', value: Number(s) }
  // `{me.self.offense}|2.ceil` = 攻击力的一半、向上取整（冰界鹿王）
  const div = /^\{me\.self\.(\w+)\}\|(\d+)\.ceil$/.exec(s)
  if (div && div[1] === selfStat) {
    return { kind: 'self', stat: selfStat, multiplier: 1, divideCeil: Number(div[2]) }
  }
  if (s === `{me.self.${selfStat}}`) return { kind: 'self', stat: selfStat, multiplier: 1 }
  const selfMul = new RegExp(`^(-?\\d+(?:\\.\\d+)?)\\*\\{me\\.self\\.${selfStat}\\}$`).exec(s)
  if (selfMul) return { kind: 'self', stat: selfStat, multiplier: Number(selfMul[1]) }
  return null
}


// ─────────────────────────────────────────────────────────────────────────────
// 触发条件求值
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 解析条件表达式。
 *
 * 官方写法很"半结构化"，一个条件里可能同时出现：
 *   · 花括号里的路径：        {me.destroyed_card.unit.count}
 *   · 花括号里的筛选：        {me.summoned_card.unit.card_class=dragon}
 *   · 花括号外接后缀/运算：   }>0   }%2=0   }=0
 * 多个条件用 & 或 | 串联。
 *
 * @returns {{ok:true, ast:object}|{ok:false, reason:string}}
 */
export function parseCondition(raw) {
  const text = String(raw || '').trim()
  if (!text) return { ok: true, ast: { op: 'always' } }
  if (['{me.self}', '{me.self.unit}', '{me.self.card}'].includes(text)) {
    return { ok: true, ast: { op: 'always' } }
  }

  // `turn=battle` / `turn=operate` 这类是**阶段标记**，不是可求值的条件：
  // 我们本来就在战斗结算里，等价于恒真。之前它让解析整条失败
  // （冰界鹿王等多张牌的站场效果就是这么被丢掉的）。
  const _sanitized = text
    .replace(/(^|&)\s*turn=[^&|]*/g, '')
    .replace(/^&+|&+$/g, '')
    .trim()
  if (!_sanitized) return { ok: true, ast: { op: 'always' } }
  const _text2 = _sanitized

  // 先按 | 切（优先级低），再按 & 切
  const orParts = _text2.split('|').map((x) => x.trim()).filter(Boolean)
  const orNodes = []
  for (const part of orParts) {
    const andParts = part.split('&').map((x) => x.trim()).filter(Boolean)
    const andNodes = []
    for (const p of andParts) {
      const node = parseTerm(p)
      if (!node) return { ok: false, reason: `条件无法解析：${p}` }
      andNodes.push(node)
    }
    orNodes.push(andNodes.length === 1 ? andNodes[0] : { op: 'and', nodes: andNodes })
  }
  return {
    ok: true,
    ast: orNodes.length === 1 ? orNodes[0] : { op: 'or', nodes: orNodes },
  }
}

/** 解析单个条件项，例如 `{me.x.y.count}>0` 或 `{me.x.card_class=dragon}` */
function parseTerm(s) {
  const m = /^\{([^}]*)\}(.*)$/.exec(s)
  if (!m) return null
  const inner = m[1].trim()
  const tail = (m[2] || '').trim()

  // 花括号里带 `=` 的：`ability=shield.count` / `card_class=dragon`
  let path = inner
  let filter = null
  let filterCount = false
  if (inner.includes('=')) {
    const idx = inner.indexOf('=')
    path = inner.slice(0, idx).trim()
    let rhs = inner.slice(idx + 1).trim()
    // 形如 `shield.count`：末尾 .count 表示"计数"
    if (/\.count$/.test(rhs)) {
      filterCount = true
      rhs = rhs.replace(/\.count$/, '')
    }
    filter = rhs
  } else if (/\.count$/.test(path)) {
    filterCount = true
    path = path.replace(/\.count$/, '')
  }

  // 花括号外的运算：>0 / =0 / %2=0 / >=1 ...
  let cmp = null
  if (tail) {
    let mm = /^%(\d+)=(\d+)$/.exec(tail)
    if (mm) {
      cmp = { kind: 'mod', mod: Number(mm[1]), eq: Number(mm[2]) }
    } else {
      mm = /^(>=|<=|!=|>|<|=)(-?\d+)$/.exec(tail)
      if (mm) cmp = { kind: 'cmp', rel: mm[1], value: Number(mm[2]) }
      else return null
    }
  }
  return { op: 'term', path, filter, filterCount, cmp }
}

/**
 * 求值。返回 true / false / undefined（undefined = 数据不足，无法判断）
 *
 * @param ast      parseCondition 的结果
 * @param ctx      { self, selfSide, enemySide, destroyedCount, evolvedCount, summonedClass, ... }
 */
export function evalCondition(ast, ctx) {
  if (!ast) return true
  if (ast.op === 'always') return true
  if (ast.op === 'and') {
    const vals = ast.nodes.map((n) => evalCondition(n, ctx))
    if (vals.some((v) => v === false)) return false
    if (vals.some((v) => v === undefined)) return undefined
    return true
  }
  if (ast.op === 'or') {
    const vals = ast.nodes.map((n) => evalCondition(n, ctx))
    if (vals.some((v) => v === true)) return true
    if (vals.some((v) => v === undefined)) return undefined
    return false
  }
  if (ast.op !== 'term') return undefined

  const val = resolvePath(ast.path, ast.filter, ast.filterCount, ctx)
  if (val === undefined) return undefined
  if (!ast.cmp) return Boolean(val)
  if (ast.cmp.kind === 'mod') return (Number(val) % ast.cmp.mod) === ast.cmp.eq
  const n = Number(val)
  switch (ast.cmp.rel) {
    case '>': return n > ast.cmp.value
    case '>=': return n >= ast.cmp.value
    case '<': return n < ast.cmp.value
    case '<=': return n <= ast.cmp.value
    case '=': return n === ast.cmp.value
    case '!=': return n !== ast.cmp.value
    default: return undefined
  }
}

/** 路径 → 运行时数值（无法判断时返回 undefined） */
function resolvePath(path, filter, filterCount, ctx) {
  const p = String(path || '')
  const self = ctx && ctx.self
  const selfSide = ctx && ctx.selfSide
  const aliveSelf = ((selfSide && selfSide.board) || []).filter((f) => f.alive && f.hp > 0)

  // 己方阵亡数（残响类）
  if (p === 'me.destroyed_card.unit' || p === 'me.destroyed_card') {
    return ctx.destroyedCount
  }
  if (p === 'me.inplay_self.unit_destroyed_card_list.unit'
      || p === 'me.inplay_self.unit_destroyed_card_list') {
    return ctx.destroyedCount
  }
  // 场上己方随从数
  if (p === 'me.inplay_self.unit' || p === 'me.inplay_self') {
    return aliveSelf.length
  }
  // 已进化数
  if (p === 'me.evolution_card.unit' || p === 'me.evolution_card') {
    return ctx.evolvedCount
  }
  // 本次触发相关的召唤物职业
  if (p === 'me.summoned_card.unit' || p === 'me.summoned_card') {
    if (filter && filter.startsWith('card_class=')) {
      return ctx.summonedClass === filter.slice('card_class='.length)
    }
    return ctx.summonedClass ? 1 : 0
  }
  // 自身受伤量
  if (p === 'me.self.unit.damage_num') {
    if (!self) return undefined
    return Math.max(0, (self.health || 0) - (self.hp || 0))
  }
  // 自身护盾层数
  if (p === 'me.self.ability') {
    if (!self) return undefined
    if (filter === 'shield') return (self.shield || 0)
    return undefined
  }

  // 主战者光环计数：{me.leader.aura.base_card_id=X.count}
  if (p === 'me.leader.aura.base_card_id') {
    const auras = (selfSide && selfSide.auras) || []
    const id = filter != null ? String(filter) : null
    if (id == null) return auras.length
    return auras.filter((a) => a.baseCardId === id).length
  }
  if (p === 'me.leader.aura') {
    return ((selfSide && selfSide.auras) || []).length
  }

  // 自己
  if (p === 'me.self') return 1
  return undefined
}

/**
 * 编译单条技能。
 * @returns {{ ok: boolean, reason?: string, effect?: object }}
 */


/**
 * 让一个随从**进化**。
 *
 * 按用户确认的规则，进化只做两件事：
 *   ① 面板加成（进化 +2/+2）
 *   ② 触发「随从进化时」（onEvolveOther）
 *
 * **不会**触发「进化时」（onEvolve）—— 那个只有**吃进化点**才发动，
 * 由玩家动作驱动，和效果造成的进化是两回事。
 *
 * @returns {boolean} 是否真的发生了进化（已经进化过的不重复进化）
 */
export function evolveFollower(follower, options = {}) {
  if (!follower || !follower.alive) return false
  if (follower.evolved) return false          // 已进化过，不叠加
  const atk = Number(options.attackGain ?? 2)
  const life = Number(options.healthGain ?? 2)
  follower.attack = (follower.attack || 0) + atk
  follower.health = (follower.health || 0) + life
  if (follower.hp > 0) follower.hp += life
  follower.evolved = true
  // ⚠️ 引擎里判"当前形态"用的是 isEvolved/isSuperEvolved/isTripleEvolved，
  //    这里只写 evolved 的话，效果进化出来的随从在别处仍被当成基础形态
  //    → 进化形态技能（含【进化时】）全部被形态把关筛掉。
  follower.isEvolved = true
  return true
}


// ─────────────────────────────────────────────────────────────────────────────
// 技能注册表：attach_skill / overwrite_skill 要按 skill_id 找另一条技能
// ─────────────────────────────────────────────────────────────────────────────
const SKILL_REGISTRY = new Map()

/** 由 index.js 在加载知识库后灌入：{ skillId → skill } */
// ─────────────────────────────────────────────────────────────────────────────
// 全卡池：给「按条件随机召唤」用
// ─────────────────────────────────────────────────────────────────────────────
let CARD_POOL = []

/** 由 index.js 在加载知识库后灌入：全部卡牌对象数组 */
export function setCardPool(list) {
  CARD_POOL = Array.isArray(list) ? list : []
}

/** 某张卡是否带谢幕曲 */
function hasLastWord(card) {
  return (card.skills || []).some((s) => s.timing === 'when_destroy')
}

/**
 * 「按条件随机召唤」规则表（按**卡名**索引）。
 *
 * 为什么要硬编码：官方数据里这几张卡的 summon_token 的 option 是**空的**，
 * 目标池由卡面文本描述（"随机召唤1个当前幻境等级或以下的梦魇随从"），
 * 数据里没有结构化字段可读，只能按文本逐条翻译。
 *
 * @returns {number[]} 要召唤的卡号列表
 */
/**
 * 取"当前幻境等级"。
 *
 * 用户确认的规则：**所有随机召唤出来的随从，星级都不能超过该玩家的幻境等级**。
 * 拿不到等级时按 8（最高）处理，避免把池子过滤成空。
 */
function _poolLevel(ctx) {
  const lv = Number(ctx && ctx.level)
  if (Number.isFinite(lv) && lv > 0) return lv
  return 8
}

const RANDOM_SUMMON_BY_NAME = {
  // 随机 1 个「星级 ≤ 当前幻境等级」的梦魇随从
  '怪奇探索者·尤娜': (rng, ctx) => {
    const lv = _poolLevel(ctx)
    const pool = CARD_POOL.filter(
      (c) => c.tribe === '梦魇' && Number(c.star || 0) <= lv && c.attack != null)
    if (!pool.length) return []
    const c = pickRandom(pool, rng)          // 只随机一次！
    return [Number(c.__id)]
  },

  // 随机 1 个拥有【谢幕曲】的随从
  //
  // 卡面只写了「拥有【谢幕曲】」，但**全局规则**是：
  // 随机召唤的池子 = 商店池 = **星级 ≤ 当前幻境等级**的牌
  //（尤娜的卡面把这条明写了出来，其它两张是继承）。
  // 所以这里仍然要卡星级。
  '树海的战士': (rng, ctx) => {
    const lv = _poolLevel(ctx)
    const pool = CARD_POOL.filter(
      (c) => hasLastWord(c) && Number(c.star || 0) <= lv && c.attack != null)
    if (!pool.length) return []
    const c = pickRandom(pool, rng)
    return [Number(c.__id)]
  },

  // 虹 / 金 / 银 各 1 个梦魇随从（不随机，按稀有度取）
  '暗之法则·菲迪埃尔': (rng, ctx) => {
    // 每种稀有度各随机 1 个，且星级都不超过幻境等级
    const lv = _poolLevel(ctx)
    const out = []
    for (const rar of ['虹卡', '金卡', '银卡']) {
      const pool = CARD_POOL.filter(
        (c) => c.tribe === '梦魇' && c.rarityName === rar
          && Number(c.star || 0) <= lv && c.attack != null)
      if (!pool.length) continue
      const c = pickRandom(pool, rng)
      out.push(Number(c.__id))
    }
    return out
  },
}

/**
 * 把召唤出来的随从**正确落位**到场上。
 *
 * ⚠️ 关键：`cardToFollower()` 返回的对象**没有 hp / alive 字段**
 *    （只拷了 attack/health/star/name/skills 这些静态数据）。
 *    而全局存活判定是 `alive && hp > 0`，所以直接 push 进去的召唤物
 *    会被当成**已死** —— 等于没召唤。
 *
 *    实测症状（用户报的）：暗之法则拉了三张梦魇、死亡主持人拉了尸兵，
 *    模型里我方存活数仍是 0，于是判"清场"，战斗 1 回合结束，伤害严重高估。
 */
let __summonSeq = 0

/**
 * 单侧单场的召唤次数上限。
 *
 * ⚠️ 这是**性能闸门**，不是规则：真实对局一局最多十几次召唤。
 *    但「僵尸→僵尸→…」「死亡主持人→尸兵→僵尸→…」这类连锁在模型里
 *    没有成本，可以滚到几十上百次，导致单次模拟从 1ms 涨到几十 ms，
 *    判定器（上万次模拟）被堵死，后台还在扫 5.4GB 内存，
 *    最后把游戏拖到未响应。所以必须有硬上限。
 */
const MAX_SUMMONS_PER_SIDE = 40
/** 战场格子数：超过就不再召唤（游戏真实规则） */
const MAX_BOARD_SLOTS = 7

/** 按卡名查种族（`follower` 对象上没有 tribe 字段，只能回查卡库） */
function _tribeOf(f) {
  if (!f) return undefined
  if (f.tribe) return f.tribe
  if (f.meta && f.meta.tribe) return f.meta.tribe
  const name = f.name
  if (!name) return undefined
  const hit = CARD_POOL.find((c) => c.name === name)
  return hit ? hit.tribe : undefined
}

/**
 * 给刚召唤出来的随从套上主战者光环加成。
 *
 * 用户指出：召唤出来的妖精算**精灵随从**，应该受「精灵光环 +N/+0」影响。
 * 光环条件（种族 / 星级上限）由判定器解析后放进 `side.auras`。
 */
export 
/**
 * 官方数据里光环是**独立的卡**（60000020 等），它的效果就是对"之后进场的某些随从"加成。
 * 这里把卡号映射成条件，运行期 claim_aura 发动时直接挂到本方场上。
 */
const AURA_BY_ID = {
  // 僵尸光环：自己的『腐臭的僵尸』进入战场时 +1/+1
  '60000020': { name: '腐臭的僵尸', atk: 1, hp: 1 },
  // 精灵光环：自己的精灵·随从进入战场时 +1/+0
  '60000030': { tribe: '精灵', atk: 1, hp: 0 },
  // 谭涛光环：幻境等级 3 或以下的随从进入战场时 +1/+1
  '60000040': { maxStar: 3, atk: 1, hp: 1 },
}

export function applyAurasToToken(token, side) {
  const auras = (side && side.auras) || []
  if (!auras.length || !token) return token
  const tribe = _tribeOf(token)
  for (const a of auras) {
    if (a.name && token.name !== a.name) continue     // 按具体卡名匹配（僵尸光环）
    if (a.tribe && a.tribe !== tribe) continue
    if (a.maxStar != null && Number(token.star || 0) > Number(a.maxStar)) continue
    token.attack = (token.attack || 0) + (Number(a.atk) || 0)
    token.health = (token.health || 0) + (Number(a.hp) || 0)
    token.hp = token.health
  }
  return token
}


function summonToBoard(card, selfSide, options, by, round, summoner) {
  // 性能闸门：超过上限就不再召唤
  selfSide.__summonCount = (selfSide.__summonCount || 0) + 1
  if (selfSide.__summonCount > MAX_SUMMONS_PER_SIDE) return null

  // ⚠️ 真实规则：**战场只有 7 个位置**，一次召唤超额的部分不会出现。
  //    用户的实际例子：场上快满时，引路船工（召 3 个妖精）第一个被攻击，
  //    只会召出 2 个 —— 第 3 个因为没位置而"消失"。
  //    不判这个上限，召唤物就会凭空变多，我方场面被高估。
  const liveCount = (selfSide.board || []).filter(
    (f) => f && f.alive && (f.health == null || f.health > 0)).length
  if (liveCount >= MAX_BOARD_SLOTS) return null

  const token = cardToFollower(card, {})
  // ⚠️ cardToFollower **不设置 uid**。
  // 而技能钩子的注册表是按 uid 索引的，uid 为空就没法注册 ——
  // 召唤物死掉时它的谢幕曲不会触发，连锁只能走一代。
  // 这里给它一个唯一 uid。
  __summonSeq += 1
  token.uid = token.uid || ("summon-" + __summonSeq + "-" + (by || ""))
  token.hp = token.health
  token.alive = token.health > 0
  token.wardUsed = false
  token.summonedRound = round || 0
  token.isToken = true
  token.summonedBy = by

  /**
   * 「僵尸召唤的僵尸不能再召唤自己」。
   *
   * 官方数据里腐臭的僵尸的谢幕曲最后一条是
   *     remove_skill  skill_timing=when_destroy
   * 它的作用是让**召唤出来的复制体**失去该时点的技能。
   * 不这么处理的话 僵尸→僵尸→僵尸… 会无限连锁（实测滚到 40 次）。
   */
  try {
    const src = summoner && Array.isArray(summoner.meta && summoner.meta.skills)
      ? summoner.meta.skills
      : []
    const stripped = new Set()
    for (const s0 of src) {
      if (s0 && s0.action === 'remove_skill') {
        // option 是字符串（"skill_timing=when_destroy"），必须解析
        const opt0 = typeof s0.option === 'string'
          ? parseOptions(s0.option)
          : (s0.option || {})
        const tm = String(opt0.skill_timing ?? opt0.skillTiming ?? '')
        if (tm && tm !== 'none') stripped.add(tm)
      }
    }
    if (stripped.size && token.meta && Array.isArray(token.meta.skills)) {
      token.meta.skills = token.meta.skills.filter(
        (s1) => !stripped.has(String(s1.timing)))
    }
  } catch (e) { /* 剥技能失败不影响召唤本身 */ }

  // 进化形态：+2/+2（用户指出「拉出来的新绿的妖精会自动进化」）
  if (options && options.evolve) {
    token.attack = (token.attack || 0) + 2
    token.health = (token.health || 0) + 2
    token.hp = token.health
    token.evolved = true
  }
  applyAurasToToken(token, selfSide)
  selfSide.board.push(token)
  return token
}

function pickRandom(arr, rng) {
  const r = typeof rng === 'function' ? rng() : Math.random()
  return arr[Math.min(arr.length - 1, Math.floor(r * arr.length))]
}

export function setSkillRegistry(list) {
  SKILL_REGISTRY.clear()
  for (const s of list || []) {
    if (s && s.skillId != null) SKILL_REGISTRY.set(String(s.skillId), s)
  }
}

export function getSkillById(id) {
  const k = String(id)
  const hit = SKILL_REGISTRY.get(k)
  if (hit) return hit
  // ⚠️ 回退：有些「被附加的技能」没进全局注册表（例：无极猎人的残响_4
  //    是 SkillId 500000018，只在卡池里）。不找回它，attach_skill 就会整条失败，
  //    "进化时获得残响"永远不生效。
  for (const c of CARD_POOL || []) {
    for (const s of (c && c.skills) || []) {
      const sid = (s && (s.skillId != null ? s.skillId : s.SkillId))
      if (sid != null && String(sid) === k) return s
    }
  }
  return null
}


export function compileSkill(skill, opts = {}) {
  // forceTiming：invoke_skill 要重放「入场曲」这类**正常不在战斗里触发**的技能，
  //   所以允许绕过时点过滤编译出来；这种效果只进"可重放表"，不挂任何钩子。
  const forced = !!(opts && opts.forceTiming)
  const timing = TIMING_ENGINE[skill.timing]
  if (!timing || timing === 'none') {
    if (!forced) {
      return { ok: false, nonBattle: true, reason: `时点 ${skill.timing} 不参与战斗模拟` }
    }
  }
  // 只计算战斗阶段会发动的效果：入场曲/备战/回合结束等运营时点一律排除。
  //
  // ⚠️ 但要**优先采信卡数据里的 battleTiming 标记**：官方数据给每条技能都标了
  //    battleTiming，它才是"这个效果会不会在战斗里触发"的权威答案。
  //    之前只看时点白名单，把 onDestroyOther 一律当运营阶段，于是【残响】类效果
  //    （例：煌击战士·贝鲁 —— 每 2 个随从被破坏，对敌方随机随从造成 4 点伤害）
  //    在战斗里完全不结算，胜率被高估。
  const battleByData = skill.battleTiming === true
  if (!forced && !battleByData && !BATTLE_TIMINGS.includes(timing)) {
    return {
      ok: false,
      nonBattle: true,
      reason: timing === 'turnStart'
        ? '回合开始时的效果在战斗中不触发'
        : `时点 ${timing} 属运营阶段，战斗结算不计`,
    }
  }
  const action = skill.action
  if (!SIMPLE_ACTIONS.has(action)) {
    return { ok: false, reason: `能力类型 ${action} 尚未建模` }
  }
  // 触发条件：解析成 AST，留到**触发瞬间**再求值（条件依赖当时的局面）
  const condRes = parseCondition(skill.condition)
  if (!condRes.ok) {
    return { ok: false, reason: condRes.reason }
  }
  const conditionAst = condRes.ast
  const opt = parseOptions(skill.option)
  const effect = { timing, action, condition: conditionAst,
    raw: typeof skill.option === 'string' ? skill.option : JSON.stringify(skill.option || {}) }

  switch (action) {
    case 'powerup': {
      for (const key of Object.keys(opt)) {
        if (isGlobalExpression(opt[key]) && !/^\{me\.self\./.test(String(opt[key]))) {
          return { ok: false, reason: `增益数值依赖全局统计：${opt[key]}` }
        }
      }
      const atk = parseAmount(opt.add_offense, 'offense')
      const life = parseAmount(opt.add_life, 'life')
      if (!atk && !life) return { ok: false, reason: `增益数值无法解析：${skill.option}` }
      effect.addAttack = atk
      effect.addHealth = life
      return { ok: true, effect }
    }
    case 'damage': {
      const amount = parseAmount(opt.damage, 'offense')
      if (!amount) return { ok: false, reason: `伤害数值无法解析：${skill.option}` }
      effect.amount = amount
      return { ok: true, effect }
    }
    case 'attack_count': {
      const count = Number(opt.attack_count)
      if (!Number.isFinite(count)) return { ok: false, reason: `攻击次数无法解析：${skill.option}` }
      effect.count = count
      return { ok: true, effect }
    }
    case 'guard':
    case 'shield':
    case 'killer':
    case 'indestructible':
      effect.ability = action
      return { ok: true, effect }
    case 'evolve':
      effect.evolve = true
      return { ok: true, effect }
    case 'summon_token': {
      // 参数形如 `summon_token=90051120` 或 `summon_token=90051120:90051120`（多选一）
      const rawToken = opt.summon_token
      if (rawToken == null || rawToken === true) {
        // 官方数据里「按条件随机召唤」的卡（尤娜/树海的战士/暗之法则）
        // summon_token 是空的，目标池由卡面文本决定 —— 这里放行，
        // 运行时按卡名查 RANDOM_SUMMON_BY_NAME。
        effect.summon = { cardId: null, dynamic: true }
        return { ok: true, effect }
      }
      const candidates = String(rawToken).split(':').filter(Boolean)
      const tokenCardId = Number(candidates[0])
      if (!Number.isFinite(tokenCardId)) {
        return { ok: false, reason: `召唤卡号无法解析：${rawToken}` }
      }
      const nums = candidates.map(Number).filter(Number.isFinite)
      const uniq = [...new Set(nums)]
      effect.summon = {
        cardId: tokenCardId,
        alternatives: nums,
        /**
         * ⚠️ `A:A:A` 这种**同卡号重复**表示"召唤 N 个"，不是"N 选一"。
         *    之前一律按单次召唤处理，于是引路船工（召 3 个妖精）只召出 1 个，
         *    直接低估了我方场面（用户指出：满场时应该只召出 2 个才算对）。
         */
        repeat: uniq.length === 1 && nums.length > 1 ? nums.length : 1,
        randomAlternative: uniq.length > 1,
        // `&evolve=1` → 召唤出来的是**进化后**的形态
        evolve: opt.evolve === 1 || opt.evolve === '1',
      }
      return { ok: true, effect }
    }
    case 'random_array': {
      // 「随机选一个分支」：凯尔贝洛斯的【残响_4】用它决定召唤哪只守卫犬。
      // 真正的分支内容在**同一张卡的其它技能**里（cond=only_index=N），
      // 编译阶段拿不到兄弟技能，所以在 buildSkillHooks 里补全。
      const range = Number(opt.random_range ?? opt.randomRange ?? 1)
      const sum = Number(opt.sum ?? 1)
      effect.randomIndex = {
        range: Number.isFinite(range) && range > 0 ? range : 1,
        count: Number.isFinite(sum) && sum > 0 ? sum : 1,
      }
      return { ok: true, effect }
    }
    case 'remove_skill': {
      // 移除自己某个时点的技能。
      // 腐臭的僵尸靠它**防止谢幕曲无限连锁召唤**（每次死亡都再召唤一个）。
      // 不实现的话僵尸会一直生，战场被塞满，胜率严重失真。
      const tm = opt.skill_timing ?? opt.skillTiming ?? ''
      effect.removeTiming = String(tm)
      return { ok: true, effect }
    }
    case 'claim_aura': {
      // 给主战者挂一个光环。战斗里它会影响**之后登场的随从**
      // （用户确认：僵尸的谢幕曲光环作用于下一个僵尸），所以不能当成 noop。
      const baseId = opt.base_card_id ?? opt.baseCardId
      if (baseId == null) {
        return { ok: false, reason: '光环缺少 base_card_id' }
      }
      effect.aura = {
        baseCardId: String(baseId),
        saveAfterBattle: opt.save_after_battle === 1 || opt.save_after_battle === '1',
      }
      return { ok: true, effect }
    }
    case 'attach_skill': {
      // 给随从附加另一条技能：找到目标技能后**递归编译**，
      // 让它在同一时点一起结算。
      const sid = opt.skill_id ?? opt.skillId
      const target = sid != null ? getSkillById(sid) : null
      if (!target) {
        return { ok: false, reason: `附加的技能 ${sid} 不在技能注册表` }
      }
      const sub = compileSkill(target)
      if (!sub.ok) {
        return { ok: false, nonBattle: !!sub.nonBattle, reason: `附加技能编译失败：${sub.reason}` }
      }
      effect.attach = sub.effect
      return { ok: true, effect }
    }
    case 'overwrite_skill': {
      // 把一条技能替换成另一条（典型：超进化后换掉残响）
      const toId = opt.to_skill_id ?? opt.toSkillId
      const target = toId != null ? getSkillById(toId) : null
      if (!target) {
        return { ok: false, reason: `替换目标技能 ${toId} 不在技能注册表` }
      }
      const sub = compileSkill(target)
      if (!sub.ok) {
        return { ok: false, nonBattle: !!sub.nonBattle, reason: `替换技能编译失败：${sub.reason}` }
      }
      effect.attach = sub.effect
      return { ok: true, effect }
    }
    case 'token_draw':
    case 'coin_modifier':
    case 'upgrade_cost_modifier': {
      // 这四类在**战斗结算里确实什么都不做**（抽到手里/挂运营光环/加金币/降升级费），
      // 所以标 noop 而不是"未建模"——否则覆盖率会虚低，
      // 也会掩盖真正缺的东西。
      effect.noop = true
      effect.why = `${action} 不影响战斗结算`
      return { ok: true, effect }
    }
    case 'repeat': {
      // 【残响】/「此效果将进行2次」——本质是**放大器**：
      // 把同一随从、同一时点的其它效果结算 N 次。
      // 这里只记录次数，真正的倍增在 buildSkillHooks 里做（见 repeatByTiming）。
      const n = Number(opt.repeat_count ?? opt.count ?? 1)
      effect.repeat = { count: Number.isFinite(n) && n > 0 ? n : 1 }
      effect.noop = true
      return { ok: true, effect }
    }
    case 'select': {
      // 选择动作（selectType=client/random、selectCount=N）本身不改数值：
      // 真正的效果由同一张卡的其它技能行承接（这类技能 option 都是空的）。
      // 标 noop 而不是"未建模"，否则覆盖率会虚低、也掩盖真正缺的东西。
      effect.noop = true
      effect.why = `选择动作（${skill.selectType || ''}×${skill.selectCount || 0}）由后续技能承接`
      return { ok: true, effect }
    }
    case 'banish':
    case 'remove_by_banish': {
      // 放逐 / 被放逐移除：客户端只是**服务端事件的演出**
      // （NotifyEventBanish → BanishFinalAction 复原面板），本卡数据没有目标或数值。
      effect.noop = true
      effect.why = '放逐是服务端事件（本卡数据无目标/数值）'
      return { ok: true, effect }
    }
    case 'invoke_skill': {
      // 「发动与【入场曲】相同的能力」：把同一张卡、同一时点的技能**再跑一遍**。
      // 典型：育龙少女 / 侮蔑的祈祷者 / 勇猛的鲁米那斯枪士 的「进化时」。
      const rawType = String(opt.invoke_type ?? opt.invokeType ?? '')
      const timing = TIMING_ENGINE[rawType]
      if (!timing || timing === 'none') {
        return { ok: false, reason: `invoke_type 无法识别：${rawType}` }
      }
      effect.invoke = { timing: rawType, skillEvo: String(opt.is_skill_evo ?? '') === '1' }
      return { ok: true, effect }
    }
    case 'attack':
      // 「攻击时」这类动作本身不带参数，触发点即效果
      effect.noop = true
      return { ok: true, effect }
    default:
      return { ok: false, reason: `未处理的能力：${action}` }
  }
}

/** 数值求值：const / self */
function evalAmount(spec, follower) {
  if (!spec) return 0
  if (spec.kind === 'const') return spec.value
  if (spec.kind === 'self') {
    const base = spec.stat === 'offense' ? follower.attack : follower.health
    let v = base * (spec.multiplier || 1)
    if (spec.divideCeil) v = Math.ceil(v / spec.divideCeil)
    return v
  }
  return 0
}

function addAbility(follower, id) {
  if (!Array.isArray(follower.abilities)) follower.abilities = []
  if (!follower.abilities.includes(id)) follower.abilities.push(id)
}

/**
 * 把一份局面里所有随从的技能编译成 hooks。
 *
 * @param {object} battle 归一化后的局面（随从需带 meta.skills，可由 knowledge.enrichBattle 产生）
 * @param {object} [options] { battleCardById }
 * @returns {{ hooks: object, compiled: number, skipped: Array<{name,reason}> }}
 */
export function buildSkillHooks(battle, options = {}) {
  const skipped = []
  let compiled = 0
  /**
   * 建立 uid → 随从 的索引。
   *
   * 注意：`enrichBattle`（按卡名补全面板的那条路径）产出的随从**可能没有 uid**，
   * 这时用 uid 当 Map key 会让所有随从塌成同一个 `undefined` key，
   * 结果是一条技能都不会被编译（实测踩过：compiled=0 且 skipped 为空）。
   * 因此这里对缺失 uid 的随从**补一个稳定的合成 id**。
   */
  const byUid = new Map()
  let synthesized = 0
  for (const key of ['player', 'opponent']) {
    for (const f of (battle[key] && battle[key].board) || []) {
      if (f.uid === undefined || f.uid === null) {
        synthesized += 1
        f.uid = `synth-${key}-${synthesized}`
      }
      byUid.set(f.uid, { side: key, follower: f })
    }
  }

  const hooks = {}
  /**
   * 处理器列表 + 固定分发器。
   *
   * 为什么不用「链式覆盖」：战斗里存在**同回合内先召唤、后死亡**的情况
   * （死亡主持人被破坏 → 立刻召唤 2 个尸兵 → 尸兵当回合被打死）。
   * 链式覆盖下，尸兵的处理是在本次 onDeath 调用**之后**才追加到 hooks 上的，
   * 这次的死亡事件收不到 —— 尸兵的谢幕曲（召僵尸）就丢了。
   *
   * 用数组 + 分发器后，追加的处理器会被正在进行的那次遍历看到，
   * 当场生效。
   */
  const HANDLERS = {}
  let __registeringUid = null
  let __handlerTotal = 0
  const makeDispatcher = (name) => (ctx) => {
    // 先补注册场上新出现的召唤物（它可能就在本次事件里刚登场）
    try { if (ctx && ctx.state) syncState(ctx.state) } catch (e) { /* 忽略 */ }
    const list = HANDLERS[name] || []
    // 用下标遍历：list 可能在遍历中增长（新召唤物的处理器），需要一并执行
    for (let i = 0; i < list.length; i += 1) {
      try { list[i](ctx) } catch (e) { /* 单个处理器出错不影响其他 */ }
    }
  }
  const on = (name, fn) => {
    if (!HANDLERS[name]) HANDLERS[name] = []
    // 记下这个处理器属于哪个随从，方便后面按"是否还在场上"清理
    fn.__uid = __registeringUid
    HANDLERS[name].push(fn)
    __handlerTotal += 1
    if (!hooks[name]) hooks[name] = makeDispatcher(name)
  }

  const registered = new Set()
  const registerAll = () => {
  for (const [__uid, { side, follower }] of byUid) {
    if (registered.has(__uid)) continue
    registered.add(__uid)
    const rawSkills = (follower.meta && follower.meta.skills) || options.skillsByUid?.get(follower.uid) || []
    /**
     * 只取**与当前形态匹配**的技能。
     *
     * 官方数据里同一张卡会把「进化前 / 进化后 / 超进化后」三套技能
     * 一起放进 skills 数组，用 evolveForm 区分（0/1/2）。
     * 不过滤的话，未进化的随从会**同时触发进化形态的效果** ——
     * 实测：腐臭的僵尸召唤 2 次（应为 1 次）、尸兵召唤 3 次（应为 1 次）。
     */
    /**
     * ⚠️ 2026-10-18 改：**不再在建表时按形态过滤**。
     *
     * 原来这里按 `evolveForm === 当前形态` 过滤，但进化是**战斗中途**发生的，
     * 而注册表只在随从登场时建一次 —— 于是进化后的「进化时」技能永远注册不上，
     * 育龙少女/侮蔑的祈祷者这类牌的【进化时】在模拟里完全不存在。
     *
     * 现在：**所有形态都注册**，在 apply 里按"触发瞬间的形态"把关
     * （effect.form 与当前形态不一致就直接 return）。语义等价，而且进化后立刻能生效。
     */
    const formOf = (s0) => Number(s0 && s0.evolveForm != null ? s0.evolveForm : 0)
    const skills = rawSkills

    /**
     * invoke_skill 想重放的时点（例如 `invoke_type=when_play`）。
     * 这些技能本来会被时点过滤掉（入场曲在战斗前就结算完了），
     * 但【进化时：发动与入场曲相同的能力】需要重放 → 强制编译一份放进重放表，
     * **不挂钩子**（否则入场曲会在开战时白嫖一次）。
     */
    const __invokeTimings = new Set()
    for (const s0 of skills) {
      if (s0 && s0.action === 'invoke_skill') {
        const o0 = parseOptions(s0.option)
        const t0 = String(o0.invoke_type ?? o0.invokeType ?? '')
        if (t0) __invokeTimings.add(t0)
      }
    }

    /** 同卡已编译效果的引用表（invoke_skill 重放用） */
    const __siblings = []


    // random_array 的分支表：同卡里 cond 含 only_index=N 的 summon_token
    // 就是第 N 号分支。凯尔贝洛斯靠这个决定召唤米米还是可可。
    const randomBranches = []
    {
      const byIndex = new Map()
      for (const s0 of skills) {
        if (s0.action !== 'summon_token') continue
        const c0 = String(s0.condition || '')
        const m0 = /only_index=(\d+)/.exec(c0)
        if (!m0) continue
        const id0 = Number(parseOptions(s0.option).summon_token)
        if (!Number.isFinite(id0)) continue
        const k0 = Number(m0[1])
        if (!byIndex.has(k0)) byIndex.set(k0, [])
        byIndex.get(k0).push(id0)
      }
      for (const k0 of [...byIndex.keys()].sort((a, b) => a - b)) {
        randomBranches.push(byIndex.get(k0))
      }
    }
    if (randomBranches.length) {
      follower.__randomBranches = randomBranches
    }

    // 「此效果将进行 N 次」：按**时点**收集放大器的次数。
    // 例：煌击战士·贝鲁 进化形态 repeat(repeat_count=2) 作用于同时点的
    //     【残响】伤害 → 该伤害结算 2 次。
    const repeatByTiming = new Map()
    for (const s0 of skills) {
      if (s0.action !== 'repeat') continue
      const o0 = parseOptions(s0.option)
      const n0 = Number(o0.repeat_count ?? o0.count ?? 1)
      if (Number.isFinite(n0) && n0 > 1) repeatByTiming.set(s0.timing, n0)
    }

    for (const skill of skills) {
      let result = compileSkill(skill)
      let __forcedOnly = false
      if (!result.ok && __invokeTimings.has(skill.timing)) {
        const forcedRes = compileSkill(skill, { forceTiming: true })
        if (forcedRes.ok) { result = forcedRes; __forcedOnly = true }
      }
      if (!result.ok) {
        skipped.push({ name: follower.name, skillId: skill.skillId, timing: skill.timing,
                       action: skill.action, reason: result.reason,
                       nonBattle: !!result.nonBattle })
        continue
      }
      if (!__forcedOnly) compiled += 1
      const effect = result.effect
      // 形态标记：注册时全收，触发时按当前形态把关（见 apply 顶部）
      effect.form = formOf(skill)
      /**
       * 谢幕曲（lastWord）在**随从已经被破坏之后**才触发，
       * 所以不能套用「自己必须存活」的前置检查 —— 否则谢幕曲会被整个吞掉（实测踩过）。
       * 其他时点仍然要求自己还活着。
       */
      const firesAfterDeath = effect.timing === 'lastWord'
      const apply = (ctx) => {
  // 召唤之后广播「有随从进入战场」，供站场效果（徒姬）使用
  const notifySummoned = (token) => {
    if (!token) return
    try {
      if (typeof hooks.onSummonOther === 'function') {
        hooks.onSummonOther({ summoned: token, side, state: ctx.state,
                              rng: ctx.rng, log: options.log })
      }
    } catch (e) { /* 单个效果失败不影响召唤 */ }
  }

        // ⚠️ 有的钩子**不带 state**（onEvolve 就是：battle.mjs 只传 follower/mode/rules/log），
        //    旧代码直接读 ctx.state → undefined → 整个效果抛错被分发器静默吞掉，
        //    所有「进化时」技能等于不存在。这里退回构建时的 battle。
        const state = (ctx && ctx.state) || battle
        const selfSide = state[side === 'player' ? 'player' : 'opponent']
        const enemySide = state[side === 'player' ? 'opponent' : 'player']
        const self = byUid.get(follower.uid)?.follower
        if (!self) return
        if (!self.alive && !firesAfterDeath) return
        // 形态把关：所有形态都注册了，但只让"当前形态"的技能生效。
        // （进化后 self.isEvolved 变 true，手里这套 handler 立刻按新形态执行）
        if (effect.form != null && !(ctx && ctx.__skipForm)) {
          const fNow = self.isSuperEvolved ? 2
            : (self.isEvolved || self.isTripleEvolved) ? 1 : 0
          if (effect.form !== fNow) return
        }
        // 被 remove_skill 移除过的时点不再发动
        if (self.__removedTimings && self.__removedTimings[effect.timing]) return

        // onSummonOther 的种族条件已在处理器里手动判过，这里跳过通用条件求值
        const __skipCond = !!(ctx && ctx.skipCondition)
        // ── 触发条件检查（在**触发瞬间**用当前局面求值）──
        // 之前完全没有这一步：只要编译通过就会无条件结算，
        // 或者（改之前）条件稍微复杂就直接拒绝。两种都不对。
        if (effect.condition && !__skipCond) {
          const cctx = {
            self,
            selfSide,
            enemySide,
            // 己方阵亡数：优先用局面级计数，退回随从自己的计数
            destroyedCount: (selfSide && selfSide.destroyedCount) ?? (follower.__destroyCount || 0),
            evolvedCount: ((selfSide && selfSide.board) || [])
              .filter((f) => f.alive && f.hp > 0 && f.evolved).length,
            summonedClass: (ctx.summoned && (ctx.summoned.tribe || ctx.summoned.cardClass)) || null,
          }
          const okCond = evalCondition(effect.condition, cctx)
          if (okCond === false) return
          if (okCond === undefined && typeof options.log === 'function') {
            options.log({ t: 'cond-unknown', by: self.name, skillId: skill.skillId,
                          condition: String(skill.condition || '') })
          }
          // undefined = 数据不足，**照常结算**（宁可算进来，也不默默丢掉）
        }
        switch (effect.action) {
          case 'claim_aura': {
            /**
             * 给本方挂一个主战者光环。
             *
             * 之前**只有编译没有运行**：claim_aura 编出了 effect.aura，
             * 但运行期 switch 里没有对应分支 → 僵尸的光环在模拟里完全不存在
             * （用户反馈：僵尸死了应该上光环，但胜率没体现）。
             * 现在挂到 side.auras 上，后续进场的随从按条件吃加成。
             */
            const aid = String((effect.aura && effect.aura.baseCardId) || '')
            const st = ctx.state[side === 'player' ? 'player' : 'opponent']
            if (st && aid) {
              if (!Array.isArray(st.auras)) st.auras = []
              const cond = AURA_BY_ID[aid] || { atk: 0, hp: 0 }
              const key = aid + '|' + JSON.stringify(cond)
              st.__auraKeys = st.__auraKeys || {}
              if (!st.__auraKeys[key]) {
                st.__auraKeys[key] = 1
                st.auras.push({ tribe: cond.tribe || null,
                                name: cond.name || null,
                                maxStar: cond.maxStar == null ? null : cond.maxStar,
                                atk: cond.atk || 0, hp: cond.hp || 0 })
              }
            }
            break
          }
          case 'powerup': {
            // ⚠️ 有 ctx.target（例如「其他随从进入战场时」）时，加在**目标**上，
            //    而不是技能持有者自己。徒姬就是这个模式：
            //    她站场时梦魇随从进场 +6/+6，之前却加到了她自己身上。
            const tgt = (ctx.target && ctx.target.alive) ? ctx.target
              : ((ctx.summoned && ctx.summoned.alive) ? ctx.summoned : self)
            // `{me.self.offense}` 指的是**技能持有者**的攻击力（例如冰界鹿王），
            // 其它表达式才按目标算。
            const srcForAtk = /me\.self/.test(String(effect.addAttack || '')) ? self : tgt
            const srcForHp = /me\.self/.test(String(effect.addHealth || '')) ? self : tgt
            const atk = evalAmount(effect.addAttack, srcForAtk)
            const life = evalAmount(effect.addHealth, srcForHp)
            // 「自己的战场上的所有随从 +N/+N」（哪吒）→ 一次作用到全体
            const multi = Array.isArray(ctx.targets) && ctx.targets.length
              ? ctx.targets.filter((x) => x && x.alive)
              : [tgt]
            for (const tg of multi) {
              tg.attack += atk
              tg.health += life
              if (tg.hp > 0) tg.hp += life
            }
            break
          }
          case 'damage': {
            const amount = evalAmount(effect.amount, self)
            const target = ctx.target && ctx.target.alive ? ctx.target : null
            if (target && amount > 0) {
              target.hp -= amount
              if (target.hp <= 0) { target.hp = 0; target.alive = false }
            }
            break
          }
          case 'guard': case 'shield': case 'killer': case 'indestructible':
            addAbility(self, effect.ability)
            break
          case 'attack_count':
            addAbility(self, 'doubleAttack')
            break
          case 'summon_token': {
            // 谢幕曲召唤类（如魅魔莉莉姆召唤蝙蝠、尸兵召唤腐臭的僵尸）：
            // 在**自己这一侧**登场一个 token，面板取官方卡表的基础值。
            const spec = effect.summon
            // spec.dynamic = 空参数的"按条件随机召唤"（尤娜/树海/暗之法则）
            if (!spec || spec.dynamic) {
              // 没有固定卡号 → 按卡名查「随机召唤」规则
              const rule = RANDOM_SUMMON_BY_NAME[self.name]
              if (rule) {
                const ids = rule(ctx.rng, { level: (selfSide && selfSide.level) || 0 })
                for (const cid of ids) {
                  const card = findCardById(cid)
                  if (!card) continue
                  const room = (selfSide.board || []).filter((f) => f.alive && f.hp > 0).length
                  if (room >= (options.maxBoard || 7)) break
                  const token = summonToBoard(card, selfSide, options, self.name, ctx.round, self)
                  if (token) notifySummoned(token)
                  if (token && typeof options.log === 'function') {
                    options.log({ t: 'summon-token', side, by: self.name,
                                  token: token.name, tokenCardId: cid, atk: token.attack, hp: token.health, via: 'random-rule' })
                  }
                  if (typeof options.onSummon === 'function') options.onSummon(token, selfSide)
                }
              }
              break
            }
            const tokenCard = findCardById(spec.cardId)
            if (!tokenCard) {
              skipped.push({
                name: self.name, skillId: skill.skillId, timing: skill.timing, action: 'summon_token',
                reason: `召唤目标卡号 ${spec.cardId} 不在知识库（token 表可能未收录）`,
              })
              break
            }
            // `A:A:A`（引路船工召 3 个妖精）= 召唤 repeat 次；
            // 中途满 7 格就停 —— 真实规则：超额的那几个不会出现。
            for (let _rep = 0; _rep < Math.max(1, spec.repeat || 1); _rep += 1) {
              const room = (selfSide.board || []).filter((f) => f.alive && f.hp > 0).length
              if (room >= (options.maxBoard || 7)) break
              const token = summonToBoard(tokenCard, selfSide, { ...options, evolve: !!(spec && spec.evolve) }, self.name, ctx.round, self)
              if (!token) break
              notifySummoned(token)
              if (typeof options.log === 'function') {
                options.log({ t: 'summon-token', side, by: self.name, token: token.name, tokenCardId: spec.cardId, atk: token.attack, hp: token.health })
              }
              if (typeof options.onSummon === 'function') options.onSummon(token, selfSide)
            }
            break
          }
          case 'evolve': {
            // 「使某随从进化」：加面板 + 触发「随从进化时」。
            // 原来的实现只加 rush，面板不变、也不发事件，等于没进化。
            const did = evolveFollower(self)
            if (did) {
              if (typeof options.log === 'function') {
                options.log({ t: 'evolve', side, who: self.name })
              }
              // ① 这个随从**自己的**「进化时」也要触发。
              //    用户 2026-10-18 明确：效果造成的进化（卡牌效果 / 主战者效果）同样算进化，
              //    【进化时】要发动（例：虫风花、古兰&姬塔）；只有**三连进化**不触发。
              //    `__effectEvolving` 防递归：进化时效果里再进化，不会无限触发。
              if (typeof hooks.onEvolve === 'function' && !self.__effectEvolving) {
                self.__effectEvolving = true
                try {
                  // ⚠️ timing 用 'skill'：**效果造成的进化**只触发「进化的时候」类效果
                  //    （数据里的 when_skill_evolve），不触发带括号的【进化时】（那只认吃进化点）。
                  hooks.onEvolve({
                    follower: self, side, state, mode: 'skill', timing: 'skill',
                    rng: ctx.rng, log: options.log, round: ctx.round,
                  })
                } finally {
                  self.__effectEvolving = false
                }
              }
              // ② 再广播给"其他随从进化时"（圣德芬这类），语义是另一条。
              if (typeof hooks.onEvolveOther === 'function') {
                hooks.onEvolveOther({
                  evolved: self, side, state, rng: ctx.rng,
                  log: options.log, round: ctx.round,
                })
              }
            }
            self.canSuperEvolve = !!self.canSuperEvolve
            addAbility(self, 'rush')
            break
          }
          case 'invoke_skill': {
            /**
             * 「发动与【入场曲】相同的能力」。
             *
             * 游戏里的做法（反编译里那套「重放同签名技能」的行为树）就是
             * 让同一张卡、同一时点的技能**再跑一遍**；这里照做，且各自重新求值条件。
             *
             * 典型收益：育龙少女 / 侮蔑的祈祷者 / 勇猛的鲁米那斯枪士 的【进化时】
             * 原来在模拟里完全不存在（进化后技能注册不上），现在能生效。
             */
            const wantTiming = effect.invoke && effect.invoke.timing
            // 表里存的是**引擎时点**（when_play → fanfare），这里要转一下
            const wantEngine = wantTiming ? (TIMING_ENGINE[wantTiming] || wantTiming) : ''
            if (wantTiming) {
              let sibs = __siblings.filter(
                (x) => x.timing === wantEngine && x.action !== 'invoke_skill' && !x.noop)
              // 优先基础形态（form 0）：育龙少女的 form 1 那条是「三连进化入场曲」，
              // 不属于"入场曲"，重放它就会多召一只。
              const baseForm = sibs.filter((x) => x.form === 0)
              if (baseForm.length) sibs = baseForm
              for (const sib of sibs) {
                // __skipForm：重放的是基础形态的入场曲，此时随从已经是进化形态，
                // 不能因为形态不一致把它筛掉。
                sib.apply({ state, target: null, rng: ctx && ctx.rng,
                            round: ctx && ctx.round, __skipForm: true })
              }
              if (sibs.length && typeof options.log === 'function') {
                options.log({ t: 'invoke-skill', side, by: self.name,
                              timing: wantTiming, n: sibs.length })
              }
            }
            break
          }
          default:
            break
        }




        // random_array：随机选一个分支，并执行该分支的召唤
        if (effect.randomIndex) {
          const spec = self.__randomBranches && self.__randomBranches[effect.randomIndex.count - 1]
          if (spec && spec.length) {
            const idx = Math.floor((typeof ctx.rng === 'function' ? ctx.rng() : Math.random())
              * effect.randomIndex.range)
            const cardId = spec[Math.min(spec.length - 1, idx)]
            const tokenCard = findCardById(cardId)
            if (tokenCard) {
              const room = (selfSide.board || []).filter((f) => f.alive && f.hp > 0).length
              if (room < (options.maxBoard || 7)) {
                const token = summonToBoard(tokenCard, selfSide, { ...options, evolve: !!(spec && spec.evolve) }, self.name, ctx.round, self)
                if (token) notifySummoned(token)
              for (let _rep = 0; _rep < Math.max(1, spec.repeat || 1); _rep += 1) {
              const token = summonToBoard(tokenCard, selfSide, { ...options, evolve: !!(spec && spec.evolve) }, self.name, ctx.round, self)
              if (token) notifySummoned(token)
              else break          // 满 7 格 → 后面的不再出现（真实规则）
              }
                if (token) notifySummoned(token)
                if (typeof options.log === 'function') {
                  options.log({ t: 'summon-token', side, by: self.name,
                                token: token.name, tokenCardId: cardId, via: 'random_array' })
                }
                if (typeof options.onSummon === 'function') options.onSummon(token, selfSide)
              }
            }
          }
        }

        // remove_skill：该随从这个时点起不再发动效果
        if (effect.removeTiming) {
          if (!self.__removedTimings) self.__removedTimings = {}
          self.__removedTimings[effect.removeTiming] = true
        }

        // claim_aura：把光环挂到己方（已有的不重复挂，对应官方条件 <1）
        if (effect.aura) {
          if (!Array.isArray(selfSide.auras)) selfSide.auras = []
          if (!selfSide.auras.some((a) => a.baseCardId === effect.aura.baseCardId)) {
            selfSide.auras.push({ ...effect.aura, by: self.name })
            if (typeof options.log === 'function') {
              options.log({ t: 'aura', side, by: self.name, baseCardId: effect.aura.baseCardId })
            }
          }
        }

        // ── attach_skill：如果是「谢幕曲」，不能立刻执行，要**挂到随从身上** ──
        if (effect.attach && effect.attach.timing === 'lastWord'
            && effect.attach.action === 'summon_token') {
          const sub0 = effect.attach
          /**
           * 作用范围：
           *   不弑的团结者 → 己方**所有精灵随从**（文本：「自己战场上的所有精灵·随从」）
           *   其它卡       → 只有自己
           *
           * 叠加：多张团结者会各自给精灵挂一份，数组里就是多份，死亡时逐个召唤。
           */
          // ⚠️ 必须落到**克隆棋面**上的对象：byUid 里的是原始对象，
          //    而战斗里死亡事件拿到的是克隆对象，记错对象会导致谢幕曲永不触发。
          const liveBoard = (selfSide && selfSide.board) || []
          const selfLive = liveBoard.find((f) => f && f.uid === self.uid) || self
          let targets = [selfLive]
          if (self.name === '不弑的团结者') {
            // 用 _tribeOf 回查卡库：follower 对象上没有 tribe 字段
            targets = liveBoard.filter(
              (f) => f && f.alive && f.hp > 0 && _tribeOf(f) === '精灵')
            const hasSelf = targets.some((f) => f && f.uid === self.uid)
            if (!hasSelf) targets.push(selfLive)
          }
          for (const tgt0 of targets) {
            if (!tgt0) continue
            if (!Array.isArray(tgt0.__grantedLastwords)) tgt0.__grantedLastwords = []
            tgt0.__grantedLastwords.push({
              cardId: sub0.summon && sub0.summon.cardId,
              evolve: !!sub0.evolve,
              by: self.name,
            })
          }
          if (typeof options.log === 'function') {
            options.log({ t: 'grant-lastword', side, by: self.name,
                          targets: targets.length, cardId: sub0.summon && sub0.summon.cardId })
          }
        } else
        // attach_skill / overwrite_skill：其它附加技能仍然立即结算
        if (effect.attach) {
          const sub = effect.attach
          if (sub && sub.action === 'damage') {
            const amount = evalAmount(sub.amount, self)
            const tgt = ctx.target && ctx.target.alive ? ctx.target : null
            if (tgt && amount > 0) {
              tgt.hp -= amount
              if (tgt.hp <= 0) { tgt.hp = 0; tgt.alive = false }
            }
          } else if (sub && sub.action === 'powerup') {
            self.attack += evalAmount(sub.addAttack, self)
            const life = evalAmount(sub.addHealth, self)
            self.health += life
            if (self.hp > 0) self.hp += life
          }
        }

        void selfSide
        void enemySide
      }

      // 按时点决定的结算次数（repeat 放大器）
      const times = skill.action === 'repeat'
        ? 0
        : (repeatByTiming.get(skill.timing) || 1)
      if (times === 0) {
        compiled -= 1
        continue          // repeat 技能本身不产生效果，只当放大器
      }
      const applyN = times > 1
        ? (ctx) => { for (let k = 0; k < times; k += 1) apply(ctx) }
        : apply
      // 记进"可重放表"：invoke_skill 会按 (时点, 基础形态) 取出来重放。
      __siblings.push({
        timing: effect.timing, action: effect.action, form: effect.form,
        noop: !!effect.noop, apply: applyN,
      })
      // 强制编译的"非战斗时点"技能：只进重放表，不挂任何钩子（避免开战白嫖）
      if (__forcedOnly) continue

      switch (effect.timing) {
        case 'static':
          // 常驻效果：开战铺一次；**进化后获得的能力**还要在进化瞬间补挂一次
          // （卡面是"进化后获得守护/必杀"这种，不是【进化时】触发）。
          on('transform', applyN)
          on('onEvolve', (ctx) => {
            const evolved = ctx && ctx.follower
            if (!evolved || evolved.uid !== follower.uid) return
            apply({ state: ctx.state || battle, target: null })
          })
          break
        case 'battleStart':
          on('transform', applyN)
          break
        case 'attack':
          on('onAttack', (ctx) => applyN({ ...ctx, state: ctx.state }))
          break
        case 'onFight':
          /**
           * 交战时（when_fight）。
           * 引擎按「攻击方先、被攻击方后」的顺序**分别调用** onFight，
           * 每次把「本次发动的随从」放在 ctx.attacker 里；
           * 这里只在轮到自己时才结算，从而天然满足「攻击的随从优先发动」。
           */
          on('onFight', (ctx) => {
            const acting = ctx.attacker
            if (!acting || acting.uid !== follower.uid) return
            apply({ state: ctx.state, target: ctx.target })
          })
          break
        case 'damage':
          /**
           * 「本随从受到伤害且**没被破坏**时」（when_damage）。
           *
           * 之前这里写成 `on('onDamage', ctx => applyN(ctx))`：
           *   · 没检查受伤的是不是**自己** —— 任何随从挨打它都触发；
           *   · 也没检查自己是否**被这次伤害破坏** —— 死了还在发动；
           *   · powerup 只加到某一个目标上，"其他随从 +1/+1" 根本加不全。
           * 结果战斧屠龙者这类牌的效果几乎等于没算（胜负因此偏乐观）。
           */
          on('onDamage', (ctx) => {
            const victim = ctx.target
            if (!victim || victim.uid !== follower.uid) return      // 只认自己挨打
            if (!victim.alive || victim.health <= 0) return          // 被破坏不触发
            const st = ctx.state[side === 'player' ? 'player' : 'opponent']
            const board = (st && st.board) || []
            // 卡面里写「其他随从」的才是全体加（战斧屠龙者）；
            // 写「本随从 +X/+X」的只加自己（炎之法则·威尔纳斯）。
            const card = (CARD_POOL || []).find((c) => c && c.name === follower.name)
            let txt = ''
            if (card) {
              txt = typeof card.text === 'string' ? card.text
                : String((card.text && (card.text.normal || card.text.en)) || '')
            }
            const toOthers = /其他/.test(txt)
            if (toOthers) {
              const others = board.filter((f) => f && f.alive && f.uid !== follower.uid)
              if (others.length) {
                apply({ state: ctx.state, targets: others, skipCondition: true })
              }
            } else {
              apply({ state: ctx.state, target: victim, skipCondition: true })
            }
          })
          break
        case 'evolve':
          // 【进化时】：只认普通进化。**超进化时不等于进化时**，不能互相触发。
          on('onEvolve', (ctx) => {
            const evolved = ctx && ctx.follower
            if (!evolved || evolved.uid !== follower.uid) return
            // 【进化时】= **带括号的关键词**，只有"吃进化点"的普通进化才触发：
            //   超进化（superEvolve）不触发；效果造成的进化（skill）也不触发
            //   —— 后者走 when_skill_evolve（见下面 onSkillEvolve 分支）。
            if (ctx.timing !== 'evolve') return
            // __skipForm：超进化过渡阶段要跑"进化形态"的【进化时】
            apply({ state: ctx.state, target: null, __skipForm: ctx.__skipForm })
          })
          break
        case 'superEvolve':
          // 【超进化时】：只认超进化。
          on('onEvolve', (ctx) => {
            const evolved = ctx && ctx.follower
            if (!evolved || evolved.uid !== follower.uid) return
            if (ctx.timing !== 'superEvolve') return          // 普通进化 → 不触发【超进化时】
            apply({ state: ctx.state, target: null, __skipForm: ctx.__skipForm })
          })
          break
        case 'onSkillEvolve':
          // 「本随从进化时」这类**不带括号**的效果：吃进化点会触发，被效果进化也会触发。
          // 数据里对应 when_skill_evolve（虫风花 / 古兰&姬塔 的 form 1 都有一条）。
          on('onEvolve', (ctx) => {
            const evolved = ctx && ctx.follower
            if (!evolved || evolved.uid !== follower.uid) return
            if (ctx.timing !== 'skill') return
            apply({ state: ctx.state || battle, target: null, __skipForm: ctx.__skipForm })
          })
          break
        case 'lastWord':
          on('onDeath', ({ follower: dead, state }) => {
            if (dead && dead.uid === follower.uid) apply({ state, target: null })
          })
          break
        case 'onEvolveOther':
          /**
           * 「自己的**其他**随从进化时」——圣德芬等。
           *
           * 注意与「进化时」（onEvolve / when_evolve）区分：
           *   · onEvolve      玩家**吃进化点**主动进化 → 只给那个随从自己
           *   · onEvolveOther 任何随从进化（含效果造成的）→ 给场上其他随从
           */
          on('onEvolveOther', (ctx) => {
            const who = ctx.evolved
            if (!who || who.uid === follower.uid) return       // 不含自己
            if (ctx.side && ctx.side !== side) return          // 只算己方
            const state = ctx.state
            /**
             * ⚠️ 目标不一样：
             *   · `evolve`（古兰&姬塔）：「其他随从进化时，**使自己**进化」→ 作用在自己身上；
             *   · 其它（圣德芬）：「给随机的其他随从 +X/+X」→ 从"已进化的随从"里抽。
             * 以前一律按后者处理，于是古兰这类"看到别人进化就自己进化"的效果永远不发动。
             */
            if (skill && skill.action === 'evolve') {
              apply({ state, target: null })
              return
            }
            const selfSide = state[side === 'player' ? 'player' : 'opponent']
            const pool = ((selfSide && selfSide.board) || [])
              .filter((f) => f.alive && f.hp > 0 && f.evolved)
            if (!pool.length) return
            const n = Math.min(3, pool.length)
            const picked = new Set()
            for (let k = 0; k < n; k += 1) {
              const idx = Math.floor((typeof ctx.rng === 'function' ? ctx.rng() : Math.random())
                * pool.length)
              picked.add(pool[Math.min(pool.length - 1, idx)])
            }
            for (const t of picked) apply({ state, target: t })
          })
          break
        case 'onSummonOther':
          /**
           * 「自己的其他随从**进入战场**时」——站场持续效果（徒姬）。
           *
           * 与光环的区别：光环挂在主战者身上、整局有效；
           * 这个是**这张牌活着的时候**才触发，她一死后续召唤就不再享受。
           *
           * 条件里 `card_class=nightmare` 表示只对**梦魇**随从生效。
           */
          on('onSummonOther', (ctx) => {
            const target = ctx && ctx.summoned
            if (!target || !target.alive) return
            if (ctx.side && ctx.side !== side) return        // 只算自己这边
            // 条件：card_class=nightmare → 必须是梦魇种族
            const c0 = String((skill && skill.condition) || '')
            const m = /card_class=(\w+)/.exec(c0)
            if (m) {
              const want = m[1]
              const tribe = _tribeOf(target)
              const map = { nightmare: '梦魇', forest: '精灵', royal: '皇家护卫',
                            dragon: '龙族', neutral: '中立' }
              const wantCn = map[want] || want
              if (tribe && tribe !== wantCn) return
              if (!tribe) return                              // 查不到种族就不触发
            }
            apply({ state: ctx.state, target, summoned: target, skipCondition: true })
          })
          break
        case 'onDestroyOther':
          /**
           * 【残响】类：己方随从被破坏时累积计数，每 N 个触发一次。
           *
           * 官方条件 `{...count}%2=0` 里的 2 来自技能名【残响_2】。
           * 这里按"每 2 次死亡触发 1 次"实现；计数挂在随从身上。
           */
          on('onDeathOther', (ctx) => {
            const dead = ctx.dead
            if (!dead || dead.uid === follower.uid) return
            // ⚠️ 侧别在 ctx 上，不在 dead 对象上（dead 只是个随从，
            //    没有 side 字段；用 dead.side 判断会永远不等 → 整个效果静默失效）
            const deadSide = ctx.side || dead.side
            if (deadSide !== side) return           // 只算己方阵亡
            follower.__destroyCount = (follower.__destroyCount || 0) + 1
            if (follower.__destroyCount % 2 !== 0) return
            const state = ctx.state
            const enemySide = state[side === 'player' ? 'opponent' : 'player']
            const pool = (enemySide.board || []).filter((f) => f.alive && f.hp > 0)
            if (!pool.length) return
            const rnd = typeof ctx.rng === 'function' ? ctx.rng() : Math.random()
            const target = pool[Math.min(pool.length - 1,
              Math.floor(rnd * pool.length))]
            apply({ state, target })
          })
          break
        case 'prepare':
        case 'turnStart':
        case 'fanfare':
        case 'onSummon':
        case 'onSummonOther':
        case 'onPlayOther':
        case 'onPlayOtherBefore':
        case 'onAttackSelfAndOther':
        case 'onBuy':
        case 'onSell':
        case 'onShieldLoss':
        case 'onFight':
        default:
          // 这些时点在战斗模拟里没有对应触发点（运营阶段 / 战斗内不触发）
          skipped.push({
            name: follower.name,
            skillId: skill.skillId,
            timing: skill.timing,
            action: skill.action,
            reason: effect.timing === 'turnStart'
              ? '回合开始时的效果在战斗中不触发'
              : (effect.timing === 'fanfare'
                ? '入场曲属运营阶段，战斗模拟内不结算'
                : `时点 ${effect.timing} 无对应触发点`),
          })
          compiled -= 1
          break
      }
    }
    }
  }
  registerAll()


  /**
   * 把场上**新出现**的随从（召唤物）补进注册表。
   *
   * 为什么必须有：开局时只注册了当时的随从。召唤出来的随从不在 byUid 里，
   * 它的谢幕曲永远不会发动 —— 「树海→暗之法则→三张梦魇」
   * 「死亡主持人→尸兵→僵尸→僵尸」这类连锁只能走到第一代。
   */
  /**
   * 清理不在当前局面上的随从及其处理器。
   *
   * 为什么必须做：toolSimulate 只构建一次 hooks 就跑几千次模拟，
   * 而每次模拟都会召唤新随从并注册处理器。不清理的话
   * HANDLERS 会堆到几十万条，事件遍历变成瓶颈（实测 4000 次模拟从 2.5s 涨到 45s+）。
   */
  const purgeStale = (state) => {
    const live = new Set()
    for (const key of ['player', 'opponent']) {
      const side = state[key]
      if (!side || !Array.isArray(side.board)) continue
      for (const f of side.board) if (f && f.uid != null) live.add(f.uid)
    }
    for (const uid of [...byUid.keys()]) {
      if (!live.has(uid)) byUid.delete(uid)
    }
    __handlerTotal = 0
    for (const name of Object.keys(HANDLERS)) {
      const list = HANDLERS[name]
      let keep = list
      if (list.length > 60) {
        keep = list.filter((fn) => fn.__uid == null || live.has(fn.__uid))
      }
      HANDLERS[name] = keep
      __handlerTotal += keep.length
      if (keep.length !== list.length) {
        // 数组被换掉了，分发器要重新指向新数组
        hooks[name] = makeDispatcher(name)
      }
    }
  }

  const syncState = (state) => {
    if (!state) return false
    let added = false
    for (const key of ['player', 'opponent']) {
      const side = state[key]
      if (!side || !Array.isArray(side.board)) continue
      for (const f of side.board) {
        if (!f || f.uid == null || byUid.has(f.uid)) continue
        byUid.set(f.uid, { side: key, follower: f })
        added = true
      }
    }
    if (added) registerAll()
    // 注册表过大时清理不在场上的随从
    if (added && __handlerTotal > 400) purgeStale(state)
    return added
  }


  /**
   * 被「授予」的谢幕曲（不弑的团结者给精灵挂的那条）。
   *
   * 这类技能是在**战斗开始时动态挂上去**的，不在 follower.meta.skills 里，
   * 所以注册表构建时看不到它。这里注册一个全局处理器，
   * 死亡时按 `__grantedLastwords` 里记录的份数逐个召唤（多份 = 叠加）。
   */
  const __grantHandler = (ctx) => {
    const dead = ctx && ctx.follower
    if (!dead || !Array.isArray(dead.__grantedLastwords)) return
    if (dead.__grantedLastwordDone) return          // 同一批只结算一次
    dead.__grantedLastwordDone = true
    const state = ctx.state
    if (!state) return
    const sideKey = ctx.side === 'opponent' ? 'opponent' : 'player'
    const selfSide = state[sideKey]
    if (!selfSide) return
    for (const g of dead.__grantedLastwords) {
      if (g.cardId == null) continue
      const card = findCardById(g.cardId)
      if (!card) continue
      const room = (selfSide.board || []).filter((f) => f.alive && f.hp > 0).length
      if (room >= 7) break
      let token = null
      try {
        token = summonToBoard(card, selfSide, {}, dead.name, ctx.round, dead)
      } catch (e) { token = null }
      if (token && g.evolve) {
        token.attack = (token.attack || 0) + 2
        token.health = (token.health || 0) + 2
        token.hp = token.health
        token.evolved = true
      }
      if (typeof ctx.log === 'function') {
        ctx.log({ t: 'summon-token', side: sideKey, by: dead.name,
                  token: token ? token.name : '?', tokenCardId: g.cardId, via: 'granted-lastword' })
      }
    }
  }
  on('onDeath', __grantHandler)
  // 置 null：它不属于任何随从，purgeStale 不能清掉它
  __grantHandler.__uid = null

  return { hooks, compiled, skipped }
}

/**
 * 一份局面的技能覆盖度：能编译多少、跳过多少、为什么。
 */
export function skillCoverage(battle) {
  const { compiled, skipped } = buildSkillHooks(battle)
  /**
   * 口径（2026-10-18 用户要求）：
   *   **时点不在战斗内**的技能（入场曲 / 回合结束 / none…）算"已覆盖"——
   *   它们在战斗开始前就结算完了，不是模拟的缺口。
   * 真正算缺口的，只有"战斗时点但没编译出来"的那些（见 gaps）。
   */
  const nonBattle = skipped.filter((x) => x.nonBattle)
  const gaps = skipped.filter((x) => !x.nonBattle)
  const byReason = new Map()
  for (const item of gaps) {
    byReason.set(item.reason, (byReason.get(item.reason) || 0) + 1)
  }
  const total = compiled + skipped.length
  const covered = compiled + nonBattle.length
  return {
    compiled,
    skipped: skipped.length,
    nonBattle: nonBattle.length,
    gaps: gaps.length,
    total,
    covered,
    ratio: total > 0 ? covered / total : 1,
    reasons: [...byReason.entries()].map(([reason, count]) => ({ reason, count })).sort((a, b) => b[1] - a[1]),
    details: gaps.slice(0, 40),
  }
}

/**
 * 全局技能覆盖率（按「只在战斗时点发动」的口径统计）。
 * 用于 `huanjing_coverage` 工具与交付说明，避免把运营阶段的效果算进分母或分子。
 */
export function compileAllSkills(cards) {
  const all = []
  for (const card of Object.values(cards || {})) {
    for (const skill of card.skills || []) all.push({ card: card.name, ...skill })
  }
  let compiled = 0
  let battleTimed = 0
  let nonBattle = 0
  // 真正算缺口的：战斗时点、但没编译出来
  const gaps = []
  const reasons = new Map()
  for (const skill of all) {
    const engineTiming = TIMING_ENGINE[skill.timing]
    const isBattle = (engineTiming && BATTLE_TIMINGS.includes(engineTiming))
    || skill.battleTiming === true
    if (isBattle) battleTimed += 1
    const result = compileSkill(skill)
    if (result.ok) compiled += 1
    else if (result.nonBattle) nonBattle += 1     // 时点不在战斗内：算"已覆盖"，不是缺口
    else {
      gaps.push({ card: skill.card, timing: skill.timing, action: skill.action, reason: result.reason })
      reasons.set(result.reason, (reasons.get(result.reason) || 0) + 1)
    }
  }
  const covered = compiled + nonBattle
  return {
    total: all.length,
    /** 战斗阶段会发动的技能条数（真正该进模拟的分母） */
    battleTimed,
    /** 能机械编译成钩子的条数 */
    compiled,
    /** 时点不在战斗内、被正常排除的条数（按用户口径算"已覆盖"） */
    nonBattle,
    /** 战斗时点但没编译出来 —— 这才是真正的缺口 */
    gaps: gaps.length,
    gapList: gaps.slice(0, 40),
    /** 战斗时点内可编译的比例 */
    battleRatio: battleTimed > 0 ? compiled / battleTimed : 0,
    /** 全量覆盖率：非战斗时点算已覆盖（用户 2026-10-18 要求的口径） */
    covered,
    coverage: covered / (all.length || 1),
    /** 旧口径：只算真编译过的 */
    ratio: compiled / (all.length || 1),
    reasons: [...reasons.entries()].map(([reason, count]) => ({ reason, count })).sort((a, b) => b[1] - a[1]),
  }
}
