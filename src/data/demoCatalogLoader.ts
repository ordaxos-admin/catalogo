import type { CatalogCategory, CatalogModel } from '../types/catalog'

export type DemoCatalog = {
  categories: CatalogCategory[]
  models: CatalogModel[]
}

let pending: Promise<DemoCatalog> | null = null

export function loadDemoCatalog(): Promise<DemoCatalog> {
  if (import.meta.env.VITE_CATALOG_MODE !== 'demo') {
    return Promise.reject(new Error('demo_catalog_not_enabled'))
  }

  if (!pending) {
    pending = import('./mockCatalog').then(({ categories, models }) => ({ categories, models }))
  }
  return pending
}
