/**
 * 胜率统计层：把「打过的每一局」汇总成可信的胜率/排名指标。
 *
 * 设计参照 HSReplay 酒馆战棋（Battlegrounds）的指标口径：
 *   · 平均排名（Average Placement）—— 8 人局的核心指标
 *   · 前四率 / 优胜率（Top 4 / Win Rate）
 *   · 选取率（Pick Rate）—— 某个英雄被选中打的概率
 *   · 分档（Tier）—— 按排名把英雄/流派分成 S/A/B/C…
 *   · **小样本修正（Bayesian smoothing）** —— 样本少的英雄不许靠着 3 局全胜冲到榜首
 *
 * 与本插件其他部分的区别：那些算的是「这一局打起来的胜率」（模拟），
 * 这里算的是「我这个人/这个英雄/这个流派长期表现如何」（统计），两者互补。
 */

/**
 * Wilson 置信区间（比例）。小样本时区间很宽，天然抑制"3 局全胜就是神"。
 * @param {number} successes
 * @param {number} total
 * @param {number} z 置信系数（1.96 ≈ 95%）
 */
export function wilsonInterval(successes, total, z = 1.96) {
  const n = Math.max(0, Math.trunc(total) || 0)
  if (n === 0) return { low: 0, high: 1, p: 0, n: 0 }
  const p = Math.min(1, Math.max(0, successes / n))
  const z2 = z * z
  const denom = 1 + z2 / n
  const center = (p + z2 / (2 * n)) / denom
  const spread = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom
  return {
    p: Number(p.toFixed(4)),
    low: Number(Math.max(0, center - spread).toFixed(4)),
    high: Number(Math.min(1, center + spread).toFixed(4)),
    n,
  }
}

/**
 * 贝叶斯平滑：把观测值向先验（总体均值）收缩，权重 = 样本量 / (样本量 + 先验强度)。
 *
 * @param {number} observed 该组的观测值（如平均排名）
 * @param {number} n 该组样本量
 * @param {number} prior 先验值（通常取总体均值）
 * @param {number} strength 先验强度（等效"先验占几局"）
 */
export function shrink(observed, n, prior, strength = 20) {
  const k = Math.max(0, Number(strength) || 0)
  const count = Math.max(0, Number(n) || 0)
  if (count + k === 0) return prior
  return (observed * count + prior * k) / (count + k)
}

/** 平均排名的分档阈值（8 人局，排名越小越好；参照 HSReplay 的分档思路） */
export const TIER_THRESHOLDS = [
  { tier: 'S', max: 3.4 },
  { tier: 'A', max: 3.9 },
  { tier: 'B', max: 4.3 },
  { tier: 'C', max: 4.7 },
  { tier: 'D', max: 5.2 },
  { tier: 'E', max: Infinity },
]

export function tierOf(avgRank) {
  for (const t of TIER_THRESHOLDS) if (avgRank <= t.max) return t.tier
  return 'E'
}

/**
 * 汇总一组对局。
 *
 * @param {Array<object>} matches 每局记录：{ finalRank, playerHero?, tribe?, comp?, board?, durationMs? }
 * @param {{playersPerMatch?: number, priorStrength?: number, priorRank?: number}} options
 *   priorRank 是**收缩的目标值**，应当传全局均值（groupBy 会自动传），
 *   不传时才退化成用本组均值（这时组内样本少就不会被收缩，注意区别）。
 */
export function aggregate(matches, options = {}) {
  const list = Array.isArray(matches) ? matches.filter((m) => m && Number.isFinite(Number(m.finalRank))) : []
  const playersPerMatch = Math.max(2, Math.trunc(options.playersPerMatch ?? 8))
  const priorStrength = Number(options.priorStrength ?? 20)

  const ranks = list.map((m) => Number(m.finalRank)).filter((r) => r >= 1 && r <= playersPerMatch)
  const n = ranks.length
  const ownMean = n ? ranks.reduce((s, r) => s + r, 0) / n : (playersPerMatch + 1) / 2
  // 先验：优先用调用方给的全局均值；没给就只能用组内均值（此时组内样本少=不会被收缩）
  const priorRank = Number.isFinite(Number(options.priorRank)) ? Number(options.priorRank) : ownMean
  const wins = ranks.filter((r) => r === 1).length
  const top4 = ranks.filter((r) => r <= Math.ceil(playersPerMatch / 2)).length
  const bottom = ranks.filter((r) => r > Math.ceil(playersPerMatch / 2)).length

  // 排名分布
  const dist = {}
  for (let i = 1; i <= playersPerMatch; i += 1) dist[i] = ranks.filter((r) => r === i).length

  return {
    matches: n,
    playersPerMatch,
    averageRank: n ? Number(ownMean.toFixed(3)) : null,
    /** 小样本修正后的平均排名（样本少时向全局均值收缩） */
    smoothedAverageRank: n ? Number(shrink(ownMean, n, priorRank, priorStrength).toFixed(3)) : null,
    winRate: n ? Number((wins / n).toFixed(4)) : null,
    top4Rate: n ? Number((top4 / n).toFixed(4)) : null,
    bottomHalfRate: n ? Number((bottom / n).toFixed(4)) : null,
    winRateCI: wilsonInterval(wins, n),
    top4CI: wilsonInterval(top4, n),
    rankDistribution: dist,
    priorRank: Number(priorRank.toFixed(3)),
  }
}

/**
 * 按某个维度分组统计（例如按英雄、按流派），并排序出分档榜单。
 *
 * @param {Array<object>} matches
 * @param {(m: object) => string|string[]|null} keyFn 分组键（可返回多个键）
 */
export function groupBy(matches, keyFn, options = {}) {
  const list = Array.isArray(matches) ? matches : []
  const buckets = new Map()
  for (const m of list) {
    let keys = keyFn(m)
    if (!keys) continue
    if (!Array.isArray(keys)) keys = [keys]
    for (const k of keys) {
      const key = String(k)
      if (!key) continue
      if (!buckets.has(key)) buckets.set(key, [])
      buckets.get(key).push(m)
    }
  }
  const overall = aggregate(list, options)
  // 关键：收缩的先验必须是**全局均值**，否则小样本组会向自身收缩（等于没收缩）
  const groupOptions = { ...options, priorRank: overall.averageRank }
  const rows = []
  for (const [key, group] of buckets) {
    const stats = aggregate(group, groupOptions)
    rows.push({
      key,
      ...stats,
      /** 相对总体平均排名的提升（负数=比平均更好） */
      rankDelta: stats.smoothedAverageRank != null && overall.smoothedAverageRank != null
        ? Number((stats.smoothedAverageRank - overall.smoothedAverageRank).toFixed(3))
        : null,
      tier: stats.smoothedAverageRank != null ? tierOf(stats.smoothedAverageRank) : null,
      /** 样本是否足够下结论 */
      reliable: stats.matches >= Number(options.minSamples ?? 20),
    })
  }
  rows.sort((a, b) => (a.smoothedAverageRank ?? 99) - (b.smoothedAverageRank ?? 99))
  return { overall, rows }
}

/**
 * 把对局记录汇总成 HSReplay 风格的完整报告（英雄榜 / 流派榜 / 流派×英雄矩阵）。
 *
 * @param {Array<object>} matches
 * @param {{playersPerMatch?: number, minSamples?: number}} options
 */
export function buildWinRateReport(matches, options = {}) {
  const list = Array.isArray(matches) ? matches : []
  const overall = aggregate(list, options)
  const heroes = groupBy(list, (m) => (m.playerHero ? String(m.playerHero) : null), options)
  const comps = groupBy(list, (m) => {
    if (Array.isArray(m.comps)) return m.comps.map(String)
    if (m.comp) return String(m.comp)
    if (m.tribe) return String(m.tribe)
    return null
  }, options)
  const heroComp = groupBy(list, (m) => {
    const h = m.playerHero ? String(m.playerHero) : null
    const c = m.comp ? String(m.comp) : (m.tribe ? String(m.tribe) : null)
    return h && c ? `${h} × ${c}` : null
  }, options)

  const minSamples = Number(options.minSamples ?? 20)
  const leaderboard = heroes.rows.filter((r) => r.matches >= minSamples)
  return {
    overall,
    minSamples,
    heroes: heroes.rows,
    comps: comps.rows,
    heroComp: heroComp.rows,
    /** 只保留样本足够的榜单（可直接展示给用户） */
    topHeroes: leaderboard.slice(0, 10),
    topComps: comps.rows.filter((r) => r.matches >= minSamples).slice(0, 10),
    notes: [
      '小样本已做贝叶斯收缩，样本不足的条目会被标 reliable=false，不会靠运气冲到榜首。',
      '平均排名是第一指标（8 人局），前四率是第二指标。',
      '本机记录只代表你自己的对局，不等于全服环境。',
    ],
  }
}

/**
 * 计算两套阵容对局数的置信度：需要多少局才能区分两个胜率。
 * HSReplay 式的实用工具：告诉你"这局数够不够下结论"。
 *
 * @param {number} p1 观察到的胜率
 * @param {number} p2 对照组胜率
 * @param {{alpha?: number, power?: number}} options
 */
export function requiredSampleSize(p1, p2, options = {}) {
  const alpha = options.alpha ?? 0.05
  const power = options.power ?? 0.8
  // 正态近似：n = (z_{1-a/2} + z_{1-b})^2 * (p1(1-p1) + p2(1-p2)) / (p1-p2)^2
  const zA = alpha === 0.05 ? 1.96 : 1.96
  const zB = power === 0.8 ? 0.8416 : 0.8416
  const diff = Math.abs(p1 - p2)
  if (diff < 1e-6) return { n: Infinity, note: '两组胜率相同，无法用样本量区分' }
  const variance = p1 * (1 - p1) + p2 * (1 - p2)
  const n = Math.ceil(((zA + zB) ** 2 * variance) / (diff * diff))
  return {
    n,
    p1, p2,
    difference: Number(diff.toFixed(4)),
    note: `每组约需 ${n} 局才能在 95% 置信、80% 检验力下区分 ${(p1 * 100).toFixed(1)}% 与 ${(p2 * 100).toFixed(1)}%`,
  }
}
