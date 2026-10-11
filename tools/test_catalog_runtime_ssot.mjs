import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const apiSource = fs.readFileSync(new URL('../src/services/catalogApi.ts', import.meta.url), 'utf8')
const quoteSource = fs.readFileSync(new URL('../src/services/quotes.ts', import.meta.url), 'utf8')
const runtimeSource = fs.readFileSync(new URL('../src/config/catalogRuntime.ts', import.meta.url), 'utf8')
const contractSource = fs.readFileSync(new URL('../src/config/catalogRuntimeContract.ts', import.meta.url), 'utf8')

test('catalog API and quotes share the canonical runtime resolver', () => {
  assert.match(apiSource, /from '\.\.\/config\/catalogRuntime'/)
  assert.match(quoteSource, /from '\.\.\/config\/catalogRuntime'/)
  assert.match(apiSource, /catalogRuntime\.mode/)
  assert.match(quoteSource, /catalogRuntime\.mode/)
  assert.match(apiSource, /catalogApiUrl\(/)
  assert.match(quoteSource, /catalogApiUrl\('/)
})

test('service modules do not resolve VITE_API_BASE_URL independently', () => {
  assert.doesNotMatch(apiSource, /VITE_API_BASE_URL/)
  assert.doesNotMatch(quoteSource, /VITE_API_BASE_URL/)
  assert.match(runtimeSource, /resolveCatalogRuntime\(import\.meta\.env/)
  assert.match(contractSource, /VITE_API_BASE_URL/)
})

test('missing API configuration is not interpreted as demo mode', () => {
  assert.doesNotMatch(apiSource, /apiBase\s*\?\s*'live'\s*:\s*'demo'/)
  assert.doesNotMatch(apiSource, /!apiBase[^\n]*mode:\s*'demo'/)
  assert.doesNotMatch(quoteSource, /if\s*\(\s*!apiBase\s*\)/)
  assert.match(contractSource, /if \(!mode \|\| mode === 'live'\) return 'live'/)
  assert.match(contractSource, /if \(mode === 'demo'\) return 'demo'/)
})

test('Achegue-se proxy ownership exists only in the runtime contract', () => {
  assert.match(contractSource, /acheguese\.com\.br/)
  assert.match(contractSource, /catalogo-api/)
  assert.doesNotMatch(apiSource, /acheguese\.com\.br/)
  assert.doesNotMatch(quoteSource, /acheguese\.com\.br/)
})
