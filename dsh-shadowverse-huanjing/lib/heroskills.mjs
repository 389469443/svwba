/**
 * 主战者技能（战斗内生效的部分）
 *
 * 为什么要单独一个模块：
 *   主战者的技能只有**文本描述**，没有结构化数据，只能逐条人工建模。
 *   而且要把「战斗里才发生」的效果和「运营阶段就施放」的效果分开：
 *
 *   ✅ 必须在模拟里补（读取面板时还看不到）：
 *       1101 亚里莎    所有随从获得「谢幕曲：召唤 1 个妖精」
 *       1105 露娜      1 个随从获得「谢幕曲：召唤 1 个自身同名进化前随从」
 *       1006 埃斯佩兰莎 守护随从被攻击时 +1/+1（超进化回合 +3/+3）
 *
 *   ❌ 不用补（战斗开始时结算，已包含在我们读到的面板里）：
 *       100000 璐缇雅   战斗开始时进化最左侧随从  → +2/+2 已在面板
 *       1005 迪亚洛杰莱 战斗开始时梦魇随从 +1/+0 → 已在面板
 *       1002 玛莉亚     随机 2 个随从 +1/+1      → 运营阶段施放，已锁定
 *       1106 伊莉丝     +0/+3 并获得守护        → 运营阶段施放
 *
 *   关键区别：加**面板数值**的效果会体现在我们读到的攻血里；
 *   而加**能力**（谢幕曲、光环、屏障）的效果**读不到**，必须在这里补。
 */

/** 妖精 token 卡号 */
export const FAIRY_CARD_ID = 90011110;

/** 用卡名反查卡号（露娜要"召唤自身同名随从"，需要知道自己的卡号） */
function buildNameToId(cards) {
  const m = new Map();
  for (const [id, c] of Object.entries(cards || {})) {
    const n = c && c.name;
    if (n && !m.has(n)) m.set(n, Number(id));
  }
  return m;
}

/** 给随从追加一条技能（复用现有技能管线，不另造一套） */
function addSkill(follower, skill) {
  if (!follower.meta) follower.meta = {};
  const cur = Array.isArray(follower.meta.skills) ? follower.meta.skills : [];
  // ⚠️ 必须**复制**再追加。
  //    enrichBattle 补技能时直接引用了卡库里的 card.skills 数组，
  //    往那个数组 push 等于永久修改卡牌数据 —— 实测表现：
  //    打过一次亚里莎之后，之后每一局所有卡都带上她的谢幕曲。
  if (cur.some((s) => String(s.skillId) === String(skill.skillId))) return;
  follower.meta.skills = [...cur, skill];
}

/** 谢幕曲：召唤某个卡号 */
function lastWordSummon(skillId, cardId) {
  return {
    skillId,
    evolveForm: 0,
    timing: 'when_destroy',
    battleTiming: true,
    action: 'summon_token',
    option: { summon_token: cardId },
    condition: '{me.self}',
  }
}

/**
 * 主战者战斗效果表。
 * @param {'player'|'opponent'} sideKey
 */
const HERO_EFFECTS = {
  // 亚里莎：下一个战斗阶段中，自己所有随从获得「谢幕曲：召唤 1 个妖精」
  1101: (board, ctx) => {
    for (const f of board) {
      addSkill(f, lastWordSummon('hero-1101', FAIRY_CARD_ID))
    }
  },

  // 露娜：下一个战斗阶段中，1 个随从获得「谢幕曲：召唤 1 个与本随从同名的进化前随从」
  1105: (board, ctx) => {
    const alive = board.filter((f) => f && f.name)
    if (!alive.length) return
    // 官方是"指定的 1 个"，我们不知道玩家选了谁，取随机 1 个（对期望值影响很小）
    const idx = Math.floor((ctx.rng ? ctx.rng() : Math.random()) * alive.length)
    const target = alive[Math.min(alive.length - 1, idx)]
    const cardId = ctx.nameToId && ctx.nameToId.get(target.name)
    if (!cardId) return          // 卡名对不上卡库就跳过，不瞎猜
    addSkill(target, lastWordSummon('hero-1105', cardId))
  },
};

/**
 * 「hook 型」主战者效果：不能靠注入技能实现，要在战斗事件里挂回调。
 *
 * 目前只有 1006 埃斯佩兰莎。官方文本：
 *   「战斗阶段中，自己的拥有【守护】的随从**被攻击时**，使其+1/+1。
 *     若为超进化已解禁的回合，则改为+3/+3。」
 *
 * 时点解读：被攻击时 = 攻击宣告之后、伤害结算**之前** ——
 * 所以这个 +1/+1 能帮随从扛住这次攻击。
 * 引擎的 onFight 正好在 applyDamage 之前触发，并给被攻击方传 isDefender: true。
 */
const HERO_HOOKS = {
  1006: (sideKey) => ({
    onFight: (ctx) => {
      if (!ctx || !ctx.isDefender) return          // 只在"被攻击"时触发
      const f = ctx.attacker                        // 引擎把被攻击方放在 attacker 里
      if (!f || !f.alive || f.hp <= 0) return
      if (ctx.side && ctx.side !== sideKey) return  // 必须是这位主战者自己这边
      const abilities = Array.isArray(f.abilities) ? f.abilities : []
      if (!abilities.includes('guard')) return      // 只对拥有【守护】的随从
      const gain = ctx.superEvolveUnlocked ? 3 : 1  // 超进化已解禁 +3/+3
      f.attack = (f.attack || 0) + gain
      f.health = (f.health || 0) + gain
      if (f.hp > 0) f.hp += gain
      if (typeof ctx.log === 'function') {
        ctx.log({ t: 'hero-1006', who: f.name, gain })
      }
    },
  }),
}

/**
 * 把双方主战者的战斗效果应用到局面上（**原地修改**）。
 *
 * @param {object} battle   归一化后的局面（board 上的随从需已带 name）
 * @param {object} options  { playerHero, opponentHero, cards, rng }
 * @returns {Array<{side:string,hero:number,applied:boolean,reason?:string}>}
 */
export function applyHeroSkills(battle, options = {}) {
  const out = []
  const nameToId = buildNameToId(options.cards)
  const ctx = { nameToId, rng: options.rng }

  const hooks = {}
  for (const [sideKey, heroId] of [['player', options.playerHero],
                                   ['opponent', options.opponentHero]]) {
    if (heroId == null) continue
    const hid = Number(heroId)
    // hook 型效果（埃斯佩兰莎）：挂到战斗事件上
    const hookFactory = HERO_HOOKS[hid]
    if (hookFactory) {
      const h = hookFactory(sideKey)
      for (const [k, v] of Object.entries(h)) {
        const prev = hooks[k]
        hooks[k] = prev ? (...args) => { prev(...args); v(...args) } : v
      }
      out.push({ side: sideKey, hero: hid, applied: true, kind: 'hook' })
    }
    const fn = HERO_EFFECTS[hid]
    if (!fn) continue
    const side = battle[sideKey]
    if (!side || !Array.isArray(side.board)) continue
    try {
      fn(side.board, ctx)
      out.push({ side: sideKey, hero: hid, applied: true })
    } catch (e) {
      out.push({ side: sideKey, hero: hid, applied: false, reason: String(e) })
    }
  }
  return { report: out, hooks }
}

/** 供测试/报告使用：列出已建模的主战者 */
export function heroCoverage() {
  // 技能注入型 + hook 型都要算进来，否则报告会漏掉埃斯佩兰莎
  const ids = new Set()
  for (const k of Object.keys(HERO_EFFECTS)) ids.add(Number(k))
  for (const k of Object.keys(HERO_HOOKS)) ids.add(Number(k))
  return [...ids].sort((a, b) => a - b)
}
