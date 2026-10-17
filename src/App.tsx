import { FormEvent, Fragment, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { CatalogHome } from './components/CatalogHome'
import { CatalogUpdatesDialog } from './components/CatalogUpdatesDialog'
import { FranchiseBrowser } from './components/FranchiseBrowser'
import { FavoritesDialog } from './components/FavoritesDialog'
import { ModelComparison } from './components/ModelComparison'
import { ModelDetailDialog } from './components/ModelDetailDialog'
import { QuoteDialog } from './components/QuoteDialog'
import { isTurnstileConfigured } from './components/TurnstileWidget'
import { catalogDocumentTitle } from './config/brand'
import { useCatalogRuntime, useModelGallery } from './hooks/useCatalogRuntime'
import { galleryGridColumnCount, nextGalleryGridIndex, type GalleryDirection } from './lib/galleryGridNavigation'
import {
  compactKnownModelCache,
  readKnownModelCache,
  readProtectedKnownModelIds,
  writeKnownModelCache,
  type KnownModelSummary,
} from './services/knownModelCache'
import { submitQuoteRequest } from './services/quotes'
import type { CatalogImage, CatalogModel } from './types/catalog'

const formatter = new Intl.NumberFormat('pt-BR')
const MAX_QUOTE_ITEMS = 50
const MAX_COMPARE_MODELS = 4
const GALLERY_PAGE_SIZE = 12
const MAX_RECENT_MODELS = 12

type PreviewPageTarget = 'first' | 'last' | null
type GalleryPageToken = number | 'gap-left' | 'gap-right'

function initialSlugFromHash() {
  if (!window.location.hash.startsWith('#modelo=')) return ''
  try {
    return decodeURIComponent(window.location.hash.slice(8))
  } catch {
    return ''
  }
}

function galleryPageTokens(pageIndex: number, totalPages: number): GalleryPageToken[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index)
  const visible = new Set([0, totalPages - 1, pageIndex - 1, pageIndex, pageIndex + 1])
  const pages = Array.from(visible).filter((page) => page >= 0 && page < totalPages).sort((a, b) => a - b)
  const tokens: GalleryPageToken[] = []
  pages.forEach((page, index) => {
    const previous = pages[index - 1]
    if (index > 0 && page - previous > 1) tokens.push(page < pageIndex ? 'gap-left' : 'gap-right')
    tokens.push(page)
  })
  return tokens
}

function loadStoredIds(key: string, maxItems = 100) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) ?? '[]') as unknown
    if (!Array.isArray(parsed)) return []
    return Array.from(new Set(parsed.filter((value): value is string => typeof value === 'string' && value.trim().length > 0))).slice(0, maxItems)
  } catch {
    return []
  }
}

function compactKnownModels(models: Record<string, KnownModelSummary>) {
  return compactKnownModelCache(models, readProtectedKnownModelIds())
}

function persistLocalValue(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // The catalog must keep working even when storage is disabled or quota is exhausted.
  }
}

function ModelArt({ model, compact = false, angle = 0 }: { model: CatalogModel; compact?: boolean; angle?: number }) {
  return (
    <div className={`model-art ${compact ? 'model-art--compact' : ''}`} style={{ '--accent': model.accent } as React.CSSProperties}>
      {model.coverUrl ? (
        <img className="model-art__image" src={model.coverUrl} alt={model.name} loading={compact ? 'lazy' : 'eager'} decoding="async" />
      ) : (
        <>
          <div className="model-art__halo" />
          <div className="model-art__figure" style={{ transform: `translateX(-50%) rotate(${angle}deg)` }}>
            <span className="model-art__head" /><span className="model-art__shoulders" /><span className="model-art__torso" />
            <span className="model-art__leg model-art__leg--left" /><span className="model-art__leg model-art__leg--right" /><span className="model-art__weapon" />
          </div>
          <div className="model-art__base" />
        </>
      )}
      <div className="model-art__grain" />
    </div>
  )
}

function GalleryArt({ image, model, angle = 0 }: { image: CatalogImage; model: CatalogModel; angle?: number }) {
  if (image.url) return <div className="gallery-real-image"><img src={image.url} alt={`${model.name} — vista`} loading="lazy" decoding="async" /></div>
  return <ModelArt model={model} compact angle={angle} />
}

function loadKnownModels(): Record<string, KnownModelSummary> {
  return compactKnownModels(readKnownModelCache())
}

export default function App() {
  const initialModelSlug = useRef(initialSlugFromHash()).current
  const catalog = useCatalogRuntime(initialModelSlug)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const previewPrefetchedUrls = useRef(new Set<string>())
  const galleryGridRef = useRef<HTMLDivElement>(null)
  const [favorites, setFavorites] = useState<string[]>(() => loadStoredIds('tonecos:favorites'))
  const [quoteList, setQuoteList] = useState<string[]>(() => loadStoredIds('tonecos:quote', MAX_QUOTE_ITEMS))
  const [compareIds, setCompareIds] = useState<string[]>(() => loadStoredIds('tonecos:compare', MAX_COMPARE_MODELS))
  const [recentIds, setRecentIds] = useState<string[]>(() => loadStoredIds('tonecos:recent-models', MAX_RECENT_MODELS))
  const [knownModels, setKnownModels] = useState<Record<string, KnownModelSummary>>(loadKnownModels)
  const [explorerOpen, setExplorerOpen] = useState(false)
  const [explorerInitialQuery, setExplorerInitialQuery] = useState('')
  const [updatesOpen, setUpdatesOpen] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)
  const [modelDetailOpen, setModelDetailOpen] = useState(Boolean(initialModelSlug))
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [previewImage, setPreviewImage] = useState<CatalogImage | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewPageTarget, setPreviewPageTarget] = useState<PreviewPageTarget>(null)
  const [favoritesOpen, setFavoritesOpen] = useState(false)
  const [quoteOpen, setQuoteOpen] = useState(false)
  const [sent, setSent] = useState(false)
  const [quoteMode, setQuoteMode] = useState<'live' | 'demo' | null>(null)
  const [quoteReference, setQuoteReference] = useState('')
  const [submittedQuoteCount, setSubmittedQuoteCount] = useState(0)
  const [quoteSubmitting, setQuoteSubmitting] = useState(false)
  const [quoteError, setQuoteError] = useState('')
  const [turnstileResetKey, setTurnstileResetKey] = useState(0)

  const gallery = useModelGallery(catalog.mode, catalog.selected, galleryOpen)
  const selected = catalog.selected
  const visibleModels = catalog.models
  const anyModalOpen = explorerOpen || updatesOpen || compareOpen || modelDetailOpen || galleryOpen || Boolean(previewImage) || favoritesOpen || quoteOpen
  const expandedImageUrl = previewImage?.detailUrl ?? previewImage?.url
  const previewIndex = previewImage ? gallery.items.findIndex((image) => image.id === previewImage.id) : -1
  const canPreviewPrevious = previewIndex > 0 || (previewIndex >= 0 && gallery.hasPreviousPage)
  const canPreviewNext = previewIndex >= 0 && (previewIndex < gallery.items.length - 1 || gallery.hasNextPage)
  const previewGlobalPosition = previewIndex >= 0 ? gallery.pageIndex * GALLERY_PAGE_SIZE + previewIndex + 1 : null
  const galleryTokens = galleryPageTokens(gallery.pageIndex, gallery.totalPages)
  const galleryProgress = gallery.total > 0 ? Math.min(100, (gallery.pageEnd / gallery.total) * 100) : 0

  useEffect(() => {
    setPreviewLoading(Boolean(expandedImageUrl))
  }, [expandedImageUrl])

  useEffect(() => persistLocalValue('tonecos:favorites', favorites), [favorites])
  useEffect(() => persistLocalValue('tonecos:quote', quoteList), [quoteList])
  useEffect(() => persistLocalValue('tonecos:compare', compareIds), [compareIds])
  useEffect(() => persistLocalValue('tonecos:recent-models', recentIds), [recentIds])
  useEffect(() => { writeKnownModelCache(knownModels) }, [knownModels])

  useEffect(() => {
    const seen = [...visibleModels, selected].filter((model) => model.id !== 'loading')
    setKnownModels((current) => {
      const next = { ...current }
      let changed = false
      for (const model of seen) {
        const known = next[model.id]
        if (!known || known.name !== model.name || known.slug !== model.slug) {
          next[model.id] = { name: model.name, slug: model.slug }
          changed = true
        }
      }
      const compacted = compactKnownModels(next)
      if (Object.keys(compacted).length !== Object.keys(next).length) changed = true
      return changed ? compacted : current
    })
  }, [visibleModels, selected, favorites, quoteList, compareIds, recentIds])

  useEffect(() => {
    if (!modelDetailOpen || catalog.routeStatus !== 'ready' || selected.id === 'loading') return
    setRecentIds((current) => [selected.id, ...current.filter((id) => id !== selected.id)].slice(0, MAX_RECENT_MODELS))
  }, [catalog.routeStatus, modelDetailOpen, selected.id])

  useEffect(() => {
    setPreviewImage(null); setPreviewPageTarget(null)
    if (!modelDetailOpen) {
      document.title = catalogDocumentTitle()
      return
    }
    if (catalog.routeStatus === 'not_found') {
      document.title = catalogDocumentTitle('Modelo não encontrado')
      return
    }
    document.title = catalog.routeStatus === 'ready' && selected.id !== 'loading'
      ? catalogDocumentTitle(selected.name)
      : catalogDocumentTitle('Abrindo modelo')
  }, [catalog.routeStatus, modelDetailOpen, selected.id, selected.name])

  useEffect(() => {
    const syncModelRoute = () => setModelDetailOpen(window.location.hash.startsWith('#modelo='))
    window.addEventListener('hashchange', syncModelRoute)
    return () => window.removeEventListener('hashchange', syncModelRoute)
  }, [])

  useEffect(() => {
    if (!previewPageTarget || gallery.loading || !gallery.items.length) return
    const target = previewPageTarget === 'first' ? gallery.items[0] : gallery.items[gallery.items.length - 1]
    setPreviewPageTarget(null); if (!target) return; prefetchPreview(target); setPreviewImage(target)
  }, [previewPageTarget, gallery.loading, gallery.items])

  useEffect(() => {
    if (!previewImage || previewIndex < 0) return
    const previous = gallery.items[previewIndex - 1]; const next = gallery.items[previewIndex + 1]
    if (previous) prefetchPreview(previous); if (next) prefetchPreview(next)
  }, [previewImage?.id, previewIndex, gallery.items])

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (previewImage) closePreview(); else if (quoteOpen) setQuoteOpen(false); else if (favoritesOpen) setFavoritesOpen(false); else if (compareOpen) setCompareOpen(false); else if (updatesOpen) setUpdatesOpen(false); else if (galleryOpen) setGalleryOpen(false); else if (explorerOpen) setExplorerOpen(false); else if (modelDetailOpen) closeModelDetail()
        return
      }
      if (previewImage && event.key === 'ArrowRight') { event.preventDefault(); navigatePreview(1); return }
      if (previewImage && event.key === 'ArrowLeft') { event.preventDefault(); navigatePreview(-1); return }
      if (galleryOpen && !previewImage && event.key === 'PageDown') { event.preventDefault(); gallery.nextPage(); return }
      if (galleryOpen && !previewImage && event.key === 'PageUp') { event.preventDefault(); gallery.previousPage(); return }
      if (galleryOpen && !previewImage && event.key === 'Home') { event.preventDefault(); gallery.goToPage(0); return }
      if (galleryOpen && !previewImage && event.key === 'End') { event.preventDefault(); gallery.goToPage(gallery.totalPages - 1); return }
      const target = event.target
      const isTyping = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || (target instanceof HTMLElement && target.isContentEditable)
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !anyModalOpen) { event.preventDefault(); openExplorer(catalog.search); return }
      if (event.key === '/' && !isTyping && !anyModalOpen) { event.preventDefault(); openExplorer(catalog.search); return }
      if (isTyping || anyModalOpen) return
      if (event.key.toLowerCase() === 'e') openExplorer()
    }
    window.addEventListener('keydown', handleKey); return () => window.removeEventListener('keydown', handleKey)
  })

  function openExplorer(initialQuery = '') {
    setExplorerInitialQuery(initialQuery)
    setExplorerOpen(true)
  }
  function toggleFavorite(id: string) { setFavorites((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]) }
  function toggleCompare(id: string) {
    setCompareIds((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id)
      if (current.length >= MAX_COMPARE_MODELS) { setCompareOpen(true); return current }
      return [...current, id]
    })
  }
  function safeToggleQuote(id: string) {
    setQuoteList((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id)
      if (current.length >= MAX_QUOTE_ITEMS) { setQuoteError(`Cada solicitação aceita até ${MAX_QUOTE_ITEMS} modelos.`); setQuoteOpen(true); return current }
      return [...current, id]
    })
  }
  function openModel(model: Pick<CatalogModel, 'id' | 'slug'>) {
    if (model.id === 'loading') return
    catalog.setSelectedId(model.id)
    setModelDetailOpen(true)
    const nextHash = `#modelo=${encodeURIComponent(model.slug)}`
    if (window.location.hash !== nextHash) window.location.hash = nextHash
  }
  function closeModelDetail() {
    setModelDetailOpen(false)
    if (window.location.hash.startsWith('#modelo=')) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
      window.dispatchEvent(new Event('hashchange'))
    }
  }
  function openKnownModel(id: string, close: () => void) {
    const slug = knownModels[id]?.slug
    if (!slug) return
    close()
    setModelDetailOpen(true)
    window.location.hash = `modelo=${encodeURIComponent(slug)}`
  }
  function addFavoritesToQuote() { setQuoteList((current) => { const next = [...current]; for (const id of favorites) { if (next.length >= MAX_QUOTE_ITEMS) break; if (!next.includes(id)) next.push(id) } return next }); setFavoritesOpen(false); setQuoteOpen(true) }
  function addComparisonToQuote(ids: string[]) { setQuoteList((current) => { const next = [...current]; for (const id of ids) { if (next.length >= MAX_QUOTE_ITEMS) break; if (!next.includes(id)) next.push(id) } return next }); setCompareOpen(false); setQuoteOpen(true) }
  function chooseExplorerCategory(category: string) { catalog.setSearch(''); catalog.setCategory(category) }
  function chooseExplorerFranchise(category: string, franchise: string) { catalog.setSearch(''); catalog.setCategory(category); catalog.setFranchise(franchise) }
  function handleGalleryGridKey(event: ReactKeyboardEvent<HTMLDivElement>) {
    const directionByKey: Partial<Record<string, GalleryDirection>> = {
      ArrowLeft: 'left',
      ArrowRight: 'right',
      ArrowUp: 'up',
      ArrowDown: 'down',
    }
    const direction = directionByKey[event.key]
    if (!direction) return

    const target = event.target instanceof HTMLElement
      ? event.target.closest<HTMLButtonElement>('button[data-gallery-index]')
      : null
    const grid = galleryGridRef.current
    if (!target || !grid) return

    const currentIndex = Number(target.dataset.galleryIndex)
    if (!Number.isInteger(currentIndex)) return

    const buttons = Array.from(grid.querySelectorAll<HTMLButtonElement>('button[data-gallery-index]'))
    const columns = galleryGridColumnCount(window.getComputedStyle(grid).gridTemplateColumns)
    let nextIndex = nextGalleryGridIndex(currentIndex, buttons.length, columns, direction)

    while (nextIndex !== currentIndex && buttons[nextIndex]?.disabled) {
      const candidate = nextGalleryGridIndex(nextIndex, buttons.length, columns, direction)
      if (candidate === nextIndex) break
      nextIndex = candidate
    }

    const next = buttons[nextIndex]
    if (!next || next.disabled || next === target) return
    event.preventDefault()
    next.focus()
  }
  function closePreview() { setPreviewLoading(false); setPreviewPageTarget(null); setPreviewImage(null) }
  function prefetchPreview(image: CatalogImage) { const url = image.detailUrl; if (!url || url === image.url || previewPrefetchedUrls.current.has(url)) return; previewPrefetchedUrls.current.add(url); const preload = new Image(); preload.decoding = 'async'; preload.src = url }
  function navigatePreview(offset: number) {
    if (!previewImage || gallery.loading || previewIndex < 0) return
    const nextImage = gallery.items[previewIndex + offset]
    if (nextImage) { prefetchPreview(nextImage); setPreviewImage(nextImage); return }
    if (offset > 0 && gallery.hasNextPage) { setPreviewPageTarget('first'); gallery.nextPage(); return }
    if (offset < 0 && gallery.hasPreviousPage) { setPreviewPageTarget('last'); gallery.previousPage() }
  }
  async function submitQuote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); const turnstileToken = String(data.get('turnstileToken') ?? '').trim()
    if (catalog.mode === 'live' && !isTurnstileConfigured()) { setQuoteError('A proteção do formulário ainda não foi configurada no ambiente de produção.'); return }
    if (catalog.mode === 'live' && !turnstileToken) { setQuoteError('Conclua a verificação anti-bot antes de enviar.'); return }
    const submittedCount = quoteList.length
    setQuoteSubmitting(true); setQuoteError('')
    try {
      const result = await submitQuoteRequest({ name: String(data.get('name') ?? '').trim(), email: String(data.get('email') ?? '').trim(), notes: String(data.get('notes') ?? '').trim(), modelIds: quoteList, turnstileToken })
      setQuoteMode(result.mode); setQuoteReference(result.reference); setSubmittedQuoteCount(submittedCount)
      if (result.mode === 'live') setQuoteList([])
      setSent(true)
    } catch (error) {
      const message = error instanceof Error ? error.message : ''
      setQuoteError(message === 'turnstile_failed' ? 'A verificação anti-bot expirou ou não foi aceita. Tente novamente.' : 'Não foi possível enviar agora. Sua seleção continua salva neste navegador.')
      setTurnstileResetKey((current) => current + 1)
    } finally { setQuoteSubmitting(false) }
  }

  return (
    <div className="app-shell">
      <CatalogHome
        catalog={catalog}
        searchInputRef={searchInputRef}
        favorites={favorites}
        quoteList={quoteList}
        onOpenExplorer={openExplorer}
        onOpenUpdates={() => setUpdatesOpen(true)}
        onOpenFavorites={() => setFavoritesOpen(true)}
        onOpenQuote={() => setQuoteOpen(true)}
        onOpenModel={openModel}
        onToggleFavorite={toggleFavorite}
        onToggleQuote={safeToggleQuote}
      />
      <ModelDetailDialog
        open={modelDetailOpen}
        routeStatus={catalog.routeStatus}
        model={selected}
        categoryLabel={catalog.categories.find((item) => item.id === selected.category)?.label ?? selected.category}
        favorite={favorites.includes(selected.id)}
        compared={compareIds.includes(selected.id)}
        quoted={quoteList.includes(selected.id)}
        onClose={closeModelDetail}
        onOpenGallery={() => setGalleryOpen(true)}
        onToggleFavorite={() => toggleFavorite(selected.id)}
        onToggleCompare={() => toggleCompare(selected.id)}
        onToggleQuote={() => safeToggleQuote(selected.id)}
      />
      <FranchiseBrowser open={explorerOpen} mode={catalog.mode} categories={catalog.categories} activeCategory={catalog.category} initialQuery={explorerInitialQuery} onClose={() => setExplorerOpen(false)} onSelectCategory={chooseExplorerCategory} onSelectFranchise={chooseExplorerFranchise} />

      <ModelComparison open={compareOpen} mode={catalog.mode} ids={compareIds} knownModels={knownModels} onClose={() => setCompareOpen(false)} onRemove={(id) => setCompareIds((current) => current.filter((item) => item !== id))} onClear={() => setCompareIds([])} onOpenModel={(id) => openKnownModel(id, () => setCompareOpen(false))} onAddToQuote={addComparisonToQuote} />

      <CatalogUpdatesDialog
        open={updatesOpen}
        mode={catalog.mode}
        viewedIds={recentIds}
        knownModels={knownModels}
        onClose={() => setUpdatesOpen(false)}
        onOpenRecentModel={(model) => { setUpdatesOpen(false); openModel(model) }}
        onOpenViewedModel={(id) => openKnownModel(id, () => setUpdatesOpen(false))}
        onClearViewed={() => setRecentIds([])}
      />


      {galleryOpen && <div className="modal-backdrop" onMouseDown={() => setGalleryOpen(false)}><section className="gallery-modal" role="dialog" aria-modal="true" aria-labelledby="gallery-title" onMouseDown={(event) => event.stopPropagation()}><div className="modal-head"><div><span>GALERIA DO PERSONAGEM</span><h2 id="gallery-title">{selected.name}</h2><p>{gallery.total ? `${formatter.format(gallery.pageStart)}–${formatter.format(gallery.pageEnd)} de ${formatter.format(gallery.total)} imagens` : `${selected.galleryCount} imagens`} · página {gallery.pageIndex + 1} de {gallery.totalPages}</p></div><button type="button" aria-label="Fechar galeria" onClick={() => setGalleryOpen(false)}>×</button></div>{gallery.error && <p className="runtime-alert" role="alert">{gallery.error}</p>}<div ref={galleryGridRef} className={`gallery-grid ${gallery.loading ? 'is-loading' : ''}`} aria-busy={gallery.loading} onKeyDown={handleGalleryGridKey}>{gallery.items.map((image, localIndex) => { const canPreview = Boolean(image.detailUrl ?? image.url); return <button type="button" key={image.id} data-gallery-index={localIndex} disabled={!canPreview} aria-label={canPreview ? `Ampliar imagem ${gallery.pageIndex * GALLERY_PAGE_SIZE + localIndex + 1} de ${selected.name}` : undefined} className={image.role === 'cover' ? 'is-cover' : ''} onPointerEnter={() => prefetchPreview(image)} onFocus={() => prefetchPreview(image)} onClick={() => canPreview && setPreviewImage(image)}><GalleryArt image={image} model={selected} angle={((localIndex % 5) - 2) * 3} /><span>{image.role === 'cover' ? 'CAPA · MELHOR QUALIDADE' : `VISTA ${String(gallery.pageIndex * GALLERY_PAGE_SIZE + localIndex + 1).padStart(2, '0')}`}</span></button> })}{gallery.loading && <div className="gallery-loading">Carregando imagens...</div>}{!gallery.loading && !gallery.items.length && <div className="gallery-loading">Nenhuma imagem disponível nesta página.</div>}</div><div className="gallery-collection-progress"><div><strong>{gallery.total ? `${formatter.format(gallery.pageStart)}–${formatter.format(gallery.pageEnd)}` : '0'}</strong><span>de {formatter.format(gallery.total || selected.galleryCount)} imagens</span></div><div className="gallery-collection-progress__track" aria-hidden="true"><i style={{ width: `${galleryProgress}%` }} /></div><small>PgUp/PgDn navegar · Home/End início/fim</small></div><div className="gallery-footer gallery-footer--paged"><span>Miniaturas otimizadas na grade · alta resolução carregada somente ao ampliar.</span><div className="gallery-pager"><button type="button" aria-label="Primeira página da galeria" disabled={!gallery.hasPreviousPage || gallery.loading} onClick={() => gallery.goToPage(0)}>« início</button><button type="button" aria-label="Página anterior" disabled={!gallery.hasPreviousPage || gallery.loading} onClick={gallery.previousPage}>‹</button><div className="gallery-page-rail" aria-label="Páginas da galeria">{galleryTokens.map((token) => typeof token === 'number' ? <button type="button" key={token} aria-current={token === gallery.pageIndex ? 'page' : undefined} className={token === gallery.pageIndex ? 'is-current' : ''} disabled={gallery.loading} onClick={() => gallery.goToPage(token)}>{token + 1}</button> : <span key={token} aria-hidden="true">…</span>)}</div><button type="button" aria-label="Próxima página" disabled={!gallery.hasNextPage || gallery.loading} onClick={gallery.nextPage}>›</button><button type="button" aria-label="Última página da galeria" disabled={!gallery.hasNextPage || gallery.loading} onClick={() => gallery.goToPage(gallery.totalPages - 1)}>fim »</button></div></div></section></div>}

      {expandedImageUrl && previewImage && <div className="image-lightbox-backdrop" onMouseDown={closePreview}><section className="image-lightbox" role="dialog" aria-modal="true" aria-labelledby="lightbox-title" onMouseDown={(event) => event.stopPropagation()}><div className="image-lightbox__head"><div><span>VISUALIZAÇÃO · USE ← → PARA NAVEGAR</span><h2 id="lightbox-title">{selected.name}</h2></div><button type="button" aria-label="Fechar imagem ampliada" onClick={closePreview}>×</button></div><div className={`image-lightbox__stage ${previewLoading ? 'is-loading' : ''}`} aria-busy={previewLoading}><button type="button" className="image-lightbox__nav image-lightbox__nav--prev" aria-label="Imagem anterior" disabled={!canPreviewPrevious || gallery.loading} onClick={() => navigatePreview(-1)}>‹</button><img src={expandedImageUrl} alt={`${selected.name} — imagem ampliada`} fetchPriority="high" decoding="async" onLoad={() => setPreviewLoading(false)} onError={() => setPreviewLoading(false)} />{previewLoading && <span className="image-lightbox__loading" role="status"><i aria-hidden="true" />Carregando alta resolução…</span>}<button type="button" className="image-lightbox__nav image-lightbox__nav--next" aria-label="Próxima imagem" disabled={!canPreviewNext || gallery.loading} onClick={() => navigatePreview(1)}>›</button></div><div className="image-lightbox__footer"><span className="image-lightbox__position"><strong>{previewGlobalPosition ? `${formatter.format(previewGlobalPosition)} / ${formatter.format(gallery.total || selected.galleryCount)}` : 'Imagem do catálogo'}</strong>{previewImage.width > 0 && previewImage.height > 0 && <span>{formatter.format(previewImage.width)} × {formatter.format(previewImage.height)} px</span>}</span><a href={expandedImageUrl} target="_blank" rel="noreferrer">Abrir imagem em nova aba ↗</a></div></section></div>}

      <FavoritesDialog
        open={favoritesOpen}
        ids={favorites}
        knownModels={knownModels}
        onClose={() => setFavoritesOpen(false)}
        onOpenModel={(id) => openKnownModel(id, () => setFavoritesOpen(false))}
        onRemove={toggleFavorite}
        onClear={() => setFavorites([])}
        onAddAllToQuote={addFavoritesToQuote}
      />

      <QuoteDialog
        open={quoteOpen}
        ids={quoteList}
        knownModels={knownModels}
        maxItems={MAX_QUOTE_ITEMS}
        mode={catalog.mode}
        sent={sent}
        quoteMode={quoteMode}
        quoteReference={quoteReference}
        submittedQuoteCount={submittedQuoteCount}
        submitting={quoteSubmitting}
        error={quoteError}
        turnstileResetKey={turnstileResetKey}
        onClose={() => setQuoteOpen(false)}
        onRemove={safeToggleQuote}
        onOpenModel={(id) => openKnownModel(id, () => setQuoteOpen(false))}
        onSubmit={submitQuote}
        onDismissSuccess={() => { setSent(false); setQuoteOpen(false) }}
      />
    </div>
  )
}
