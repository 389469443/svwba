/**
 * 知识库：把官方数据集（warchess_cards_cn.json / skill_icon_map.json）转成的
 * `lib/knowledge/*.json` 载入，并提供卡名反查、面板补全、能力覆盖度报告。
 *
 * 设计要点：
 *   · 只读本地 JSON，不联网、不读取游戏进程（插件与游戏完全独立，靠截图/手工输入）
 *   · 缺文件时全部降级为空库，不抛错（没数据也能跑模拟）
 *   · 卡名反查带 OCR 容错（去空白/全半角/括号差异）
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { normalizeAbilities, KEYWORDS } from './rules.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const KB_DIR = path.join(HERE, 'knowledge')

function loadJson(name, fallback) {
  try {
    const raw = fs.readFileSync(path.join(KB_DIR, name), 'utf8')
    const parsed = JSON.parse(raw)
    return parsed || fallback
  } catch (err) {
    return fallback
  }
}

const cardsFile = loadJson('cards.json', { cards: {}, meta: {} })
const heroesFile = loadJson('heroes.json', { heroes: {}, meta: {} })
const keywordsFile = loadJson('keywords.json', { keywords: {}, meta: {} })
const patchesFile = loadJson('patches.json', { items: [] })
const metaFile = loadJson('meta.json', {})

export const cards = cardsFile.cards || {}
export const heroes = heroesFile.heroes || {}
export const keywords = keywordsFile.keywords || {}
export const patches = patchesFile.items || []
export const meta = metaFile

export const isLoaded = Object.keys(cards).length > 0

/** 官方 Action -> 是否能被当前引擎建模 */
export const ENGINE_SUPPORTED_ACTIONS = [
  'guard', 'shield', 'killer', 'indestructible', 'powerup', 'damage', 'attack_count',
]

/** 官方 Timing -> 引擎时点（与 tools/build_knowledge.py 保持一致） */
export const TIMING_ENGINE = {
  when_play: 'fanfare',
  when_destroy: 'lastWord',
  when_evolve: 'evolve',
  when_super_evolve: 'superEvolve',
  when_attack: 'attack',
  when_damage: 'damage',
  when_fight: 'onFight',
  when_shield_loss: 'onShieldLoss',
  self_turn_end: 'prepare',
  self_turn_start: 'turnStart',
  battle_turn_start: 'battleStart',
  permanent: 'static',
  none: 'none',
}

// ── 卡名反查 ────────────────────────────────────────────────────────────────

/** 归一化卡名用于反查：去掉空白、标点、全半角差异 */
export function normalizeCardName(input) {
  return String(input == null ? '' : input)
    .replace(/[\u3000\s]/g, '')
    .replace(/[·・.。,，、!！?？:：'"“”‘’()（）\[\]【】\-—_]/g, '')
    .replace(/[Ａ-Ｚａ-ｚ０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .toLowerCase()
}

const NAME_INDEX = new Map()
for (const card of Object.values(cards)) {
  const keys = [card.name, ...(card.aliases || [])]
  for (const key of keys) {
    const norm = normalizeCardName(key)
    if (norm && !NAME_INDEX.has(norm)) NAME_INDEX.set(norm, card)
  }
}

/** 按名称查卡（带 OCR 容错）；找不到返回 null */
export function findCardByName(name) {
  if (!name) return null
  const norm = normalizeCardName(name)
  if (!norm) return null
  if (NAME_INDEX.has(norm)) return NAME_INDEX.get(norm)
  // 退化：包含匹配（OCR 常常多/少一两个字）
  for (const [key, card] of NAME_INDEX) {
    if (key.length >= 3 && (key.includes(norm) || norm.includes(key))) return card
  }
  return null
}

export function findCardById(id) {
  return cards[String(id)] || null
}

export function findHeroByName(name) {
  if (!name) return null
  const norm = normalizeCardName(name)
  for (const hero of Object.values(heroes)) {
    if (normalizeCardName(hero.name) === norm || normalizeCardName(hero.nameJa) === norm) return hero
  }
  for (const hero of Object.values(heroes)) {
    const hn = normalizeCardName(hero.name)
    if (hn && (hn.includes(norm) || norm.includes(hn))) return hero
  }
  return null
}

/**
 * 把一张卡（知识库条目或 OCR 名称）转成引擎的随从对象。
 * @param {object|string} input 卡对象 / 卡名
 * @param {object} [options] { star, evolved, superEvolved, tripleEvolved, attack, health, abilities }
 */
export function cardToFollower(input, options = {}) {
  const card = typeof input === 'string' ? findCardByName(input) : (input && input.baseCardId ? input : findCardByName(input && input.name))
  const star = Number.isFinite(Number(options.star)) ? Number(options.star) : (card ? card.star : 1)
  const base = card
    ? { attack: card.attack, health: card.health }
    : { attack: Number(options.attack) || 0, health: Number(options.health) || 0 }

  let attack = Number.isFinite(Number(options.attack)) ? Number(options.attack) : base.attack
  let health = Number.isFinite(Number(options.health)) ? Number(options.health) : base.health
  let isEvolved = !!options.evolved
  let isSuperEvolved = !!options.superEvolved
  let isTripleEvolved = !!options.tripleEvolved

  if (card && !Number.isFinite(Number(options.attack))) {
    // 注意：必须用 ?? 而不是 ||。官方数据里存在**攻击为 0** 的卡
    // （叮当天使·莉亚 0/2、涸绝的显现·吉尔内莉莎 0/3），
    // 用 || 会把 0 当成「缺失」，误取进化后的数值。
    if (isSuperEvolved) {
      attack = card.superEvolve.attack ?? card.evolve.attack
      health = card.superEvolve.health ?? card.evolve.health
      isEvolved = true
    } else if (isTripleEvolved) {
      attack = card.threeEvolve ? (card.threeEvolve.attack ?? card.evolve.attack) : card.evolve.attack
      health = card.threeEvolve ? (card.threeEvolve.health ?? card.evolve.health) : card.evolve.health
      isEvolved = true
    } else if (isEvolved) {
      attack = card.evolve.attack
      health = card.evolve.health
    }
  }

  const cardAbilities = card ? (card.abilities || []).map((id) => (KEYWORDS[id] ? id : id)) : []
  const abilities = options.abilities != null
    ? normalizeAbilities(options.abilities)
    : [...new Set([...cardAbilities, ...inferAbilitiesFromText(card)])]

  return {
    name: card ? card.name : String(input || '未知随从'),
    cardId: card ? String(card.baseCardId) : undefined,
    tribe: card ? card.tribe : undefined,
    star,
    attack: Math.max(0, attack),
    health: Math.max(0, health),
    abilities,
    isEvolved,
    isSuperEvolved,
    isTripleEvolved,
    canOnlyEvolveWithPoint: card ? hasSkillEvolve(card) : false,
    meta: card
      ? {
        unsupportedActions: card.unsupportedActions,
        text: card.text,
        skills: card.skills || [],
        tribe: card.tribe,
        rarity: card.rarity,
      }
      : undefined,
  }
}

/** 从中文描述里推断关键词（官方描述用【守护】这类标记） */
export function inferAbilitiesFromText(card) {
  if (!card || !card.text) return []
  const all = [card.text.normal, card.text.evolved, card.text.superEvolved, card.text.patched].filter(Boolean).join(' ')
  const found = []
  for (const kw of Object.values(KEYWORDS)) {
    if (all.includes(`【${kw.label}】`) || all.includes(kw.label)) {
      if (!found.includes(kw.id)) found.push(kw.id)
    }
  }
  return found
}

/** 该卡是否有「使用进化点时发动」的技能（when_skill_evolve / when_evolve） */
export function hasSkillEvolve(card) {
  if (!card || !Array.isArray(card.skills)) return false
  return card.skills.some((s) => s.timing === 'when_skill_evolve' || s.timing === 'when_evolve')
}

/** 该卡是否有超进化技能 */
export function hasSuperEvolveSkill(card) {
  if (!card || !Array.isArray(card.skills)) return false
  return card.skills.some((s) => s.timing === 'when_super_evolve')
}

// ── 覆盖度报告（诚实告诉用户哪些能力还没建模）─────────────────────────────

/**
 * 生成能力建模覆盖度报告。
 * @param {object} [options] { unsupportedOnly }
 */
export function coverageReport(options = {}) {
  const byAction = new Map()
  const partiallySupported = []
  const fullySupported = []
  const unsupportedCards = []

  for (const card of Object.values(cards)) {
    const skills = card.skills || []
    if (skills.length === 0) { fullySupported.push(card); continue }
    const bad = skills.filter((s) => !ENGINE_SUPPORTED_ACTIONS.includes(s.action))
    for (const s of bad) {
      const entry = byAction.get(s.action) || { action: s.action, label: s.actionLabel, count: 0, samples: [] }
      entry.count += 1
      if (entry.samples.length < 3) entry.samples.push(`${card.name}（${card.star}星）`)
      byAction.set(s.action, entry)
    }
    if (bad.length === 0) fullySupported.push(card)
    else if (bad.length < skills.length) partiallySupported.push({ card, bad })
    else unsupportedCards.push({ card, bad })
  }

  const actions = [...byAction.values()].sort((a, b) => b.count - a.count)
  return {
    ok: true,
    knowledgeLoaded: isLoaded,
    sourceVersion: meta.sourceVersion,
    totals: {
      cards: Object.keys(cards).length,
      heroes: Object.keys(heroes).length,
      keywords: Object.keys(keywords).length,
      skills: Object.values(cards).reduce((s, c) => s + (c.skills || []).length, 0),
    },
    /** 引擎能完整建模的卡 */
    fullyModeled: fullySupported.length,
    /** 部分建模（有部分效果未实现） */
    partiallyModeled: partiallySupported.length,
    /** 完全未建模 */
    notModeled: unsupportedCards.length,
    /** 未建模的能力类型排行 */
    unsupportedActions: actions,
    partiallySupported: options.unsupportedOnly ? undefined : partiallySupported.slice(0, 30).map((x) => ({
      name: x.card.name, star: x.card.star, unsupported: x.bad.map((s) => s.action),
    })),
    note: '未建模的能力只做标注，不参与模拟；因此含这些能力的对局结论会偏乐观/偏保守。',
  }
}

/**
 * 用知识库补全一份局面（截图/手工输入的随从只给了名字时，自动补面板与能力）。
 * @returns {{ battle: object, resolved: object[], unresolved: object[] }}
 */
export function enrichBattle(input) {
  const resolved = []
  const unresolved = []
  const convertSide = (side) => {
    if (!side || !Array.isArray(side.board)) return side
    const board = side.board.map((f, index) => {
      const card = f.cardId ? findCardById(f.cardId) : findCardByName(f.name)
      if (!card) {
        unresolved.push({ side: side === input.player ? 'player' : 'opponent', index, name: f.name })
        return f
      }
      resolved.push({ name: card.name, baseCardId: card.baseCardId, star: f.star || card.star })
      const converted = cardToFollower(card, {
        star: f.star,
        evolved: f.isEvolved,
        superEvolved: f.isSuperEvolved,
        tripleEvolved: f.isTripleEvolved,
        attack: f.attack === undefined || f.attack === null ? undefined : f.attack,
        health: f.health === undefined || f.health === null ? undefined : f.health,
        abilities: f.abilities,
      })
      return { ...converted, position: index }
    })
    return { ...side, board }
  }
  return { battle: { ...input, player: convertSide(input.player), opponent: convertSide(input.opponent) }, resolved, unresolved }
}

/** 知识库摘要（给 UI / 工具返回） */
export function knowledgeSummary() {
  const byTribe = new Map()
  const byStar = new Map()
  for (const card of Object.values(cards)) {
    byTribe.set(card.tribe, (byTribe.get(card.tribe) || 0) + 1)
    byStar.set(card.star, (byStar.get(card.star) || 0) + 1)
  }
  return {
    loaded: isLoaded,
    source: { client: meta.sourceClient, version: meta.sourceVersion, updatedAt: meta.sourceUpdatedAt },
    counts: meta.counts,
    byTribe: Object.fromEntries([...byTribe.entries()].sort((a, b) => String(a[0]).localeCompare(String(b[0])))),
    byStar: Object.fromEntries([...byStar.entries()].sort((a, b) => a[0] - b[0])),
    patches: patches.length,
    unsupportedActions: meta.unsupportedActions,
  }
}
