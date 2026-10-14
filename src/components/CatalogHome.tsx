import { useEffect, useMemo, useState, type ReactNode, type RefObject } from 'react'
import { BRAND_NAME, BRAND_SHORT_NAME, CATALOG_LABEL } from '../config/brand'
import { useCatalogRuntime } from '../hooks/useCatalogRuntime'
import { listCatalogFranchises } from '../services/catalogApi'
import { FRANCHISE_SEARCH_MIN_LENGTH } from '../services/catalogRepository'
import { hasResolvedCatalogDiscovery, isCatalogResultsMode, modelsForCatalogHome } from '../lib/catalogHomeView'
import { CatalogSidebarTree } from './CatalogSidebarTree'
import type { CatalogCategory, CatalogFranchise, CatalogModel } from '../types/catalog'

const formatter = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 })

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'tonecos:catalog-sidebar-collapsed'

function loadSidebarCollapsed() {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function imageCountLabel(count: number) {
  return `${formatter.format(count)} ${count === 1 ? 'imagem' : 'imagens'}`
}

type CatalogRuntime = ReturnType<typeof useCatalogRuntime>

type CatalogHomeProps = {
  catalog: CatalogRuntime
  searchInputRef: RefObject<HTMLInputElement | null>
  favorites: string[]
  quoteList: string[]
  onOpenExplorer: (initialQuery?: string) => void
  onOpenUpdates: () => void
  onOpenFavorites: () => void
  onOpenQuote: () => void
  onOpenModel: (model: CatalogModel) => void
  onToggleFavorite: (id: string) => void
  onToggleQuote: (id: string) => void
}

type IconName = 'home' | 'star' | 'clock' | 'layers' | 'search' | 'heart' | 'grid' | 'chevron' | 'plus' | 'sliders' | 'image'

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    home: <path d="M3 11.5 12 4l9 7.5v8a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
    star: <path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" />,
    clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></>,
    layers: <><path d="m12 3 8 4-8 4-8-4z" /><path d="m4 12 8 4 8-4M4 17l8 4 8-4" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></>,
    heart: <path d="M20.5 8.8c0 5.6-8.5 11.2-8.5 11.2S3.5 14.4 3.5 8.8A4.8 4.8 0 0 1 12 5.7a4.8 4.8 0 0 1 8.5 3.1Z" />,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
    chevron: <path d="m9 6 6 6-6 6" />,
    plus: <><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></>,
    sliders: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></>,
    image: <><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><circle cx="9" cy="9" r="1.6" /><path d="m5.5 17 4.6-4.7 3.3 3.1 2.2-2.2 2.9 3.8" /></>,
  }
  return <svg className="storefront-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>
}
function ModelCard({ model, favorite, quoted, active, priority = false, categoryLabel, onSelect, onFavorite, onQuote }: {
  model: CatalogModel
  favorite: boolean
  quoted: boolean
  active?: boolean
  priority?: boolean
  categoryLabel?: string
  onSelect: () => void
  onFavorite: () => void
  onQuote: () => void
}) {
  return (
    <article className={`storefront-model-card ${active ? 'is-active' : ''}`}>
      <button type="button" className="storefront-model-card__main" onClick={onSelect} aria-current={active ? 'true' : undefined}>
        <div className="storefront-model-card__media">
          {model.coverUrl ? <img src={model.coverUrl} alt="" loading={priority ? 'eager' : 'lazy'} fetchPriority={priority ? 'high' : 'auto'} decoding="async" /> : <span className="storefront-model-card__fallback">{model.name.slice(0, 1)}</span>}
          <span className="storefront-model-card__shade" />
          {categoryLabel && <span className="storefront-model-card__category">{categoryLabel}</span>}
          {model.galleryCount > 1 && (
            <span className="storefront-model-card__gallery-badge" aria-hidden="true">
              <Icon name="image" /> {formatter.format(model.galleryCount)}
            </span>
          )}
        </div>
        <span className="storefront-model-card__copy">
          <strong>{model.name}</strong>
          <small>{model.variantName || model.franchise || 'Catálogo'}</small>
          <span className="storefront-model-card__meta"><b>{model.code}</b><i aria-hidden="true" /><span>{imageCountLabel(model.galleryCount)}</span></span>
        </span>
        <span className="storefront-model-card__open" aria-hidden="true"><Icon name="chevron" /></span>
      </button>
      <div className="storefront-model-card__quick-actions">
        <button type="button" className={`storefront-model-card__heart ${favorite ? 'is-active' : ''}`} onClick={onFavorite} aria-label={favorite ? `Remover ${model.name} dos favoritos` : `Favoritar ${model.name}`} aria-pressed={favorite}>
          <Icon name="heart" />
        </button>
        <button type="button" className={`storefront-model-card__quote ${quoted ? 'is-active' : ''}`} onClick={onQuote} aria-label={quoted ? `Remover ${model.name} da minha lista` : `Adicionar ${model.name} à minha lista`} aria-pressed={quoted}>
          <Icon name={quoted ? 'layers' : 'plus'} />
        </button>
      </div>
    </article>
  )
}

function ModelCardSkeleton({ index }: { index: number }) {
  return (
    <article className="storefront-model-card storefront-model-card--skeleton" aria-hidden="true">
      <span className="storefront-model-card--skeleton__media" />
      <span className="storefront-model-card--skeleton__line storefront-model-card--skeleton__line--title" />
      <span className="storefront-model-card--skeleton__line storefront-model-card--skeleton__line--meta" />
      <span className="storefront-model-card--skeleton__index">{String(index + 1).padStart(2, '0')}</span>
    </article>
  )
}

function FranchiseMark({ item }: { item: CatalogFranchise }) {
  const fallback = item.label.trim().slice(0, 1).toLocaleUpperCase('pt-BR') || '•'
  return (
    <span className={`storefront-franchise-list__mark ${item.coverUrl ? 'has-image' : ''}`} aria-hidden="true">
      {item.coverUrl ? <img src={item.coverUrl} alt="" loading="lazy" decoding="async" /> : <span>{fallback}</span>}
    </span>
  )
}

function CategoryTile({ category, cover, active, onSelect }: {
  category: CatalogCategory
  cover?: string
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      className={`storefront-category-tile ${active ? 'is-active' : ''}`}
      onClick={onSelect}
      aria-pressed={active}
      aria-label={`${category.label}: ${category.count} modelos`}
    >
      {cover && <img src={cover} alt="" loading="lazy" decoding="async" />}
      <span className="storefront-category-tile__shade" />
      <span><strong>{category.label}</strong><small>{formatter.format(category.count)} modelos</small></span>
      <i><Icon name="chevron" /></i>
    </button>
  )
}

export function CatalogHome({ catalog, searchInputRef, favorites, quoteList, onOpenExplorer, onOpenUpdates, onOpenFavorites, onOpenQuote, onOpenModel, onToggleFavorite, onToggleQuote }: CatalogHomeProps) {
  const [franchiseFilter, setFranchiseFilter] = useState('')
  const [sidebarSearchItems, setSidebarSearchItems] = useState<CatalogFranchise[]>([])
  const [sidebarSearchLoading, setSidebarSearchLoading] = useState(false)
  const [sidebarSearchError, setSidebarSearchError] = useState('')
  const [sidebarSearchTruncated, setSidebarSearchTruncated] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(loadSidebarCollapsed)
  const [expandedFranchiseKey, setExpandedFranchiseKey] = useState<string | null>(null)
  const [heroCampaignIndex, setHeroCampaignIndex] = useState(0)
  const [showFullCatalogPage, setShowFullCatalogPage] = useState(false)
  const [activeNavSection, setActiveNavSection] = useState<'catalogo' | 'destaques' | 'colecoes'>('catalogo')
  const selected = catalog.selected
  const models = catalog.models
  const catalogTotal = catalog.categories.find((item) => item.id === 'all')?.count ?? catalog.totalCount
  const homeModeInput = {
    showFullCatalogPage,
    pageIndex: catalog.pageIndex,
    category: catalog.category,
    franchise: catalog.franchise,
    folder: catalog.folder,
    search: catalog.search,
    searchPending: catalog.searchPending,
  }
  const hasResolvedDiscovery = hasResolvedCatalogDiscovery(homeModeInput)
  const resultsMode = isCatalogResultsMode(homeModeInput)
  const featuredModels = modelsForCatalogHome(models, resultsMode)
  const secondaryModels = models.slice(6, 10).length ? models.slice(6, 10) : models.slice(0, 4)
  const publicCategories = catalog.categories.filter((item) => item.id !== 'all')
  const categoryTiles = publicCategories.slice(0, 6)
  const hasScopeFilters = catalog.category !== 'all' || catalog.franchise !== 'all' || Boolean(catalog.folder)
  const hasAppliedSearch = Boolean(catalog.search.trim()) && !catalog.searchPending
  const showActiveFilterStrip = hasScopeFilters || hasAppliedSearch
  const activeCategoryLabel = catalog.categories.find((item) => item.id === catalog.category)?.label
  const activeFranchiseLabel = catalog.franchises.find((item) => item.id === catalog.franchise)?.label ?? selected.franchise
  const resultTitle = hasAppliedSearch
    ? `Resultados para “${catalog.search.trim()}”`
    : catalog.folder
      ? (catalog.folderLabel || catalog.folder.split('/').at(-1) || 'Pasta selecionada')
      : catalog.franchise !== 'all'
        ? activeFranchiseLabel
        : catalog.category !== 'all'
          ? (activeCategoryLabel ?? 'Categoria selecionada')
          : 'Todos os modelos'
  const resultScope = [
    catalog.category !== 'all' ? activeCategoryLabel : '',
    catalog.franchise !== 'all' ? activeFranchiseLabel : '',
    catalog.folder ? (catalog.folderLabel || catalog.folder.split('/').at(-1) || '') : '',
  ].filter(Boolean).join(' · ')
  const franchiseNeedle = franchiseFilter.trim().toLocaleLowerCase('pt-BR')
  const franchiseSearchLength = Array.from(franchiseFilter.trim()).length
  const sidebarRemoteSearch = catalog.mode === 'live' && franchiseSearchLength >= FRANCHISE_SEARCH_MIN_LENGTH
  const visibleFranchises = useMemo(() => {
    const source = sidebarRemoteSearch ? sidebarSearchItems : catalog.franchises
    return source.filter((item) => !franchiseNeedle || item.label.toLocaleLowerCase('pt-BR').includes(franchiseNeedle))
  }, [catalog.franchises, franchiseNeedle, sidebarRemoteSearch, sidebarSearchItems])
  const franchiseCards = catalog.franchises.slice(0, 4)
  const categoryLabels = useMemo(() => new Map(catalog.categories.map((item) => [item.id, item.label])), [catalog.categories])
  const heroCampaigns = useMemo(() => {
    const candidates = catalog.categories.filter((item) => item.id !== 'all' && item.count > 0)
    const preferred = candidates.find((item) => item.id === 'games')
    return (preferred ? [preferred, ...candidates.filter((item) => item.id !== preferred.id)] : candidates).slice(0, 5)
  }, [catalog.categories])
  const normalizedHeroCampaignIndex = heroCampaigns.length ? heroCampaignIndex % heroCampaigns.length : 0
  const heroCampaignCategory = heroCampaigns[normalizedHeroCampaignIndex]
  const heroCampaignCover = heroCampaignCategory ? coverForCategory(heroCampaignCategory) : undefined
  const runtimeStatus = catalog.mode === 'demo'
    ? { label: 'Prévia local', tone: 'demo' }
    : catalog.apiHealthy
      ? { label: 'Catálogo online', tone: 'live' }
      : catalog.loading
        ? { label: 'Conectando...', tone: 'pending' }
        : { label: 'Catálogo indisponível', tone: 'error' }

  useEffect(() => {
    if (!sidebarRemoteSearch) {
      setSidebarSearchItems([])
      setSidebarSearchLoading(false)
      setSidebarSearchError('')
      setSidebarSearchTruncated(false)
      return
    }

    let cancelled = false
    setSidebarSearchItems([])
    setSidebarSearchLoading(true)
    setSidebarSearchError('')
    setSidebarSearchTruncated(false)
    const timer = window.setTimeout(() => {
      listCatalogFranchises(catalog.category, 24, franchiseFilter.trim())
        .then((page) => {
          if (cancelled) return
          setSidebarSearchItems(page.items)
          setSidebarSearchTruncated(page.truncated)
        })
        .catch(() => {
          if (cancelled) return
          setSidebarSearchItems([])
          setSidebarSearchTruncated(false)
          setSidebarSearchError('Não foi possível pesquisar todas as franquias agora.')
        })
        .finally(() => {
          if (!cancelled) setSidebarSearchLoading(false)
        })
    }, 220)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [catalog.category, franchiseFilter, sidebarRemoteSearch])

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, sidebarCollapsed ? '1' : '0')
    } catch {
      // The layout still works when local storage is unavailable.
    }
  }, [sidebarCollapsed])

  useEffect(() => {
    if (catalog.franchise === 'all') return
    const current = catalog.franchises.find((item) => item.id === catalog.franchise && item.category === catalog.category)
    if (current) setExpandedFranchiseKey(`${current.category}:${current.id}`)
  }, [catalog.category, catalog.franchise, catalog.franchises])

  useEffect(() => {
    if (!heroCampaigns.length && heroCampaignIndex !== 0) setHeroCampaignIndex(0)
    else if (heroCampaignIndex >= heroCampaigns.length) setHeroCampaignIndex(0)
  }, [heroCampaignIndex, heroCampaigns.length])

  useEffect(() => {
    let frame = 0
    const update = () => {
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(() => {
        const threshold = 120
        const collections = document.getElementById('colecoes')
        const highlights = document.getElementById('destaques')
        const next = collections && collections.getBoundingClientRect().top <= threshold
          ? 'colecoes'
          : highlights && highlights.getBoundingClientRect().top <= threshold
            ? 'destaques'
            : 'catalogo'
        setActiveNavSection((current) => current === next ? current : next)
      })
    }
    update()
    window.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
    }
  }, [resultsMode])

  function selectModel(model: CatalogModel) {
    onOpenModel(model)
  }

  function exploreHeroCampaign() {
    if (heroCampaignCategory) catalog.setCategory(heroCampaignCategory.id)
    setShowFullCatalogPage(true)
    window.requestAnimationFrame(() => {
      document.querySelector('#destaques')?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    })
  }

  function showPreviousHeroCampaign() {
    if (heroCampaigns.length < 2) return
    setHeroCampaignIndex((current) => (current - 1 + heroCampaigns.length) % heroCampaigns.length)
  }

  function showNextHeroCampaign() {
    if (heroCampaigns.length < 2) return
    setHeroCampaignIndex((current) => (current + 1) % heroCampaigns.length)
  }

  function coverForCategory(category: CatalogCategory) {
    return category.coverUrl ?? models.find((model) => model.category === category.id && model.coverUrl)?.coverUrl
  }

  function coverForFranchise(item: CatalogFranchise) {
    return item.coverUrl ?? models.find((model) => model.franchise === item.label && model.coverUrl)?.coverUrl
  }

  function franchiseKey(item: CatalogFranchise) {
    return `${item.category}:${item.id}`
  }

  function toggleFranchise(item: CatalogFranchise) {
    const key = franchiseKey(item)
    setExpandedFranchiseKey((current) => current === key ? null : key)
  }

  function selectFranchise(item: CatalogFranchise) {
    setExpandedFranchiseKey(franchiseKey(item))
    if (catalog.category !== item.category) catalog.setCategory(item.category)
    catalog.setFranchise(item.id)
    setShowFullCatalogPage(true)
    scrollToResults()
  }

  function selectFranchiseFolder(item: CatalogFranchise, folder: string) {
    if (catalog.category !== item.category) catalog.setCategory(item.category)
    if (catalog.franchise !== item.id) catalog.setFranchise(item.id)
    catalog.setFolder(folder)
    setShowFullCatalogPage(true)
    scrollToResults()
  }

  function scrollToResults() {
    window.requestAnimationFrame(() => {
      document.querySelector('#destaques')?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      })
    })
  }

  function selectCategory(category: string) {
    catalog.setCategory(category)
    setShowFullCatalogPage(true)
    scrollToResults()
  }

  function showCatalogPage() {
    setShowFullCatalogPage(true)
    scrollToResults()
  }

  function showEditorialHome() {
    setShowFullCatalogPage(false)
    catalog.resetDiscovery()
    window.requestAnimationFrame(() => {
      document.querySelector('#catalogo')?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      })
    })
  }

  function nextCatalogPage() {
    catalog.goNextPage()
    setShowFullCatalogPage(true)
    scrollToResults()
  }

  function previousCatalogPage() {
    catalog.goPreviousPage()
    setShowFullCatalogPage(true)
    scrollToResults()
  }

  return (
    <div className={`storefront-shell ${sidebarCollapsed ? 'is-sidebar-collapsed' : ''}`}>
      <aside className="storefront-sidebar">
        <div className="storefront-brand">
          <span className="storefront-brand__mark" aria-hidden="true">{BRAND_SHORT_NAME.slice(0, 1).toLocaleUpperCase('pt-BR')}</span>
          <span><strong>{BRAND_NAME}</strong><small>{CATALOG_LABEL}</small></span>
          <button type="button" className="storefront-sidebar__collapse" aria-label={sidebarCollapsed ? 'Expandir menu' : 'Recolher menu'} aria-pressed={sidebarCollapsed} onClick={() => setSidebarCollapsed((current) => !current)}>{sidebarCollapsed ? '››' : '‹‹'}</button>
        </div>

        <nav className="storefront-sidebar__nav" aria-label="Navegação do catálogo">
          <a href="#catalogo" className={activeNavSection === 'catalogo' ? 'is-active' : ''} aria-current={activeNavSection === 'catalogo' ? 'page' : undefined}><Icon name="home" /><span>Catálogo</span></a>
          <a href="#destaques" className={activeNavSection === 'destaques' ? 'is-active' : ''} aria-current={activeNavSection === 'destaques' ? 'page' : undefined}><Icon name="star" /><span>Em destaque</span></a>
          <button type="button" onClick={onOpenUpdates}><Icon name="clock" /><span>Recém adicionados</span></button>
          <a href="#colecoes" className={activeNavSection === 'colecoes' ? 'is-active' : ''} aria-current={activeNavSection === 'colecoes' ? 'page' : undefined}><Icon name="layers" /><span>Coleções</span><b>{catalog.franchises.length}</b></a>
          <button type="button" onClick={onOpenFavorites}><Icon name="heart" /><span>Favoritos</span><b>{favorites.length}</b></button>
        </nav>

        <div className="storefront-sidebar__section-head"><span>Franquias</span><button type="button" onClick={() => onOpenExplorer()} aria-label="Explorar todas as franquias">+</button></div>
        <label className="storefront-franchise-search"><Icon name="search" /><input value={franchiseFilter} onChange={(event) => setFranchiseFilter(event.target.value)} placeholder="Buscar franquias..." /></label>

        <div className="storefront-franchise-list" aria-busy={sidebarSearchLoading}>
          {catalog.mode === 'live' && franchiseSearchLength > 0 && franchiseSearchLength < FRANCHISE_SEARCH_MIN_LENGTH && <div className="storefront-franchise-status is-hint">Digite {FRANCHISE_SEARCH_MIN_LENGTH}+ caracteres para pesquisar todo o acervo.</div>}
          {sidebarSearchLoading && <div className="storefront-franchise-status" role="status">Pesquisando em todas as franquias…</div>}
          {sidebarSearchError && <div className="storefront-franchise-status is-error" role="status">{sidebarSearchError}</div>}
          {!sidebarSearchLoading && sidebarRemoteSearch && !sidebarSearchError && visibleFranchises.length === 0 && <div className="storefront-franchise-status">Nenhuma franquia encontrada.</div>}
          {visibleFranchises.slice(0, 12).map((item) => {
            const key = franchiseKey(item)
            const active = catalog.franchise === item.id && catalog.category === item.category
            const expanded = expandedFranchiseKey === key && catalog.mode === 'live'
            return (
              <div className={`storefront-franchise-entry ${active ? 'is-active' : ''} ${expanded ? 'is-expanded' : ''}`} key={key}>
                <div className="storefront-franchise-row">
                  <button type="button" className="storefront-franchise-select" aria-current={active ? 'page' : undefined} onClick={() => selectFranchise(item)}>
                    <FranchiseMark item={item} /><strong>{item.label}</strong><small>{formatter.format(item.count)}</small>
                  </button>
                  <button type="button" className="storefront-franchise-expand" aria-label={`${expanded ? 'Recolher' : 'Expandir'} estrutura de ${item.label}`} aria-expanded={expanded} disabled={catalog.mode !== 'live'} onClick={() => toggleFranchise(item)}>
                    <Icon name="chevron" />
                  </button>
                </div>
                {expanded && (
                  <CatalogSidebarTree
                    key={key}
                    category={item.category}
                    franchise={item.id}
                    activeFolder={active ? catalog.folder : ''}
                    trail={active ? catalog.folderTrail : []}
                    activeFolderCount={active && catalog.folder && !catalog.hasPreviousPage && !catalog.hasNextPage ? catalog.models.length : undefined}
                    onSelectFolder={(folder) => selectFranchiseFolder(item, folder)}
                  />
                )}
              </div>
            )
          })}
          {!sidebarSearchLoading && sidebarRemoteSearch && !sidebarSearchError && (visibleFranchises.length > 12 || sidebarSearchTruncated) && (
            <button type="button" className="storefront-franchise-more" onClick={() => onOpenExplorer(franchiseFilter.trim())}>
              Ver mais resultados <Icon name="chevron" />
            </button>
          )}
        </div>

        <div className="storefront-sidebar__summary">
          <Icon name="grid" /><div><strong>{formatter.format(catalogTotal)}</strong><span>modelos no acervo</span></div>
          <button type="button" onClick={() => onOpenExplorer()}>Explorar tudo <Icon name="chevron" /></button>
        </div>
      </aside>

      <div className="storefront-main">
        <header className="storefront-topbar">
          <nav aria-label="Navegação principal"><a href="#catalogo" className={activeNavSection !== 'colecoes' ? 'is-active' : ''} aria-current={activeNavSection !== 'colecoes' ? 'page' : undefined}>Explorar</a><a href="#colecoes" className={activeNavSection === 'colecoes' ? 'is-active' : ''} aria-current={activeNavSection === 'colecoes' ? 'page' : undefined}>Coleções</a><button type="button" onClick={onOpenUpdates}>Novos</button><button type="button" className="storefront-topbar__favorites" onClick={onOpenFavorites}>Favoritos <b>{favorites.length}</b></button><button type="button" onClick={onOpenQuote}>Minha lista <b>{quoteList.length}</b></button></nav>
          <div className={`storefront-search ${catalog.searchPending ? 'is-pending' : ''}`} role="search">
            <Icon name="search" />
            <input
              ref={searchInputRef}
              value={catalog.search}
              onChange={(event) => catalog.setSearch(event.target.value)}
              placeholder="Buscar modelos, personagens, franquias..."
              aria-label="Buscar no catálogo"
            />
            {catalog.search.trim() ? (
              <button type="button" className="storefront-search__clear" onClick={() => catalog.setSearch('')} aria-label="Limpar busca">×</button>
            ) : (
              <span className="storefront-search__shortcut" aria-hidden="true"><kbd>Ctrl</kbd><kbd>K</kbd></span>
            )}
          </div>
          <div className="storefront-topbar__status" role="status" aria-live="polite"><span className={`is-${runtimeStatus.tone}`} /><strong>{runtimeStatus.label}</strong></div>
        </header>
        <main id="catalogo" className="storefront-content" aria-busy={catalog.loading}>
          {catalog.error && <div className="storefront-alert" role="alert">{catalog.error}</div>}

          <section className="storefront-hero" aria-label="Destaque editorial do catálogo">
            <div className="storefront-hero__backdrop">
              {heroCampaignCover && <>
                <span key={`mobile-${heroCampaignCategory?.id ?? 'default'}`} className="storefront-hero__mobile-media" style={{ backgroundImage: `url("${heroCampaignCover}")` }} aria-hidden="true" />
                <img key={`blur-${heroCampaignCategory?.id ?? 'default'}`} className="storefront-hero__backdrop-blur" src={heroCampaignCover} alt="" loading="eager" fetchPriority="high" decoding="async" />
                <img key={`subject-${heroCampaignCategory?.id ?? 'default'}`} className="storefront-hero__backdrop-subject" src={heroCampaignCover} alt="" loading="eager" fetchPriority="high" decoding="async" />
              </>}
            </div>
            {heroCampaigns.length > 1 && <>
              <button type="button" className="storefront-hero__nav storefront-hero__nav--previous" onClick={showPreviousHeroCampaign} aria-label="Coleção anterior em destaque"><Icon name="chevron" /></button>
              <button type="button" className="storefront-hero__nav storefront-hero__nav--next" onClick={showNextHeroCampaign} aria-label="Próxima coleção em destaque"><Icon name="chevron" /></button>
            </>}
            <div className="storefront-hero__copy">
              <span className="storefront-hero__eyebrow"><Icon name="star" /> Destaque · {heroCampaignCategory?.label ?? `Acervo ${BRAND_SHORT_NAME}`}</span>
              <h1>Encontre o modelo certo <em>sem se perder no catálogo.</em></h1>
              <p>Explore por franquia, personagem ou categoria, abra cada ficha e compare as vistas disponíveis antes de adicionar o modelo à sua lista.</p>
              <div className="storefront-hero__metrics" aria-label="Resumo do acervo">
                <span><strong>{formatter.format(catalogTotal)}</strong><small>modelos</small></span>
                <span><strong>{formatter.format(publicCategories.length)}</strong><small>categorias</small></span>
                <span><strong>{heroCampaignCategory ? formatter.format(heroCampaignCategory.count) : '—'}</strong><small>na coleção</small></span>
              </div>
              <div className="storefront-hero__actions">
                <button type="button" className="storefront-button storefront-button--primary" onClick={exploreHeroCampaign}>Explorar coleção <Icon name="chevron" /></button>
                <button type="button" className="storefront-button storefront-button--ghost" onClick={() => onOpenExplorer()}><Icon name="grid" /> Ver franquias</button>
              </div>
            </div>
            {heroCampaigns.length > 1 && (
              <div className="storefront-hero__dots" aria-label="Coleções em destaque">
                {heroCampaigns.map((item, index) => <button type="button" key={item.id} onClick={() => setHeroCampaignIndex(index)} aria-current={index === normalizedHeroCampaignIndex ? 'true' : undefined} aria-label={`Mostrar ${item.label}`} />)}
              </div>
            )}
          </section>

          <section className="storefront-commandbar" aria-label="Acesso rápido ao catálogo">
            <div className="storefront-commandbar__stats">
              <span><strong>{formatter.format(catalogTotal)}</strong><small>modelos</small></span>
              <span><strong>{formatter.format(catalog.franchises.length)}</strong><small>franquias carregadas</small></span>
              <span><strong>{formatter.format(publicCategories.length)}</strong><small>categorias</small></span>
            </div>
            <div className="storefront-commandbar__actions">
              <button type="button" onClick={showCatalogPage}><Icon name="grid" /> Ver catálogo completo</button>
              <button type="button" onClick={onOpenUpdates}><Icon name="clock" /> Recém adicionados</button>
              <button type="button" className="is-primary" onClick={onOpenQuote}><Icon name="layers" /> Minha lista <b>{quoteList.length}</b></button>
            </div>
          </section>

          <div className="storefront-filterbar" role="group" aria-label="Categorias do catálogo">
            <div className="storefront-filterbar__rail">
              {catalog.categories.slice(0, 12).map((item) => <button type="button" key={item.id} className={catalog.category === item.id ? 'is-active' : ''} aria-pressed={catalog.category === item.id} onClick={() => selectCategory(item.id)}>{item.label}</button>)}
            </div>
            <button type="button" className="storefront-filterbar__filters" onClick={() => onOpenExplorer()}><Icon name="sliders" /> Filtros <Icon name="chevron" /></button>
          </div>

          {catalog.searchPending && (
            <div className="storefront-search-hint" role="status">
              <Icon name="search" />
              <span>Digite pelo menos {catalog.searchMinLength} caracteres para aplicar a busca.</span>
              <button type="button" onClick={() => catalog.setSearch('')}>Limpar</button>
            </div>
          )}

          {showActiveFilterStrip && (
            <div className="storefront-active-filters" role="group" aria-label="Filtros ativos">
              <span>Recorte atual</span>
              {catalog.category !== 'all' && <button type="button" aria-label="Remover filtro de categoria" onClick={() => catalog.setCategory('all')}>{catalog.categories.find((item) => item.id === catalog.category)?.label ?? catalog.category} ×</button>}
              {catalog.franchise !== 'all' && <button type="button" aria-label="Remover filtro de franquia" onClick={() => catalog.setFranchise('all')}>{catalog.franchises.find((item) => item.id === catalog.franchise)?.label ?? selected.franchise} ×</button>}
              {catalog.folder && <button type="button" aria-label="Remover filtro de pasta" onClick={() => catalog.setFolder('')}>{catalog.folderLabel || catalog.folder.split('/').at(-1)} ×</button>}
              {hasAppliedSearch && <button type="button" aria-label="Remover filtro de busca" onClick={() => catalog.setSearch('')}>“{catalog.search.trim()}” ×</button>}
              <button type="button" className="storefront-active-filters__clear" onClick={catalog.resetDiscovery}>Limpar tudo</button>
            </div>
          )}
          {catalog.loading && <div className="storefront-loading-line" role="status"><span />Atualizando catálogo…</div>}
          <section id="destaques" className={`storefront-section ${resultsMode ? 'storefront-section--results' : ''}`}>
            <div className="storefront-section__head">
              <div>
                <span className="storefront-section__icon"><Icon name={resultsMode ? 'grid' : 'star'} /></span>
                <div>
                  <h2>{resultsMode ? resultTitle : 'Em destaque'}</h2>
                  <p>{resultsMode ? `${resultScope ? `${resultScope} · ` : ''}${formatter.format(models.length)} modelos nesta página · página ${catalog.pageIndex + 1}` : 'Uma seleção do acervo para começar a explorar.'}</p>
                </div>
              </div>
              <div className="storefront-section__actions">
                {resultsMode ? (
                  <>
                    <button type="button" disabled={catalog.loading || !catalog.hasPreviousPage} onClick={previousCatalogPage}>‹ <span>Anterior</span></button>
                    <span>Página {catalog.pageIndex + 1}</span>
                    <button type="button" disabled={catalog.loading || !catalog.hasNextPage} onClick={nextCatalogPage}><span>Próxima</span> ›</button>
                    {!hasResolvedDiscovery && catalog.pageIndex === 0 && <button type="button" className="is-secondary" onClick={showEditorialHome}>Ver destaques</button>}
                  </>
                ) : (
                  <button type="button" onClick={showCatalogPage}>Ver página completa <Icon name="chevron" /></button>
                )}
              </div>
            </div>
            {featuredModels.length ? (
              <div className={`storefront-model-grid ${resultsMode ? 'is-results' : ''} ${catalog.loading ? 'is-updating' : ''}`} aria-busy={catalog.loading}>
                {featuredModels.map((model, index) => <ModelCard key={model.id} model={model} favorite={favorites.includes(model.id)} quoted={quoteList.includes(model.id)} active={selected.id === model.id} priority={index < 6} categoryLabel={categoryLabels.get(model.category)} onSelect={() => selectModel(model)} onFavorite={() => onToggleFavorite(model.id)} onQuote={() => onToggleQuote(model.id)} />)}
              </div>
            ) : catalog.loading ? (
              <div className={`storefront-model-grid storefront-model-grid--skeleton ${resultsMode ? 'is-results' : ''}`} role="status" aria-label="Carregando modelos">
                {Array.from({ length: resultsMode ? 12 : 6 }, (_, index) => <ModelCardSkeleton key={index} index={index} />)}
              </div>
            ) : (
              <div className="storefront-empty">
                <strong>Nenhum modelo neste recorte</strong>
                <span>Remova filtros, altere a busca ou navegue pelas franquias para continuar explorando.</span>
                <div className="storefront-empty__actions">
                  <button type="button" onClick={catalog.resetDiscovery}>Limpar filtros</button>
                  <button type="button" className="is-secondary" onClick={() => onOpenExplorer(catalog.search.trim())}>Explorar franquias</button>
                </div>
              </div>
            )}
            {resultsMode && featuredModels.length > 0 && (
              <div className="storefront-results-footer" aria-label="Paginação dos modelos">
                <span><strong>{formatter.format(models.length)}</strong> modelos carregados nesta página</span>
                <div>
                  <button type="button" disabled={catalog.loading || !catalog.hasPreviousPage} onClick={previousCatalogPage}>‹ Anterior</button>
                  <b>Página {catalog.pageIndex + 1}</b>
                  <button type="button" disabled={catalog.loading || !catalog.hasNextPage} onClick={nextCatalogPage}>Próxima ›</button>
                </div>
              </div>
            )}
          </section>

          {!resultsMode && <>
          <section className="storefront-section storefront-section--categories">
            <div className="storefront-section__head"><div><span className="storefront-section__icon"><Icon name="grid" /></span><div><h2>Categorias</h2><p>Explore o acervo por temática.</p></div></div><button type="button" onClick={() => onOpenExplorer()}>Ver todas <Icon name="chevron" /></button></div>
            <div className="storefront-category-grid">{categoryTiles.map((category) => <CategoryTile key={category.id} category={category} cover={coverForCategory(category)} active={catalog.category === category.id} onSelect={() => selectCategory(category.id)} />)}</div>
          </section>

          <div className="storefront-lower-grid">
            <section id="colecoes" className="storefront-section storefront-section--compact">
              <div className="storefront-section__head"><div><span className="storefront-section__icon"><Icon name="layers" /></span><div><h2>Coleções populares</h2><p>Navegue pelas coleções organizadas.</p></div></div><button type="button" onClick={() => onOpenExplorer()}>Ver todas <Icon name="chevron" /></button></div>
              <div className="storefront-franchise-cards">{franchiseCards.map((item) => {
                const cover = coverForFranchise(item)
                return <button type="button" key={`${item.category}:${item.id}`} onClick={() => selectFranchise(item)}>{cover && <img src={cover} alt="" loading="lazy" decoding="async" />}<span className="storefront-franchise-cards__shade" /><span><strong>{item.label}</strong><small>{formatter.format(item.count)} modelos</small></span><i><Icon name="chevron" /></i></button>
              })}</div>
            </section>

            <section className="storefront-section storefront-section--compact">
              <div className="storefront-section__head"><div><span className="storefront-section__icon"><Icon name="plus" /></span><div><h2>Mais para explorar</h2><p>Continue navegando no recorte atual.</p></div></div><div className="storefront-pager"><button type="button" disabled={catalog.loading || !catalog.hasPreviousPage} onClick={previousCatalogPage}>‹</button><button type="button" disabled={catalog.loading || !catalog.hasNextPage} onClick={nextCatalogPage}>›</button></div></div>
              <div className="storefront-mini-grid">{secondaryModels.map((model) => <ModelCard key={model.id} model={model} favorite={favorites.includes(model.id)} quoted={quoteList.includes(model.id)} active={selected.id === model.id} categoryLabel={categoryLabels.get(model.category)} onSelect={() => selectModel(model)} onFavorite={() => onToggleFavorite(model.id)} onQuote={() => onToggleQuote(model.id)} />)}</div>
            </section>
          </div>
          </>}

          <footer className="storefront-footer"><span role="status" aria-live="polite"><b className={`is-${runtimeStatus.tone}`} />{runtimeStatus.label}</span><strong>{formatter.format(catalogTotal)} modelos organizados</strong><button type="button" onClick={onOpenQuote}>Minha lista <b>{quoteList.length}</b> <Icon name="chevron" /></button></footer>
        </main>
      </div>
    </div>
  )
}
