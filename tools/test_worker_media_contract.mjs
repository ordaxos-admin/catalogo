import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

import { publicMediaKey, servePublicMedia } from '../worker/media.ts'

function stream(value) {
  const bytes = new TextEncoder().encode(value)
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes)
      controller.close()
    },
  })
}

function object({ body = 'abc', size = 3, range } = {}) {
  return {
    body: stream(body),
    size,
    httpEtag: '"etag-test"',
    range,
    writeHttpMetadata(headers) {
      headers.set('content-type', 'image/webp')
    },
  }
}

test('public media key is canonical and rejects traversal or malformed paths', () => {
  assert.equal(publicMediaKey('/media/mdl_1/image/card.webp'), 'media/mdl_1/image/card.webp')
  assert.equal(publicMediaKey('/api/recent'), null)
  assert.equal(publicMediaKey('/media/%2e%2e/secret.webp'), null)
  assert.equal(publicMediaKey('/media/model/%5Csecret.webp'), null)
  assert.equal(publicMediaKey('/media/model//card.webp'), null)
  assert.equal(publicMediaKey('/media/%E0%A4%A'), null)
})

test('gateway streams R2 object with cache, ETag and public media headers', async () => {
  const calls = []
  const response = await servePublicMedia(new Request('https://catalog.example/media/mdl_1/image/card.webp'), {
    async get(key, options) {
      calls.push({ key, options })
      return object()
    },
  })

  assert.equal(response.status, 200)
  assert.equal(await response.text(), 'abc')
  assert.equal(calls[0].key, 'media/mdl_1/image/card.webp')
  assert.equal(response.headers.get('etag'), '"etag-test"')
  assert.equal(response.headers.get('content-type'), 'image/webp')
  assert.equal(response.headers.get('content-length'), '3')
  assert.equal(response.headers.get('accept-ranges'), 'bytes')
  assert.equal(response.headers.get('access-control-allow-origin'), '*')
  assert.equal(response.headers.get('cross-origin-resource-policy'), 'cross-origin')
  assert.match(response.headers.get('cache-control') ?? '', /s-maxage=604800/)
})

test('gateway preserves byte-range semantics', async () => {
  const request = new Request('https://catalog.example/media/mdl_1/image/card.webp', {
    headers: { range: 'bytes=0-0' },
  })
  const response = await servePublicMedia(request, {
    async get() {
      return object({ body: 'a', size: 3, range: { offset: 0, length: 1 } })
    },
  })

  assert.equal(response.status, 206)
  assert.equal(await response.text(), 'a')
  assert.equal(response.headers.get('content-range'), 'bytes 0-0/3')
  assert.equal(response.headers.get('content-length'), '1')
})

test('gateway supports HEAD without returning object body', async () => {
  const response = await servePublicMedia(new Request('https://catalog.example/media/mdl_1/image/card.webp', { method: 'HEAD' }), {
    async get() {
      return object()
    },
  })

  assert.equal(response.status, 200)
  assert.equal(await response.text(), '')
  assert.equal(response.headers.get('content-length'), '3')
})

test('gateway returns 304 for failed If-None-Match precondition', async () => {
  const response = await servePublicMedia(new Request('https://catalog.example/media/mdl_1/image/card.webp', {
    headers: { 'if-none-match': '"etag-test"' },
  }), {
    async get() {
      const value = object()
      delete value.body
      return value
    },
  })

  assert.equal(response.status, 304)
  assert.equal(response.headers.get('etag'), '"etag-test"')
})

test('gateway fails closed for missing objects and unsupported methods', async () => {
  const missing = await servePublicMedia(new Request('https://catalog.example/media/mdl_1/missing.webp'), {
    async get() { return null },
  })
  assert.equal(missing.status, 404)
  assert.equal(missing.headers.get('cache-control'), 'no-store')

  const post = await servePublicMedia(new Request('https://catalog.example/media/mdl_1/image.webp', { method: 'POST' }), {
    async get() { throw new Error('must not be called') },
  })
  assert.equal(post.status, 405)
  assert.equal(post.headers.get('allow'), 'GET, HEAD')
})

test('entrypoint routes media before delegating to the D1-backed API handler', () => {
  const entry = fs.readFileSync(new URL('../worker/entry.ts', import.meta.url), 'utf8')
  const mediaBranch = entry.indexOf("url.pathname.startsWith(PUBLIC_MEDIA_PREFIX)")
  const mediaReturn = entry.indexOf('return servePublicMedia(request, env.MEDIA')
  const apiReturn = entry.indexOf('return apiWorker.fetch(request, env)')

  assert.ok(mediaBranch >= 0)
  assert.ok(mediaReturn > mediaBranch)
  assert.ok(apiReturn > mediaReturn)
  assert.ok(!entry.slice(mediaBranch, apiReturn).includes('env.DB'))
})
