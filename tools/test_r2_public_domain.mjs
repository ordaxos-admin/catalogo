import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assertActiveCustomDomain,
  assertManagedR2DevState,
  canonicalMediaBucket,
  productionMediaDomain,
} from './check_r2_public_domain.mjs'

const canonicalConfig = {
  r2_buckets: [{ binding: 'MEDIA', bucket_name: 'tonecos-catalogo-media' }],
}

function activeDomain(overrides = {}) {
  return {
    domain: 'media.example.com',
    enabled: true,
    minTLS: '1.2',
    status: {
      ownership: 'active',
      ssl: 'active',
    },
    ...overrides,
  }
}

test('MEDIA bucket is derived from wrangler SSOT', () => {
  assert.equal(canonicalMediaBucket(canonicalConfig), 'tonecos-catalogo-media')
  assert.throws(() => canonicalMediaBucket({ r2_buckets: [] }), /exactly one MEDIA R2 binding/)
})

test('production media origin must be a clean HTTPS custom domain', () => {
  assert.equal(productionMediaDomain('https://media.example.com'), 'media.example.com')
  assert.equal(productionMediaDomain('https://MEDIA.EXAMPLE.COM/'), 'media.example.com')
  assert.throws(() => productionMediaDomain('http://media.example.com'), /must use HTTPS/)
  assert.throws(() => productionMediaDomain('https://media.example.com/path'), /without a path/)
  assert.throws(() => productionMediaDomain('https://media.example.com/?v=1'), /query or fragment/)
  assert.throws(() => productionMediaDomain('https://pub-example.r2.dev'), /non-production r2.dev/)
})

test('active custom domain requires enabled ownership SSL and TLS 1.2+', () => {
  const payload = { domains: [activeDomain()] }
  assert.equal(assertActiveCustomDomain(payload, 'media.example.com').domain, 'media.example.com')

  assert.throws(
    () => assertActiveCustomDomain({ domains: [activeDomain({ enabled: false })] }, 'media.example.com'),
    /is not enabled/,
  )
  assert.throws(
    () => assertActiveCustomDomain({ domains: [activeDomain({ status: { ownership: 'pending', ssl: 'active' } })] }, 'media.example.com'),
    /ownership is pending/,
  )
  assert.throws(
    () => assertActiveCustomDomain({ domains: [activeDomain({ status: { ownership: 'active', ssl: 'pending' } })] }, 'media.example.com'),
    /SSL is pending/,
  )
  assert.throws(
    () => assertActiveCustomDomain({ domains: [activeDomain({ minTLS: '1.1' })] }, 'media.example.com'),
    /minimum TLS 1.2/,
  )
  assert.doesNotThrow(() => assertActiveCustomDomain({ domains: [activeDomain({ minTLS: '1.3' })] }, 'media.example.com'))
})

test('custom domain match is exact and unique', () => {
  assert.throws(() => assertActiveCustomDomain({ domains: [] }, 'media.example.com'), /found 0/)
  assert.throws(
    () => assertActiveCustomDomain({ domains: [activeDomain(), activeDomain()] }, 'media.example.com'),
    /found 2/,
  )
})

test('r2.dev may coexist during rollback window but can be required disabled after cutover', () => {
  assert.doesNotThrow(() => assertManagedR2DevState({ enabled: true }))
  assert.doesNotThrow(() => assertManagedR2DevState({ enabled: false }, { requireDisabled: true }))
  assert.throws(
    () => assertManagedR2DevState({ enabled: true }, { requireDisabled: true }),
    /must be disabled after the production cutover/,
  )
})
