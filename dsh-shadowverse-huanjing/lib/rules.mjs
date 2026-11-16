/**
 * DSH 影之幻境辅助插件 —— 规则配置层
 *
 * 设计原则：**所有未经实机校准的规则都在这里，且都可覆盖。**
 * 模拟内核（battle.mjs）不写死任何数值，只读本文件产出的规则对象。
 *
 * ⚠️ 标注 `校准项` 的字段是当前方案文本里没有给出确切公式的部分，
 *    先用保守默认值保证引擎可跑，等实机数据来了只改这里 / 传 overrides。
 */

/** 关键词（随从能力）定义。字符串解析时支持中文 / 英文 / 常见别名。 */
export const KEYWORDS = {
  /** 守护：作为被攻击目标的优先池，且不可被绕开（有守护时必须先打守护） */
  guard: { id: 'guard', label: '守护', aliases: ['守护', 'guard', 'taunt', '盾'] },
  /** 限制攻击：轮到该随从行动时被跳过 */
  cannotAttack: { id: 'cannotAttack', label: '限制攻击', aliases: ['限制攻击', 'cannotattack', 'cantattack', '无法攻击', '不能攻击'] },
  /** 毁灭：不管目标剩余多少生命值，且无视屏障，直接击杀 */
  destroy: { id: 'destroy', label: '毁灭', aliases: ['毁灭', 'destroy', '必杀', 'bane', '破坏'] },
  /** 屏障：免疫下一次受到的伤害（但不能免疫毁灭） */
  ward: { id: 'ward', label: '屏障', aliases: ['屏障', 'ward', 'divine', '圣盾', '免伤'] },
  /** 潜行：不可被选为攻击目标（无其他目标时自动失效） */
  stealth: { id: 'stealth', label: '潜行', aliases: ['潜行', 'stealth'] },
  /** 疾驰：入场当回合即可攻击（关闭“快照攻击”限制时生效） */
  rush: { id: 'rush', label: '疾驰', aliases: ['疾驰', 'rush', 'charge'] },
  /** 顺劈：攻击时对目标相邻随从造成等量伤害 */
  cleave: { id: 'cleave', label: '顺劈', aliases: ['顺劈', 'cleave', '溅射'] },
  /** 不灭（官方 Ability_keyword_0045）：无法被能力破坏 */
  indestructible: { id: 'indestructible', label: '不灭', aliases: ['不灭', 'indestructible'] },
  /** 2 连打（官方 Ability_keyword_0049）：攻击时可进行 2 次攻击 */
  doubleAttack: { id: 'doubleAttack', label: '2连打', aliases: ['2连打', '二连打', 'doubleattack'] },
}

/**
 * ⚠️ 幻境**没有**「吸血 / 虹吸」这类回复主战者的能力。
 * 早期版本误把本体卡的关键词表（Ability_keyword_0009 / Title_0009 虹吸）当成幻境能力，
 * 已彻底移除；官方幻境卡表里也没有对应的 Action。
 * 若将来确认存在，请在 KEYWORDS 里重新加回并在此登记，不要静默恢复。
 */
export const REMOVED_KEYWORDS = ['siphon', 'drain', '吸血', '虹吸']

/** 别名 -> 规范 id */
export const KEYWORD_ALIAS = (() => {
  const map = new Map()
  for (const kw of Object.values(KEYWORDS)) {
    map.set(kw.id.toLowerCase(), kw.id)
    map.set(kw.label, kw.id)
    for (const a of kw.aliases) map.set(String(a).toLowerCase(), kw.id)
  }
  return map
})()

/**
 * 规范化随从能力列表：接受 ['守护', '限制攻击'] / ['guard'] / '守护,guard'
 */
export function normalizeAbilities(input) {
  const list = Array.isArray(input)
    ? input
    : (input == null ? [] : String(input).split(/[,，、|\s]+/))
  const out = []
  for (const raw of list) {
    const key = String(raw == null ? '' : raw).trim()
    if (!key) continue
    const id = KEYWORD_ALIAS.get(key.toLowerCase()) || KEYWORD_ALIAS.get(key)
    if (id && !out.includes(id)) out.push(id)
    else if (!id) {
      const slug = key.toLowerCase().replace(/\s+/g, '_')
      if (!out.includes(slug)) out.push(slug) // 未收录能力照样保留，作为扩展钩子的键
    }
  }
  return out
}

/** 兵种（种族）。中立不属于任何一个种类。 */
export const TRIBES = {
  neutral: { id: 'neutral', label: '中立', aliases: ['中立', '无', 'neutral'] },
  elf: { id: 'elf', label: '精灵', aliases: ['精灵', 'elf', '妖精'] },
  royal: { id: 'royal', label: '皇家护卫', aliases: ['皇家护卫', '皇家', 'royal'] },
  nightmare: { id: 'nightmare', label: '梦魇', aliases: ['梦魇', 'nightmare', '梦'] },
  dragon: { id: 'dragon', label: '龙族', aliases: ['龙族', '龙', 'dragon'] },
}

const TRIBE_ALIAS = (() => {
  const map = new Map()
  for (const tribe of Object.values(TRIBES)) {
    map.set(tribe.id.toLowerCase(), tribe.id)
    map.set(tribe.label, tribe.id)
    for (const alias of tribe.aliases) map.set(String(alias).toLowerCase(), tribe.id)
  }
  return map
})()

/** 把任意写法的兵种归一化成 id；未知兵种原样保留（转小写下划线形式） */
export function normalizeTribe(input) {
  if (input == null || input === '') return 'neutral'
  const key = String(input).trim()
  return TRIBE_ALIAS.get(key.toLowerCase()) || TRIBE_ALIAS.get(key) || key.toLowerCase().replace(/\s+/g, '_')
}

/** 兵种中文名 */
export function tribeLabel(id) {
  return TRIBES[id] ? TRIBES[id].label : String(id)
}

/**
 * 默认规则。**校准项**必须实机核对。
 */
export const DEFAULT_RULES = {
  /** 版本标记：用于缓存键与结果可追溯 */
  version: 'huanjing-0.1.0',

  // ── 先后手（方案文本明确）─────────────────────────────────────────────
  order: {
    /** 比较优先级序列；'random' 放最后 = 全相等则随机 */
    precedence: ['followerCount', 'level', 'starSum', 'random'],
    /**
     * 校准项：先手方是第一行动方还是第二行动方。
     * 'higher'（默认）：指标更高的一方先手；'lower'：指标更低的一方先手。
     */
    prefer: 'higher',
  },

  // ── 目标选择 ─────────────────────────────────────────────────────────
  targeting: {
    /**
     * 守护优先成为目标，且**不可被绕开**（官方 Ability_keyword_0005：
     * 「对手的随从只能攻击拥有守护能力的随从」）。
     * 有多个守护时，在守护之间**等概率随机**。
     */
    guardFirst: true,
    /** 潜行不可被选为目标（官方 0007：不会被对手的随从攻击；无其他目标时自动失效） */
    respectStealth: true,
    /** 目标随机：'uniform' 等概率 + 'weighted' 按生命值反比加权 */
    mode: 'uniform',
    /** weighted 模式下：权重 = 1 / (health + weightBias) */
    weightBias: 0,
  },

  // ── 伤害结算 ─────────────────────────────────────────────────────────
  combat: {
    /**
     * 'mutual'（已由实机规则确认）：互相造成伤害 ——
     * 攻击方对目标造成攻击力伤害，同时受到目标攻击力的反击伤害，双方同时结算。
     */
    damageMode: 'mutual',
    /** 攻击力大于 0 才造成伤害；**0 攻随从照常出手，只是打出 0 点**（仍会消耗屏障） */
    requirePositiveAttack: false,
    /** 毁灭：无视目标剩余生命值直接击杀 */
    destroyAlwaysLethal: true,
    /** 毁灭：无视屏障（已由实机规则确认：有屏障也直接击杀） */
    destroyBypassesWard: true,
    /** 屏障：抵消下一次受到的伤害（不溢出） */
    wardBlocksOneHit: true,
    /** 顺劈：对目标相邻随从造成的伤害 = 攻击力 × cleaveRatio */
    cleaveRatio: 1,
    /** 2 连打（官方 0049）：拥有该能力的随从每轮攻击次数 */
    doubleAttackCount: 2,
  },

  // ── 行动顺序 ─────────────────────────────────────────────────────────
  turn: {
    /**
     * true（默认）：每轮开始时对双方存活随从做一次快照，
     *              本轮内新召唤/复活的随从不在本轮行动。
     */
    attackOrderSnapshot: true,
    /** 每轮内行动顺序：'position' 按站位下标；'reverse' 反向 */
    inSideOrder: 'position',
    /**
     * false（已由实机规则确认）：**决定先后手后双方交替行动** ——
     * 先手方第 1 个随从 → 后手方第 1 个 → 先手方第 2 个 → …
     * true 则是一方整队行动完再轮到另一方（保留作为可切换规则）。
     */
    sequentialSides: false,
  },

  // ── 结束与超时 ───────────────────────────────────────────────────────
  end: {
    /** 防止死循环：超过该轮数按“存活随从数 → 星级合计 → 平局”判定 */
    maxRounds: 200,
    /** maxRounds 超时后的判定力度 */
    timeoutResolve: 'board', // 'board' | 'draw' | 'first'
  },

  // ── 对主战者伤害（已按实机规则确认：胜方幻境等级 + 残存随从星级合计）──
  damage: {
    /**
     * 'levelPlusStarSum'（默认，实机规则）：
     *   伤害 = 胜利方幻境等级 + 残存随从各自的星级之和
     */
    formula: 'levelPlusStarSum',
    /** 幻境等级项的乘数（默认 1） */
    levelMultiplier: 1,
    /** 星级合计项的乘数（默认 1） */
    starMultiplier: 1,
    /** 下面这些是给其它口径预留的可切换公式参数 */
    baseFormula: 'starSum', // 'count' | 'starSum' | 'starTiers' | 'fixed'
    flat: 0,
    mult: 1,
    /** 'starTiers'：按存活随从的星级分段取权重后求和 */
    starTiers: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6 },
    /** 'fixed'：固定伤害 */
    fixed: 0,
    /** 伤害下限 / 上限（上限 null = 不限） */
    min: 0,
    max: null,
    /** 若为 true，进化随从的星级额外再计一次 */
    countEvolvedDouble: false,
  },

  // ── 进化 / 超进化 ────────────────────────────────────────────────────
  evolution: {
    /**
     * 是否使用「进化点」自动进化。
     * 实机规则：有些随从写了【进化时】效果，**只有被指使用进化点进化时**才发动；
     * 而三连得到的进化后卡牌，会拿到进化后的能力，但**不触发进化时效果**。
     */
    enabled: true,
    /**
     * **进化点解锁日程**（实机确认）：
     *   第 1 个进化点 → 第 5 回合解锁
     *   第 2 个进化点 → 第 7 回合解锁
     *   超进化点（共 1 个）→ 第 9 回合解锁
     * 主战者技能可以额外给点数（如「以持有 3 个进化点开始游戏」）或提前解锁，
     * 这类情况用 side.evolutionPoints / side.preUnlockedEvolutionPoints 直接给。
     */
    unlock: {
      evolveRounds: [5, 7],
      superEvolveRound: 9,
      /** 主战者技能带来的提前解锁回合数（side.evolutionUnlockOffset，正数=更早） */
      offsetField: 'evolutionUnlockOffset',
    },
    /** 进化加成（攻/血）—— 官方 103 张卡的 Evo - base 增量全为 +2/+2 */
    evolveBonus: { attack: 2, health: 2 },
    /** 超进化加成（攻/血）—— 官方增量全为 +3/+3 */
    superEvolveBonus: { attack: 3, health: 3 },
    /** 每个进化点可进化的随从数 */
    followersPerPoint: 1,
    /** 进化后是否按新生命值回满 */
    healOnEvolve: true,
    /**
     * 自动分配时，是否**只**给标注了「须使用进化点」的随从（canOnlyEvolveWithPoint）
     * 分配点数。false = 所有可进化随从都参与自动分配。
     */
    preferPointOnly: false,
    /** 选择进化对象的偏好 */
    prefer: {
      byAttack: true,
      /** 用技能进化有额外收益的随从加分（when_skill_evolve） */
      keywordBonus: { destroy: 2, cleave: 2, guard: 1, doubleAttack: 2 },
    },
  },

  // ── 三连合成与发现 ───────────────────────────────────────────────────
  fusion: {
    /** 三张相同进化前随从 → 一张该随从的「进化后」
     *  （合成表本身由知识库提供：cardId -> evolvedCardId） */
    triplesToEvolve: 3,
    /**
     * 三连得到的卡牌：**获得进化后的能力与面板，但不触发【进化时】效果**。
     * 置为 false 可改成「三连也触发进化时效果」。
     */
    triggerEvolutionOnTriple: false,
    /** 打出进化后随从时获得的发现：随机 3 张「当前幻境等级 + 1」的随从选 1 张 */
    discover: { choices: 3, levelOffset: 1 },
  },

  // ── 随机数 ───────────────────────────────────────────────────────────
  rng: {
    /** 默认种子；null = 结果不可复现（用时钟） */
    seed: 20260101,
  },
}

/** 简易深合并（对象、数组按覆盖处理，数组不做逐元素合并） */
export function mergeRules(base, overrides) {
  if (overrides == null) return clone(base)
  const out = clone(base)
  for (const [k, v] of Object.entries(overrides)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
      out[k] = mergeRules(out[k], v)
    } else if (v !== undefined) {
      out[k] = clone(v)
    }
  }
  return out
}

function clone(value) {
  if (value == null || typeof value !== 'object') return value
  if (Array.isArray(value)) return value.map(clone)
  const out = {}
  for (const [k, v] of Object.entries(value)) out[k] = clone(v)
  return out
}

/** 生成生效规则对象 */
export function resolveRules(overrides) {
  const merged = mergeRules(DEFAULT_RULES, overrides)
  // 一致性校验
  if (!Array.isArray(merged.order.precedence) || merged.order.precedence.length === 0) {
    throw new Error('rules.order.precedence 不能为空')
  }
  for (const key of merged.order.precedence) {
    if (!['followerCount', 'level', 'starSum', 'random'].includes(key)) {
      throw new Error(`rules.order.precedence 含未知比较项: ${key}`)
    }
  }
  if (!['higher', 'lower'].includes(merged.order.prefer)) {
    throw new Error(`rules.order.prefer 只能是 higher / lower，收到 ${merged.order.prefer}`)
  }
  if (!['oneWay', 'mutual'].includes(merged.combat.damageMode)) {
    throw new Error(`rules.combat.damageMode 只能是 oneWay / mutual，收到 ${merged.combat.damageMode}`)
  }
  if (!['uniform', 'weighted'].includes(merged.targeting.mode)) {
    throw new Error(`rules.targeting.mode 只能是 uniform / weighted，收到 ${merged.targeting.mode}`)
  }
  return merged
}

/**
 * 计算对败方主战者的伤害。
 *
 * 实机规则（默认）：**伤害 = 胜利方幻境等级 + 残存随从各自的星级之和**
 *
 * @param {object[]} survivors 胜方存活随从
 * @param {object} rules 生效规则
 * @param {object} [winnerSide] 胜方战场（用于取幻境等级）
 */
export function damageFromSurvivors(survivors, rules, winnerSide) {
  const cfg = rules.damage
  const list = Array.isArray(survivors) ? survivors : []
  const starSum = list.reduce((s, f) => s + num(f.star, 1), 0)
  const heroLevel = num(winnerSide && winnerSide.level, 1)

  let value
  switch (cfg.formula) {
    case 'levelPlusStarSum': {
      const levelPart = heroLevel * num(cfg.levelMultiplier, 1)
      const starPart = starSum * num(cfg.starMultiplier, 1)
      value = num(cfg.flat, 0) + levelPart + starPart
      break
    }
    case 'starSum':
      value = starSum
      break
    case 'starSum+flat':
      value = starSum + num(cfg.flat, 0)
      break
    case 'starSum*mult':
      value = starSum * num(cfg.mult, 1)
      break
    case 'starTiers':
      value = list.reduce((s, f) => s + num(cfg.starTiers[num(f.star, 1)], num(f.star, 1)), 0)
      break
    case 'count':
      value = list.length
      break
    case 'fixed':
      value = num(cfg.fixed, 0)
      break
    default:
      throw new Error(`rules.damage.formula 未知: ${cfg.formula}`)
  }
  if (cfg.countEvolvedDouble) value += list.filter((f) => f.isEvolved).length
  let out = Math.round(value)
  if (cfg.max != null) out = Math.min(out, num(cfg.max, 0))
  out = Math.max(out, num(cfg.min, 0))
  return out
}

function num(v, fallback) {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

/** 比较项取值 */
export function orderMetric(key, side) {
  switch (key) {
    case 'followerCount':
      return side.board.length
    case 'level':
      return num(side.level, 0)
    case 'starSum':
      return side.board.reduce((s, f) => s + num(f.star, 1), 0)
    default:
      return null
  }
}

/**
 * 判定先后手。
 * 返回 { firstSide, reason, rolls }，firstSide ∈ {'A','B'}（A = player，B = opponent）
 */
export function decideFirstSide(battle, rules, rng) {
  const sides = { A: battle.player, B: battle.opponent }
  for (const key of rules.order.precedence) {
    if (key === 'random') {
      const roll = rng.next()
      const firstSide = roll < 0.5 ? 'A' : 'B'
      return { firstSide, reason: 'random', detail: `随机判定 roll=${roll.toFixed(6)}` }
    }
    const av = orderMetric(key, sides.A)
    const bv = orderMetric(key, sides.B)
    if (av === bv) continue
    const aWins = rules.order.prefer === 'lower' ? av < bv : av > bv
    return {
      firstSide: aWins ? 'A' : 'B',
      reason: key,
      detail: `${key}: A=${av} B=${bv}（${rules.order.prefer === 'lower' ? '低者' : '高者'}先手）`,
    }
  }
  return { firstSide: 'A', reason: 'fallback', detail: '所有比较项相等（未启用随机项），回退 A 先手' }
}

export const RULE_KEYS = ['order', 'targeting', 'combat', 'turn', 'end', 'damage', 'rng', 'version']
