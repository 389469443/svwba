/**
 * 常驻模拟 worker。
 *
 * 为什么需要它：
 *   原来每次算一场都要 `node _dist_run.mjs` 起一个新进程，
 *   Node 启动 + 加载引擎 + 读 87 张卡的知识库，固定开销就有 300ms 左右，
 *   根本不可能在 0.3 秒内出结果。
 *
 * 协议（按行）：
 *   stdin  每行一个 JSON：{"battle": {...}, "simulations": N, "seed": "..."}
 *   stdout 每行一个 JSON：{"ok": true, "result": {...}} 或 {"ok": false, "error": "..."}
 *
 * 用 readline 逐行处理，进程一直活着，所以每场的成本只剩「纯模拟」。
 */
import fs from 'node:fs'
import readline from 'node:readline'

const P = await import('../../lib/index.js')

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })

const write = (obj) => {
  process.stdout.write(JSON.stringify(obj) + '\n')
}

rl.on('line', (line) => {
  const text = String(line || '').trim()
  if (!text) return
  try {
    const p = JSON.parse(text)
    if (p && p.cmd === 'ping') {
      write({ ok: true, result: { pong: true } })
      return
    }
    const r = P.toolSimulate(p)
    write({
      ok: true,
      result: {
        winRate: r.winRatePercent,
        damage: r.damage,
        expected: r.expectedDamage,
        avgSurvivors: r.avgSurvivors,
        avgRounds: r.avgRounds,
        sample: r.sample,
      },
    })
  } catch (e) {
    write({ ok: false, error: String(e && e.message ? e.message : e) })
  }
})
