import type { CatalogCategory, CatalogFolder, CatalogFranchise, CatalogImage, CatalogModel } from '../types/catalog'
import { catalogApiUrl, catalogRuntime } from '../config/catalogRuntime'
import type { CatalogRuntimeMode } from '../config/catalogRuntimeContract'
import { FRANCHISE_SEARCH_MIN_LENGTH, type CatalogListQuery, type CatalogModelCard, type CursorPage, type GalleryQuery } from './catalogRepository'
import { legacyCompositeGalleryVersion, planLegacyGalleryGroups, registeredLegacyGalleryForRow, uniqueLegacyGalleryImages, type LegacyGalleryOverride } from '../lib/legacyGalleryGrouping'

export type { CatalogRuntimeMode } from '../config/catalogRuntimeContract'

export type ApiCatalogRow = {
  id: string
  slug: string
  code: string
  name: string
  variant_name: string
  franchise: string
  franchise_slug: string
  category: string
  category_slug: string
  collection: string | null
  folder_path: string | null
  image_count: number
  gallery_version: number
  cover_storage_key: string | null
}

export type ApiModelRow = ApiCatalogRow & {
  material: string | null
  height_cm: number | null
  description: string | null
  search_text?: string
}

export type ApiGalleryImage = {
  id: string
  role: 'cover' | 'gallery'
  width: number
  height: number
  bytes: number
  mime: string
  qualityScore: number
  sourceSha256: string
  variantKeys: {
    thumb?: string
    card?: string
    detail?: string
    original?: string
  }
}

export type GalleryPage = {
  items: ApiGalleryImage[]
  nextCursor: string | null
  total: number
  version: number
}

export type FranchiseDiscoveryPage = {
  items: CatalogFranchise[]
  truncated: boolean
}

export type FolderDiscoveryPage = {
  current: Pick<CatalogFolder, 'id' | 'label' | 'count'> | null
  trail: Array<Pick<CatalogFolder, 'id' | 'label'>>
  items: CatalogFolder[]
}

type GalleryCacheEntry = {
  expiresAt: number
  promise: Promise<GalleryPage>
}

const apiBase = catalogRuntime.apiBase
const GALLERY_PAGE_CACHE_LIMIT = 40
const GALLERY_PAGE_CACHE_TTL_MS = 5 * 60 * 1000
const galleryPageCache = new Map<string, GalleryCacheEntry>()

export function getCatalogRuntimeMode(): CatalogRuntimeMode {
  return catalogRuntime.mode
}

function endpoint(path: string, params?: Record<string, string | number | undefined>) {
  const url = new URL(catalogApiUrl(path))
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== '') url.searchParams.set(key, String(value))
  }
  return url.toString()
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(body?.error || `catalog_request_failed_${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function checkCatalogApi() {
  if (catalogRuntime.mode === 'demo') return { mode: 'demo' as const, ok: true }
  if (!apiBase) return { mode: 'live' as const, ok: false }
  try {
    const result = await requestJson<{ ok: boolean }>(endpoint('/api/health'))
    return { mode: 'live' as const, ok: result.ok }
  } catch {
    return { mode: 'live' as const, ok: false }
  }
}

export async function listCatalogCategories(): Promise<CatalogCategory[]> {
  const result = await requestJson<{ items: Array<Omit<CatalogCategory, 'coverUrl'> & { cover_storage_key?: string | null }> }>(endpoint('/api/categories'))
  return result.items.map(({ cover_storage_key, ...item }) => ({
    ...item,
    coverUrl: mediaObjectUrl(cover_storage_key),
  }))
}

export async function listCatalogFranchises(category?: string, limit = 24, search?: string): Promise<FranchiseDiscoveryPage> {
  const page = await requestJson<{ items: Array<Omit<CatalogFranchise, 'coverUrl'> & { cover_storage_key?: string | null }>; truncated: boolean }>(endpoint('/api/franchises', {
    category: category && category !== 'all' ? category : undefined,
    limit,
    q: search && Array.from(search.trim()).length >= FRANCHISE_SEARCH_MIN_LENGTH ? search.trim() : undefined,
  }))
  return {
    truncated: page.truncated,
    items: page.items.map(({ cover_storage_key, ...item }) => ({
      ...item,
      coverUrl: mediaObjectUrl(cover_storage_key),
    })),
  }
}

export async function listCatalogFolders(category: string, franchise: string, parent?: string): Promise<FolderDiscoveryPage> {
  const page = await requestJson<Partial<FolderDiscoveryPage>>(endpoint('/api/folders', {
    category,
    franchise,
    parent: parent?.trim() || undefined,
  }))
  return {
    current: page.current ?? null,
    trail: Array.isArray(page.trail) ? page.trail : [],
    items: Array.isArray(page.items) ? page.items : [],
  }
}

function mediaObjectUrl(key: string | null | undefined) {
  if (!key || !apiBase) return undefined
  return `${apiBase}/${key.split('/').map(encodeURIComponent).join('/')}`
}

function liveSearchTerm(value?: string) {
  const trimmed = value?.trim()
  if (!trimmed || Array.from(trimmed).length < 3) return undefined
  return trimmed
}

export function collapseLegacyViewRows(rows: ApiCatalogRow[]): CatalogModelCard[] {
  const plans = planLegacyGalleryGroups(rows)
  const planByMemberId = new Map<string, (typeof plans)[number]>()
  for (const plan of plans) {
    for (const memberId of plan.memberIds) planByMemberId.set(memberId, plan)
  }

  const emitted = new Set<string>()
  const bySlug = new Map(rows.map((row) => [row.slug, row]))
  const byId = new Map(rows.map((row) => [row.id, row]))
  const cards: CatalogModelCard[] = []

  for (const row of rows) {
    const plan = planByMemberId.get(row.id)
    if (!plan) {
      cards.push(toCatalogModelCard(row))
      continue
    }
    if (emitted.has(plan.key)) continue
    emitted.add(plan.key)

    const canonical = bySlug.get(plan.canonicalSlug)
    const members = plan.memberIds.map((id) => byId.get(id)).filter((item): item is ApiCatalogRow => Boolean(item))
    if (!canonical || members.length !== plan.memberIds.length) {
      cards.push(toCatalogModelCard(row))
      continue
    }

    const card = toCatalogModelCard(canonical)
    card.galleryCount = members.reduce((sum, member) => sum + member.image_count, 0)
    card.galleryVersion = legacyCompositeGalleryVersion(
      members.map((member) => ({ slug: member.slug, gallery_version: member.gallery_version })),
    )
    card.gallerySourceSlugs = plan.memberSlugs
    cards.push(card)
  }

  return cards
}

const legacyModelDetailCache = new Map<string, Promise<CatalogModel | null>>()

function loadLegacyModelDetail(slug: string) {
  const cached = legacyModelDetailCache.get(slug)
  if (cached) return cached
  const pending = getCatalogModel(slug).catch((error) => {
    legacyModelDetailCache.delete(slug)
    throw error
  })
  legacyModelDetailCache.set(slug, pending)
  return pending
}

function catalogModelToCard(model: CatalogModel): CatalogModelCard {
  return {
    id: model.id,
    slug: model.slug,
    code: model.code,
    name: model.name,
    variantName: model.variantName,
    franchise: model.franchise,
    franchiseSlug: model.franchiseSlug,
    category: model.category,
    collection: model.collection,
    folderPath: model.folderPath,
    galleryCount: model.galleryCount,
    galleryVersion: model.galleryVersion,
    accent: model.accent,
    coverUrl: model.coverUrl,
  }
}

async function collapseLegacyViewRowsAcrossPages(rows: ApiCatalogRow[]): Promise<CatalogModelCard[]> {
  const baseCards = collapseLegacyViewRows(rows)
  const touched = new Map<string, LegacyGalleryOverride>()

  for (const row of rows) {
    const group = registeredLegacyGalleryForRow(row)
    if (group) touched.set(group.canonicalSlug, group)
  }
  if (!touched.size) return baseCards

  const hydrated = new Map<string, CatalogModelCard>()
  await Promise.all(Array.from(touched.values(), async (group) => {
    try {
      const details = await Promise.all(group.memberSlugs.map((slug) => loadLegacyModelDetail(slug)))
      if (details.some((model) => !model || model.galleryCount !== 1)) return

      const models = details.filter((model): model is CatalogModel => Boolean(model))
      const canonical = models.find((model) => model.slug === group.canonicalSlug)
      if (!canonical || models.length !== group.memberSlugs.length) return

      const versionedMembers = models.map((model) => {
        const version = model.galleryVersion
        if (version === undefined || !Number.isSafeInteger(version) || version < 1) return null
        return { slug: model.slug, gallery_version: version }
      })
      if (versionedMembers.some((member) => member === null)) return

      const galleryPages = await Promise.all(models.map((model) =>
        listCatalogImages(model.slug, {
          page: 0,
          limit: 2,
          version: model.galleryVersion,
        }),
      ))
      if (galleryPages.some((page) => page.total !== 1 || page.items.length !== 1)) return
      const uniqueImages = uniqueLegacyGalleryImages(galleryPages.flatMap((page) => page.items))
      if (!uniqueImages.length) return

      const galleryVersion = legacyCompositeGalleryVersion(
        versionedMembers.filter(
          (member): member is { slug: string; gallery_version: number } => member !== null,
        ),
      )

      hydrated.set(group.canonicalSlug, {
        ...catalogModelToCard(canonical),
        galleryCount: uniqueImages.length,
        galleryVersion,
        gallerySourceSlugs: group.memberSlugs,
      })
    } catch {
      // Legacy compatibility must never make the primary catalog unavailable.
      // If enrichment fails, the base cards remain visible and independently usable.
    }
  }))

  if (!hydrated.size) return baseCards

  const rowBySlug = new Map(rows.map((row) => [row.slug, row]))
  const emitted = new Set<string>()
  const result: CatalogModelCard[] = []

  for (const card of baseCards) {
    const sourceRow = rowBySlug.get(card.slug)
    const group = sourceRow ? registeredLegacyGalleryForRow(sourceRow) : null
    const replacement = group ? hydrated.get(group.canonicalSlug) : undefined
    if (!replacement) {
      result.push(card)
      continue
    }
    if (emitted.has(group!.canonicalSlug)) continue
    emitted.add(group!.canonicalSlug)
    result.push(replacement)
  }

  return result
}

function toCatalogModelCard(row: ApiCatalogRow): CatalogModelCard {
  return {
    id: row.id,
    slug: row.slug,
    code: row.code,
    name: row.name,
    variantName: row.variant_name ?? '',
    franchise: row.franchise,
    franchiseSlug: row.franchise_slug,
    category: row.category_slug,
    collection: row.collection ?? '',
    folderPath: row.folder_path ?? '',
    galleryCount: row.image_count,
    galleryVersion: row.gallery_version,
    accent: '#c98a3d',
    coverUrl: mediaObjectUrl(row.cover_storage_key),
  }
}

export async function listCatalogModels(query: CatalogListQuery = {}): Promise<CursorPage<CatalogModelCard>> {
  const result = await requestJson<{ items: ApiCatalogRow[]; nextCursor: string | null }>(endpoint('/api/catalog', {
    category: query.category && query.category !== 'all' ? query.category : undefined,
    franchise: query.franchise && query.franchise !== 'all' ? query.franchise : undefined,
    folder: query.folder?.trim() || undefined,
    q: liveSearchTerm(query.search),
    cursor: query.cursor,
    limit: query.limit,
  }))

  return {
    items: await collapseLegacyViewRowsAcrossPages(result.items),
    nextCursor: result.nextCursor,
    totalApprox: 0,
  }
}

export async function listRecentCatalogModels(limit = 12): Promise<CatalogModelCard[]> {
  const result = await requestJson<{ items: ApiCatalogRow[] }>(endpoint('/api/recent', {
    limit: Math.max(1, Math.min(24, Math.trunc(limit))),
  }))
  return collapseLegacyViewRowsAcrossPages(result.items)
}

export async function getCatalogModel(slug: string, galleryVersion?: number): Promise<CatalogModel | null> {
  try {
    const row = await requestJson<ApiModelRow>(endpoint(`/api/models/${encodeURIComponent(slug)}`, {
      v: galleryVersion,
    }))
    return {
      id: row.id,
      slug: row.slug,
      code: row.code,
      name: row.name,
      variantName: row.variant_name ?? '',
      franchise: row.franchise,
      franchiseSlug: row.franchise_slug,
      category: row.category_slug,
      collection: row.collection ?? '',
      folderPath: row.folder_path ?? '',
      material: row.material ?? '',
      heightCm: row.height_cm ?? 0,
      galleryCount: row.image_count,
      galleryVersion: row.gallery_version,
      description: row.description ?? '',
      tags: [],
      accent: '#c98a3d',
      coverUrl: mediaObjectUrl(row.cover_storage_key),
      images: [],
    }
  } catch (error) {
    if (error instanceof Error && error.message === 'model_not_found') return null
    throw error
  }
}

function galleryCacheKey(slug: string, query: GalleryQuery) {
  return `${slug}|v:${query.version ?? 'unknown'}|page:${query.page ?? 'cursor'}|cursor:${query.cursor ?? 'first'}|limit:${query.limit ?? 24}`
}

function directGalleryCursor(query: GalleryQuery) {
  if (query.cursor) return query.cursor
  if (query.page === undefined) return undefined
  const limit = query.limit ?? 24
  const page = Math.max(0, Math.trunc(query.page))
  return btoa(String(page * limit))
}

function rememberGalleryPage(key: string, promise: Promise<GalleryPage>) {
  if (!galleryPageCache.has(key) && galleryPageCache.size >= GALLERY_PAGE_CACHE_LIMIT) {
    const oldest = galleryPageCache.keys().next().value
    if (oldest) galleryPageCache.delete(oldest)
  }
  galleryPageCache.set(key, { expiresAt: Date.now() + GALLERY_PAGE_CACHE_TTL_MS, promise })
  return promise
}

function loadGalleryPage(slug: string, query: GalleryQuery): Promise<GalleryPage> {
  const key = galleryCacheKey(slug, query)
  const cached = galleryPageCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.promise
  if (cached) galleryPageCache.delete(key)

  const pending = requestJson<GalleryPage>(endpoint(`/api/models/${encodeURIComponent(slug)}/images`, {
    cursor: directGalleryCursor(query),
    limit: query.limit,
    v: query.version,
  })).catch((error) => {
    galleryPageCache.delete(key)
    throw error
  })

  return rememberGalleryPage(key, pending)
}

export async function listCatalogImages(slug: string, query: GalleryQuery = {}): Promise<GalleryPage> {
  const page = await loadGalleryPage(slug, query)

  // Antecipamos apenas o JSON da página seguinte. As imagens continuam lazy e só
  // são baixadas quando a página entra na interface.
  if (page.nextCursor) {
    const nextQuery: GalleryQuery = query.page !== undefined
      ? { page: query.page + 1, limit: query.limit, version: query.version }
      : { cursor: page.nextCursor, limit: query.limit, version: query.version }
    const nextKey = galleryCacheKey(slug, nextQuery)
    const cached = galleryPageCache.get(nextKey)
    if (!cached || cached.expiresAt <= Date.now()) {
      void loadGalleryPage(slug, nextQuery).catch(() => undefined)
    }
  }

  return page
}

export function imageVariantUrl(image: ApiGalleryImage, variant: keyof ApiGalleryImage['variantKeys'] = 'detail') {
  const key = image.variantKeys[variant]
    ?? image.variantKeys.card
    ?? image.variantKeys.thumb
    ?? image.variantKeys.detail
    ?? image.variantKeys.original
  return mediaObjectUrl(key)
}

export function toCatalogImage(image: ApiGalleryImage): CatalogImage {
  return {
    id: image.id,
    url: imageVariantUrl(image, 'card'),
    detailUrl: imageVariantUrl(image, 'detail'),
    width: image.width,
    height: image.height,
    bytes: image.bytes,
    role: image.role,
    qualityScore: image.qualityScore,
  }
}
