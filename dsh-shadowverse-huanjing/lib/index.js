/**
 * DSH 宿主插件：影之诗·影之幻境 战斗模拟与胜率分析
 *
 * 双重暴露：
 *   1) 工具（如果宿主注入了工具服务）—— 让模型直接调用
 *   2) HTTP 路由 /shadow/huanjing/*（如果宿主注入了 webServer）—— 用于自检、外部脚本、
 *      以及工具服务不可用时的兜底。参考 DSH 生态既有插件（webServer.register + ctx.effect）的写法。
 *
 * 所有能力都用「能力探测」注册：宿主没有对应服务时降级跳过并打日志，
 * 不会因为某一种宿主版本缺少某个服务而让插件崩溃。
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'

import { runSimulation, summarize, bayesianFusion, suggestSimulationCount } from './simulate.mjs'
import { normalizeBattle, resolveFirstSide } from './battle.mjs'
import { resolveRules, DEFAULT_RULES, KEYWORDS } from './rules.mjs'
import { gameStateToBattle, UNVERIFIED_RULES } from './validate.mjs'
import { fuseTriples, createDiscoverOptions } from './mechanics.mjs'
import {
  isLoaded as knowledgeLoaded, knowledgeSummary, findCardByName, findHeroByName,
  cardToFollower, coverageReport, enrichBattle, cards,
} from './knowledge.mjs'
import { buildSkillHooks, skillCoverage, compileAllSkills, setSkillRegistry, setCardPool } from './skills.mjs'
import { applyHeroSkills } from './heroskills.mjs'
import {
  stateJsonToBattle, recognitionQuality, findCardsByStats, statAmbiguity, matchGlyphs,
  matchNamesByLib, loadGlyphLib, rarityName, RARITY_NAMES,
} from './recognizer.mjs'
import { buildWinRateReport, requiredSampleSize } from './winrate.mjs'
import { Prng } from './rng.mjs'

const DSH_HOME = process.env.DSH_HOME || path.join(os.homedir(), '.dsh')
const DATA_DIR = path.join(DSH_HOME, 'huanjing')
const HISTORY_FILE = path.join(DATA_DIR, 'history.json')
const PLUGIN_VERSION = '0.1.0'
export const ROUTE_PREFIX = '/shadow/huanjing'

export const name = 'dsh-shadowverse-huanjing'
/**
 * 只把 webServer 声明为必需（与 DSH 生态既有插件一致）；
 * 工具服务通过能力探测访问，不写进 inject —— 避免宿主版本缺该服务时整个插件被拒绝加载。
 */
export const inject = ['webServer']

/** 最近一次「战斗开始」自动分析的结果（供路由/调试读取） */
let lastAnalysis = null

// ─────────────────────────────────────────────────────────────────────────────
// 工具定义（DSH 工具服务的入参 Schema）
// ─────────────────────────────────────────────────────────────────────────────

export const TOOL_DEFS = [
  {
    name: 'huanjing_simulate_battle',
    description:
      '影之幻境战斗蒙特卡洛模拟：输入双方战场（随从数量/攻击力/生命值/星级/能力、主战者、幻境等级），' +
      '返回胜率、先后手判定依据、对双方主战者的伤害分布（伤害 = 胜方幻境等级 + 残存随从星级合计）、' +
      '预计掉血与结论。用于「这波打得过吗、会掉多少血」。',
    parameters: {
      battle: {
        type: 'object',
        required: true,
        description: '局面：{ player:{hp,level,board[]}, opponent:{hp,level,board[]} }；'
          + 'board 项为 {name,attack,health,star,abilities[]}（star 为随从等级 1~6、level 为主战者幻境等级）',
      },
      simulations: { type: 'number', description: '模拟次数，默认按局面复杂度自动建议（5000~20000）' },
      rules: { type: 'object', description: '规则覆盖（先后手顺序、伤害公式、关键词开关等），不传用默认' },
      seed: { type: 'string', description: '随机种子，用于复现同一结果' },
    },
  },
  {
    name: 'huanjing_first_side',
    description: '影之幻境先后手判定：按随从数量 → 幻境等级 → 星级合计 → 随机的顺序判定谁先手，返回判定依据。',
    parameters: {
      battle: { type: 'object', description: '局面（同 huanjing_simulate_battle）', required: true },
    },
  },
  {
    name: 'huanjing_recommend',
    description:
      '基于当前局面给出运营建议：估算战力差距、判断是否值得投入/该补什么（当前为启发式版本，' +
      '卡牌与阵容知识库待接入后升级为检索式推荐）。',
    parameters: {
      battle: { type: 'object', description: '局面', required: true },
      gold: { type: 'number', description: '可用金币，用于判断能否升星/刷新' },
    },
  },
  {
    name: 'huanjing_validate_board',
    description: '校验并修复（截图/OCR 识别出的）战场数据：随从数 ≤7、面板非负、星级 1~6，返回修复后的局面与告警。',
    parameters: {
      gameState: { type: 'object', description: '截图识别插件的 GameState 输出', required: true },
    },
  },
  {
    name: 'huanjing_rulebook',
    description: '返回当前模拟引擎使用的完整规则表、默认值与「尚未实机校准的规则点」清单。',
    parameters: {},
  },
  {
    name: 'huanjing_fuse_history',
    description: '贝叶斯融合：把模拟先验胜率与历史对局似然合成为后验胜率（Beta-二项共轭）。',
    parameters: {
      simulations: { type: 'object', description: '模拟结果或直接给 {player: 胜率0~1}', required: true },
      history: { type: 'object', description: '{wins, losses, draws} 相似局面的历史战绩', required: true },
      priorWeight: { type: 'number', description: '先验等价样本量，默认 200' },
    },
  },
  {
    name: 'huanjing_operate',
    description:
      '影之幻境运营阶段机制计算：三连合成（3 张相同进化前 → 1 张进化后）与「发现」预演' +
      '（打出进化后随从时，随机 3 张「当前幻境等级 + 1」的随从选 1）。',
    parameters: {
      action: { type: 'string', description: 'fuseTriples（三连合成）或 discover（生成发现选项）', required: true },
      hand: { type: 'array', description: 'fuseTriples 用：手牌数组 [{cardId,name,isEvolved?}]' },
      evolvedMap: { type: 'object', description: 'fuseTriples 用：{ 进化前id: 进化后卡牌对象 } 映射' },
      level: { type: 'number', description: 'discover 用：当前幻境等级' },
      cardPool: { type: 'array', description: 'discover 用：可选卡池（元素带 star 表示随从等级）' },
    },
  },
  {
    name: 'huanjing_knowledge',
    description: '返回已载入的官方卡表概览：来源版本、卡牌/主战者/关键词数量、按兵种与星级分布、已应用的平衡性调整条数。',
    parameters: {},
  },
  {
    name: 'huanjing_lookup',
    description: '按名称查询官方数据：随从卡（面板/进化与超进化数值/能力文本/技能）或主战者（职业技能文本）。',
    parameters: {
      name: { type: 'string', description: '卡牌名或主战者名（支持 OCR 容错）', required: true },
    },
  },
  {
    name: 'huanjing_coverage',
    description: '能力建模覆盖度报告：哪些卡牌能力已被引擎建模、哪些只做标注未参与模拟，以及原因清单。',
    parameters: {
      battle: { type: 'object', description: '可选：给定局面时，额外给出这一局的技能覆盖率' },
      unsupportedOnly: { type: 'boolean', description: '只返回未建模的能力类型排行' },
    },
  },
  {
    name: 'huanjing_stats',
    description: '胜率统计报告（HSReplay 酒馆战棋口径）：平均排名、前四率、选取率、分档（S/A/B/C）、'
      + '小样本贝叶斯修正、Wilson 置信区间。不传 matches 就用本机历史记录。',
    parameters: {
      matches: { type: 'array', description: '对局记录 [{finalRank, playerHero, comp, tribe}]；不传则用本机历史' },
      playersPerMatch: { type: 'number', description: '每局人数，默认 8' },
      minSamples: { type: 'number', description: '上榜单的最少样本数，默认 20' },
      priorStrength: { type: 'number', description: '贝叶斯先验强度（等效局数），默认 10' },
      compare: { type: 'object', description: '可选：{p1,p2} 算需要多少局才能区分两个胜率' },
    },
  },
  {
    name: 'huanjing_recognize',
    description:
      '截图识别结果处理：把识别出的「攻/血」读数转成候选卡名（官方卡表反查），'
      + '或把完整识别结果（Python 侧 recognize_shot.py 的 state JSON）转成可直接模拟的局面。'
      + '攻/血唯一对应的卡会自动定名，对应多张卡时给出候选并需确认。',
    parameters: {
      slots: { type: 'array', description: '只要候选时用：[{attack, health}, ...]' },
      state: { type: 'object', description: '完整识别结果（含 player/opponent/nameLabel）' },
      nameOverrides: { type: 'object', description: '人工指定的卡名，如 {"player-0":"纯真的水之妖精"}' },
    },
  },
]

// ─────────────────────────────────────────────────────────────────────────────
// 业务实现（工具与路由共用）
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 统一入口：把局面交给知识库补全（只给卡名也能算），并编译技能钩子。
 * 返回 { battle, hooks, coverage, resolved, unresolved }
 */
/**
 * 首次使用前把全库技能按 skillId 灌进注册表。
 * attach_skill / overwrite_skill 需要按 id 找另一条技能。
 */
let __skillRegistryReady = false
function ensureSkillRegistry() {
  if (__skillRegistryReady) return
  __skillRegistryReady = true
  try {
    const all = []
    for (const c of Object.values(cards || {})) {
      for (const s of (c.skills || [])) all.push(s)
    }
    setSkillRegistry(all)
    // 全卡池：按条件随机召唤（尤娜/树海的战士/暗之法则）要用
    setCardPool(Object.entries(cards || {}).map(([id, c]) => ({ ...c, __id: Number(id) })))
  } catch (e) { /* 忽略 */ }
}

export function prepareBattle(input, options = {}) {
  ensureSkillRegistry()
  if (!input || typeof input !== 'object') throw new Error('缺少 battle 参数')
  if (!input.player && !input.opponent) throw new Error('battle 必须包含 player / opponent')

  let battle = input
  let coverageHero = null
  let heroHooks = null
  let resolved = []
  let unresolved = []
  if (knowledgeLoaded && options.enrich !== false) {
    const enriched = enrichBattle(input)
    battle = enriched.battle
    resolved = enriched.resolved
    unresolved = enriched.unresolved
  }
  const { battle: normalized, warnings } = normalizeBattle(battle)

  // 主战者技能：加面板的那类已经在读到的数据里，
  // 加**能力**的（谢幕曲等）读不到，必须在这里补。
  try {
    const heroRes = applyHeroSkills(normalized, {
      playerHero: normalized.player && normalized.player.hero,
      opponentHero: normalized.opponent && normalized.opponent.hero,
      cards,
    })
    coverageHero = (heroRes && heroRes.report) || null
    if (heroRes && heroRes.hooks) heroHooks = heroRes.hooks
  } catch (e) { /* 主战者技能失败不影响模拟 */ }

  let hooks = options.hooks
  let coverage = null
  if (options.skills !== false) {
    const built = buildSkillHooks(normalized)
    coverage = {
      compiled: built.compiled,
      skipped: built.skipped.length,
      ratio: built.compiled + built.skipped.length > 0
        ? built.compiled / (built.compiled + built.skipped.length)
        : 1,
      details: built.skipped.slice(0, 20),
    }
    if (built.compiled > 0) {
      hooks = options.hooks ? { ...built.hooks, ...options.hooks } : built.hooks
    }
    // 主战者的 hook 型效果（埃斯佩兰莎）合并进来
    if (heroHooks) {
      for (const [k, v] of Object.entries(heroHooks)) {
        const prev = hooks && hooks[k]
        if (!hooks) hooks = {}
        hooks[k] = prev ? (...a) => { prev(...a); v(...a) } : v
      }
    }
  }
  return { battle: normalized, warnings, hooks, coverage, resolved, unresolved, heroSkills: coverageHero }
}

function toBattle(input, options = {}) {
  return prepareBattle(input, options)
}

export function toolSimulate(args = {}) {
  const prepared = toBattle(args.battle, { skills: args.useSkills !== false })
  const battle = prepared.battle
  const simulations = Number.isFinite(Number(args.simulations)) && Number(args.simulations) > 0
    ? Math.trunc(Number(args.simulations))
    : suggestSimulationCount(battle)
  const result = runSimulation(battle, {
    simulations,
    rules: args.rules,
    seed: args.seed,
    hooks: prepared.hooks,
  })
  return {
    ...result,
    knowledge: {
      loaded: knowledgeLoaded,
      resolvedCards: prepared.resolved.length,
      unresolvedCards: prepared.unresolved,
      skillCoverage: prepared.coverage,
    },
    warnings: [...(result.warnings || []), ...prepared.warnings],
    summary: summarize(result),
  }
}

export function toolFirstSide(args = {}) {
  const { battle } = toBattle(args.battle, { skills: false })
  const rules = resolveRules(args.rules)
  const decided = resolveFirstSide(battle, rules, args.seed)
  return {
    firstSide: decided.firstSide,
    firstLabel: decided.firstSide === 'A' ? '己方' : '对方',
    reason: decided.reason,
    detail: decided.detail,
    metrics: {
      player: { followerCount: battle.player.board.length, level: battle.player.level, starSum: starSum(battle.player) },
      opponent: { followerCount: battle.opponent.board.length, level: battle.opponent.level, starSum: starSum(battle.opponent) },
    },
  }
}

function starSum(side) {
  return side.board.reduce((s, f) => s + (Number(f.star) || 1), 0)
}

/** 启发式运营建议（Phase 3 会替换成知识库检索式推荐） */
export function toolRecommend(args = {}) {
  const prepared = toBattle(args.battle, { skills: args.useSkills !== false })
  const battle = prepared.battle
  const result = runSimulation(battle, {
    simulations: Number(args.simulations) || 8000,
    rules: args.rules,
    hooks: prepared.hooks,
  })
  const gold = Number.isFinite(Number(args.gold)) ? Number(args.gold) : battle.player.gold
  const advice = []
  const myCount = battle.player.board.length
  const theirCount = battle.opponent.board.length
  const myStars = starSum(battle.player)
  const theirStars = starSum(battle.opponent)

  if (myCount < 7) advice.push(`战场未满（${myCount}/7），优先补随从数量：数量既是先手判据，也是清场能力`)
  if (myStars < theirStars) advice.push(`星级合计落后（${myStars} vs ${theirStars}），三星化收益高于铺新卡`)
  if (theirCount > myCount) advice.push(`对方随从数占优，注意先后手会判给对方，需要靠关键词（守护/毁灭/屏障）弥补`)
  if (result.winRate.player >= 0.65) advice.push('当前胜率占优，可考虑保守运营存钱，等强势回合再爆发')
  else if (result.winRate.player <= 0.4) advice.push('当前胜率劣势，优先保命：补充守护/高生命随从降低掉血')
  if (gold >= 3) advice.push(`金币 ${gold}：可用于刷新或升星，注意保留升幻境等级的费用`)
  if (!advice.length) advice.push('局面均衡，按阵容核心卡继续升星即可')

  return {
    winRate: result.winRate,
    expectedDamage: result.expectedDamage,
    advice,
    basis: { simulations: result.simulations, rulesVersion: result.version },
    skillCoverage: prepared.coverage,
    disclaimer: '当前为启发式建议（基于局面统计特征），卡牌/阵容知识库已接入（面板与部分能力），完整阵容推荐仍待完善',
  }
}

/** 知识库概览（卡表/主战者/关键词/平衡调整） */
export function toolKnowledge() {
  return { ok: true, ...knowledgeSummary() }
}

/**
 * 胜率统计报告（HSReplay 酒馆战棋口径）：
 * 平均排名 / 前四率 / 选取率 / 分档 / 小样本修正 / 置信区间。
 *
 * 不传 matches 时用本机历史记录（`~/.dsh/.../history.json`）。
 */
export function toolStats(args = {}) {
  const options = {
    playersPerMatch: Number(args.playersPerMatch) || 8,
    minSamples: Number(args.minSamples) || 20,
    priorStrength: Number(args.priorStrength) || 10,
  }
  const source = Array.isArray(args.matches) && args.matches.length
    ? args.matches
    : (readHistory().matches || [])
  const report = buildWinRateReport(source, options)
  const out = {
    ok: true,
    source: Array.isArray(args.matches) && args.matches.length ? 'arguments' : 'local-history',
    options,
    ...report,
  }
  if (args.compare) {
    out.requiredSample = requiredSampleSize(Number(args.compare.p1) || 0.5, Number(args.compare.p2) || 0.55)
  }
  return out
}

/**
 * 截图识别：把「攻/血」读数转成候选卡名，或把识别结果转成可直接模拟的局面。
 *
 * 输入两种形态：
 *   1) 只要候选：{ slots: [{ attack, health }, ...] }
 *   2) 完整结果：Python 侧 recognize_shot.py 输出的 state JSON（含 player/opponent/nameLabel）
 */
export function toolRecognize(args = {}) {
  const overrides = args.nameOverrides && typeof args.nameOverrides === 'object' ? args.nameOverrides : {}

  if (Array.isArray(args.slots)) {
    const rows = args.slots.map((slot, index) => {
      const atk = Number(slot.attack)
      const life = Number(slot.health)
      const candidates = findCardsByStats(atk, life)
      return {
        slot: index, attack: atk, health: life,
        resolved: candidates.length === 1 ? candidates[0].name : null,
        candidates: candidates.map((c) => ({ name: c.name, star: c.star, tribe: c.tribe, baseCardId: c.baseCardId })),
        needConfirm: candidates.length !== 1,
      }
    })
    return { ok: true, mode: 'slots', slots: rows, ambiguity: statAmbiguity() }
  }

  if (args.state && typeof args.state === 'object') {
    const quality = recognitionQuality(args.state, { nameOverrides: overrides })
    const { battle, unresolved, warnings } = stateJsonToBattle(args.state, { nameOverrides: overrides })
    // 卡名识别优先级：真实字形库（主） > 字体渲染模板（辅）
    // 面板**不参与**定名 —— 本局有加面板的效果，面板早已不等于基础面板（用户明确要求）
    const labelShapes = args.state.nameLabel && args.state.nameLabel.shapes
    const libResult = labelShapes ? matchNamesByLib(labelShapes, { confirmThreshold: args.nameConfirmThreshold }) : null
    const glyphFallback = !libResult || !libResult.name
      ? (labelShapes ? matchGlyphs(labelShapes, { confirmThreshold: args.nameConfirmThreshold }) : null)
      : null
    return {
      ok: true,
      mode: 'state',
      battle,
      resolved: quality.autoMatched,
      needConfirm: quality.needConfirm,
      unresolved,
      warnings,
      /** 卡名识别结果（真实字形库优先） */
      nameLabel: libResult || glyphFallback,
      nameLabelMethod: libResult && libResult.name ? 'glyph-lib（真实截图字形库）'
        : (glyphFallback && glyphFallback.name ? 'font-template（字体模板，需人工确认）' : null),
      glyphLib: libResult ? { chars: libResult.libChars, unknown: (libResult.charUnknown || []).length } : null,
      ambiguity: quality.ambiguity,
      hint: '把 battle 直接交给 huanjing_simulate_battle 即可出胜率；卡名定不下来时用 nameOverrides 指定。'
        + '注意：不要用面板反查卡名（本局有加面板的效果）。',
    }
  }

  throw new Error('需要 slots 或 state 参数')
}

/** 能力建模覆盖度：哪些效果已建模、哪些只是标注 */
export function toolCoverage(args = {}) {
  const report = coverageReport({ unsupportedOnly: !!args.unsupportedOnly })
  report.skillCompilation = compileAllSkills(cards)
  report.battleOnlyRule = '只有战斗阶段会发动的效果参与模拟；入场曲/备战/回合结束等运营时点一律不计。'
  if (args.battle) {
    const prepared = toBattle(args.battle, { skills: false })
    report.thisBattle = skillCoverage(prepared.battle)
  }
  return report
}

/** 按卡名/主战者名查询官方数据 */
export function toolLookup(args = {}) {
  const name = String(args.name || '').trim()
  if (!name) throw new Error('缺少 name 参数')
  const card = findCardByName(name)
  if (card) {
    return {
      ok: true,
      kind: 'card',
      card: {
        baseCardId: card.baseCardId,
        name: card.name,
        tribe: card.tribe,
        star: card.star,
        attack: card.attack,
        health: card.health,
        evolve: card.evolve,
        superEvolve: card.superEvolve,
        threeEvolve: card.threeEvolve,
        abilities: card.abilities,
        text: card.text,
        skills: (card.skills || []).map((s) => ({
          timing: s.timing, action: s.action, actionLabel: s.actionLabel,
          option: s.option, engineSupported: s.engineSupported,
        })),
      },
    }
  }
  const hero = findHeroByName(name)
  if (hero) {
    return {
      ok: true,
      kind: 'hero',
      hero: {
        heroId: hero.heroId, name: hero.name, profession: hero.profession,
        life: hero.life, skillText: hero.skillText, placeholder: hero.placeholder,
      },
    }
  }
  return { ok: false, error: `知识库里没有「${name}」` }
}

export function toolValidate(args = {}) {
  const src = args.gameState && typeof args.gameState === 'object' ? args.gameState : args
  const converted = gameStateToBattle(src)
  return {
    ok: converted.errors.length === 0,
    battle: converted.battle,
    warnings: converted.warnings,
    errors: converted.errors,
  }
}

export function toolRulebook() {
  return {
    version: PLUGIN_VERSION,
    rulesVersion: DEFAULT_RULES.version,
    defaults: DEFAULT_RULES,
    keywords: Object.values(KEYWORDS).map((k) => ({ id: k.id, label: k.label, aliases: k.aliases })),
    unverified: UNVERIFIED_RULES,
    note: 'unverified 里的规则点尚未实机校准，模拟结论受其影响；拿到实机公式后通过 rules 覆盖或改 rules.mjs 默认值。',
  }
}

export function toolFuse(args = {}) {
  const sim = args.simulations
  const prior = typeof sim === 'number' ? { player: sim } : (sim?.winRate || sim || {})
  return bayesianFusion(prior, args.history || {}, { priorWeight: Number(args.priorWeight) || 200 })
}

/**
 * 运营阶段机制：三连合成 / 发现预演。
 *
 * ⚠️ 目前没有卡牌知识库，`discover` 只能吃调用方传入的卡池（`cardPool`）；
 *    卡表接入后会自动改用本地卡池。合成映射同理（`evolvedCardOf` 缺省时只加标记）。
 */
export function toolOperate(args = {}) {
  const action = String(args.action || '').trim()
  const rules = resolveRules(args.rules)

  if (action === 'fuseTriples' || action === 'fuse') {
    const evolvedMap = args.evolvedMap && typeof args.evolvedMap === 'object' ? args.evolvedMap : null
    const cardPool = Array.isArray(args.cardPool) ? args.cardPool : []
    const byId = new Map()
    for (const card of cardPool) {
      const key = card?.cardId || card?.name
      if (key) byId.set(key, card)
    }
    // 支持两种写法：显式 evolvedMap，或卡池里带 evolved 字段的卡
    const lookup = (card) => {
      const key = card.cardId || card.name
      if (evolvedMap && evolvedMap[key]) return evolvedMap[key]
      const base = byId.get(key)
      if (base && base.evolved && typeof base.evolved === 'object') return base.evolved
      return null
    }
    const hasMapping = !!evolvedMap || cardPool.some((c) => c && c.evolved)
    const result = fuseTriples(args.hand || [], {
      triplesToEvolve: rules.fusion.triplesToEvolve,
      evolvedCardOf: hasMapping ? lookup : null,
    })
    return {
      ok: true,
      action: 'fuseTriples',
      needPerTriple: rules.fusion.triplesToEvolve,
      created: result.created,
      consumedCount: result.consumed.length,
      hand: result.hand,
      note: hasMapping
        ? '已解析「进化后」卡牌（来自 evolvedMap 或卡池的 evolved 字段）'
        : '未提供进化后映射（evolvedMap / 卡池 evolved 字段），合成结果只标记 isEvolved（等卡表接入）',
    }
  }

  if (action === 'discover') {
    const pool = Array.isArray(args.cardPool) ? args.cardPool : []
    const options = createDiscoverOptions(args.level ?? 1, pool, new Prng(args.seed ?? rules.rng.seed), {
      choices: rules.fusion.discover.choices,
      levelOffset: rules.fusion.discover.levelOffset,
    })
    return {
      ok: true,
      action: 'discover',
      ...options,
      note: pool.length === 0 ? '未提供卡池（cardPool），等卡表接入后可自动按幻境等级取牌' : undefined,
    }
  }

  throw new Error(`未知的 action: ${action || '(空)'}（支持 fuseTriples / discover）`)
}

const TOOL_IMPL = {
  huanjing_simulate_battle: toolSimulate,
  huanjing_first_side: toolFirstSide,
  huanjing_recommend: toolRecommend,
  huanjing_validate_board: toolValidate,
  huanjing_rulebook: toolRulebook,
  huanjing_fuse_history: toolFuse,
  huanjing_operate: toolOperate,
  huanjing_knowledge: toolKnowledge,
  huanjing_lookup: toolLookup,
  huanjing_coverage: toolCoverage,
  huanjing_stats: toolStats,
  huanjing_recognize: toolRecognize,
}

// ─────────────────────────────────────────────────────────────────────────────
// 历史数据（贝叶斯融合的似然来源，本地留档；上传走 Phase 4）
// ─────────────────────────────────────────────────────────────────────────────

function ensureDataDir() {
  try { fs.mkdirSync(DATA_DIR, { recursive: true }) } catch (err) { /* 只读环境降级 */ }
}

export function readHistory() {
  try {
    const raw = fs.readFileSync(HISTORY_FILE, 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') return parsed
  } catch (err) { /* 首次运行无文件 */ }
  return { version: 1, matches: [] }
}

/**
 * 记录一局对局（去重：同 matchId 只留一条）。
 * 只保存统计所需字段，不含账号/设备标识。
 */
export function recordMatch(record) {
  const history = readHistory()
  const entry = {
    matchId: String(record.matchId || createHash('sha1').update(JSON.stringify(record)).digest('hex').slice(0, 16)),
    timestamp: Number(record.timestamp) || Date.now(),
    playerHero: record.playerHero ? String(record.playerHero) : undefined,
    finalRank: Number(record.finalRank) || undefined,
    level: Number(record.level) || undefined,
    evolutionCount: Number(record.evolutionCount) || undefined,
    coreCards: Array.isArray(record.coreCards) ? record.coreCards.map(String).slice(0, 12) : [],
    finalBoard: Array.isArray(record.finalBoard)
      ? record.finalBoard.slice(0, 7).map((f) => ({
        name: f?.name ? String(f.name) : undefined,
        star: Number(f?.star) || 1,
        attack: Number(f?.attack) || 0,
        health: Number(f?.health) || 0,
        abilities: Array.isArray(f?.abilities) ? f.abilities.map(String) : [],
      }))
      : [],
    version: record.version ? String(record.version) : `plugin-${PLUGIN_VERSION}`,
    signature: signatureOf(record),
  }
  const matches = [entry, ...(history.matches || []).filter((m) => m.matchId !== entry.matchId)].slice(0, 5000)
  const next = { version: 1, updatedAt: Date.now(), matches }
  ensureDataDir()
  try {
    fs.writeFileSync(HISTORY_FILE, JSON.stringify(next, null, 2), 'utf8')
  } catch (err) {
    return { ok: false, error: `历史写入失败：${err.message}`, entry }
  }
  return { ok: true, entry, total: matches.length }
}

/** 阵容指纹：主战者 + 排序后的核心卡名，用作“相似局面”的键 */
export function signatureOf(record) {
  const cards = [
    ...(Array.isArray(record.coreCards) ? record.coreCards : []),
    ...(Array.isArray(record.finalBoard) ? record.finalBoard.map((f) => f?.name).filter(Boolean) : []),
  ].map((s) => String(s)).sort()
  const uniq = [...new Set(cards)].slice(0, 10)
  return createHash('sha1').update(`${record.playerHero || '?'}|${uniq.join(',')}`).digest('hex').slice(0, 12)
}

/** 按指纹统计历史战绩（贝叶斯融合的似然输入） */
export function historyStats(record) {
  const history = readHistory()
  const sig = signatureOf(record || {})
  const same = (history.matches || []).filter((m) => m.signature === sig)
  const wins = same.filter((m) => m.finalRank === 1).length
  const losses = same.length - wins
  const top4 = same.filter((m) => m.finalRank >= 1 && m.finalRank <= 4).length
  return {
    signature: sig,
    matches: same.length,
    wins,
    losses,
    draws: 0,
    top4Rate: same.length ? top4 / same.length : 0,
    avgRank: same.length ? same.reduce((s, m) => s + (m.finalRank || 0), 0) / same.length : 0,
    note: '历史样本来自本机记录（非上传聚合）；Phase 4 服务端聚合接入后替换为全局统计',
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 插件主体
// ─────────────────────────────────────────────────────────────────────────────

export function apply(ctx) {
  const disposers = []
  const log = (...args) => {
    try { ctx?.logger?.info?.(...args) ?? console.log('[huanjing]', ...args) } catch (err) { /* noop */ }
  }

  const toolDisposer = registerTools(ctx, log)
  if (typeof toolDisposer === 'function') disposers.push(toolDisposer)

  const routeDisposers = registerRoutes(ctx, log)
  disposers.push(...routeDisposers)

  const eventDisposers = registerEvents(ctx, log)
  disposers.push(...eventDisposers)

  if (typeof ctx?.effect === 'function') {
    ctx.effect(() => () => {
      for (const dispose of disposers) {
        try { dispose() } catch (err) { /* noop */ }
      }
    })
  }
  log(`已加载：工具 ${toolDisposer ? 'ok' : 'skipped'}，路由 ${routeDisposers.length} 条`)
}

/**
 * 注册工具。DSH 工具服务的注册接口在不同版本里可能不同，这里按能力探测：
 * 依次尝试 tools.register / tools.define / tools.add，全部不可用则跳过（路由仍可用）。
 */
function registerTools(ctx, log) {
  const tools = ctx?.tools || ctx?.get?.('tools')
  if (!tools) {
    log('宿主未注入 tools 服务，跳过工具注册（HTTP 路由仍可用）')
    return null
  }
  const register = typeof tools.register === 'function' ? tools.register.bind(tools)
    : typeof tools.define === 'function' ? tools.define.bind(tools)
      : typeof tools.add === 'function' ? tools.add.bind(tools)
        : null
  if (!register) {
    log('tools 服务没有 register/define/add 接口，跳过工具注册')
    return null
  }

  const made = []
  for (const def of TOOL_DEFS) {
    const impl = TOOL_IMPL[def.name]
    const spec = {
      name: def.name,
      description: def.description,
      parameters: def.parameters,
      async execute(args) {
        return impl(args || {})
      },
    }
    try {
      const d = register(spec)
      made.push(typeof d === 'function' ? d : () => { })
    } catch (err) {
      log(`注册工具 ${def.name} 失败：${err.message}（继续）`)
    }
  }
  if (made.length === 0) return null
  return () => { for (const d of made) { try { d() } catch (e) { /* noop */ } } }
}

/**
 * 注册 HTTP 路由。参考 DSH 生态既有插件：
 *   ctx.webServer.register(path, handler) / (.get / .post) / ctx.router
 * 返回的 disposer 全部收集起来交给 ctx.effect 清理。
 */
function registerRoutes(ctx, log) {
  const web = ctx?.webServer
  const disposers = []
  if (!web) {
    log('宿主未注入 webServer 服务，跳过 HTTP 路由注册')
    return disposers
  }

  const routes = [
    { path: `${ROUTE_PREFIX}/health.json`, method: 'GET', run: () => healthPayload() },
    { path: `${ROUTE_PREFIX}/rules.json`, method: 'GET', run: () => toolRulebook() },
    {
      path: `${ROUTE_PREFIX}/first-side.json`, method: 'GET',
      run: (req) => toolFirstSide(readQueryBattle(req)),
    },
    {
      path: `${ROUTE_PREFIX}/simulate.json`, method: 'POST',
      run: async (req) => toolSimulate(mergeBody(await readBody(req))),
    },
    {
      path: `${ROUTE_PREFIX}/simulate.json`, method: 'GET',
      run: (req) => toolSimulate(readQueryBattle(req)),
    },
    {
      path: `${ROUTE_PREFIX}/validate.json`, method: 'POST',
      run: async (req) => toolValidate(await readBody(req)),
    },
    {
      path: `${ROUTE_PREFIX}/history.json`, method: 'GET',
      run: () => ({ ok: true, stats: historyStats({}), matches: readHistory().matches?.length || 0 }),
    },
    {
      path: `${ROUTE_PREFIX}/match.json`, method: 'POST',
      run: async (req) => recordMatch(await readBody(req)),
    },
    {
      path: `${ROUTE_PREFIX}/operate.json`, method: 'POST',
      run: async (req) => toolOperate(await readBody(req)),
    },
    {
      path: `${ROUTE_PREFIX}/knowledge.json`, method: 'GET',
      run: () => toolKnowledge(),
    },
    {
      path: `${ROUTE_PREFIX}/coverage.json`, method: 'GET',
      run: () => toolCoverage({}),
    },
    {
      path: `${ROUTE_PREFIX}/lookup.json`, method: 'GET',
      run: (req) => {
        const query = req?.query || parseQuery(req?.url)
        return toolLookup({ name: query.name ? decodeURIComponent(String(query.name)) : '' })
      },
    },
    {
      path: `${ROUTE_PREFIX}/stats.json`, method: 'GET',
      run: (req) => {
        const query = req?.query || parseQuery(req?.url)
        return toolStats({
          playersPerMatch: query.players ? Number(query.players) : undefined,
          minSamples: query.min ? Number(query.min) : undefined,
        })
      },
    },
    {
      path: `${ROUTE_PREFIX}/recognize.json`, method: 'POST',
      run: async (req) => toolRecognize(await readBody(req)),
    },
    {
      path: `${ROUTE_PREFIX}/last-analysis.json`, method: 'GET',
      run: () => ({ ok: true, lastAnalysis }),
    },
  ]

  for (const route of routes) {
    try {
      const handler = wrapHandler(route)
      const disposer = registerOneRoute(web, route, handler)
      if (typeof disposer === 'function') disposers.push(disposer)
    } catch (err) {
      log(`注册路由 ${route.method} ${route.path} 失败：${err.message}（继续）`)
    }
  }
  return disposers
}

/**
 * 按方法注册单条路由。
 *
 * ⚠️ 必须区分 GET / POST：只用 `webServer.register(path, handler)` 会把 POST
 *   也注册成 GET，真实客户端发 POST 时就会 404。这里按宿主流行的三种写法依次尝试：
 *     1) webServer.get / webServer.post（DSH 生态常见写法）
 *     2) webServer.register(path, method, handler)
 *     3) webServer.register(path, { method }, handler)
 *   全部不可用时再退回 register(path, handler)（仅 GET 语义正确）。
 */
function registerOneRoute(web, route, handler) {
  const methodFn = route.method === 'POST' ? web.post : web.get
  if (typeof methodFn === 'function') return methodFn.call(web, route.path, handler)

  if (typeof web.register !== 'function') {
    throw new Error('webServer 既没有 get/post，也没有 register')
  }
  // 通过函数签名判断宿主是否接受 method 参数（register.length >= 3 视为 (path, method, handler)）
  if (web.register.length >= 3) return web.register(route.path, route.method, handler)
  try {
    return web.register(route.path, { method: route.method }, handler)
  } catch (err) {
    return web.register(route.path, handler)
  }
}

/**
 * 路由处理器适配层：不同 DSH 版本的 webServer 对 handler 的约定不一样，
 * 这里同时兼容三种形态：
 *   1) 返回 { status, body }         → 由宿主序列化
 *   2) 返回普通对象                  → 视为 200 JSON
 *   3) handler 接收 (req, res) 原生写法 → 我们只在 res 上尝试 json/end 写出
 * 任何已知宿主形态都不命中时，把结果挂到 req.__huanjingResult 供上层读取，绝不抛到宿主。
 */
function wrapHandler(route) {
  return async (req, res) => {
    let payload
    let status = 200
    try {
      const data = await route.run(req || {})
      if (data && typeof data === 'object' && 'status' in data && 'body' in data) {
        status = Number(data.status) || 200
        payload = data.body
      } else {
        payload = data
      }
    } catch (err) {
      status = 400
      payload = { ok: false, error: err && err.message ? err.message : String(err) }
    }

    if (req && typeof req === 'object') {
      try { req.__huanjingResult = { status, body: payload } } catch (err) { /* noop */ }
    }
    if (res && typeof res === 'object') {
      try {
        if (typeof res.status === 'function' && typeof res.json === 'function') {
          res.status(status).json(payload)
          return
        }
        if (typeof res.json === 'function') { res.json(payload); return }
        if (typeof res.end === 'function') {
          res.end(JSON.stringify(payload))
          return
        }
      } catch (err) { /* 落到返回值 */ }
    }
    return { status, body: payload }
  }
}

function healthPayload() {
  return {
    ok: true,
    plugin: name,
    version: PLUGIN_VERSION,
    rulesVersion: DEFAULT_RULES.version,
    dataDir: DATA_DIR,
    historyMatches: readHistory().matches?.length || 0,
    routes: [
      `GET  ${ROUTE_PREFIX}/health.json`,
      `GET  ${ROUTE_PREFIX}/rules.json`,
      `GET  ${ROUTE_PREFIX}/first-side.json?battle=<urlencoded json>`,
      `GET  ${ROUTE_PREFIX}/simulate.json?battle=<urlencoded json>&simulations=20000`,
      `POST ${ROUTE_PREFIX}/simulate.json  body: { battle, simulations, rules, seed }`,
      `POST ${ROUTE_PREFIX}/validate.json  body: <GameState>`,
      `POST ${ROUTE_PREFIX}/match.json     body: <MatchRecord>`,
      `POST ${ROUTE_PREFIX}/operate.json   body: { action:'fuseTriples'|'discover', ... }`,
      `GET  ${ROUTE_PREFIX}/knowledge.json`,
      `GET  ${ROUTE_PREFIX}/coverage.json`,
      `GET  ${ROUTE_PREFIX}/lookup.json?name=<卡牌或主战者名>`,
      `POST ${ROUTE_PREFIX}/recognize.json  body: { slots } 或 { state }`,
      `GET  ${ROUTE_PREFIX}/history.json`,
    ],
    unverifiedRules: UNVERIFIED_RULES.map((r) => r.path),
  }
}

function mergeBody(body) {
  if (!body || typeof body !== 'object') return {}
  // 允许 body 本身就是 battle（含 player/opponent）或 GameState
  if (body.player || body.opponent) return { battle: body }
  return body
}

async function readBody(req) {
  // 1) 宿主已解析
  if (req?.body && typeof req.body === 'object') return req.body
  if (typeof req?.body === 'string' && req.body.trim()) return safeJson(req.body)
  // 2) Node 原生流
  try {
    if (req && typeof req.on === 'function') {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      const text = Buffer.concat(chunks.map((c) => (Buffer.isBuffer(c) ? c : Buffer.from(String(c))))).toString('utf8')
      return safeJson(text)
    }
  } catch (err) { /* 落到空对象 */ }
  return {}
}

function readQueryBattle(req) {
  const query = req?.query || parseQuery(req?.url)
  const raw = query.battle ?? query.gameState ?? query.state
  if (!raw) throw new Error('缺少 battle 查询参数（URL 编码的 JSON）')
  const parsed = safeJson(typeof raw === 'string' ? decodeURIComponent(raw) : JSON.stringify(raw))
  return mergeBody(parsed)
}

function parseQuery(url) {
  const out = {}
  const idx = String(url || '').indexOf('?')
  if (idx < 0) return out
  for (const pair of String(url).slice(idx + 1).split('&')) {
    if (!pair) continue
    const [k, v = ''] = pair.split('=')
    out[decodeURIComponent(k)] = v
  }
  return out
}

function safeJson(text) {
  try { return JSON.parse(text) } catch (err) { throw new Error(`JSON 解析失败：${err.message}`) }
}

/**
 * 事件监听：DSH 的会话/游戏事件 → 自动分析。
 * 事件名与载荷结构因宿主版本而异，这里做「宽匹配 + 容错」，
 * 命中不了就是静默跳过（不影响其他能力）。
 */
function registerEvents(ctx, log) {
  const disposers = []
  if (typeof ctx?.on !== 'function') return disposers

  const handlers = [
    {
      event: 'huanjing/battle/start',
      run: (payload) => {
        const { battle } = toBattle(payload?.battle || payload)
        const result = runSimulation(battle, { simulations: Number(payload?.simulations) || 10000 })
        lastAnalysis = { at: Date.now(), kind: 'battle/start', result }
        log(`战斗开始自动分析：我方胜率 ${result.winRatePercent.player}%`)
      },
    },
    {
      event: 'huanjing/match/end',
      run: (payload) => {
        const saved = recordMatch(payload?.record || payload)
        log(`对局结束记录：${saved.ok ? `已保存（共 ${saved.total} 局）` : saved.error}`)
      },
    },
  ]

  for (const { event, run } of handlers) {
    try {
      const disposer = ctx.on(event, (payload) => {
        try { run(payload) } catch (err) { log(`事件 ${event} 处理失败：${err.message}`) }
      })
      if (typeof disposer === 'function') disposers.push(disposer)
    } catch (err) {
      log(`监听事件 ${event} 失败：${err.message}`)
    }
  }
  return disposers
}

export function getLastAnalysis() {
  return lastAnalysis
}
export default { name, inject, apply }
