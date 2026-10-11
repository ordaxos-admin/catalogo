import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { loadWranglerConfig } from './wrangler_config_contract.mjs'

const continuityPath = new URL('../docs/CONTINUIDADE-OPERACIONAL.md', import.meta.url)
const publicRuntimePath = new URL('../config/public-runtime.json', import.meta.url)

async function loadState() {
  const [continuity, wrangler, publicRuntimeRaw] = await Promise.all([
    readFile(continuityPath, 'utf8'),
    loadWranglerConfig(),
    readFile(publicRuntimePath, 'utf8'),
  ])
  return {
    continuity,
    wrangler,
    publicRuntime: JSON.parse(publicRuntimeRaw),
  }
}

function onlyBinding(entries, binding, label) {
  const matches = (entries ?? []).filter((entry) => entry?.binding === binding)
  assert.equal(matches.length, 1, `expected exactly one ${label} binding named ${binding}`)
  return matches[0]
}

test('operational continuity references canonical runtime resources', async () => {
  const { continuity, wrangler, publicRuntime } = await loadState()
  const d1 = onlyBinding(wrangler.d1_databases, 'DB', 'D1')
  const media = onlyBinding(wrangler.r2_buckets, 'MEDIA', 'R2')

  for (const value of [
    wrangler.name,
    d1.database_name,
    d1.database_id,
    media.bucket_name,
    publicRuntime.publicSiteUrl,
  ]) {
    assert.ok(value && continuity.includes(value), `continuity SSOT must reference canonical value ${value}`)
  }
})

test('operational continuity enforces one public API/media boundary', async () => {
  const { continuity } = await loadState()

  assert.match(continuity, /\/media\/\*/)
  assert.match(continuity, /Public Development URL do R2 deve permanecer \*\*desabilitado\*\*/)
  assert.match(continuity, /mesmo `apiBase` usado pela API/)
  assert.doesNotMatch(continuity, /https:\/\/[^\s`]+\.r2\.dev/i)
  assert.doesNotMatch(continuity, /VITE_MEDIA_BASE_URL/)
  assert.doesNotMatch(continuity, /mídia pública:\s*`https?:\/\//i)
})

test('operational continuity cannot resurrect expired blockers', async () => {
  const { continuity } = await loadState()
  const forbidden = [
    'PR **#152** está aberta',
    'Deploy Cloudflare API #109',
    '08d3f523868c257e267745a06a3a864405b35b79',
    'bloqueio operacional temporário',
    'única verificação pendente',
    'cota D1 Free esgotou',
  ]

  for (const marker of forbidden) {
    assert.ok(!continuity.includes(marker), `expired operational marker returned: ${marker}`)
  }
})

test('continuity explicitly prefers fresh measurement over embedded commit ids', async () => {
  const { continuity } = await loadState()
  assert.match(continuity, /conferir novamente `main`, PRs\/issues abertas, CI e estado Cloudflare/)
  assert.match(continuity, /Nunca confiar em SHA antigo gravado em documentação como autoridade de execução/)
  assert.match(continuity, /última evidência conhecida boa/)
})
