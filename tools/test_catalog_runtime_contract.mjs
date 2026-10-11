import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveCatalogRuntime } from '../src/config/catalogRuntimeContract.ts'

test('live is the safe default when no API is configured', () => {
  assert.deepEqual(resolveCatalogRuntime({}), { mode: 'live', apiBase: undefined })
})

test('configured API base is normalized without changing runtime mode', () => {
  assert.deepEqual(
    resolveCatalogRuntime({ VITE_API_BASE_URL: 'https://catalog.example.test///' }),
    { mode: 'live', apiBase: 'https://catalog.example.test' },
  )
})

test('Achegue-se uses its first-party catalog proxy as the canonical API origin', () => {
  for (const hostname of ['acheguese.com.br', 'www.acheguese.com.br']) {
    assert.deepEqual(
      resolveCatalogRuntime(
        { VITE_API_BASE_URL: 'https://external.example.test' },
        { hostname, origin: `https://${hostname}` },
      ),
      { mode: 'live', apiBase: `https://${hostname}/catalogo-api` },
    )
  }
})

test('unrelated hosts keep the configured API base', () => {
  assert.deepEqual(
    resolveCatalogRuntime(
      { VITE_API_BASE_URL: 'https://api.example.test/' },
      { hostname: 'catalog.example.test', origin: 'https://catalog.example.test' },
    ),
    { mode: 'live', apiBase: 'https://api.example.test' },
  )
})

test('demo requires explicit opt-in and never inherits a live API base', () => {
  assert.deepEqual(
    resolveCatalogRuntime({ VITE_CATALOG_MODE: 'demo', VITE_API_BASE_URL: 'https://api.example.test' }),
    { mode: 'demo' },
  )
})

test('explicit live mode without API remains live and therefore fails closed at request time', () => {
  assert.deepEqual(resolveCatalogRuntime({ VITE_CATALOG_MODE: 'live' }), { mode: 'live', apiBase: undefined })
})

test('unknown runtime modes fail closed instead of silently becoming demo', () => {
  assert.throws(
    () => resolveCatalogRuntime({ VITE_CATALOG_MODE: 'preview' }),
    /catalog_runtime_mode_invalid:preview/,
  )
})
