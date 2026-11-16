/**
 * 影之幻境 —— 战斗蒙特卡洛模拟内核
 *
 * 只依赖 rng.mjs 与 rules.mjs，无第三方依赖，可在浏览器 / Node / Web Worker 里跑。
 *
 * 已实现的规则：
 *   1. 先后手：随从数量 → 幻境等级 → 星级合计 → 随机（顺序可配）
 *   2. 行动：决定先后手后**双方交替行动**（先手方第 1 个 → 后手方第 1 个 → …），
 *      每个随从每轮行动一次
 *   3. 目标：随机选择对方存活随从；守护**不可被绕开**；潜行不可选
 *   4. 关键词：守护 / 限制攻击 / 毁灭 / 屏障 / 潜行 / 疾驰 / 顺劈 / 不灭 / 2连打
 *   5. 结算：一方随从全灭 → 另一方获胜；双方全灭 → 平局；超时按剩余场面判定
 *   6. 伤害：伤害 = 胜利方幻境等级 + 残存随从星级合计（rules.damage 可覆盖）
 *   7. 扩展：onAttack / onKill / onDeath / transform 钩子（主战者技能、卡牌效果接在这里）
 */

import { Prng, hashSeed, shortHash } from './rng.mjs'
import {
  resolveRules, normalizeAbilities, decideFirstSide,
  damageFromSurvivors, orderMetric, normalizeTribe,
} from './rules.mjs'

/** 战场随从上限（方案文本：最多 7） */
export const MAX_BOARD = 7
/** 星级取值区间 */
export const STAR_RANGE = [1, 6]

// ─────────────────────────────────────────────────────────────────────────────
// 归一化
// ─────────────────────────────────────────────────────────────────────────────

let uidSeq = 0
function nextUid() {
  uidSeq = (uidSeq + 1) >>> 0
  return `u${uidSeq.toString(36)}`
}

function toInt(value, fallback) {
  const n = Math.trunc(Number(value))
  return Number.isFinite(n) ? n : fallback
}

function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value))
}

/**
 * 归一化一方战场。position 缺省时按数组下标补齐并重排。
 * 返回 { side, warnings }
 */
export function normalizeSide(raw, sideName, options = {}) {
  const warnings = []
  const starRange = options.starRange || STAR_RANGE
  const maxBoard = options.maxBoard || MAX_BOARD
  const where = sideName === 'player' ? '己方' : '对方'
  const src = raw && typeof raw === 'object' ? raw : {}

  let board = Array.isArray(src.board) ? src.board : []
  if (board.length > maxBoard) {
    warnings.push(`${where}随从数 ${board.length} 超过战场上限 ${maxBoard}，已截断（疑似识别错误）`)
    board = board.slice(0, maxBoard)
  }

  const followers = board.map((f, index) => {
    const item = f && typeof f === 'object' ? f : {}
    const attack = toInt(item.attack, 0)
    const health = toInt(item.health, 0)
    const star = clamp(toInt(item.star, 1), starRange[0], starRange[1])
    if (toInt(item.attack, 0) < 0) warnings.push(`${where}第 ${index + 1} 个随从攻击力为负，已按 0 处理`)
    if (toInt(item.health, 0) < 0) warnings.push(`${where}第 ${index + 1} 个随从生命值为负，已按 0 处理`)
    if (item.star != null && toInt(item.star, 1) !== star) {
      warnings.push(`${where}第 ${index + 1} 个随从星级 ${item.star} 超出 ${starRange[0]}~${starRange[1]}，已夹紧为 ${star}`)
    }
    return {
      uid: item.uid || nextUid(),
      name: item.name ? String(item.name) : `未知随从#${index + 1}`,
      attack: Math.max(0, attack),
      health: Math.max(0, health),
      star,
      abilities: normalizeAbilities(item.abilities),
      isEvolved: !!item.isEvolved,
      isSuperEvolved: !!item.isSuperEvolved,
      isTripleEvolved: !!item.isTripleEvolved,
      canEvolve: item.canEvolve === undefined ? undefined : !!item.canEvolve,
      canSuperEvolve: !!item.canSuperEvolve,
      canOnlyEvolveWithPoint: !!item.canOnlyEvolveWithPoint,
      evolveMode: item.evolveMode,
      position: Number.isFinite(Number(item.position)) ? Number(item.position) : index,
      cardId: item.cardId ? String(item.cardId) : undefined,
      tribe: item.tribe ? normalizeTribe(item.tribe) : undefined,
      meta: item.meta && typeof item.meta === 'object' ? { ...item.meta } : undefined,
      // 运行期字段（每次模拟重建，不参与归一化输出）
      hp: Math.max(0, health),
      alive: Math.max(0, health) > 0,
      summonedRound: 0,
      wardUsed: false,
    }
  })

  // 重排并纠正 position
  followers.sort((a, b) => a.position - b.position || a.uid.localeCompare(b.uid))
  followers.forEach((f, i) => { f.position = i })

  const rawLevel = toInt(src.level, 1)
  const level = rawLevel < 1 ? (warnings.push(`${where}幻境等级 ${src.level} 非法，已按 1 处理`), 1) : rawLevel

  return {
    side: {
      hero: src.hero != null ? String(src.hero) : src.mainHero != null ? String(src.mainHero) : undefined,
      heroSkill: src.heroSkill != null ? String(src.heroSkill) : undefined,
      hp: toInt(src.hp, 40),
      level,
      gold: toInt(src.gold, 0),
      evolutionCount: toInt(src.evolutionCount, 0),
      board: followers,
      auras: Array.isArray(src.auras) ? src.auras.map((a) => ({ tribe: a.tribe || null, maxStar: a.maxStar == null ? null : Number(a.maxStar), atk: Number(a.atk) || 0, hp: Number(a.hp) || 0 })) : [],
    },
    warnings,
  }
}

/**
 * 归一化整场战斗。返回 { battle, warnings }
 */
export function normalizeBattle(input, options = {}) {
  if (!input || typeof input !== 'object') throw new Error('battle 输入必须是对象')
  const warnings = []
  const p = normalizeSide(input.player, 'player', options)
  const o = normalizeSide(input.opponent, 'opponent', options)
  warnings.push(...p.warnings, ...o.warnings)
  return {
    battle: {
      turn: toInt(input.turn, 1),
      phase: input.phase === 'battle' ? 'battle' : 'operate',
      player: p.side,
      opponent: o.side,
    },
    warnings,
  }
}

/** 深拷贝战斗状态（模拟内层用，尽量少分配） */
export function cloneBattle(battle) {
  const clone = (side) => ({
    hero: side.hero,
    heroSkill: side.heroSkill,
    hp: side.hp,
    level: side.level,
    gold: side.gold,
    evolutionCount: side.evolutionCount,
    evolutionPoints: side.evolutionPoints,
    auras: side.auras,          // 主战者光环：召唤出来的随从要吃加成
    board: side.board.map((f) => ({
      uid: f.uid,
      name: f.name,
      attack: f.attack,
      health: f.health,
      star: f.star,
      abilities: f.abilities,
      isEvolved: f.isEvolved,
      isSuperEvolved: f.isSuperEvolved,
      isTripleEvolved: f.isTripleEvolved,
      canEvolve: f.canEvolve,
      canSuperEvolve: f.canSuperEvolve,
      canOnlyEvolveWithPoint: f.canOnlyEvolveWithPoint,
      evolveMode: f.evolveMode,
      cardId: f.cardId,
      position: f.position,
      meta: f.meta,
      hp: f.health,
      alive: f.health > 0,
      summonedRound: 0,
      wardUsed: false,
    })),
  })
  return {
    turn: battle.turn,
    phase: battle.phase,
    player: clone(battle.player),
    opponent: clone(battle.opponent),
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 单场战斗
// ─────────────────────────────────────────────────────────────────────────────

const SIDE_KEYS = { A: 'player', B: 'opponent' }

function living(board) {
  return board.filter((f) => f.alive && f.hp > 0)
}

function hasAbility(f, id) {
  return f.abilities.includes(id)
}

/** 当前可被选为目标的随从 */
function targetPool(defender, rules) {
  const alive = living(defender.board)
  if (alive.length === 0) return []
  let pool = alive
  if (rules.targeting.respectStealth) {
    const visible = alive.filter((f) => !hasAbility(f, 'stealth'))
    if (visible.length > 0) pool = visible
  }
  if (rules.targeting.guardFirst) {
    const guards = pool.filter((f) => hasAbility(f, 'guard'))
    if (guards.length > 0) pool = guards
  }
  return pool
}

function pickTarget(board, rules, rng) {
  const pool = targetPool(board, rules)
  if (pool.length === 0) return null
  if (pool.length === 1) return pool[0]
  if (rules.targeting.mode === 'weighted') {
    const bias = Number(rules.targeting.weightBias) || 0
    const weights = pool.map((f) => 1 / Math.max(1, f.hp + bias))
    return pool[rng.pickIndexByWeight(weights)]
  }
  return rng.pick(pool)
}

/**
 * 施加伤害并处理屏障 / 毁灭 / 死亡触发。
 * 返回 { dealt, lethal, blocked }
 */
function applyDamage(target, amount, context) {
  const { rules, hooks, attacker, sideKey, round } = context
  const push = makeLogSink(context.log)
  if (!target || !target.alive) return { dealt: 0, lethal: false, blocked: false }

  // 毁灭：无视剩余生命值直接击杀，且**无视屏障**
  const destroy = !!(attacker && rules.combat.destroyAlwaysLethal && hasAbility(attacker, 'destroy'))

  if (!destroy && hasAbility(target, 'ward') && rules.combat.wardBlocksOneHit && !target.wardUsed) {
    // 屏障抵挡**第一次伤害，即使伤害为 0**；被抵挡的这次不算「造成伤害」
    target.wardUsed = true
    push({
      t: 'ward', round, target: target.name,
      attacker: attacker ? attacker.name : null,
      incoming: Math.max(0, amount),
      blocked: true,
      countedAsDamage: false,
    })
    return { dealt: 0, lethal: false, blocked: true }
  }
  if (destroy && hasAbility(target, 'ward') && rules.combat.destroyBypassesWard && !target.wardUsed) {
    // 屏障被毁灭直接击穿：消耗掉屏障，但本次击杀照常成立
    target.wardUsed = true
    push({ t: 'ward-pierced', round, target: target.name, attacker: attacker ? attacker.name : null })
  }

  const dealt = destroy ? target.hp : Math.max(0, amount)
  target.hp -= dealt
  const lethal = target.hp <= 0
  if (lethal) target.hp = 0
  push({ t: 'damage', round, from: attacker ? attacker.name : null, to: target.name, dealt, lethal, destroy })

  if (hooks && typeof hooks.onDamage === 'function') {
    hooks.onDamage({ target, attacker, dealt, lethal, state: context.state, rules, rng: context.rng, log: push })
  }
  return { dealt, lethal, blocked: false }
}

/** 清理死亡随从并触发 onDeath（谢幕曲） */
function reapDeaths(state, round, hooks, rng, log) {
  const push = makeLogSink(log)
  const dead = []
  for (const key of ['player', 'opponent']) {
    const side = state[key]
    for (const f of side.board) {
      if (f.alive && f.hp <= 0) {
        f.alive = false
        f.hp = 0
        dead.push(f)
        // 谢幕曲：被破坏时触发；被「消失」而离场时不应触发（由调用方保证不入此分支）
        if (hooks && typeof hooks.onDeath === 'function') {
          hooks.onDeath({ follower: f, side: key, state, rng, log: push, round, timing: 'lastWord' })
      // 【残响】类触发点：己方随从被破坏（onDestroyOther）
      // 己方阵亡计数：残响类条件 {me.destroyed_card.unit.count} 要用
      state[key].destroyedCount = (state[key].destroyedCount || 0) + 1
      if (hooks.onDeathOther) {
        hooks.onDeathOther({ dead: f, side: key, state, rng, log: push, round })
      }
        }
      }
    }
  }
  if (dead.length > 0) push({ t: 'deaths', round, names: dead.map((f) => f.name) })
  return dead
}

// ─────────────────────────────────────────────────────────────────────────────
// 进化 / 超进化
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 统一的日志出口：既接受数组（外部直接传 log[]），也接受函数（模拟内部的 pushLog）。
 * 早期版本在 applyEvolutions -> applyEvolutionToFollower 之间传错了类型，导致
 * `log.push is not a function`，这里做一次性收敛。
 */
function makeLogSink(log) {
  if (typeof log === 'function') return log
  if (Array.isArray(log)) return (entry) => log.push(entry)
  return () => { }
}

/**
 * 给单个随从应用进化（+2/+2）或超进化（+3/+3），并触发「进化时」钩子。
 *
 * @param {object} follower
 * @param {'evolve'|'superEvolve'|'threeEvolve'} mode
 * @param {object} rules
 * @param {object} [hooks]
 * @param {object[]|Function} [log] 日志数组或回调
 * @param {object} [options]
 * @param {boolean} [options.triggerEffects=true]
 *        false = 只加数值与「进化后的能力」，不触发【进化时】效果。
 *        三连得到的进化后卡牌就是这个语义（实机规则：三连不触发进化效果，但会获得进化后的能力）。
 */
export function applyEvolutionToFollower(follower, mode, rules, hooks, log, options = {}) {
  if (!follower) return null
  const sink = makeLogSink(log)
  const cfg = rules.evolution
  const triggerEffects = options.triggerEffects !== false
  const bonus = mode === 'superEvolve'
    ? {
      attack: num2(cfg.superEvolveBonus && cfg.superEvolveBonus.attack, 3),
      health: num2(cfg.superEvolveBonus && cfg.superEvolveBonus.health, 3),
    }
    : {
      attack: num2(cfg.evolveBonus && cfg.evolveBonus.attack, 2),
      health: num2(cfg.evolveBonus && cfg.evolveBonus.health, 2),
    }
  follower.attack += bonus.attack
  follower.health += bonus.health
  if (cfg.healOnEvolve !== false) follower.hp = follower.health
  else follower.hp += bonus.health
  follower.isEvolved = true
  if (mode === 'superEvolve') follower.isSuperEvolved = true
  if (mode === 'threeEvolve') follower.isTripleEvolved = true
  follower.evolveMode = mode
  sink({
    t: 'evolve', who: follower.name, mode,
    bonus: `+${bonus.attack}/+${bonus.health}`,
    stats: `${follower.attack}/${follower.health}`,
    triggerEffects,
  })
  // ⚠️ 三连进化（threeEvolve）**不算**「进化时」：
  //    用户确认「三连的进化效果、以及别的效果让他进化，都不会获得残响」。
  //    正常吃进化点（evolve）和超进化（superEvolve）才算。
  if (triggerEffects && hooks && typeof hooks.onEvolve === 'function'
      && mode !== 'threeEvolve') {
    /**
     * 触发顺序（用户 2026-10-18 20:58 明确）：
     *   · 普通进化：先【进化时】(timing=evolve)，再「进化的时候」(timing=skill)；
     *   · **超进化也算进化**：先【进化时】→「进化的时候」→【超进化时】，
     *     最终保留**超进化形态**（技能表/形态按 form 2 算）；
     *   · 三连进化：上面这些都不触发（mode 在这里已被排除）。
     *
     * __skipForm：超进化那一刻随从已经是 form 2 了，但「进化时 / 进化的时候」
     * 是 form 1 的技能 —— 这是真实的"过渡阶段"，不能按当前形态筛掉。
     */
    const fire = (timing, skipForm) => {
      try {
        hooks.onEvolve({ follower, mode, rules, log: sink, timing,
                         __skipForm: !!skipForm })
      } catch (e) { /* 单个效果失败不影响进化本身 */ }
    }
    const superNow = (mode === 'superEvolve')
    fire('evolve', superNow)
    fire('skill', superNow)
    if (superNow) fire('superEvolve')
    // 「自己的其他随从进化时」（古兰&姬塔这类）也要收到**吃进化点**的进化。
    // 以前只有效果进化会广播 onEvolveOther，于是靠进化点让别的随从进化时古兰永远不发动。
    if (options && options.state) {
      emitEvolveOther(hooks, {
        evolved: follower, side: options.side, state: options.state,
        rng: options.rng, log: sink, round: options.round,
      })
    }
  }
  return follower
}

function num2(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 进化候选打分：默认优先攻击力高的随从，带关键词额外加分 */
function evolveScore(follower, rules) {
  const prefer = rules.evolution.prefer || {}
  let score = prefer.byAttack === false ? 0 : follower.attack * 10 + follower.hp
  const bonusTable = prefer.keywordBonus || {}
  for (const ability of follower.abilities || []) {
    if (bonusTable[ability]) score += bonusTable[ability] * 10
  }
  return score
}

/**
 * 计算某一方在当前回合可用（已解锁）的进化点 / 超进化点数量。
 *
 * 实机规则：第 5 回合解锁第 1 个进化点、第 7 回合第 2 个、第 9 回合超进化点。
 * 另外主战者技能可以直接给点（preUnlocked*）或提前解锁（evolutionUnlockOffset 回合）。
 *
 * @returns {{ evolvePoints: number, superEvolvePoints: number, detail: object }}
 */
export function availableEvolutionPoints(side, rules, turn) {
  const cfg = (rules && rules.evolution) || {}
  const unlock = cfg.unlock || {}
  const currentTurn = Number.isFinite(Number(turn)) ? Number(turn) : Number(side && side.turn) || 1
  const offset = Number(side && side[unlock.offsetField || 'evolutionUnlockOffset']) || 0

  const evolveRounds = Array.isArray(unlock.evolveRounds) ? unlock.evolveRounds : [5, 7]
  const superRound = Number.isFinite(Number(unlock.superEvolveRound)) ? Number(unlock.superEvolveRound) : 9

  const unlockedEvolve = evolveRounds.filter((r) => currentTurn >= Number(r) - offset).length
  const unlockedSuper = currentTurn >= superRound - offset ? 1 : 0
  const preEvolve = Math.max(0, Number(side && side.preUnlockedEvolutionPoints) || 0)
  const preSuper = Math.max(0, Number(side && side.preUnlockedSuperEvolutionPoints) || 0)

  return {
    evolvePoints: unlockedEvolve + preEvolve,
    superEvolvePoints: unlockedSuper + preSuper,
    detail: {
      turn: currentTurn,
      evolveRounds,
      superEvolveRound: superRound,
      offset,
      unlockedEvolve,
      unlockedSuper,
      preEvolve,
      preSuper,
      /** 距第 1 / 第 2 个进化点与超进化点解锁还剩几回合（对应界面上的两个数字） */
      roundsUntil: {
        firstEvolve: Math.max(0, Number(evolveRounds[0] || 5) - offset - currentTurn),
        secondEvolve: Math.max(0, Number(evolveRounds[1] || evolveRounds[0] || 7) - offset - currentTurn),
        superEvolve: Math.max(0, superRound - offset - currentTurn),
      },
    },
  }
}

/**
 * 按主战者的进化点数自动进化：每个进化点可进化 `followersPerPoint` 个随从。
 * 只处理带 `canSuperEvolve` 的随从会走超进化（消耗 1 点）。
 * 返回被进化的随从列表。
 */
export function applyEvolutions(state, rules, hooks, log) {
  const push = makeLogSink(log)
  const cfg = rules.evolution
  const evolved = []
  if (!cfg.enabled) return evolved
  const perPoint = Math.max(1, num2(cfg.followersPerPoint, 1))
  const currentTurn = num2(state.turn, 1)

  for (const key of ['player', 'opponent']) {
    const side = state[key]
    if (!side) continue
    // 进化点来源：① 回合解锁（第 5/7 回合）② 主战者技能直接给的点数 ③ 兼容旧字段 evolutionCount
    const available = availableEvolutionPoints(side, rules, currentTurn)
    const legacy = Math.max(0, num2(side.evolutionCount, 0))
    const evolvePoints = available.evolvePoints + legacy
    const superPoints = available.superEvolvePoints
    if (available.detail.unlockedEvolve === 0 && evolvePoints === 0 && superPoints === 0) {
      push({ t: 'evolve-locked', side: key, detail: available.detail })
    }
    let budget = evolvePoints * perPoint
    let superBudget = superPoints

    let candidates = side.board
      .filter((f) => f.capacity !== true && (f.canEvolve !== false) && !f.isEvolved && f.alive && f.hp > 0)
      .sort((a, b) => evolveScore(b, rules) - evolveScore(a, rules))

    // preferPointOnly：只给「必须使用进化点才有效」的随从分配点数
    if (cfg.preferPointOnly) {
      const pointOnly = candidates.filter((f) => f.canOnlyEvolveWithPoint)
      if (pointOnly.length > 0) candidates = pointOnly
    }

    for (const follower of candidates) {
      const wantsSuper = !!follower.canSuperEvolve
      let mode = null
      if (wantsSuper) {
        // 「可以超进化」的随从只走超进化；超进化点没解锁时**不该降级成普通进化**
        // （实机里超进化与进化是两种独立的点，第 9 回合才有超进化点）
        if (superBudget > 0) {
          mode = 'superEvolve'
          superBudget -= 1
        } else {
          continue
        }
      } else if (budget > 0) {
        mode = 'evolve'
        budget -= 1
      } else {
        continue
      }
      // 三连得到的卡牌不触发【进化时】效果（rules.fusion.triggerEvolutionOnTriple）
      const triggerEffects = !follower.isTripleEvolved || rules.fusion.triggerEvolutionOnTriple !== false
      applyEvolutionToFollower(follower, mode, rules, hooks, push,
                              { triggerEffects, state, side: key })
      evolved.push({ side: key, follower, mode, triggerEffects })
    }
  }
  return evolved
}

/**
 * 三连合成后的随从直接进入战场时的处理：
 * 拿到**进化后的面板与能力**，但**不触发【进化时】效果**（实机规则）。
 * 数值上用 `threeEvolve` 增量（官方 ThreeEvoAtk/ThreeEvoLife）优先，缺省时退回进化增量。
 */
export function applyTripleEvolution(follower, rules, hooks, log, threeStats) {
  if (!follower) return null
  const sink = makeLogSink(log)
  const attackDelta = threeStats && Number.isFinite(Number(threeStats.attack))
    ? Number(threeStats.attack) - num2(follower.attack, 0)
    : num2(rules.evolution.evolveBonus && rules.evolution.evolveBonus.attack, 2)
  const healthDelta = threeStats && Number.isFinite(Number(threeStats.health))
    ? Number(threeStats.health) - num2(follower.health, 0)
    : num2(rules.evolution.evolveBonus && rules.evolution.evolveBonus.health, 2)
  follower.attack += attackDelta
  follower.health += healthDelta
  if (rules.evolution.healOnEvolve !== false) follower.hp = follower.health
  else follower.hp += healthDelta
  follower.isEvolved = true
  follower.isTripleEvolved = true
  follower.evolveMode = 'threeEvolve'
  sink({
    t: 'evolve', who: follower.name, mode: 'threeEvolve',
    bonus: `+${attackDelta}/+${healthDelta}`,
    stats: `${follower.attack}/${follower.health}`,
    triggerEffects: false,
  })
  return follower
}

/**
 * 执行一次攻击。返回 { verdict: 'acted'|'skipped', hits }（hits = 本次实际攻击次数，含 2 连打）
 */
/**
 * 广播「某随从进化了」，供 onEvolveOther 订阅。
 * 效果造成的进化（例如主战者技能）走这里；玩家吃进化点的进化走 onEvolve。
 */
export function emitEvolveOther(hooks, { evolved, side, state, rng, log, round }) {
  if (hooks && typeof hooks.onEvolveOther === 'function') {
    hooks.onEvolveOther({ evolved, side, state, rng, log, round })
  }
}

function performAttack(attacker, sideKey, state, rules, hooks, rng, round, log) {
  const push = makeLogSink(log)
  const enemyKey = sideKey === 'A' ? 'opponent' : 'player'
  const defender = state[enemyKey]

  if (!attacker.alive || attacker.hp <= 0) return { verdict: 'skipped', hits: 0 }
  if (hasAbility(attacker, 'cannotAttack')) {
    push({ t: 'skip', round, who: attacker.name, why: 'cannotAttack' })
    return { verdict: 'skipped', hits: 0 }
  }
  if (living(defender.board).length === 0) return { verdict: 'skipped', hits: 0 }

  const target = pickTarget(defender, rules, rng)
  if (!target) return { verdict: 'skipped', hits: 0 }

  // 攻击力为 0 的随从：在确定「它攻击了谁」之后再判定。
  // 这样屏障仍然会被这次（0 点）伤害消耗掉 —— 实机规则：屏障抵挡第一次伤害，即使伤害是 0。
  if (rules.combat.requirePositiveAttack && attacker.attack <= 0) {
    push({ t: 'skip', round, who: attacker.name, why: 'attack<=0' })
    return { verdict: 'skipped', hits: 0 }
  }

  push({ t: 'attack', round, who: attacker.name, side: sideKey, target: target.name, timing: 'attack' })
  if (hooks && typeof hooks.onAttack === 'function') {
    hooks.onAttack({ attacker, target, side: sideKey, state, rules, rng, log: push, round })
  }

  /**
   * **交战时（when_fight）**：双方随从都有交战时效果时，**攻击方先发动**（用户确认的规则）。
   * 因此这里先给攻击方，再给被攻击方；两者都在伤害结算之前。
   */
  if (hooks && typeof hooks.onFight === 'function') {
    hooks.onFight({ attacker, target, side: sideKey, state, rules, rng, log: push, round, order: 'attacker-first' })
    if (target && target.alive) {
      hooks.onFight({
        attacker: target, target: attacker, side: enemyKey, state, rules, rng, log: push, round,
        order: 'defender-second', isDefender: true,
      })
    }
  }

  const context = { rules, hooks, attacker, sideKey, log: push, round, rng, state }
  const damage = rules.combat.requirePositiveAttack ? Math.max(0, attacker.attack) : attacker.attack

  applyDamage(target, damage, context)

  // 顺劈：对目标相邻随从造成伤害
  if (hasAbility(attacker, 'cleave')) {
    const board = defender.board
    const idx = board.indexOf(target)
    for (const nb of [board[idx - 1], board[idx + 1]]) {
      if (nb && nb.alive) {
        applyDamage(nb, damage * (Number(rules.combat.cleaveRatio) || 1), context)
      }
    }
  }

  // 互相伤害模式：被攻击方反击。
  // 关键约束：被跳过的随从（限制攻击）不该"顺手"打出反击伤害，
  // 否则「限制攻击」形同虚设；0 攻随从的反击照常进行但只造成 0 点。
  if (rules.combat.damageMode === 'mutual' && !hasAbility(attacker, 'stealth')) {
    const targetCanStrike = !hasAbility(target, 'cannotAttack')
    const back = targetCanStrike
      ? (rules.combat.requirePositiveAttack ? Math.max(0, target.attack) : target.attack)
      : 0
    if (back > 0) {
      const counterCtx = { ...context, attacker: target, sideKey: enemyKey }
      applyDamage(attacker, back, counterCtx)
    }
  }

  // 2 连打（官方 0049）：攻击时可进行 2 次攻击 —— 额外再打一轮同样的目标选择
  let hits = 1
  const extraHits = Math.max(0, Number(rules.combat.doubleAttackCount) || 2) - 1
  if (extraHits > 0 && hasAbility(attacker, 'doubleAttack') && attacker.alive && attacker.hp > 0) {
    for (let hit = 0; hit < extraHits; hit++) {
      if (!attacker.alive || attacker.hp <= 0) break
      const again = pickTarget(defender, rules, rng)
      if (!again) break
      hits += 1
      push({ t: 'attack', round, who: attacker.name, side: sideKey, target: again.name, timing: 'attack', extra: hit + 2 })
      const extraDamage = rules.combat.requirePositiveAttack ? Math.max(0, attacker.attack) : attacker.attack
      applyDamage(again, extraDamage, { ...context, attacker })
      if (rules.combat.damageMode === 'mutual' && !hasAbility(attacker, 'stealth') && !hasAbility(again, 'cannotAttack')) {
        const back2 = rules.combat.requirePositiveAttack ? Math.max(0, again.attack) : again.attack
        if (back2 > 0) {
          applyDamage(attacker, back2, { ...context, attacker: again, sideKey: enemyKey })
        }
      }
      reapDeaths(state, round, hooks, rng, push)
    }
  }

  reapDeaths(state, round, hooks, rng, push)
  return { verdict: 'acted', hits }
}

function decideOutcome(state, rules) {
  const aAlive = living(state.player.board).length
  const bAlive = living(state.opponent.board).length
  if (aAlive > 0 && bAlive === 0) return { winner: 'A', reason: 'wipe' }
  if (bAlive > 0 && aAlive === 0) return { winner: 'B', reason: 'wipe' }
  if (aAlive === 0 && bAlive === 0) return { winner: 'draw', reason: 'mutual-wipe' }
  return null
}

function timeoutOutcome(state, rules, firstSide) {
  const mode = rules.end.timeoutResolve
  if (mode === 'draw') return { winner: 'draw', reason: 'timeout-draw' }
  if (mode === 'first') return { winner: firstSide, reason: 'timeout-first' }
  const a = orderMetric('followerCount', state.player)
  const b = orderMetric('followerCount', state.opponent)
  if (a !== b) return { winner: a > b ? 'A' : 'B', reason: 'timeout-count' }
  const as = orderMetric('starSum', state.player)
  const bs = orderMetric('starSum', state.opponent)
  if (as !== bs) return { winner: as > bs ? 'A' : 'B', reason: 'timeout-star' }
  return { winner: 'draw', reason: 'timeout-draw' }
}

/**
 * 打一场（战斗阶段完整流程）。
 *
 * @param {object} battle 归一化后的战斗（normalizeBattle().battle）
 * @param {object} [options]
 * @param {object} [options.rules] 规则覆盖
 * @param {Prng}   [options.rng]   随机源（不传则按 rules.rng.seed 新建）
 * @param {object} [options.hooks] { onAttack, onDamage, onKill, onDeath, transform }
 * @param {boolean}[options.withLog] 是否保留逐步日志（默认 false，模拟时关闭以省内存）
 * @param {number} [options.logLimit] 日志条数上限
 * @returns 战斗结果
 */
export function simulateOnce(battle, options = {}) {
  const rules = options.rules || resolveRules()
  const rng = options.rng || new Prng(rules.rng.seed)
  const hooks = options.hooks || {}
  const state = cloneBattle(battle)

  // 每次模拟重置运行期字段（允许复用同一份输入对象反复模拟）
  for (const key of ['player', 'opponent']) {
    for (const f of state[key].board) {
      f.hp = f.health
      f.alive = f.health > 0
      f.wardUsed = false
      f.summonedRound = 0
    }
  }

  const starSumOf = (side) => side.board.reduce((s, f) => s + (Number(f.star) || 1), 0)
  const startCount = { A: state.player.board.length, B: state.opponent.board.length }

  const log = []
  const logLimit = Number.isFinite(options.logLimit) ? options.logLimit : 500
  const keepLog = !!options.withLog
  const pushLog = (entry) => { if (keepLog && log.length < logLimit) log.push(entry) }

  // 进化 / 超进化：按主战者的进化点自动强化（+2/+2 或 +3/+3）
  const evolutions = applyEvolutions(state, rules, hooks, pushLog)

  // 主战者技能等开局钩子
  if (typeof hooks.transform === 'function') {
    hooks.transform({ state, rules, rng })
    for (const key of ['player', 'opponent']) {
      for (const f of state[key].board) { f.hp = f.health; f.alive = f.health > 0 }
    }
  }

  /**
   * **开战时效果是战斗阶段最优先的**（用户确认的规则）。
   * 放在进化之后、任何攻击之前：此时双方随从已就位（进化已生效），
   * 但还没有任何交战发生，因此「开战时」看到的是完整阵容。
   */
  if (typeof hooks.battleStart === 'function') {
    pushLog({ t: 'phase', phase: 'battleStart', note: '开战时效果（战斗阶段最优先）' })
    hooks.battleStart({ state, rules, rng, timing: 'battleStart' })
  }

  const order = decideFirstSide(state, rules, rng)
  const sequence = order.firstSide === 'A' ? ['A', 'B'] : ['B', 'A']

  let outcome = decideOutcome(state, rules)
  let round = 0
  const attackCounts = new Map()

  /** 取本方本轮行动者快照：新召唤/复活的随从不在本轮行动（疾驰可例外） */
  const actorSnapshot = (sideKey, roundNo) => {
    const self = state[SIDE_KEYS[sideKey]]
    const actors = living(self.board).filter((f) => {
      if (hasAbility(f, 'cannotAttack')) return true // 仍需占用一次行动（日志里体现为 skip）
      if (rules.turn.attackOrderSnapshot) return !(f.summonedRound >= roundNo)
      if (f.summonedRound >= roundNo && !hasAbility(f, 'rush')) return false
      return true
    })
    return rules.turn.inSideOrder === 'reverse' ? actors.slice().reverse() : actors
  }

  while (!outcome && round < rules.end.maxRounds) {
    round += 1

    if (rules.turn.sequentialSides) {
      // 一方整队行动完，再轮到另一方
      for (const sideKey of sequence) {
        for (const actor of actorSnapshot(sideKey, round)) {
          if (!actor.alive || actor.hp <= 0) continue
          const verdict = performAttack(actor, sideKey, state, rules, hooks, rng, round, pushLog)
          if (verdict.verdict === 'acted') attackCounts.set(actor.uid, (attackCounts.get(actor.uid) || 0) + verdict.hits)
          outcome = decideOutcome(state, rules)
          if (outcome) break
        }
        if (outcome) break
      }
      continue
    }

    // 交替行动：先手方第 1 个 → 后手方第 1 个 → 先手方第 2 个 → …
    const queues = new Map()
    const cursors = new Map()
    for (const sideKey of sequence) {
      queues.set(sideKey, actorSnapshot(sideKey, round))
      cursors.set(sideKey, 0)
    }
    const takeNext = (sideKey) => {
      const queue = queues.get(sideKey)
      let cursor = cursors.get(sideKey)
      while (cursor < queue.length) {
        const candidate = queue[cursor]
        cursor += 1
        cursors.set(sideKey, cursor)
        if (candidate.alive && candidate.hp > 0) return candidate
      }
      cursors.set(sideKey, cursor)
      return null
    }

    let pending = true
    while (pending && !outcome) {
      pending = false
      for (const sideKey of sequence) {
        const actor = takeNext(sideKey)
        if (!actor) continue
        pending = true
        const verdict = performAttack(actor, sideKey, state, rules, hooks, rng, round, pushLog)
        if (verdict.verdict === 'acted') attackCounts.set(actor.uid, (attackCounts.get(actor.uid) || 0) + verdict.hits)
        outcome = decideOutcome(state, rules)
        if (outcome) break
      }
    }
  }

  if (!outcome) outcome = timeoutOutcome(state, rules, order.firstSide)

  const survivorsA = living(state.player.board)
  const survivorsB = living(state.opponent.board)
  const damageTo = { A: 0, B: 0 }
  if (outcome.winner === 'A') damageTo.B = damageFromSurvivors(survivorsA, rules, state.player)
  else if (outcome.winner === 'B') damageTo.A = damageFromSurvivors(survivorsB, rules, state.opponent)

  const perFollower = []
  for (const key of ['A', 'B']) {
    for (const f of state[SIDE_KEYS[key]].board) {
      perFollower.push({
        uid: f.uid,
        name: f.name,
        side: key,
        star: f.star,
        attack: f.attack,
        health: f.health,
        isEvolved: !!f.isEvolved,
        isSuperEvolved: !!f.isSuperEvolved,
        evolveMode: f.evolveMode,
        survives: f.alive && f.hp > 0,
        hpLeft: f.hp,
        attacks: attackCounts.get(f.uid) || 0,
      })
    }
  }

  return {
    winner: outcome.winner, // 'A' | 'B' | 'draw'
    reason: outcome.reason,
    rounds: round,
    firstSide: order.firstSide,
    firstReason: order.reason,
    firstDetail: order.detail,
    damageTo, // 对 A / B 主战者造成的伤害（0 表示未被击败）
    survivors: { A: survivorsA.length, B: survivorsB.length },
    stars: { A: starSumOf(state.player), B: starSumOf(state.opponent) },
    heroHp: { A: state.player.hp, B: state.opponent.hp },
    evolutions: evolutions.map((e) => ({ side: e.side, name: e.follower.name, mode: e.mode })),
    /** 双方的进化点解锁情况（第 5/7 回合进化点、第 9 回合超进化点） */
    evolutionPoints: {
      A: availableEvolutionPoints(battle.player, rules, battle.turn),
      B: availableEvolutionPoints(battle.opponent, rules, battle.turn),
    },
    perFollower,
    startCount,
    startStars: { A: starSumOf(battle.player), B: starSumOf(battle.opponent) },
    log: keepLog ? log : undefined,
  }
}

/** 计算先后手（不跑整场战斗，供 UI 展示用） */
export function resolveFirstSide(battle, rulesOverride, seed) {
  const rules = rulesOverride || resolveRules()
  const rng = new Prng(seed == null ? hashSeed(shortHash(battle)) : seed)
  return decideFirstSide(battle, rules, rng)
}
