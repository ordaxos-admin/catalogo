#!/usr/bin/env node

export const DEFAULT_CACHE_HIT_ATTEMPTS = 6
export const DEFAULT_CACHE_HIT_DELAY_MS = 750

const TERMINAL_UNCACHEABLE_STATUSES = new Set(['BYPASS', 'DYNAMIC'])

function cacheStatus(response) {
  return String(response?.headers?.get?.('cf-cache-status') ?? '').trim().toUpperCase()
}

async function probe(fetchImpl, url, label) {
  const response = await fetchImpl(url, {
    headers: {
      accept: 'application/json',
      'user-agent': 'tonecos-catalog-cache-smoke/1',
    },
  })

  if (!response?.ok) {
    throw new Error(`${label} failed with HTTP ${response?.status ?? 'unknown'}`)
  }

  const status = cacheStatus(response)
  if (!status) throw new Error(`${label} returned no Cf-Cache-Status`)
  return status
}

function positiveInteger(value, label) {
  if (!Number.isInteger(value) || value < 1) throw new Error(`${label} must be a positive integer`)
  return value
}

function nonNegativeInteger(value, label) {
  if (!Number.isInteger(value) || value < 0) throw new Error(`${label} must be a non-negative integer`)
  return value
}

const sleep = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs))

export async function verifyWorkerCacheHit(apiBase, {
  fetchImpl = fetch,
  probeId = `${Date.now()}-${Math.random().toString(16).slice(2)}`,
  attempts = DEFAULT_CACHE_HIT_ATTEMPTS,
  delayMs = DEFAULT_CACHE_HIT_DELAY_MS,
  sleepImpl = sleep,
} = {}) {
  const base = String(apiBase ?? '').trim().replace(/\/$/, '')
  if (!/^https?:\/\//.test(base)) throw new Error('API base must be http(s)')

  positiveInteger(attempts, 'attempts')
  nonNegativeInteger(delayMs, 'delayMs')
  if (typeof sleepImpl !== 'function') throw new Error('sleepImpl must be a function')

  const url = new URL(`${base}/api/recent`)
  url.searchParams.set('limit', '1')
  url.searchParams.set('__cache_probe', String(probeId))

  const statuses = []
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const status = await probe(fetchImpl, url, `cache probe ${attempt}/${attempts}`)
    statuses.push(status)

    if (status === 'HIT') {
      return {
        ok: true,
        attemptsUsed: attempt,
        statuses,
      }
    }

    if (TERMINAL_UNCACHEABLE_STATUSES.has(status)) {
      throw new Error(`Workers Cache is not cacheable: status=${status}; observed=${statuses.join(' -> ')}`)
    }

    if (attempt < attempts && delayMs > 0) await sleepImpl(delayMs)
  }

  throw new Error(`Workers Cache did not produce HIT within ${attempts} attempts: ${statuses.join(' -> ')}`)
}

async function main() {
  const apiBase = process.argv[2] || process.env.CATALOG_API_URL || process.env.VITE_API_BASE_URL
  if (!apiBase) throw new Error('API base is required as argv[2], CATALOG_API_URL or VITE_API_BASE_URL')
  process.stdout.write(JSON.stringify(await verifyWorkerCacheHit(apiBase)) + '\n')
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(`ERRO: ${error instanceof Error ? error.message : String(error)}`)
    process.exit(2)
  })
}
