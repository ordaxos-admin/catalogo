import apiWorker from './index'
import { PUBLIC_MEDIA_PREFIX, servePublicMedia, type PublicMediaBucket } from './media'

type ApiEnv = Parameters<typeof apiWorker.fetch>[1]

export default {
  async fetch(request: Request, env: ApiEnv): Promise<Response> {
    const url = new URL(request.url)
    if (url.pathname.startsWith(PUBLIC_MEDIA_PREFIX)) {
      return servePublicMedia(request, env.MEDIA as unknown as PublicMediaBucket)
    }
    return apiWorker.fetch(request, env)
  },
}
