/**
 * 影之幻境的「运营阶段」机制（非战斗结算部分）
 *
 * 涵盖：
 *   · 兵种（种族）：中立 / 精灵 / 皇家护卫 / 梦魇 / 龙族
 *   · 进化 / 超进化（+2/+2、+3/+3，以及进化后效果变化）
 *   · 三连合成（3 张同名进化前 → 1 张「进化后」的该牌）
 *   · 打出进化后随从时的「发现」：随机 3 张「当前幻境等级 + 1」的随从选 1
 *   · 效果时点常量（入场曲 / 谢幕曲 / 进化时 / 超进化时 …）
 *
 * 这里只做**数据模型 + 规则计算**，不绑定具体卡牌数值（数值由知识库提供）。
 */

import { applyEvolutionToFollower, applyEvolutions } from './battle.mjs'
import { TRIBES, normalizeTribe, tribeLabel } from './rules.mjs'

export { TRIBES, normalizeTribe, tribeLabel }

/**
 * 效果时点常量（对应卡牌数据里的 effect.timing / abilitiesDetail[i].timing）
 * 说明来自实机规则：
 *   · 入场曲：打出随从时发动
 *   · 谢幕曲：随从被破坏时发动（被「消失」离场时不发动）
 *   · 备战：**回合结束时**发动 → 属运营阶段，不进战斗模拟
 *   · 进化时 / 超进化时：进化后效果可能与进化前不同
 */
export const TIMINGS = {
  fanfare: { id: 'fanfare', label: '入场曲', engine: 'onSummon（打出时）', phase: 'operate' },
  lastWord: { id: 'lastWord', label: '谢幕曲', engine: 'onDeath（被破坏时，被消失不触发）', phase: 'battle' },
  prepare: { id: 'prepare', label: '备战', engine: 'turnEnd（回合结束时，运营阶段结算）', phase: 'operate' },
  deploy: { id: 'deploy', label: '登场', engine: 'onSummon（入场后立即结算）', phase: 'battle' },
  evolve: { id: 'evolve', label: '进化时', engine: 'onEvolve（mode=evolve）', phase: 'battle' },
  superEvolve: { id: 'superEvolve', label: '超进化时', engine: 'onEvolve(mode=superEvolve)', phase: 'battle' },
  attack: { id: 'attack', label: '攻击时', engine: 'onAttack', phase: 'battle' },
  damage: { id: 'damage', label: '受到伤害时', engine: 'onDamage', phase: 'battle' },
  kill: { id: 'kill', label: '破坏随从时', engine: 'onKill', phase: 'battle' },
  static: { id: 'static', label: '常驻', engine: 'static（开局 transform）', phase: 'battle' },
  battleStart: { id: 'battleStart', label: '开战时', engine: 'transform（开战后立即结算）', phase: 'battle' },
  turnStart: { id: 'turnStart', label: '回合开始时', engine: '战斗中不触发（仅运营阶段）', phase: 'operate' },
  onFight: { id: 'onFight', label: '交战时', engine: 'onFight', phase: 'battle' },
  shieldLoss: { id: 'shieldLoss', label: '失去屏障时', engine: 'onShieldLoss', phase: 'battle' },
  turnEnd: { id: 'turnEnd', label: '回合结束时', engine: '运营阶段，不进战斗模拟', phase: 'operate' },
}

/** 只在运营阶段结算的时点（这些效果**不参与战斗模拟**） */
export const OPERATE_ONLY_TIMINGS = Object.values(TIMINGS).filter((t) => t.phase === 'operate').map((t) => t.id)

/**
 * 战斗阶段会真正结算的时点（**只有这些进模拟**）。
 *
 * 规则（实机确认）：
 *   · 战斗时**只计算会在战斗时发动的效果** —— 入场曲 / 备战（回合结束时）/ 回合结束 等运营时点不计
 *   · **回合开始时的效果在战斗中不触发**（`self_turn_start` 已从战斗时点移除）
 *   · **谢幕曲会触发**（被破坏时），被「消失」离场则不触发
 *   · 开战时（battle_turn_start）会触发
 */
export const BATTLE_TIMINGS = ['battleStart', 'onFight', 'attack', 'damage', 'lastWord', 'evolve', 'superEvolve', 'static']

/** 明确「不参与战斗」的引擎时点（用于报告与编译拒绝理由） */
export const NON_BATTLE_TIMINGS = ['turnStart', 'turnEnd', 'fanfare', 'prepare', 'onSummon', 'onSummonOther', 'onDestroyOther', 'onEvolveOther', 'onPlayOther', 'onPlayOtherBefore', 'onSkillEvolve', 'onAttackSelfAndOther', 'onBuy', 'onSell', 'onShieldLoss']

export function isBattleTiming(engineTiming) {
  return BATTLE_TIMINGS.includes(engineTiming)
}

// ─────────────────────────────────────────────────────────────────────────────
// 三连合成
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 三连合成：凑齐 `triplesToEvolve`（默认 3）张**相同**的进化前牌，
 * 合成 1 张「进化后」的该牌并置入手牌。
 *
 * @param {object[]} hand 手牌（元素为 { cardId, name, count?, isEvolved?, ... }）
 * @param {object} [options]
 * @param {number} [options.triplesToEvolve=3]
 * @param {(card:object)=>object|null} [options.evolvedCardOf] 查询「进化后」卡牌的映射函数；缺省则只加标记
 * @returns {{ hand: object[], created: object[], consumed: object[] }}
 */
export function fuseTriples(hand, options = {}) {
  const need = Math.max(2, Number(options.triplesToEvolve) || 3)
  const evolvedCardOf = typeof options.evolvedCardOf === 'function' ? options.evolvedCardOf : null
  const groups = new Map()
  const rest = []

  // 按 cardId（没有就用名称）分组；已进化的牌不参与三连
  for (const card of Array.isArray(hand) ? hand : []) {
    if (!card) continue
    if (card.isEvolved) { rest.push(card); continue }
    const key = card.cardId || card.name
    if (!key) { rest.push(card); continue }
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(card)
  }

  const created = []
  const consumed = []
  for (const [key, cards] of groups) {
    let pool = cards.slice()
    while (pool.length >= need) {
      const used = pool.splice(0, need)
      consumed.push(...used)
      const sample = used[0]
      const evolved = evolvedCardOf ? evolvedCardOf(sample) : null
      created.push(evolved
        ? { ...evolved, isEvolved: true, fromTriple: true }
        : { ...sample, name: `${sample.name || key}（进化后）`, isEvolved: true, fromTriple: true, unresolved: !evolvedCardOf })
    }
    rest.push(...pool)
  }

  return { hand: [...rest, ...created], created, consumed }
}

// ─────────────────────────────────────────────────────────────────────────────
// 发现（打出进化后随从时）
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 发现：从随机 `choices`（默认 3）张「当前幻境等级 + levelOffset（默认 1）」的随从中选 1 张。
 * 本函数只**生成选项**；选择哪张由玩家/策略决定。
 *
 * @param {number} level 当前幻境等级
 * @param {object[]} cardPool 卡池（元素需带 star 字段，表示随从等级）
 * @param {object} rng 随机源（Prng 实例，需实现 pick / int）
 * @param {object} [options] { choices = 3, levelOffset = 1, exclude = [] }
 */
export function createDiscoverOptions(level, cardPool, rng, options = {}) {
  const choices = Math.max(1, Number(options.choices) || 3)
  const targetLevel = Math.max(1, (Number(level) || 1) + (Number(options.levelOffset) || 1))
  const exclude = new Set(options.exclude || [])
  const pool = (Array.isArray(cardPool) ? cardPool : []).filter((card) => {
    if (!card) return false
    if (card.isEvolved) return false
    if (exclude.has(card.cardId || card.name)) return false
    return (Number(card.star) || 1) === targetLevel
  })
  if (pool.length === 0) {
    return { level: targetLevel, options: [], reason: `卡池里没有 ${targetLevel} 级随从` }
  }
  // 不重复抽取；卡池不足时有多少给多少
  const picked = []
  const bag = pool.slice()
  while (picked.length < choices && bag.length > 0) {
    const index = rng && typeof rng.int === 'function' ? rng.int(bag.length) : Math.floor(Math.random() * bag.length)
    picked.push(bag.splice(index, 1)[0])
  }
  return { level: targetLevel, options: picked, reason: null }
}

// ─────────────────────────────────────────────────────────────────────────────
// 战斗阶段说明
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 战斗阶段（开战后立即结算的时点）应输出的三项结论。
 * 实际计算由 simulate.runSimulation 完成，这里只固定口径说明，避免各处口径漂移。
 */
export const BATTLE_START_OUTPUTS = [
  { key: 'winRate', label: '胜率（含平局概率）', detail: '平局时双方都不受伤害' },
  { key: 'damage.toPlayer', label: '我方掉血分布', detail: '条件于我方落败' },
  { key: 'damage.toOpponent', label: '对方掉血分布', detail: '条件于对方落败' },
]

export { applyEvolutions, applyEvolutionToFollower }
