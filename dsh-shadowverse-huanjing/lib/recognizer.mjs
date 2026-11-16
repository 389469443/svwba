/**
 * 截图识别（Node 侧）：把 Python 抽出的像素读数接到知识库与模拟。
 *
 * **识别优先级（用户明确要求）**：
 *   1. **卡名标签**（悬停/战斗开始时的卡名条）→ 最可靠，直接定名
 *   2. **卡面特征**（立绘、外框稀有度、能力图标）→ 用来消歧
 *   3. 攻/血 —— **只能作为弱线索或最终校验，绝不能用来反查卡名**
 *
 * 为什么不能用面板反查：本局存在大量「加面板」的效果（如 傍死的安纳提玛·徒姬
 * 给梦魇随从 +6/+6、无极猎人·阿拉加维 被破坏时 +1/+1、霸空武神·哪吒 攻击时 +1/+1），
 * 面板早已不等于基础面板。实测同一套 7 个随从的面板与本局记录完全对不上。
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { cards, findCardByName, normalizeCardName } from './knowledge.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))

/** 官方 Rarity 字段 → 外框（实测与官方字段完全一致：1 铜 / 2 银 / 3 金 / 4 虹） */
export const RARITY_NAMES = { 1: '铜卡', 2: '银卡', 3: '金卡', 4: '虹卡' }

/** 外框颜色特征，用于从截图像素判稀有度（给卡名识别当先验） */
export const RARITY_FRAME_HINTS = {
  1: '棕色金属框',
  2: '银色金属框',
  3: '金色金属框',
  4: '彩虹/幻彩框（虹卡超进化后同样是虹色框）',
}

export function rarityName(rarity) {
  return RARITY_NAMES[rarity] || `Rarity${rarity}`
}

/** 建立 (attack, health) -> 卡列表 的索引（**仅供弱线索与校验使用，不用于反查卡名**） */
const STAT_INDEX = new Map()
for (const card of Object.values(cards)) {
  if (card.type !== 1) continue
  const key = `${card.attack}/${card.health}`
  if (!STAT_INDEX.has(key)) STAT_INDEX.set(key, [])
  STAT_INDEX.get(key).push(card)
}

/** 按攻/血查候选卡 */
export function findCardsByStats(attack, health) {
  return STAT_INDEX.get(`${attack}/${health}`) || []
}

/** 所有 (攻/血) 组合的出现次数，用于评估「仅凭数值能否定位卡名」 */
export function statAmbiguity() {
  const dist = new Map()
  for (const [key, list] of STAT_INDEX) dist.set(key, list.length)
  const unique = [...dist.values()].filter((n) => n === 1).length
  return {
    combos: dist.size,
    uniqueCombos: unique,
    uniqueRatio: dist.size > 0 ? unique / dist.size : 0,
    ambiguous: [...dist.entries()].filter(([, n]) => n > 1).map(([k, n]) => ({ stats: k, count: n })),
  }
}

/**
 * 从 Python 的 state JSON 组装局面。
 *
 * @param {object} stateJson Python 输出
 * @param {object} [options]
 * @param {Record<string,string>} [options.nameOverrides] { '玩家-0': '卡名' } 或 { '0': '卡名' }
 * @param {object} [options.heroes] { player: {name, hp, level}, opponent: {...} }
 * @param {boolean} [options.requirePaired=true] 只接受识别出两个数字的卡位
 */
export function stateJsonToBattle(stateJson, options = {}) {
  const overrides = options.nameOverrides || {}
  const warnings = []
  const unresolved = []
  const resolved = []

  const convertSide = (sideKey, countKey) => {
    const side = stateJson[sideKey] || {}
    const slots = (side.slots || []).filter((s) => {
      if (!options.requirePaired) return true
      return Array.isArray(s.numbers) && s.numbers.length >= 2
    })
    const board = []
    for (const slot of slots) {
      const atk = Number(slot.numbers[0] && slot.numbers[0].text)
      const life = Number(slot.numbers[1] && slot.numbers[1].text)
      if (!Number.isFinite(atk) || !Number.isFinite(life)) {
        warnings.push(`${sideKey} 第 ${slot.slot + 1} 个卡位数值无法解析，已跳过`)
        continue
      }
      const override = overrides[`${sideKey}-${slot.slot}`] || overrides[`${sideKey}:${slot.slot}`] || overrides[String(slot.slot)]
      let card = null
      if (override) {
        card = findCardByName(override)
        if (!card) warnings.push(`指定的卡名「${override}」在知识库里找不到`)
      }
      const candidates = findCardsByStats(atk, life)
      if (!card && candidates.length === 1) card = candidates[0]
      if (!card) {
        unresolved.push({
          side: sideKey, slot: slot.slot, attack: atk, health: life,
          candidates: candidates.map((c) => ({ name: c.name, baseCardId: c.baseCardId, star: c.star, tribe: c.tribe })),
          reason: candidates.length === 0 ? '知识库里没有该攻/血组合的随从' : '攻/血对应多张卡，需要指定卡名',
        })
      }
      const chosen = card || null
      resolved.push({
        side: sideKey, slot: slot.slot, attack: atk, health: life,
        name: chosen ? chosen.name : null,
        matchedBy: chosen ? (override ? 'override' : 'stats') : null,
      })
      board.push({
        name: chosen ? chosen.name : `未知随从(${atk}/${life})`,
        cardId: chosen ? String(chosen.baseCardId) : undefined,
        star: chosen ? chosen.star : 1,
        attack: atk,
        health: life,
        position: board.length,
        meta: chosen ? { tribe: chosen.tribe } : undefined,
      })
    }
    void countKey
    return board
  }

  const playerBoard = convertSide('player')
  const opponentBoard = convertSide('opponent')
  const heroInfo = options.heroes || {}

  const battle = {
    phase: 'battle',
    player: {
      hp: heroInfo.player && Number.isFinite(Number(heroInfo.player.hp)) ? Number(heroInfo.player.hp) : 40,
      level: heroInfo.player && Number.isFinite(Number(heroInfo.player.level)) ? Number(heroInfo.player.level) : 1,
      board: playerBoard,
    },
    opponent: {
      hp: heroInfo.opponent && Number.isFinite(Number(heroInfo.opponent.hp)) ? Number(heroInfo.opponent.hp) : 40,
      level: heroInfo.opponent && Number.isFinite(Number(heroInfo.opponent.level)) ? Number(heroInfo.opponent.level) : 1,
      board: opponentBoard,
    },
  }

  return { battle, resolved, unresolved, warnings }
}

/**
 * 一份识别结果的「可解释度」：多少卡位能自动定卡名、多少需要人工确认。
 */
export function recognitionQuality(stateJson, options = {}) {
  const { resolved, unresolved } = stateJsonToBattle(stateJson, options)
  const auto = resolved.filter((r) => r.matchedBy === 'stats' || r.matchedBy === 'override').length
  const total = resolved.length + unresolved.length
  return {
    total,
    autoMatched: auto,
    needConfirm: unresolved.length,
    autoRatio: total > 0 ? auto / total : 0,
    unresolved,
    ambiguity: statAmbiguity(),
  }
}

/** 卡名是否在库内（供 UI 校验用户输入） */
export function isKnownCardName(name) {
  return !!findCardByName(name) || !!cards[normalizeCardName(name)]
}

// ─────────────────────────────────────────────────────────────────────────────
// 卡名标签的字体匹配
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 用系统字体把「候选卡名用到的所有汉字」渲染成 16x16 网格，作为字形模板。
 *
 * 说明：这一步需要能渲染中文的字体文件。Node 侧没有内置字体引擎，
 * 因此在真实环境里由 Python 侧（tools/glyph_templates.py）一次性生成
 * `lib/screenshot/glyphs.json`；本函数负责读取与匹配。
 */
let glyphCache = null

export function loadGlyphTemplates(filePath) {
  if (glyphCache && !filePath) return glyphCache
  const candidates = [
    filePath,
    path.join(HERE, 'screenshot', 'glyphs.json'),
  ].filter(Boolean)
  for (const candidate of candidates) {
    try {
      const raw = fs.readFileSync(candidate, 'utf8')
      const parsed = JSON.parse(raw)
      if (parsed && parsed.glyphs) {
        glyphCache = parsed
        return glyphCache
      }
    } catch (err) { /* 继续找 */ }
  }
  return null
}

/**
 * 加载**真实截图**建的字形库（优先于字体渲染模板）。
 *
 * 为什么必须有它：游戏卡名是特定粗体+描边，用系统字体渲染的模板做像素比对，
 * 正确字在 406 个候选里会排到 300 名开外（实测）。用真实截图切出的字形才认得准。
 * 库由 tools/build_glyph_lib.py 从带标注的悬停截图生成。
 */
let glyphLibCache = null
export function loadGlyphLib(filePath) {
  const file = filePath || path.join(HERE, 'screenshot', 'glyph-lib.json')
  if (glyphLibCache && !filePath) return glyphLibCache
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (parsed && parsed.glyphs) {
      glyphLibCache = parsed
      return parsed
    }
  } catch (err) { /* 没有库文件是正常情况（尚未标定） */ }
  return null
}

/** 归一化灰度网格的余弦相似度（对笔画粗细差异比 0/1 网格宽容得多） */
export function cosineSimilarity(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return 0
  let dot = 0
  let na = 0
  let nb = 0
  for (let i = 0; i < a.length; i += 1) {
    dot += a[i] * b[i]
    na += a[i] * a[i]
    nb += b[i] * b[i]
  }
  return na && nb ? dot / Math.sqrt(na * nb) : 0
}

/** 16x16 网格 → 24x24 灰度向量的兼容转换（供库外场景使用） */
function gridToVector(grid) {
  const n = Math.round(Math.sqrt(grid.length))
  return Array.from(grid, (c) => (c === '1' ? 1 : 0))
}

/** 16x16 网格 → 16x16 的 0/1 → 缩放成 24x24（最近邻，保持可比较） */
function upsampleGrid(grid, from = 16, to = 24) {
  const out = []
  for (let y = 0; y < to; y += 1) {
    for (let x = 0; x < to; x += 1) {
      const sx = Math.min(from - 1, Math.floor((x * from) / to))
      const sy = Math.min(from - 1, Math.floor((y * from) / to))
      out.push(grid[sy * from + sx] === '1' ? 1 : 0)
    }
  }
  return out
}

/** 16x16 网格 → 24x24 灰度向量（用于字形库匹配） */
export function gridToLibVector(grid) {
  return upsampleGrid(grid)
}

/**
 * 用**真实字形库**匹配卡名。
 *
 * 与 matchGlyphs 的区别：识别不了的字返回 null（**不猜**），并列出没认出的字，
 * 让人工只补那一个字 —— 这比给一个错的卡名安全得多。
 */
export function matchNamesByLib(shapes, options = {}) {
  const lib = options.lib !== undefined && options.lib !== null ? options.lib : loadGlyphLib(options.libFile)
  if (!lib) {
    return { name: null, reason: '缺少真实字形库 lib/screenshot/glyph-lib.json（先跑 tools/build_glyph_lib.py）' }
  }
  if (!Array.isArray(shapes) || shapes.length === 0) {
    return { name: null, reason: '没有识别到字形' }
  }
  // 逐字在库里找最近的（库里没有该字就不可能认出来）
  const perChar = shapes.map((shape) => {
    const vec = shape.vector || gridToLibVector(shape.grid)
    const scored = Object.entries(lib.glyphs)
      .map(([ch, samples]) => {
        const sample = Array.isArray(samples[0]) ? samples[0] : samples
        return { ch, score: cosineSimilarity(vec, sample) }
      })
      .sort((a, b) => b.score - a.score)
    return scored
  })

  const minOverlap = Math.max(2, Math.ceil(shapes.length * 0.6))
  const scoredNames = []
  for (const card of Object.values(cards)) {
    const glyphChars = [...card.name].filter((ch) => lib.glyphs[ch])
    if (glyphChars.length < minOverlap) continue
    const span = glyphChars.length - shapes.length
    for (let start = Math.min(0, span); start <= Math.max(0, span); start += 1) {
      let total = 0
      let count = 0
      for (let i = 0; i < shapes.length; i += 1) {
        const j = start + i
        if (j < 0 || j >= glyphChars.length) continue
        const hit = perChar[i].find((c) => c.ch === glyphChars[j])
        if (!hit) continue
        total += hit.score
        count += 1
      }
      if (count < minOverlap) continue
      const cov = count / Math.max(shapes.length, glyphChars.length)
      const score = (total / count) * (0.6 + 0.4 * cov)
      const prev = scoredNames.find((s) => s.name === card.name)
      if (!prev || score > prev.score) {
        if (prev) prev.score = score
        else scoredNames.push({ name: card.name, baseCardId: card.baseCardId, score, matched: count, star: card.star, rarity: card.rarity })
      }
    }
  }
  scoredNames.sort((a, b) => b.score - a.score)
  const top = scoredNames[0]
  /**
   * 库外字（识别不了的字）：只统计**真正的汉字**。
   *
   * 两点经验（都来自实测）：
   *   · 标点（卡名里的「·」）不进字形库，必须排除，否则天天误报
   *   · 真实截图字形的余弦相似度本来就在 0.70~0.83 区间（不是 0.9+），
   *     阈值定 0.86 会把**全部**正确字都报成"不确定"；0.60 才是有效分界
   */
  const isHan = (ch) => /[\u3400-\u9fff\uf900-\ufaff]/.test(ch)
  const charThreshold = options.charThreshold ?? 0.60
  const unknownChars = perChar
    .map((c, i) => {
      const ch = c[0] ? c[0].ch : null
      const confident = c[0] && c[0].score >= charThreshold
      return confident || !ch || !isHan(ch) ? null : { index: i, guess: ch, score: Number(c[0].score.toFixed(3)) }
    })
    .filter((v) => v !== null)
  return {
    name: top ? top.name : null,
    score: top ? Number(top.score.toFixed(3)) : 0,
    margin: scoredNames.length > 1 ? Number((scoredNames[0].score - scoredNames[1].score).toFixed(3)) : null,
    candidates: scoredNames.slice(0, 5).map((c) => ({ ...c, score: Number(c.score.toFixed(3)) })),
    perChar: perChar.map((c) => c.slice(0, 3).map((x) => `${x.ch}:${x.score.toFixed(2)}`).join(' ')),
    charUnknown: unknownChars,
    // 实测：正确卡名的整名得分在 0.70~0.83（真实字形库），0.90 会全部误报「需确认」
    needsConfirm: unknownChars.length > 0 || !top || top.score < (options.confirmThreshold ?? 0.65),
    libChars: Object.keys(lib.glyphs).length,
    reason: unknownChars.length > 0
      ? `有 ${unknownChars.length} 个字不在字形库里（库内共 ${Object.keys(lib.glyphs).length} 字），需人工确认或补充标注截图`
      : '',
  }
}

/** 16x16 网格 → 相似度（0~1，1 完全相同） */
export function gridSimilarity(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return 0
  let same = 0
  for (let i = 0; i < a.length; i++) if (a[i] === b[i]) same += 1
  return same / a.length
}

/**
 * 把识别出的字形序列（stateJson.nameLabel.shapes）匹配成卡名。
 *
 * 关键点：卡名里可能有「·」（如「霸空武神·哪吒」），而游戏 UI 的卡名标签
 * 通常不显示间隔号，所以必须**忽略非文字字符**后再比长度，否则整个候选集为空。
 *
 * @returns {{ name: string|null, score: number, candidates: Array }}
 */
export function matchGlyphs(shapes, options = {}) {
  // 允许显式传 templates: null 来模拟「没有模板」的情况（测试用）
  const templates = options.templates !== undefined && options.templates !== null
    ? options.templates
    : loadGlyphTemplates(options.glyphFile)
  if (!templates) {
    return { name: null, score: 0, candidates: [], reason: '缺少字形模板 lib/screenshot/glyphs.json（先跑 tools/glyph_templates.py）' }
  }
  if (!Array.isArray(shapes) || shapes.length === 0) {
    return { name: null, score: 0, candidates: [], reason: '没有识别到字形' }
  }

  // 逐字给出候选（仅作调试展示）
  const perChar = shapes.map((shape) => Object.entries(templates.glyphs)
    .map(([ch, grid]) => ({ ch, score: gridSimilarity(shape.grid, grid) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 6))

  /**
   * 子串对齐打分：把识别到的字形序列在卡名里**逐位滑动**取最优对齐。
   *
   * 为什么不做「长度必须完全相等」：字形切分会受字体细节影响 ——
   * 「·」可能被丢掉、复杂字可能被多切一刀；严格等长会让候选集直接为空（实测踩过）。
   * 滑动对齐只要大部分字形对上就能定名，鲁棒得多。
   */
  const minOverlap = Math.max(2, Math.ceil(shapes.length * 0.6))
  const scoredNames = []
  for (const card of Object.values(cards)) {
    // 去掉「·」等模板里没有的字符
    const glyphChars = [...card.name].filter((ch) => templates.glyphs[ch])
    if (glyphChars.length < minOverlap) continue
    let best = null
    const span = glyphChars.length - shapes.length
    const startMin = Math.min(0, span)
    const startMax = Math.max(0, span)
    for (let start = startMin; start <= startMax; start += 1) {
      let total = 0
      let count = 0
      for (let i = 0; i < shapes.length; i += 1) {
        const j = start + i
        if (j < 0 || j >= glyphChars.length) continue
        const hit = perChar[i].find((c) => c.ch === glyphChars[j])
        if (!hit) continue
        total += hit.score
        count += 1
      }
      if (count < minOverlap) continue
      // 覆盖率惩罚：对上的字越少，总分按比例衰减，避免「短名字碰巧全中」
      const cov = count / Math.max(shapes.length, glyphChars.length)
      const score = (total / count) * (0.6 + 0.4 * cov)
      if (!best || score > best.score) best = { score, count, start }
    }
    if (best) {
      scoredNames.push({
        name: card.name,
        baseCardId: card.baseCardId,
        score: best.score,
        matched: best.count,
        start: best.start,
        attack: card.attack,
        health: card.health,
        star: card.star,
      })
    }
  }
  scoredNames.sort((a, b) => b.score - a.score)
  const top = scoredNames[0]
  return {
    name: top ? top.name : null,
    score: top ? Number(top.score.toFixed(3)) : 0,
    margin: scoredNames.length > 1 ? Number((scoredNames[0].score - scoredNames[1].score).toFixed(3)) : null,
    candidates: scoredNames.slice(0, 5).map((c) => ({ ...c, score: Number(c.score.toFixed(3)) })),
    perChar: perChar.map((c) => c.slice(0, 3).map((x) => `${x.ch}:${x.score.toFixed(2)}`).join(' ')),
    /** 分数低于该值时建议人工确认 */
    needsConfirm: !top || top.score < (options.confirmThreshold ?? 0.85),
    candidateCount: scoredNames.length,
    /** 匹配失败时的原因（成功时为空串），保证调用方总能读到原因字段 */
    reason: top ? '' : `没有与 ${shapes.length} 个字匹配的卡名（字形模板可能与该字体不匹配，需要按真实截图重新标定）`,
  }
}
