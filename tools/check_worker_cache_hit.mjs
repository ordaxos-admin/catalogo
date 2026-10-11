#!/usr/bin/env node

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

export async function verifyWorkerCacheHit(apiBase, {
  fetchImpl = fetch,
  probeId = `${Date.now()}-${Math.random().toString(16).slice(2)}`,
} = {}) {
  const base = String(apiBase ?? '').trim().replace(/\/$/, '')
  if (!/^https?:\/\//.test(base)) throw new Error('API base must be http(s)')

  const url = new URL(`${base}/api/recent`)
  url.searchParams.set('limit', '1')
  url.searchParams.set('__cache_probe', String(probeId))

  const first = await probe(fetchImpl, url, 'cache warmup')
  const second = await probe(fetchImpl, url, 'cache verification')

  if (second !== 'HIT') {
    throw new Error(`Workers Cache did not produce HIT after warmup: first=${first}, second=${second}`)
  }

  return {
    ok: true,
    firstStatus: first,
    secondStatus: second,
  }
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
