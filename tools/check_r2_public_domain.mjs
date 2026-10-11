import { loadWranglerConfig } from './wrangler_config_contract.mjs'

function cloudflareErrors(payload) {
  return Array.isArray(payload?.errors)
    ? payload.errors.map((error) => `${error?.code ?? 'unknown'}:${error?.message ?? 'unknown error'}`).join(', ')
    : ''
}

async function cloudflareJson({ accountId, apiToken, path, fetchImpl = fetch }) {
  if (!accountId) throw new Error('CLOUDFLARE_ACCOUNT_ID is required')
  if (!apiToken) throw new Error('CLOUDFLARE_API_TOKEN is required')

  const response = await fetchImpl(`https://api.cloudflare.com/client/v4/accounts/${encodeURIComponent(accountId)}${path}`, {
    headers: { Authorization: `Bearer ${apiToken}` },
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload?.success) {
    const detail = cloudflareErrors(payload)
    throw new Error(`Cloudflare API request failed (${response.status})${detail ? `: ${detail}` : ''}`)
  }
  return payload.result
}

export function canonicalMediaBucket(config) {
  const mediaBindings = Array.isArray(config?.r2_buckets)
    ? config.r2_buckets.filter((entry) => entry?.binding === 'MEDIA')
    : []
  if (mediaBindings.length !== 1 || !String(mediaBindings[0]?.bucket_name ?? '').trim()) {
    throw new Error('wrangler.jsonc must declare exactly one MEDIA R2 binding')
  }
  return String(mediaBindings[0].bucket_name).trim()
}

export function productionMediaDomain(value) {
  const raw = String(value ?? '').trim()
  if (!raw) throw new Error('VITE_MEDIA_BASE_URL is required')

  let url
  try {
    url = new URL(raw)
  } catch {
    throw new Error('VITE_MEDIA_BASE_URL must be a valid absolute URL')
  }

  if (url.protocol !== 'https:') throw new Error('VITE_MEDIA_BASE_URL must use HTTPS')
  if (url.username || url.password) throw new Error('VITE_MEDIA_BASE_URL must not contain credentials')
  if (url.search || url.hash) throw new Error('VITE_MEDIA_BASE_URL must not contain query or fragment components')
  if (url.pathname !== '/' && url.pathname !== '') throw new Error('VITE_MEDIA_BASE_URL must be an origin without a path')

  const hostname = url.hostname.toLowerCase()
  if (!hostname || hostname === 'localhost' || hostname.endsWith('.localhost')) {
    throw new Error('VITE_MEDIA_BASE_URL must use a public hostname')
  }
  if (hostname === 'r2.dev' || hostname.endsWith('.r2.dev')) {
    throw new Error('VITE_MEDIA_BASE_URL still points to the non-production r2.dev development endpoint')
  }
  return hostname
}

function tlsRank(value) {
  return new Map([
    ['1.0', 10],
    ['1.1', 11],
    ['1.2', 12],
    ['1.3', 13],
  ]).get(String(value ?? '')) ?? 0
}

export function assertActiveCustomDomain(domainsPayload, expectedDomain) {
  const domains = Array.isArray(domainsPayload?.domains) ? domainsPayload.domains : []
  const matches = domains.filter((entry) => String(entry?.domain ?? '').toLowerCase() === expectedDomain.toLowerCase())
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one R2 custom domain ${expectedDomain}; found ${matches.length}`)
  }

  const domain = matches[0]
  if (domain.enabled !== true) throw new Error(`R2 custom domain ${expectedDomain} is not enabled`)
  if (domain?.status?.ownership !== 'active') {
    throw new Error(`R2 custom domain ${expectedDomain} ownership is ${domain?.status?.ownership ?? '<missing>'}, expected active`)
  }
  if (domain?.status?.ssl !== 'active') {
    throw new Error(`R2 custom domain ${expectedDomain} SSL is ${domain?.status?.ssl ?? '<missing>'}, expected active`)
  }
  if (tlsRank(domain.minTLS) < tlsRank('1.2')) {
    throw new Error(`R2 custom domain ${expectedDomain} must enforce minimum TLS 1.2 or newer`)
  }

  return domain
}

export function assertManagedR2DevState(managedPayload, { requireDisabled = false } = {}) {
  if (!managedPayload || typeof managedPayload !== 'object' || typeof managedPayload.enabled !== 'boolean') {
    throw new Error('Cloudflare managed r2.dev state is invalid')
  }
  if (requireDisabled && managedPayload.enabled) {
    throw new Error('Public Development URL r2.dev must be disabled after the production cutover')
  }
}

export async function fetchR2DomainState({ accountId, apiToken, bucketName, fetchImpl = fetch }) {
  const encodedBucket = encodeURIComponent(bucketName)
  const custom = await cloudflareJson({
    accountId,
    apiToken,
    path: `/r2/buckets/${encodedBucket}/domains/custom`,
    fetchImpl,
  })
  const managed = await cloudflareJson({
    accountId,
    apiToken,
    path: `/r2/buckets/${encodedBucket}/domains/managed`,
    fetchImpl,
  })
  return { custom, managed }
}

async function main() {
  const config = await loadWranglerConfig()
  const bucketName = canonicalMediaBucket(config)
  const domain = productionMediaDomain(process.env.VITE_MEDIA_BASE_URL)
  const requireManagedDisabled = process.env.REQUIRE_R2_DEV_DISABLED === '1'

  const state = await fetchR2DomainState({
    accountId: process.env.CLOUDFLARE_ACCOUNT_ID,
    apiToken: process.env.CLOUDFLARE_API_TOKEN,
    bucketName,
  })

  const custom = assertActiveCustomDomain(state.custom, domain)
  assertManagedR2DevState(state.managed, { requireDisabled: requireManagedDisabled })

  console.log(`R2 custom domain ready: https://${domain} -> ${bucketName}`)
  console.log(`TLS minimum: ${custom.minTLS}; r2.dev enabled: ${state.managed.enabled}`)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error.message)
    process.exit(1)
  })
}
