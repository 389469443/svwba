/**
 * 批量蒙特卡洛模拟 + 统计聚合（胜率、伤害分布、关键指标）
 */

import { Prng, shortHash } from './rng.mjs'
import { resolveRules } from './rules.mjs'
import { normalizeBattle, simulateOnce } from './battle.mjs'

/** 局面复杂度 → 建议模拟次数 */
export function suggestSimulationCount(battle, base = 10000) {
  const cells = (battle.player.board.length + battle.opponent.board.length)
  const stars = [...battle.player.board, ...battle.opponent.board]
    .reduce((s, f) => s + (Number(f.star) || 1), 0)
  const keywordCount = [...battle.player.board, ...battle.opponent.board]
    .reduce((s, f) => s + (f.abilities ? f.abilities.length : 0), 0)
  const complexity = cells * 2 + stars + keywordCount * 2
  if (complexity <= 12) return Math.max(2000, Math.round(base * 0.5))
  if (complexity <= 30) return base
  return Math.min(50000, base * 2)
}

/**
 * 跑 N 次模拟并统计。
 *
 * @param {object} input 原始局面（会被归一化）
 * @param {object} [options]
 * @param {number} [options.simulations=10000]
 * @param {object} [options.rules] 规则覆盖
 * @param {number|string} [options.seed] 随机种子（默认取 rules.rng.seed）
 * @param {object} [options.hooks] 模拟钩子（主战者技能）
 * @param {number} [options.maxDamageBuckets=32] 伤害分布最多保留的桶数
 * @param {boolean}[options.detail] 是否附带单场样例
 */
export function runSimulation(input, options = {}) {
  const rules = resolveRules(options.rules)
  const { battle, warnings } = normalizeBattle(input, options)
  const simulations = Math.max(1, Math.min(1_000_000, Math.trunc(options.simulations ?? 10000)))
  const seed = options.seed ?? rules.rng.seed
  const rng = new Prng(seed)

  const startedAt = Date.now()
  const wins = { A: 0, B: 0, draw: 0 }
  const damageTo = { A: new Map(), B: new Map() }
  const reasonCount = new Map()
  const firstReasonCount = new Map()
  const firstSideCount = { A: 0, B: 0 }
  const survivorHist = { A: new Map(), B: new Map() }
  let roundsSum = 0
  let roundsMax = 0
  const damageSum = { A: 0, B: 0 }
  const survivalSum = { A: 0, B: 0 }
  let sample = null

  for (let i = 0; i < simulations; i++) {
    const result = simulateOnce(battle, {
      rules,
      rng,
      hooks: options.hooks,
      withLog: !!options.detail && i === 0,
    })
    if (i === 0 && options.detail) sample = result

    wins[result.winner] += 1
    reasonCount.set(result.reason, (reasonCount.get(result.reason) || 0) + 1)
    firstReasonCount.set(result.firstReason, (firstReasonCount.get(result.firstReason) || 0) + 1)
    firstSideCount[result.firstSide] += 1
    roundsSum += result.rounds
    roundsMax = Math.max(roundsMax, result.rounds)
    survivalSum.A += result.survivors.A
    survivalSum.B += result.survivors.B
    bump(survivorHist.A, result.survivors.A)
    bump(survivorHist.B, result.survivors.B)
    // 只有被击败的那一方才受到伤害；平局与获胜方记 0，不计入分布
    if (result.winner === 'A') { bump(damageTo.B, result.damageTo.B); damageSum.B += result.damageTo.B }
    else if (result.winner === 'B') { bump(damageTo.A, result.damageTo.A); damageSum.A += result.damageTo.A }
  }

  const elapsedMs = Date.now() - startedAt
  const p = (n) => n / simulations

  const damageTable = {
    A: distribution(damageTo.A, wins.B, options.maxDamageBuckets),
    B: distribution(damageTo.B, wins.A, options.maxDamageBuckets),
  }

  const expectedDamage = { A: 0, B: 0 }
  for (const [value, count] of damageTo.A) expectedDamage.A += value * (count / simulations)
  for (const [value, count] of damageTo.B) expectedDamage.B += value * (count / simulations)

  const result = {
    ok: true,
    version: rules.version,
    battleId: shortHash({ battle, rules, simulations, seed }),
    seed: String(seed),
    simulations,
    elapsedMs,
    throughput: elapsedMs > 0 ? Math.round((simulations / elapsedMs) * 1000) : null,
    firstSide: {
      A: p(firstSideCount.A),
      B: p(firstSideCount.B),
      reasons: mapToObject(firstReasonCount, p),
    },
    winRate: {
      player: p(wins.A),
      opponent: p(wins.B),
      draw: p(wins.draw),
    },
    winRatePercent: {
      player: pct(p(wins.A)),
      opponent: pct(p(wins.B)),
      draw: pct(p(wins.draw)),
    },
    /** 对己方 / 对方主战者的伤害分布（条件于“对方落败”） */
    damage: {
      toPlayer: damageTable.A,
      toOpponent: damageTable.B,
    },
    /** 期望伤害（含未落败的 0，即“本回合我方预计掉血”） */
    expectedDamage: {
      toPlayer: round4(expectedDamage.A),
      toOpponent: round4(expectedDamage.B),
    },
    /** 条件期望：在“落败”的前提下平均掉多少血 */
    conditionalExpectedDamage: {
      toPlayer: wins.B > 0 ? round4(damageSum.A / wins.B) : 0,
      toOpponent: wins.A > 0 ? round4(damageSum.B / wins.A) : 0,
    },
    avgRounds: round4(roundsSum / simulations),
    maxRounds: roundsMax,
    avgSurvivors: {
      player: round4(survivalSum.A / simulations),
      opponent: round4(survivalSum.B / simulations),
    },
    survivorDistribution: {
      player: mapToObject(survivorHist.A, p),
      opponent: mapToObject(survivorHist.B, p),
    },
    endReasons: mapToObject(reasonCount, p),
    warnings,
    rulesUsed: rules,
    sample,
  }

  // 评分与结论（方便模型/AI 直接引用）
  result.assessment = assess(result)
  return result
}

function assess(result) {
  const pr = result.winRate.player
  const notes = []
  let grade = 'even'
  if (pr >= 0.7) grade = 'dominant'
  else if (pr >= 0.58) grade = 'favored'
  else if (pr <= 0.3) grade = 'hopeless'
  else if (pr <= 0.42) grade = 'unfavored'
  if (result.winRate.draw >= 0.1) notes.push(`平局概率偏高（${(result.winRate.draw * 100).toFixed(1)}%），可能双方均为低攻击或高生命阵容`)
  if (result.avgRounds > 20 && result.maxRounds <= 200) notes.push('平均轮数偏长，双方清场能力不足')
  if (result.endReasons['timeout-count'] || result.endReasons['timeout-star'] || result.endReasons['timeout-draw'] || result.endReasons['timeout-first']) {
    notes.push('存在超时判定局（命中 maxRounds），建议核对规则或提高 maxRounds')
  }
  const spread = result.damage.toPlayer.entries.length
  if (spread === 0) notes.push('对方落败样本为 0，伤害分布不可用')
  return {
    grade,
    headline: `我方胜率 ${(pr * 100).toFixed(1)}%，预计掉血 ${result.expectedDamage.toPlayer.toFixed(2)} 点`,
    notes,
  }
}

function bump(map, key) {
  map.set(key, (map.get(key) || 0) + 1)
}

/**
 * 把计数表转成升序伤害表，含概率与累计概率。
 * @param {Map<number, number>} map
 * @param {number} total 条件总数（落败场次）；为 0 时返回空表
 */
function distribution(map, total, maxBuckets = 32) {
  const entries = [...map.entries()].sort((a, b) => a[0] - b[0])
  const sum = entries.reduce((s, [, c]) => s + c, 0)
  const denom = total > 0 ? total : sum
  let cumulative = 0
  const out = entries.map(([value, count]) => {
    const probability = denom > 0 ? count / denom : 0
    cumulative += probability
    return { value, count, probability, probabilityPercent: pct(probability), cumulative: round6(cumulative) }
  })
  if (out.length > maxBuckets) {
    // 合并尾部长尾，保留前 maxBuckets-1 个 + 一个合并桶
    const head = out.slice(0, maxBuckets - 1)
    const tail = out.slice(maxBuckets - 1)
    const tailCount = tail.reduce((s, e) => s + e.count, 0)
    const tailProb = tail.reduce((s, e) => s + e.probability, 0)
    head.push({
      value: `${tail[0].value}+`,
      count: tailCount,
      probability: round6(tailProb),
      probabilityPercent: pct(tailProb),
      cumulative: 1,
      merged: true,
    })
    return { denominator: denom, entries: head, truncated: true }
  }
  return { denominator: denom, entries: out, truncated: false }
}

function mapToObject(map, transform) {
  const out = {}
  for (const [key, v] of [...map.entries()].sort((a, b) => String(a[0]).localeCompare(String(b[0])))) {
    out[key] = transform ? round6(transform(v)) : v
  }
  return out
}

function pct(x) {
  return round4(x * 100)
}

function round4(x) {
  return Math.round(x * 10000) / 10000
}

function round6(x) {
  return Math.round(x * 1e6) / 1e6
}

/**
 * 贝叶斯融合：把模拟先验与历史数据似然合成为后验胜率。
 *
 * 采用 Beta-二项共轭：
 *   先验 Beta(α0, β0)，由模拟胜率 p_sim 与等价样本量 k0 得到：
 *     α0 = p_sim * k0 + 1, β0 = (1 - p_sim) * k0 + 1
 *   似然：历史 n 场中胜 m 场
 *   后验 Beta(α0 + m, β0 + n - m)，点估计取后验均值
 *
 * @param {{player:number}} prior 模拟结果（0~1）
 * @param {{wins:number, losses:number, draws?:number}} history 相似局面历史
 * @param {{priorWeight?:number}} [options] priorWeight = k0 等价样本量，越大越信模拟
 */
export function bayesianFusion(prior, history, options = {}) {
  const k0 = options.priorWeight ?? 200
  const pSim = clamp01(prior.player ?? prior.winRate?.player ?? 0)
  const wins = Math.max(0, Math.trunc(history?.wins ?? 0))
  const losses = Math.max(0, Math.trunc(history?.losses ?? 0))
  const draws = Math.max(0, Math.trunc(history?.draws ?? 0))
  const n = wins + losses // 平局不提供胜负信息
  const alpha0 = pSim * k0 + 1
  const beta0 = (1 - pSim) * k0 + 1
  const alpha = alpha0 + wins
  const beta = beta0 + losses
  const posterior = alpha / (alpha + beta)
  const variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1))
  const sd = Math.sqrt(variance)
  return {
    prior: round6(pSim),
    /** 未四舍五入的先验（后验恰好等于先验时，raw 值也严格相等） */
    priorRaw: pSim,
    posterior: round6(posterior),
    posteriorRaw: posterior,
    posteriorPercent: pct(posterior),
    credibleInterval95: [round6(Math.max(0, posterior - 1.96 * sd)), round6(Math.min(1, posterior + 1.96 * sd))],
    effectiveSample: { prior: k0, history: n, draws },
    shift: round6(posterior - pSim),
    explanation: `先验 ${(pSim * 100).toFixed(1)}%（等价 ${k0} 场） + 历史 ${wins}胜${losses}负 → 后验 ${(posterior * 100).toFixed(1)}%`,
  }
}

function clamp01(x) {
  if (!Number.isFinite(x)) return 0
  return Math.min(1, Math.max(0, x))
}

/** 结果摘要文本（给模型读的一行结论） */
export function summarize(result) {
  const wr = result.winRatePercent
  const firstReasons = Object.keys(result.firstSide.reasons || {}).join('/') || '-'
  const lines = [
    `【影之幻境模拟】样本 ${result.simulations} 次，用时 ${result.elapsedMs}ms（${result.throughput ?? '-'}/s）`,
    `先后手判定依据：${firstReasons}；己方先手概率 ${(result.firstSide.A * 100).toFixed(1)}%`,
    `胜率：己方 ${wr.player}% / 对方 ${wr.opponent}% / 平局 ${wr.draw}%`,
    `预计掉血：己方 ${result.expectedDamage.toPlayer}，对方 ${result.expectedDamage.toOpponent}`,
    `落败时平均伤害：己方 ${result.conditionalExpectedDamage.toPlayer}，对方 ${result.conditionalExpectedDamage.toOpponent}`,
    `平均轮数 ${result.avgRounds}，平均残存随从 己方 ${result.avgSurvivors.player} / 对方 ${result.avgSurvivors.opponent}`,
    `结论：${result.assessment.headline}`,
  ]
  if (result.assessment.notes.length) lines.push(`提示：${result.assessment.notes.join('；')}`)
  if (result.damage.toPlayer.entries.length) {
    lines.push('我方掉血分布：' + result.damage.toPlayer.entries
      .map((e) => `${e.value}点 ${e.probabilityPercent}%`).join(' / '))
  }
  if (result.damage.toOpponent.entries.length) {
    lines.push('对方掉血分布：' + result.damage.toOpponent.entries
      .map((e) => `${e.value}点 ${e.probabilityPercent}%`).join(' / '))
  }
  if (result.warnings.length) lines.push('识别校验提示：' + result.warnings.join('；'))
  return lines.join('\n')
}
