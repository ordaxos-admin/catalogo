import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assertMainPushProvenance,
  checkMainPushProvenance,
  fetchAssociatedPullRequests,
} from './check_main_push_provenance.mjs'

const sha = '5134897ff4a387a5302e16d78124bf39e46ab56e'

function mergedPull(overrides = {}) {
  return {
    number: 161,
    merged_at: '2026-10-11T09:48:17Z',
    merge_commit_sha: sha,
    base: { ref: 'main' },
    ...overrides,
  }
}

test('pull_request and workflow_dispatch are not treated as direct main pushes', () => {
  assert.deepEqual(
    assertMainPushProvenance({ eventName: 'pull_request', refName: 'main', sha, pullRequests: [] }),
    { required: false, reason: 'event:pull_request' },
  )
  assert.deepEqual(
    assertMainPushProvenance({ eventName: 'workflow_dispatch', refName: 'main', sha, pullRequests: [] }),
    { required: false, reason: 'event:workflow_dispatch' },
  )
})

test('push outside the protected branch is skipped', () => {
  assert.deepEqual(
    assertMainPushProvenance({ eventName: 'push', refName: 'feature/test', sha, pullRequests: [] }),
    { required: false, reason: 'branch:feature/test' },
  )
})

test('main push from exactly one merged PR passes', () => {
  assert.deepEqual(
    assertMainPushProvenance({ eventName: 'push', refName: 'main', sha, pullRequests: [mergedPull()] }),
    {
      required: true,
      pullNumber: 161,
      mergedAt: '2026-10-11T09:48:17Z',
      baseRef: 'main',
    },
  )
})

test('direct main push without an associated merged PR fails closed', () => {
  assert.throws(
    () => assertMainPushProvenance({ eventName: 'push', refName: 'main', sha, pullRequests: [] }),
    /Direct or ambiguous push to main rejected/,
  )
})

test('unmerged, wrong-base, or different merge SHA records do not satisfy provenance', () => {
  const invalid = [
    mergedPull({ merged_at: null }),
    mergedPull({ base: { ref: 'develop' } }),
    mergedPull({ merge_commit_sha: '0000000000000000000000000000000000000000' }),
  ]
  assert.throws(
    () => assertMainPushProvenance({ eventName: 'push', refName: 'main', sha, pullRequests: invalid }),
    /found 0/,
  )
})

test('ambiguous matching merged PRs fail closed', () => {
  assert.throws(
    () => assertMainPushProvenance({
      eventName: 'push',
      refName: 'main',
      sha,
      pullRequests: [mergedPull(), mergedPull({ number: 162 })],
    }),
    /found 2/,
  )
})

test('associated PR fetch uses the exact repository and commit with authenticated read-only request', async () => {
  let observed
  const pulls = await fetchAssociatedPullRequests({
    repository: 'washingtonmsdj/catalogo',
    sha,
    token: 'token-for-test',
    fetchImpl: async (url, options) => {
      observed = { url, options }
      return new Response(JSON.stringify([mergedPull()]), { status: 200 })
    },
  })

  assert.equal(observed.url, `https://api.github.com/repos/washingtonmsdj/catalogo/commits/${sha}/pulls?per_page=100`)
  assert.equal(observed.options.headers.Authorization, 'Bearer token-for-test')
  assert.equal(observed.options.headers['X-GitHub-Api-Version'], '2022-11-28')
  assert.deepEqual(pulls, [mergedPull()])
})

test('GitHub API errors and malformed payloads fail closed', async () => {
  await assert.rejects(
    fetchAssociatedPullRequests({
      repository: 'washingtonmsdj/catalogo',
      sha,
      token: 'token-for-test',
      fetchImpl: async () => new Response(JSON.stringify({ message: 'forbidden' }), { status: 403 }),
    }),
    /Unable to read associated pull requests.*403.*forbidden/,
  )

  await assert.rejects(
    fetchAssociatedPullRequests({
      repository: 'washingtonmsdj/catalogo',
      sha,
      token: 'token-for-test',
      fetchImpl: async () => new Response(JSON.stringify({ unexpected: true }), { status: 200 }),
    }),
    /response must be an array/,
  )
})

test('runtime check does not require a token when provenance is not applicable', async () => {
  const result = await checkMainPushProvenance({
    eventName: 'workflow_dispatch',
    refName: 'main',
    sha,
    repository: 'washingtonmsdj/catalogo',
    token: '',
  })
  assert.deepEqual(result, { required: false, reason: 'event:workflow_dispatch' })
})
