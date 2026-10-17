import { useEffect, useMemo, useRef, useState } from 'react'
import { models as demoModels } from '../data/mockCatalog'
import { listCatalogFranchises, listCatalogModels, type CatalogRuntimeMode } from '../services/catalogApi'
import { FRANCHISE_SEARCH_MIN_LENGTH, type CatalogModelCard } from '../services/catalogRepository'
import type { CatalogCategory, CatalogFranchise } from '../types/catalog'

const DISCOVERY_LIMIT = 48
const MODEL_DISCOVERY_LIMIT = 12
const numberFormatter = new Intl.NumberFormat('pt-BR')

type Props = {
  open: boolean
  mode: CatalogRuntimeMode
  categories: CatalogCategory[]
  activeCategory: string
  initialQuery?: string
  onClose: () => void
  onSelectCategory: (category: string) => void
  onSelectFranchise: (category: string, franchise: string) => void
}

function normalize(value: string) {
  return value.trim().toLocaleLowerCase('pt-BR')
}

function demoFranchises(category: string, query: string): CatalogFranchise[] {
  const needle = normalize(query)
  const scoped = category === 'all' ? demoModels : demoModels.filter((model) => model.category === category)
  const grouped = new Map<string, CatalogFranchise>()

  for (const model of scoped) {
    const id = model.franchise
    const existing = grouped.get(id)
    if (existing) existing.count += 1
    else grouped.set(id, { id, label: model.franchise, count: 1, category: model.category })
  }

  return [...grouped.values()]
    .filter((item) => !needle || normalize(item.label).includes(needle))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'pt-BR'))
    .slice(0, DISCOVERY_LIMIT)
}

function demoModelResults(category: string, query: string): CatalogModelCard[] {
  const needle = normalize(query)
  if (Array.from(needle).length < FRANCHISE_SEARCH_MIN_LENGTH) return []

  return demoModels
    .filter((model) => {
      if (category !== 'all' && model.category !== category) return false
      const haystack = normalize(`${model.name} ${model.variantName} ${model.franchise} ${model.collection} ${model.code} ${model.tags.join(' ')}`)
      return haystack.includes(needle)
    })
    .slice(0, MODEL_DISCOVERY_LIMIT)
    .map((model) => ({
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
      gallerySourceSlugs: model.gallerySourceSlugs,
      accent: model.accent,
      coverUrl: model.coverUrl,
    }))
}

export function FranchiseBrowser({
  open,
  mode,
  categories,
  activeCategory,
  initialQuery = '',
  onClose,
  onSelectCategory,
  onSelectFranchise,
}: Props) {
  const searchRef = useRef<HTMLInputElement>(null)
  const [category, setCategory] = useState(activeCategory)
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<CatalogFranchise[]>([])
  const [modelItems, setModelItems] = useState<CatalogModelCard[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [truncated, setTruncated] = useState(false)

  useEffect(() => {
    if (!open) return
    setCategory(activeCategory)
    setQuery(initialQuery.trim())
    setError('')
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus())
    return () => window.cancelAnimationFrame(frame)
  }, [open, activeCategory, initialQuery])

  const demoItems = useMemo(() => demoFranchises(category, query), [category, query])
  const demoModelsFound = useMemo(() => demoModelResults(category, query), [category, query])
  const queryLength = Array.from(query.trim()).length
  const searchPending = queryLength > 0 && queryLength < FRANCHISE_SEARCH_MIN_LENGTH
  const modelSearchActive = queryLength >= FRANCHISE_SEARCH_MIN_LENGTH

  useEffect(() => {
    if (!open || mode !== 'live') return
    if (searchPending) {
      setLoading(false)
      setError('')
      setItems([])
      setModelItems([])
      setTruncated(false)
      return
    }

    let cancelled = false
    const timer = window.setTimeout(() => {
      setLoading(true)
      setError('')
      const franchiseRequest = listCatalogFranchises(category, DISCOVERY_LIMIT, query.trim() || undefined)
      const modelRequest = modelSearchActive
        ? listCatalogModels({
            category,
            search: query.trim(),
            limit: MODEL_DISCOVERY_LIMIT,
          })
        : Promise.resolve({ items: [] as CatalogModelCard[], nextCursor: null, totalApprox: 0 })

      Promise.all([franchiseRequest, modelRequest])
        .then(([franchisePage, modelPage]) => {
          if (cancelled) return
          setItems(franchisePage.items)
          setTruncated(franchisePage.truncated)
          setModelItems(modelPage.items)
        })
        .catch(() => {
          if (cancelled) return
          setItems([])
          setModelItems([])
          setTruncated(false)
          setError('Não foi possível carregar a descoberta do catálogo agora.')
        })
        .finally(() => { if (!cancelled) setLoading(false) })
    }, query.trim() ? 220 : 0)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [open, mode, category, query, modelSearchActive, searchPending])

  if (!open) return null

  const needle = normalize(query)
  const visible = mode === 'live'
    ? items.filter((item) => !needle || normalize(item.label).includes(needle))
    : demoItems
  const visibleModels = mode === 'live' ? modelItems : demoModelsFound
  const selectedCategoryLabel = categories.find((item) => item.id === category)?.label ?? 'Todos'
  const hasDiscoveryResults = visibleModels.length > 0 || visible.length > 0

  function chooseCategory(next: string) {
    setCategory(next)
    setQuery('')
    setModelItems([])
  }

  function chooseFranchise(item: CatalogFranchise) {
    onSelectFranchise(item.category, item.id)
    onClose()
  }

  function chooseModel(model: CatalogModelCard) {
    onClose()
    window.location.hash = `modelo=${encodeURIComponent(model.slug)}`
  }

  function showCategory() {
    onSelectCategory(category)
    onClose()
  }

  function handleSearchKey(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter' || loading) return
    if (visibleModels[0]) {
      event.preventDefault()
      chooseModel(visibleModels[0])
      return
    }
    if (visible[0] && modelSearchActive) {
      event.preventDefault()
      chooseFranchise(visible[0])
    }
  }

  return (
    <div className="modal-backdrop explorer-backdrop" onMouseDown={onClose}>
      <section className="explorer-modal" role="dialog" aria-modal="true" aria-labelledby="explorer-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="modal-head explorer-head">
          <div>
            <span>NAVEGADOR DO ACERVO</span>
            <h2 id="explorer-title">Encontrar modelos e franquias</h2>
            <p>Pesquise no mesmo índice do catálogo ou navegue por categoria para chegar ao modelo sem percorrer milhares de itens.</p>
          </div>
          <button type="button" aria-label="Fechar navegador" onClick={onClose}>×</button>
        </div>

        <div className="explorer-toolbar">
          <label className={`explorer-search ${searchPending ? 'is-pending' : ''}`}>
            <span aria-hidden="true">⌕</span>
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleSearchKey}
              placeholder="Buscar modelo, personagem, franquia ou código..."
              aria-label="Buscar modelos e franquias"
            />
            {query && <button type="button" onClick={() => setQuery('')} aria-label="Limpar busca">×</button>}
          </label>
          <button type="button" className="explorer-category-action" onClick={showCategory}>
            Ver todos em {selectedCategoryLabel} <span>›</span>
          </button>
        </div>

        <div className="explorer-categories" aria-label="Categorias do navegador">
          {categories.map((item) => (
            <button
              type="button"
              key={item.id}
              className={category === item.id ? 'is-active' : ''}
              aria-pressed={category === item.id}
              onClick={() => chooseCategory(item.id)}
            >
              <span>{item.label}</span>
              <strong>{numberFormatter.format(item.count)}</strong>
            </button>
          ))}
        </div>

        <div className="explorer-results" aria-busy={loading}>
          <div className="explorer-results__head">
            <div>
              <span>{selectedCategoryLabel}</span>
              <strong>{searchPending ? `Digite ${FRANCHISE_SEARCH_MIN_LENGTH}+ caracteres` : query.trim() ? `Resultados para “${query.trim()}”` : 'Franquias em destaque'}</strong>
            </div>
            <small>{loading ? 'Carregando…' : searchPending ? 'Busca indexada' : query.trim() ? `${visibleModels.length} modelos · ${visible.length} franquias` : `${visible.length} franquias`}</small>
          </div>

          {error && <p className="runtime-alert" role="alert">{error}</p>}

          {searchPending ? (
            <div className="explorer-empty explorer-empty--hint">
              <span>⌕</span>
              <strong>Continue digitando</strong>
              <p>Use pelo menos {FRANCHISE_SEARCH_MIN_LENGTH} caracteres para pesquisar rapidamente no índice do catálogo.</p>
            </div>
          ) : !loading && !error && !hasDiscoveryResults ? (
            <div className="explorer-empty">
              <span>⌕</span>
              <strong>Nenhum resultado encontrado</strong>
              <p>Tente outro termo ou volte para “Todos”.</p>
              <button type="button" onClick={() => { setQuery(''); setCategory('all') }}>Limpar recorte</button>
            </div>
          ) : (
            <>
              {modelSearchActive && visibleModels.length > 0 && (
                <>
                  <div className="explorer-results__head">
                    <div><span>MODELOS</span><strong>Correspondências diretas</strong></div>
                    <small>Enter abre o primeiro</small>
                  </div>
                  <div className="explorer-grid">
                    {visibleModels.map((model, index) => (
                      <button type="button" key={model.id} onClick={() => chooseModel(model)}>
                        <span
                          className="explorer-grid__rank"
                          style={model.coverUrl ? { backgroundImage: `url("${model.coverUrl}")`, backgroundSize: 'cover', backgroundPosition: 'center', color: 'transparent' } : undefined}
                          aria-hidden="true"
                        >
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        <span className="explorer-grid__body">
                          <strong>{model.name}</strong>
                          <small>{model.variantName || model.franchise || model.code}</small>
                        </span>
                        <span className="explorer-grid__count">
                          <strong>{model.code}</strong>
                          <small>{numberFormatter.format(model.galleryCount)} imagens</small>
                        </span>
                        <span className="explorer-grid__arrow">›</span>
                      </button>
                    ))}
                  </div>
                </>
              )}

              {visible.length > 0 && (
                <>
                  {modelSearchActive && (
                    <div className="explorer-results__head">
                      <div><span>FRANQUIAS</span><strong>Coleções relacionadas</strong></div>
                      <small>{visible.length} exibidas</small>
                    </div>
                  )}
                  <div className="explorer-grid">
                    {visible.map((item, index) => (
                      <button type="button" key={`${item.category}:${item.id}`} onClick={() => chooseFranchise(item)}>
                        <span className="explorer-grid__rank">{String(index + 1).padStart(2, '0')}</span>
                        <span className="explorer-grid__body">
                          <strong>{item.label}</strong>
                          <small>{categories.find((entry) => entry.id === item.category)?.label ?? item.category}</small>
                        </span>
                        <span className="explorer-grid__count">
                          <strong>{numberFormatter.format(item.count)}</strong>
                          <small>modelos</small>
                        </span>
                        <span className="explorer-grid__arrow">›</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </>
          )}

          {truncated && !loading && !searchPending && (
            <div className="explorer-more">Mostrando as {DISCOVERY_LIMIT} franquias de maior volume neste recorte. Refine a busca para localizar outras coleções.</div>
          )}
        </div>
      </section>
    </div>
  )
}
