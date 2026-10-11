import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/data/mockCatalog.ts', import.meta.url), 'utf8')

test('demo catalog data is guarded by explicit build-time mode', () => {
  assert.match(source, /const demoEnabled = import\.meta\.env\.VITE_CATALOG_MODE === 'demo'/)
  assert.match(source, /export const categories:[^\n]+demoEnabled \? demoCategories : \[\]/)
  assert.match(source, /export const models:[^\n]+demoEnabled \? demoModels : \[\]/)
})

test('demo catalog has no live-mode fallback branch containing fake records', () => {
  assert.doesNotMatch(source, /VITE_API_BASE_URL/)
  assert.doesNotMatch(source, /\?\s*\[\]\s*:\s*demoCategories/)
  assert.doesNotMatch(source, /\?\s*\[\]\s*:\s*demoModels/)
})
