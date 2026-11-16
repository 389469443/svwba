/**
 * 可复现随机数（蒙特卡洛模拟必须是可复现的，否则概率结果无法回归测试）
 * mulberry32：32 位状态，速度快、分布足够好，无依赖。
 */

/** 把任意种子（数字 / 字符串）折叠成 32 位无符号整数 */
export function hashSeed(seed) {
  if (typeof seed === 'number' && Number.isFinite(seed)) return (seed >>> 0) || 0x9e3779b9
  const str = seed == null ? String(Date.now()) : String(seed)
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0) || 0x9e3779b9
}

export class Prng {
  constructor(seed) {
    this.seed = hashSeed(seed)
    this.state = this.seed >>> 0
  }

  /** 派生一个子随机源（用于“先手判定”与“战斗过程”互不干扰） */
  fork(salt) {
    return new Prng(this.state ^ hashSeed(`${this.seed}:${salt == null ? '' : salt}`))
  }

  /** [0, 1) */
  next() {
    this.state = (this.state + 0x6d2b79f5) >>> 0
    let t = this.state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  /** [0, max) 整数 */
  int(max) {
    if (!(max > 0)) return 0
    return Math.floor(this.next() * max)
  }

  /** 等概率取一个元素 */
  pick(list) {
    if (!Array.isArray(list) || list.length === 0) return undefined
    return list[this.int(list.length)]
  }

  /** 按权重取下标；weights 与 items 等长 */
  pickIndexByWeight(weights) {
    let total = 0
    for (const w of weights) total += w > 0 ? w : 0
    if (!(total > 0)) return this.int(weights.length || 1)
    let roll = this.next() * total
    for (let i = 0; i < weights.length; i++) {
      const w = weights[i] > 0 ? weights[i] : 0
      if (w === 0) continue
      roll -= w
      if (roll < 0) return i
    }
    return weights.length - 1
  }
}

/** 简易字符串数组 hash（用于缓存键） */
export function shortHash(input) {
  return hashSeed(typeof input === 'string' ? input : JSON.stringify(input)).toString(16).padStart(8, '0')
}
