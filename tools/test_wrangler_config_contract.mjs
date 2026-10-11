import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

import { canonicalD1DatabaseId, loadWranglerConfig } from './wrangler_config_contract.mjs'

const SYNTHETIC_ID = '11111111-2222-4333-8444-555555555555'

test('repository wrangler config exposes one canonical DB binding', async () => {
  const config = await loadWranglerConfig()
  const databaseId = canonicalD1DatabaseId(config)
  assert.match(databaseId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
  assert.notEqual(databaseId, 'REPLACE_AFTER_D1_CREATE')
})

test('repository wrangler config declares the required production secret', async () => {
  const config = await loadWranglerConfig()
  assert.deepEqual(config.secrets?.required, ['TURNSTILE_SECRET_KEY'])
})

test('repository wrangler config enables Workers Cache for cacheable public GETs', async () => {
  const config = await loadWranglerConfig()
  assert.deepEqual(config.cache, { enabled: true })
})

test('canonical D1 binding rejects missing, duplicate and placeholder ids', () => {
  assert.throws(() => canonicalD1DatabaseId({}), /must declare d1_databases/)
  assert.throws(
    () => canonicalD1DatabaseId({ d1_databases: [{ binding: 'OTHER', database_id: SYNTHETIC_ID }] }),
    /exactly one D1 binding named DB/,
  )
  assert.throws(
    () => canonicalD1DatabaseId({
      d1_databases: [
        { binding: 'DB', database_id: SYNTHETIC_ID },
        { binding: 'DB', database_id: SYNTHETIC_ID },
      ],
    }),
    /found 2/,
  )
  assert.throws(
    () => canonicalD1DatabaseId({
      d1_databases: [{ binding: 'DB', database_id: 'REPLACE_AFTER_D1_CREATE' }],
    }),
    /canonical Cloudflare database UUID/,
  )
})

test('canonical D1 binding rejects loose UUID-like strings', () => {
  assert.throws(
    () => canonicalD1DatabaseId({
      d1_databases: [{ binding: 'DB', database_id: `${SYNTHETIC_ID}-extra` }],
    }),
    /canonical Cloudflare database UUID/,
  )
})

test('renderer rejects a divergent runtime D1 assertion', () => {
  const result = spawnSync(process.execPath, ['tools/render_wrangler_config.mjs'], {
    cwd: process.cwd(),
    env: { ...process.env, CLOUDFLARE_D1_DATABASE_ID: SYNTHETIC_ID },
    encoding: 'utf8',
  })
  assert.notEqual(result.status, 0)
  assert.match(`${result.stdout}\n${result.stderr}`, /diverges from wrangler\.jsonc/)
})
