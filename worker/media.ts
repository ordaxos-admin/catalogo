export type PublicMediaObject = {
  body?: ReadableStream<Uint8Array>
  size: number
  httpEtag: string
  range?: { offset: number; length: number }
  writeHttpMetadata(headers: Headers): void
}

export type PublicMediaBucket = {
  get(key: string, options?: { onlyIf?: Headers; range?: Headers }): Promise<PublicMediaObject | null>
}

export const PUBLIC_MEDIA_PREFIX = '/media/'
const PUBLIC_MEDIA_CACHE_CONTROL = 'public, max-age=86400, s-maxage=604800, stale-while-revalidate=86400'

export function publicMediaKey(pathname: string) {
  if (!pathname.startsWith(PUBLIC_MEDIA_PREFIX)) return null

  let key: string
  try {
    key = decodeURIComponent(pathname.slice(1))
  } catch {
    return null
  }

  if (!key.startsWith('media/') || key.includes('\\') || key.includes('\0')) return null
  const parts = key.split('/')
  if (parts.some((part) => !part || part === '.' || part === '..')) return null
  if (new TextEncoder().encode(key).byteLength > 1024) return null
  return key
}

function publicHeaders(object: PublicMediaObject) {
  const headers = new Headers()
  object.writeHttpMetadata(headers)
  headers.set('etag', object.httpEtag)
  headers.set('accept-ranges', 'bytes')
  headers.set('cache-control', PUBLIC_MEDIA_CACHE_CONTROL)
  headers.set('access-control-allow-origin', '*')
  headers.set('cross-origin-resource-policy', 'cross-origin')
  headers.set('x-content-type-options', 'nosniff')
  return headers
}

export async function servePublicMedia(request: Request, bucket: PublicMediaBucket) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response(null, { status: 405, headers: { allow: 'GET, HEAD' } })
  }

  const key = publicMediaKey(new URL(request.url).pathname)
  if (!key) return new Response(null, { status: 404, headers: { 'cache-control': 'no-store' } })

  const object = await bucket.get(key, {
    onlyIf: request.headers,
    range: request.headers,
  })
  if (!object) return new Response(null, { status: 404, headers: { 'cache-control': 'no-store' } })

  const headers = publicHeaders(object)
  if (!object.body) {
    const status = request.headers.has('if-none-match') ? 304 : 412
    return new Response(null, { status, headers })
  }

  let status = 200
  if (object.range) {
    status = 206
    const end = object.range.offset + object.range.length - 1
    headers.set('content-range', `bytes ${object.range.offset}-${end}/${object.size}`)
    headers.set('content-length', String(object.range.length))
  } else {
    headers.set('content-length', String(object.size))
  }

  return new Response(request.method === 'HEAD' ? null : object.body, { status, headers })
}
