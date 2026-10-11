const DEFAULT_BRANCH = 'main'
const API_VERSION = '2022-11-28'

function required(value, name) {
  const normalized = String(value ?? '').trim()
  if (!normalized) throw new Error(`${name} is required`)
  return normalized
}

export function assertMainPushProvenance({ eventName, refName, sha, pullRequests, protectedBranch = DEFAULT_BRANCH }) {
  const event = required(eventName, 'eventName')
  const branch = required(refName, 'refName')
  const target = required(protectedBranch, 'protectedBranch')

  if (event !== 'push' || branch !== target) {
    return { required: false, reason: event !== 'push' ? `event:${event}` : `branch:${branch}` }
  }

  const commitSha = required(sha, 'sha')
  if (!Array.isArray(pullRequests)) throw new Error('pullRequests must be an array')

  const matches = pullRequests.filter((pull) => (
    pull
    && typeof pull === 'object'
    && Boolean(pull.merged_at)
    && pull.base?.ref === target
    && pull.merge_commit_sha === commitSha
  ))

  if (matches.length !== 1) {
    throw new Error(
      `Direct or ambiguous push to ${target} rejected: expected exactly one merged PR with merge_commit_sha=${commitSha}; found ${matches.length}`,
    )
  }

  const pull = matches[0]
  const number = Number(pull.number)
  if (!Number.isInteger(number) || number <= 0) throw new Error('Matching merged PR has an invalid number')

  return {
    required: true,
    pullNumber: number,
    mergedAt: pull.merged_at,
    baseRef: pull.base.ref,
  }
}

export async function fetchAssociatedPullRequests({ repository, sha, token, fetchImpl = fetch }) {
  const repo = required(repository, 'repository')
  const commitSha = required(sha, 'sha')
  const apiToken = required(token, 'token')
  const parts = repo.split('/')
  if (parts.length !== 2 || parts.some((part) => !part.trim())) {
    throw new Error(`repository must use owner/name format; found ${repo}`)
  }

  const [owner, name] = parts.map((part) => encodeURIComponent(part.trim()))
  const url = `https://api.github.com/repos/${owner}/${name}/commits/${encodeURIComponent(commitSha)}/pulls?per_page=100`
  const response = await fetchImpl(url, {
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${apiToken}`,
      'X-GitHub-Api-Version': API_VERSION,
      'User-Agent': 'tonecos-catalogo-main-provenance-gate',
    },
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof payload?.message === 'string' ? `: ${payload.message}` : ''
    throw new Error(`Unable to read associated pull requests for ${commitSha} (${response.status})${detail}`)
  }
  if (!Array.isArray(payload)) throw new Error('GitHub associated pull requests response must be an array')
  return payload
}

export async function checkMainPushProvenance({
  eventName,
  refName,
  sha,
  repository,
  token,
  protectedBranch = DEFAULT_BRANCH,
  fetchImpl = fetch,
}) {
  const event = required(eventName, 'eventName')
  const branch = required(refName, 'refName')
  const target = required(protectedBranch, 'protectedBranch')

  if (event !== 'push' || branch !== target) {
    return assertMainPushProvenance({
      eventName: event,
      refName: branch,
      sha: sha || 'not-required',
      pullRequests: [],
      protectedBranch: target,
    })
  }

  const commitSha = required(sha, 'sha')
  const pullRequests = await fetchAssociatedPullRequests({ repository, sha: commitSha, token, fetchImpl })
  return assertMainPushProvenance({
    eventName: event,
    refName: branch,
    sha: commitSha,
    pullRequests,
    protectedBranch: target,
  })
}

async function main() {
  const result = await checkMainPushProvenance({
    eventName: process.env.GITHUB_EVENT_NAME,
    refName: process.env.GITHUB_REF_NAME,
    sha: process.env.GITHUB_SHA,
    repository: process.env.GITHUB_REPOSITORY,
    token: process.env.GITHUB_TOKEN,
    protectedBranch: process.env.PROTECTED_BRANCH || DEFAULT_BRANCH,
  })

  if (!result.required) {
    console.log(`Main push provenance gate skipped (${result.reason})`)
    return
  }

  console.log(`Main push provenance verified via merged PR #${result.pullNumber}`)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error.message)
    process.exit(1)
  })
}
