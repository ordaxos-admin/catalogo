import type { CatalogCategory, CatalogModel } from '../types/catalog'

const demoEnabled = import.meta.env.VITE_CATALOG_MODE === 'demo'

const demoCategories: CatalogCategory[] = [
  { id: 'all', label: 'Todos', count: 100234 },
  { id: 'games', label: 'Games', count: 18542 },
  { id: 'anime', label: 'Animes & Desenhos', count: 15871 },
  { id: 'movies', label: 'Filmes & Séries', count: 21408 },
  { id: 'comics', label: 'Marvel & DC', count: 12963 },
  { id: 'horror', label: 'Terror', count: 6728 },
  { id: 'tokusatsu', label: 'Tokusatsu', count: 4315 },
  { id: 'originals', label: 'Originais', count: 3102 },
  { id: 'other', label: 'Outros', count: 17305 },
]

const makeImages = (prefix: string, count: number, base: number) =>
  Array.from({ length: count }, (_, index) => ({
    id: `${prefix}-${index + 1}`,
    width: index === 0 ? 2400 : 1800 + ((index * 137) % 500),
    height: index === 0 ? 3000 : 2200 + ((index * 113) % 700),
    bytes: base + index * 142731,
    role: index === 0 ? ('cover' as const) : ('gallery' as const),
    qualityScore: Math.max(50, 100 - index),
  }))

const demoModels: CatalogModel[] = [
  {
    id: 'mdl-000001', slug: 'sentinela-rubra', code: 'TS-000001', name: 'Sentinela Rubra',
    variantName: '',
    franchise: 'Crônicas de Ferro', category: 'games', collection: 'Linha Prime', material: 'Resina',
    heightCm: 28, galleryCount: 24, accent: '#d35f42', tags: ['guerreiro', 'espada', 'fantasia'],
    description: 'Guerreiro de elite em pose de combate, com acabamento de armadura envelhecida e base rochosa.',
    images: makeImages('sentinela-rubra', 8, 2600000),
  },
  {
    id: 'mdl-000002', slug: 'cacadora-celeste', code: 'TS-000002', name: 'Caçadora Celeste',
    variantName: '',
    franchise: 'Skybound', category: 'games', collection: 'Art Scale', material: 'Resina',
    heightCm: 31, galleryCount: 18, accent: '#d7b26d', tags: ['arqueira', 'fantasia', 'heroína'],
    description: 'Arqueira aventureira com composição leve, múltiplos detalhes de tecido e base temática.',
    images: makeImages('cacadora-celeste', 7, 2300000),
  },
  {
    id: 'mdl-000003', slug: 'guardiao-onix', code: 'TS-000003', name: 'Guardião Ônix',
    variantName: '',
    franchise: 'Núcleo Ônix', category: 'games', collection: 'Master Collection', material: 'Resina',
    heightCm: 42, galleryCount: 36, accent: '#6383a8', tags: ['armadura', 'energia', 'colosso'],
    description: 'Guardião pesado com placas segmentadas, núcleo energético e presença de escala premium.',
    images: makeImages('guardiao-onix', 10, 4100000),
  },
  {
    id: 'mdl-000004', slug: 'samurai-neon', code: 'TS-000004', name: 'Samurai Neon',
    variantName: '',
    franchise: 'Neo Edo', category: 'originals', collection: 'Linha Prime', material: 'Resina',
    heightCm: 30, galleryCount: 21, accent: '#9b5344', tags: ['samurai', 'katana', 'cyberpunk'],
    description: 'Samurai futurista com silhueta agressiva, katana tecnológica e visual industrial.',
    images: makeImages('samurai-neon', 8, 2950000),
  },
  {
    id: 'mdl-000005', slug: 'oraculo-mecanica', code: 'TS-000005', name: 'Oráculo Mecânica',
    variantName: '',
    franchise: 'Clockwork', category: 'anime', collection: 'Edição Especial', material: 'Resina',
    heightCm: 26, galleryCount: 28, accent: '#cbbca2', tags: ['android', 'steampunk', 'fantasia'],
    description: 'Personagem mecânica de aparência elegante, com aro ornamental e detalhes de alta precisão.',
    images: makeImages('oraculo-mecanica', 9, 2500000),
  },
  {
    id: 'mdl-000006', slug: 'arcanja-solaris', code: 'TS-000006', name: 'Arcanja Solaris',
    variantName: '',
    franchise: 'Solaris', category: 'originals', collection: 'Art Scale', material: 'Resina',
    heightCm: 36, galleryCount: 31, accent: '#d6d0bd', tags: ['asas', 'anjo', 'fantasia'],
    description: 'Figura alada com leitura vertical forte, armadura clara e base de apresentação ampla.',
    images: makeImages('arcanja-solaris', 10, 3700000),
  },
  {
    id: 'mdl-000007', slug: 'colosso-tirano', code: 'TS-000007', name: 'Colosso Tirano',
    variantName: '',
    franchise: 'Bestiário', category: 'games', collection: 'Master Collection', material: 'Resina',
    heightCm: 45, galleryCount: 42, accent: '#8c735b', tags: ['monstro', 'armadura', 'besta'],
    description: 'Criatura de grande porte com armadura quebrada, textura orgânica e base de ruínas.',
    images: makeImages('colosso-tirano', 12, 4900000),
  },
  {
    id: 'mdl-000008', slug: 'lobo-das-sombras', code: 'TS-000008', name: 'Lobo das Sombras',
    variantName: '',
    franchise: 'Nocturne', category: 'games', collection: 'Linha Prime', material: 'Resina',
    heightCm: 33, galleryCount: 27, accent: '#6b5b50', tags: ['caçador', 'lobo', 'sombrio'],
    description: 'Caçador acompanhado por uma fera, composição baixa e larga com atmosfera noturna.',
    images: makeImages('lobo-das-sombras', 9, 3300000),
  },
  {
    id: 'mdl-000009', slug: 'tita-arcano', code: 'TS-000009', name: 'Titã Arcano',
    variantName: '',
    franchise: 'Forge Unit', category: 'games', collection: 'Art Scale', material: 'Resina',
    heightCm: 38, galleryCount: 19, accent: '#c28a43', tags: ['mecha', 'robô', 'industrial'],
    description: 'Unidade mecânica robusta com placas de manutenção, pistões e acabamento industrial.',
    images: makeImages('tita-arcano', 7, 3850000),
  },
  {
    id: 'mdl-000010', slug: 'sereia-do-abismo', code: 'TS-000010', name: 'Sereia do Abismo',
    variantName: '',
    franchise: 'Abyss', category: 'originals', collection: 'Edição Especial', material: 'Resina',
    heightCm: 29, galleryCount: 33, accent: '#718aa8', tags: ['sereia', 'água', 'fantasia'],
    description: 'Escultura fluida com cabelos em movimento e efeitos aquáticos integrados à base.',
    images: makeImages('sereia-do-abismo', 10, 3200000),
  },
]

export const categories: CatalogCategory[] = demoEnabled ? demoCategories : []
export const models: CatalogModel[] = demoEnabled ? demoModels : []
