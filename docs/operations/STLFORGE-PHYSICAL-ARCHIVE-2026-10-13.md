# STLForge.cl — acervo físico externo

## Objetivo

Materializar no computador uma cópia operacional da mídia pública do catálogo `stlforge.cl`, preservando IDs e proveniência, para depois reconciliar cada produto/imagem com o catálogo Tonecos sem atrasar o lançamento.

## Decisão de rollout

- Produção atual permanece como está.
- Nenhum write em D1, R2 ou catálogo canônico é permitido durante a aquisição.
- A correção pública será incremental, por lotes explicitamente revisados.
- A fonte externa nunca vira SSOT do catálogo.
- Nenhum STL/arquivo pago é adquirido por este fluxo; somente metadados e imagens expostos pela Store API pública.
- A ingestão/promocão posterior continua obedecendo `docs/FONTES-EXTERNAS.md`.

## Fonte técnica

- Base: `https://stlforge.cl`
- Inventário: WooCommerce Store API pública `GET /wp-json/wc/store/v1/products`.
- Identidade de produto: `product.id`.
- Identidade preferida de imagem: `image.id`; fallback somente se ausente.
- URL `image.src` é preservada como proveniência.

## Workspace

Branch isolada:

`ingest/stlforge-physical-archive-20261013`

Worktree:

`C:\Users\TONECOS\Apps\VisualAgentLab\catalogo-stlforge-ingest`

Aquisição operacional, fora do Git:

`C:\Users\TONECOS\Documents\catalogo\.external-acquisition\stlforge-cl`

Arquivos esperados:

- `raw/products-page-NNN.json`: resposta bruta paginada da Store API;
- `products.jsonl`: inventário normalizado;
- `image-download-plan.jsonl`: relação produto/imagem/URL/caminho;
- `media/product-<productId>/<imageId>.<ext>`: bytes materializados;
- `download-manifest.jsonl`: SHA-256, bytes e proveniência por arquivo;
- `summary.json`: fechamento mensurável;
- `errors.json`: falhas sem mascaramento.

## Invariantes

1. caminhos físicos derivam de IDs estáveis, não de título/slug;
2. reexecução é resumível e não deve redownloadar arquivo já materializado;
3. toda imagem materializada recebe SHA-256;
4. URLs fora de `https://stlforge.cl/` falham fechado;
5. `productionWrites=0` e `canonicalCatalogMutations=0` durante esta fase;
6. não mover automaticamente nada para as seis categorias públicas;
7. reconciliação de identidade é revisão explícita: `map_existing` ou `new_model`;
8. imagem externa não substitui mídia pública por simples nome/SHA/dHash;
9. mídia já online continua servindo até promoção validada de um delta;
10. o lançamento não depende do término deste acervo externo.

## Próximo gate

Depois da materialização completa:

1. conferir `summary.json` com `errors=0`;
2. adaptar/exportar o inventário ao contrato de `tools/import_woocommerce_catalog.py`;
3. gerar manifest técnico externo;
4. auditar watermark/qualidade;
5. gerar candidatos contra o catálogo atual;
6. resolver identidades explicitamente;
7. publicar apenas deltas aprovados, em lotes pequenos;
8. validar produção após cada lote.

Nunca promover a árvore de aquisição diretamente.
