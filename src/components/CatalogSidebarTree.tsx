import { useCallback, useEffect, useRef, useState } from 'react'
import { listCatalogFolders } from '../services/catalogApi'
import type { CatalogFolder } from '../types/catalog'

const ROOT = '__root__'
const AUTO_EXPAND_GROUP_LIMIT = 2
const formatter = new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 })

type CatalogSidebarTreeProps = {
  category: string
  franchise: string
  activeFolder: string
  trail: Array<{ id: string; label: string }>
  activeFolderCount?: number
  onSelectFolder: (folder: string) => void
}

function cacheKey(parent: string) {
  return parent || ROOT
}

function activeBranchPaths(folder: string, trail: Array<{ id: string }>) {
  const paths = new Set(trail.map((item) => item.id))
  const parts = folder.split('/').map((part) => part.trim()).filter(Boolean)
  for (let index = 0; index < parts.length; index += 1) paths.add(parts.slice(0, index + 1).join('/'))
  return Array.from(paths)
}

export function CatalogSidebarTree({ category, franchise, activeFolder, trail = [], activeFolderCount, onSelectFolder }: CatalogSidebarTreeProps) {
  const [childrenByParent, setChildrenByParent] = useState<Record<string, CatalogFolder[]>>({})
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(activeBranchPaths(activeFolder, trail)))
  const [loadingParents, setLoadingParents] = useState<Set<string>>(new Set())
  const [errorParents, setErrorParents] = useState<Set<string>>(new Set())
  const loadedParents = useRef(new Set<string>())
  const pendingParents = useRef(new Map<string, Promise<void>>())
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const loadChildren = useCallback((parent: string) => {
    const key = cacheKey(parent)
    if (loadedParents.current.has(key)) return Promise.resolve()
    const pending = pendingParents.current.get(key)
    if (pending) return pending

    setLoadingParents((current) => new Set(current).add(key))
    setErrorParents((current) => {
      const next = new Set(current)
      next.delete(key)
      return next
    })

    const request = listCatalogFolders(category, franchise, parent || undefined)
      .then((page) => {
        if (!mounted.current) return
        loadedParents.current.add(key)
        setChildrenByParent((current) => ({ ...current, [key]: page.items }))
      })
      .catch(() => {
        if (!mounted.current) return
        setErrorParents((current) => new Set(current).add(key))
      })
      .finally(() => {
        pendingParents.current.delete(key)
        if (!mounted.current) return
        setLoadingParents((current) => {
          const next = new Set(current)
          next.delete(key)
          return next
        })
      })

    pendingParents.current.set(key, request)
    return request
  }, [category, franchise])

  useEffect(() => { void loadChildren('') }, [loadChildren])

  useEffect(() => {
    if (activeFolder) return
    const groups = (childrenByParent[ROOT] ?? []).filter((item) => item.hasChildren)
    if (!groups.length || groups.length > AUTO_EXPAND_GROUP_LIMIT) return
    setExpanded((current) => {
      const next = new Set(current)
      groups.forEach((item) => next.add(item.id))
      return next
    })
    groups.forEach((item) => { void loadChildren(item.id) })
  }, [activeFolder, childrenByParent, loadChildren])

  useEffect(() => {
    const branch = activeBranchPaths(activeFolder, trail)
    if (!branch.length) return
    setExpanded((current) => {
      const next = new Set(current)
      branch.forEach((path) => next.add(path))
      return next
    })
    branch.forEach((path) => { void loadChildren(path) })
  }, [activeFolder, trail, loadChildren])

  function toggleFolder(item: CatalogFolder) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(item.id)) next.delete(item.id)
      else next.add(item.id)
      return next
    })
    if (!expanded.has(item.id)) void loadChildren(item.id)
  }

  function selectFolder(item: CatalogFolder) {
    onSelectFolder(item.id)
    if (!item.hasChildren) return
    setExpanded((current) => new Set(current).add(item.id))
    void loadChildren(item.id)
  }

  function renderBranch(parent: string, depth: number): React.ReactNode {
    const key = cacheKey(parent)
    const items = [...(childrenByParent[key] ?? [])].sort((left, right) => Number(right.hasChildren) - Number(left.hasChildren))
    return items.map((item) => {
      const isExpanded = expanded.has(item.id)
      const isActive = activeFolder === item.id
      const childKey = cacheKey(item.id)
      return (
        <div className={`storefront-tree-node ${isExpanded ? 'is-expanded' : ''} ${isActive ? 'is-active' : ''}`} data-depth={depth} key={item.id}>
          <div className={`storefront-tree-row ${item.hasChildren ? 'is-group' : 'is-leaf'} ${isExpanded ? 'is-expanded' : ''} ${isActive ? 'is-active' : ''}`} data-depth={depth} style={{ '--tree-depth': depth } as React.CSSProperties}>
            {item.hasChildren ? (
              <button type="button" className="storefront-tree-toggle" aria-label={`${isExpanded ? 'Recolher' : 'Expandir'} ${item.label}`} aria-expanded={isExpanded} onClick={() => toggleFolder(item)}>
                <span aria-hidden="true">{isExpanded ? '▾' : '▸'}</span>
              </button>
            ) : <span className="storefront-tree-leaf" aria-hidden="true">•</span>}
            <button type="button" className="storefront-tree-select" aria-current={isActive ? 'page' : undefined} onClick={() => selectFolder(item)}>
              <strong>{item.label}</strong><small>{formatter.format(isActive && activeFolderCount !== undefined ? activeFolderCount : item.count)}</small>
            </button>
          </div>
          {item.hasChildren && isExpanded && (
            <div className="storefront-tree-children" role="group" aria-label={`Subpastas de ${item.label}`}>
              {loadingParents.has(childKey) && <span className="storefront-tree-status">Carregando…</span>}
              {errorParents.has(childKey) && <button type="button" className="storefront-tree-retry" onClick={() => void loadChildren(item.id)}>Tentar novamente</button>}
              {!loadingParents.has(childKey) && !errorParents.has(childKey) && renderBranch(item.id, depth + 1)}
            </div>
          )}
        </div>
      )
    })
  }

  const rootLoading = loadingParents.has(ROOT)
  const rootError = errorParents.has(ROOT)
  const rootItems = childrenByParent[ROOT] ?? []

  return (
    <div className="storefront-folder-tree" aria-label="Estrutura da franquia">
      {activeFolder && <button type="button" className="storefront-folder-tree__all" onClick={() => onSelectFolder('')}>Todos da franquia</button>}
      {rootLoading && <span className="storefront-tree-status">Carregando estrutura…</span>}
      {rootError && <button type="button" className="storefront-tree-retry" onClick={() => void loadChildren('')}>Recarregar estrutura</button>}
      {!rootLoading && !rootError && rootItems.length === 0 && <span className="storefront-tree-status">Sem subdivisões</span>}
      {!rootLoading && !rootError && renderBranch('', 0)}
    </div>
  )
}
