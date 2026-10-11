import { resolveCatalogRuntime } from './catalogRuntimeContract'
import type { CatalogRuntimeLocation } from './catalogRuntimeContract'

const browserLocation: CatalogRuntimeLocation | undefined = typeof window === 'undefined'
  ? undefined
  : { hostname: window.location.hostname, origin: window.location.origin }

export const catalogRuntime = resolveCatalogRuntime(import.meta.env, browserLocation)

export function catalogApiUrl(path: string) {
  if (!catalogRuntime.apiBase) throw new Error('catalog_api_not_configured')
  if (!path.startsWith('/')) throw new Error(`catalog_api_path_invalid:${path}`)
  return `${catalogRuntime.apiBase}${path}`
}
