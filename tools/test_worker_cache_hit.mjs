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

test('accepts a cold MISS followed by HIT for the same cache key', async () => {
  const calls = []
  const statuses = ['MISS', 'HIT']
  const result = await verifyWorkerCacheHit('https://api.example.com', {
    probeId: 'fixed-probe',
    async fetchImpl(url) {
      calls.push(String(url))
      return response(200, statuses.shift())
    },
  })

  assert.equal(result.ok, true)
  assert.equal(result.firstStatus, 'MISS')
  assert.equal(result.secondStatus, 'HIT')
  assert.equal(calls.length, 2)
  assert.equal(calls[0], calls[1])
  assert.match(calls[0], /__cache_probe=fixed-probe/)
})

test('accepts an already warm cache followed by HIT', async () => {
  const result = await verifyWorkerCacheHit('https://api.example.com/', {
    probeId: 'warm-probe',
    async fetchImpl() {
      return response(200, 'HIT')
    },
  })

  assert.equal(result.firstStatus, 'HIT')
  assert.equal(result.secondStatus, 'HIT')
})

test('fails closed when the second request is not a HIT', async () => {
  const statuses = ['MISS', 'BYPASS']
  await assert.rejects(
    () => verifyWorkerCacheHit('https://api.example.com', {
      probeId: 'bypass-probe',
      async fetchImpl() {
        return response(200, statuses.shift())
      },
    }),
    /did not produce HIT/,
  )
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

test('rejects invalid API base', async () => {
  await assert.rejects(() => verifyWorkerCacheHit('not-a-url'), /API base must be http\(s\)/)
})
