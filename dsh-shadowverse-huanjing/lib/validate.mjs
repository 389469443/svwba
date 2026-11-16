/**
 * 识别结果的校验与修复层（截图/OCR 出来的数据不可信，必须先过这一层）
 *
 * 职责：
 *   1. 数值合理性：随从数 ≤ 7、攻击力/生命值非负、星级 1~6、等级 ≥ 1
 *   2. 名称规范化：全角数字/空白、括号、OCR 常见混淆字符纠正
 *   3. 序号去重：同名随从加 #n 后缀，便于日志可读
 *   4. 待补规则清单：明确告诉上层/模型哪些规则还没校准
 */

import { MAX_BOARD, STAR_RANGE, normalizeBattle } from './battle.mjs'
import { normalizeAbilities, KEYWORDS } from './rules.mjs'

/** 常见 OCR 混淆：全角 -> 半角、易混符号 */
const CHAR_FIXES = [
  [/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)],
  [/[Ａ-Ｚａ-ｚ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)],
  [/[\u3000]/g, ' '],
  [/[（(]\s*[)）]/g, ''],
]

const NAME_REPLACERS = [
  [/\s+/g, ''],
  [/[，,、]/g, '·'],
]

export function normalizeName(raw) {
  let s = String(raw == null ? '' : raw)
  for (const [re, rep] of CHAR_FIXES) s = s.replace(re, rep)
  for (const [re, rep] of NAME_REPLACERS) s = s.replace(re, rep)
  return s.trim()
}

/** 把 "3/5"、"3 5"、"攻击3生命5" 之类的面板字符串拆成 { attack, health } */
export function parseStats(raw) {
  if (raw == null) return { attack: null, health: null }
  if (typeof raw === 'object') {
    return {
      attack: Number.isFinite(Number(raw.attack)) ? Math.trunc(Number(raw.attack)) : null,
      health: Number.isFinite(Number(raw.health)) ? Math.trunc(Number(raw.health)) : null,
    }
  }
  const s = String(raw).replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
  const nums = s.match(/\d+/g)
  if (!nums || nums.length === 0) return { attack: null, health: null }
  return {
    attack: Number(nums[0]),
    health: nums.length > 1 ? Number(nums[1]) : Number(nums[0]),
  }
}

/**
 * 校验并修复一方的随从列表。
 * @returns {{ board: object[], warnings: string[], errors: string[] }}
 */
export function sanitizeBoard(rawBoard, sideLabel = '随从区') {
  const warnings = []
  const errors = []
  if (!Array.isArray(rawBoard)) {
    errors.push(`${sideLabel}不是数组，已按空战场处理`)
    return { board: [], warnings, errors }
  }
  if (rawBoard.length > MAX_BOARD) {
    warnings.push(`${sideLabel}识别到 ${rawBoard.length} 个随从，超过上限 ${MAX_BOARD}，已截断（大概率是识别噪声）`)
  }
  const nameCount = new Map()
  const board = rawBoard.slice(0, MAX_BOARD).map((raw, index) => {
    const item = raw && typeof raw === 'object' ? raw : {}
    const stats = parseStats(item)
    let attack = stats.attack
    let health = stats.health
    if (attack == null || health == null) {
      warnings.push(`${sideLabel}第 ${index + 1} 个随从面板数值缺失，已按 0/0 兜底`)
      attack = attack == null ? 0 : attack
      health = health == null ? 0 : health
    }
    if (attack < 0 || health < 0) {
      warnings.push(`${sideLabel}第 ${index + 1} 个随从面板为负（${attack}/${health}），已按 0 处理`)
      attack = Math.max(0, attack)
      health = Math.max(0, health)
    }
    if (attack > 99 || health > 99) {
      warnings.push(`${sideLabel}第 ${index + 1} 个随从面板 ${attack}/${health} 异常偏大，疑似 OCR 串行，请人工确认`)
    }
    let star = Number.isFinite(Number(item.star)) ? Math.trunc(Number(item.star)) : 1
    if (star < STAR_RANGE[0] || star > STAR_RANGE[1]) {
      warnings.push(`${sideLabel}第 ${index + 1} 个随从星级 ${item.star} 超出 ${STAR_RANGE[0]}~${STAR_RANGE[1]}，已夹紧`)
      star = Math.min(STAR_RANGE[1], Math.max(STAR_RANGE[0], star))
    }
    let name = normalizeName(item.name) || `未知随从#${index + 1}`
    const seen = nameCount.get(name) || 0
    nameCount.set(name, seen + 1)
    if (seen > 0) name = `${name}#${seen + 1}`

    return {
      uid: item.uid,
      cardId: item.cardId ? String(item.cardId) : undefined,
      name,
      attack,
      health,
      star,
      abilities: normalizeAbilities(item.abilities),
      isEvolved: !!item.isEvolved,
      position: index,
      meta: item.meta,
    }
  })
  return { board, warnings, errors }
}

/**
 * 把「截图识别插件」输出的 GameState（3.1.3 的结构）转成模拟器输入。
 * 宽容处理字段别名（mainHero/hero、handCount/hand 等）。
 */
export function gameStateToBattle(gameState, options = {}) {
  if (!gameState || typeof gameState !== 'object') throw new Error('gameState 必须是对象')
  const pickSide = (side, label) => {
    const src = side && typeof side === 'object' ? side : {}
    const { board, warnings, errors } = sanitizeBoard(src.board, label)
    return {
      side: {
        hero: src.hero ?? src.mainHero ?? src.mainHeroName,
        heroSkill: src.heroSkill ?? src.skill,
        hp: Number.isFinite(Number(src.hp)) ? Number(src.hp) : 40,
        level: Number.isFinite(Number(src.level)) ? Number(src.level) : 1,
        gold: Number.isFinite(Number(src.gold)) ? Number(src.gold) : 0,
        evolutionCount: Number.isFinite(Number(src.evolutionCount)) ? Number(src.evolutionCount) : 0,
        board,
      },
      warnings,
      errors,
    }
  }
  const p = pickSide(gameState.player, '己方随从区')
  const o = pickSide(gameState.opponent, '对方随从区')
  return {
    battle: {
      turn: Number.isFinite(Number(gameState.turn)) ? Number(gameState.turn) : 1,
      phase: gameState.phase === 'battle' ? 'battle' : 'operate',
      player: p.side,
      opponent: o.side,
    },
    warnings: [...p.warnings, ...o.warnings],
    errors: [...p.errors, ...o.errors],
  }
}

/**
 * 当前引擎里**尚未实机校准**的规则点（给模型/UI 展示，避免把默认值当结论）。
 *
 * 以下规则已由实机信息确认，不再列在这里：
 *   · 伤害公式 = 胜方幻境等级 + 残存随从星级合计
 *   · 互相伤害（被攻击方对攻击方造成反击）
 *   · 守护不可被绕开
 *   · 先后手判定后双方交替行动
 *   · 毁灭无视剩余生命值与屏障直接击杀
 *   · 屏障免疫一次伤害（但挡不住毁灭）
 */
export const UNVERIFIED_RULES = [
  { path: 'targeting.mode', question: '随从在合法目标中是否等概率随机选择？（默认：等概率）', default: 'uniform' },
  { path: 'targeting.weighted', question: '若不等概率，权重规则是什么（生命值最低优先？站位最近优先？）', default: '未实现，需规则描述' },
  { path: 'turn.attackOrderSnapshot', question: '战斗中被召唤/复活的随从当回合能否行动？', default: '不能' },
  { path: 'end.maxRounds', question: '实际是否存在回合上限，以及超时如何判定？', default: '200 轮后比数量/星级' },
  { path: 'combat.requirePositiveAttack', question: '攻击力为 0 的随从是否完全无法行动（不反击也不被反击）？', default: '无法攻击但仍会被打' },
  { path: 'keywords', question: '完整关键词清单与精确判定（当前仅实现：' + Object.values(KEYWORDS).map((k) => k.label).join('、') + '）', default: '待补卡牌数据' },
  { path: 'heroes', question: '主战者技能在战斗阶段的实际结算（当前只有 transform/onAttack 等钩子，无具体技能）', default: '待补主战者数据' },
]

/** 一键校验：返回 { ok, battle, warnings, errors } */
export function validateBattleInput(input, options = {}) {
  const { battle, warnings } = normalizeBattle(input, options)
  return { ok: true, battle, warnings, errors: [] }
}
