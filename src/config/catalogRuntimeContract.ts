export type CatalogRuntimeMode = 'demo' | 'live'

export type CatalogRuntimeEnvironment = {
  VITE_API_BASE_URL?: string
  VITE_CATALOG_MODE?: string
}

export type CatalogRuntimeLocation = {
  hostname: string
  origin: string
}

export type CatalogRuntimeConfig = {
  mode: CatalogRuntimeMode
  apiBase?: string
}

function normalizedApiBase(value?: string) {
  const trimmed = value?.trim()
  if (!trimmed) return undefined
  return trimmed.replace(/\/+$/, '')
}

function normalizedMode(value?: string): CatalogRuntimeMode {
  const mode = value?.trim().toLocaleLowerCase('en-US')
  if (!mode || mode === 'live') return 'live'
  if (mode === 'demo') return 'demo'
  throw new Error(`catalog_runtime_mode_invalid:${value}`)
}

function achegueseApiBase(location?: CatalogRuntimeLocation) {
  if (!location) return undefined
  const hostname = location.hostname.trim().toLocaleLowerCase('en-US')
  if (hostname !== 'acheguese.com.br' && hostname !== 'www.acheguese.com.br') return undefined
  return `${location.origin.replace(/\/+$/, '')}/catalogo-api`
}

export function resolveCatalogRuntime(
  environment: CatalogRuntimeEnvironment,
  location?: CatalogRuntimeLocation,
): CatalogRuntimeConfig {
  const mode = normalizedMode(environment.VITE_CATALOG_MODE)
  if (mode === 'demo') return { mode: 'demo' }

  return {
    mode: 'live',
    apiBase: achegueseApiBase(location) ?? normalizedApiBase(environment.VITE_API_BASE_URL),
  }
}
