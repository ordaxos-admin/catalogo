import assert from 'node:assert/strict'
import test from 'node:test'

import { verifyWorkerCacheHit } from './check_worker_cache_hit.mjs'

function response(status, cacheStatus = '') {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        return name.toLowerCase() === 'cf-cache-status' ? cacheStatus : null
      },
    },
  }
}

const noSleep = async () => {}

test('accepts a cold MISS followed by HIT for the same cache key', async () => {
  const calls = []
  const statuses = ['MISS', 'HIT']
  const result = await verifyWorkerCacheHit('https://api.example.com', {
    probeId: 'fixed-probe',
    attempts: 4,
    delayMs: 0,
    sleepImpl: noSleep,
    async fetchImpl(url) {
      calls.push(String(url))
      return response(200, statuses.shift())
    },
  })

  assert.deepEqual(result, {
    ok: true,
    attemptsUsed: 2,
    statuses: ['MISS', 'HIT'],
  })
  assert.equal(calls.length, 2)
  assert.equal(calls[0], calls[1])
  assert.match(calls[0], /__cache_probe=fixed-probe/)
})

test('accepts bounded cache convergence after repeated MISS responses', async () => {
  const calls = []
  const statuses = ['MISS', 'MISS', 'MISS', 'HIT']
  const result = await verifyWorkerCacheHit('https://api.example.com', {
    probeId: 'convergence-probe',
    attempts: 6,
    delayMs: 0,
    sleepImpl: noSleep,
    async fetchImpl(url) {
      calls.push(String(url))
      return response(200, statuses.shift())
    },
  })

  assert.equal(result.attemptsUsed, 4)
  assert.deepEqual(result.statuses, ['MISS', 'MISS', 'MISS', 'HIT'])
  assert.equal(new Set(calls).size, 1)
})

test('accepts an already warm cache immediately', async () => {
  let calls = 0
  const result = await verifyWorkerCacheHit('https://api.example.com/', {
    probeId: 'warm-probe',
    async fetchImpl() {
      calls += 1
      return response(200, 'HIT')
    },
  })

  assert.equal(result.attemptsUsed, 1)
  assert.deepEqual(result.statuses, ['HIT'])
  assert.equal(calls, 1)
})

test('fails closed after the bounded convergence window without HIT', async () => {
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', {
      probeId: 'miss-only-probe',
      attempts: 3,
      delayMs: 0,
      sleepImpl: noSleep,
      async fetchImpl() {
        return response(200, 'MISS')
      },
    }),
    /did not produce HIT within 3 attempts: MISS -> MISS -> MISS/,
  )
})

test('fails immediately on terminal uncacheable status', async () => {
  let calls = 0
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', {
      probeId: 'bypass-probe',
      attempts: 6,
      delayMs: 0,
      sleepImpl: noSleep,
      async fetchImpl() {
        calls += 1
        return response(200, 'BYPASS')
      },
    }),
    /is not cacheable: status=BYPASS/,
  )
  assert.equal(calls, 1)
})

test('fails when Cloudflare cache status is absent', async () => {
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', {
      probeId: 'missing-header',
      async fetchImpl() {
        return response(200)
      },
    }),
    /no Cf-Cache-Status/,
  )
})

test('fails on non-success HTTP response', async () => {
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', {
      probeId: 'http-failure',
      async fetchImpl() {
        return response(503, 'BYPASS')
      },
    }),
    /HTTP 503/,
  )
})

test('validates bounded retry settings', async () => {
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', { attempts: 0 }),
    /attempts must be a positive integer/,
  )
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', { delayMs: -1 }),
    /delayMs must be a non-negative integer/,
  )
})

test('rejects invalid API base', async () => {
  await assert.rejects(() => verifyWorkerCacheHit('not-a-url'), /API base must be http\(s\)/)
})
