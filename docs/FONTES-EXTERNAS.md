# Fontes externas autorizadas

## Objetivo

O catálogo aceita lotes externos sem misturar aquisição de terceiros com a identidade pública do produto. A fronteira é explícita:

`fonte autorizada -> inventário normalizado -> mídia materializada -> manifest externo -> revisão de galeria -> promoção`

A origem externa nunca substitui o SSOT do catálogo e nunca autoriza remoção automática de mídia já publicada.

## Configuração da fonte

A identidade comercial, domínio e regras particulares de um fornecedor ficam fora do repositório, em arquivo operacional ignorado pelo Git, por exemplo `.external-ingest/source.json`. O repositório versiona o contrato de ingestão, não a marca ou endpoint de terceiros.

O contrato preserva:

- **1 produto externo = 1 identidade de entrada**;
- a identidade de entrada deriva de `sourceId + product.id` da origem, nunca de slug, título, preço ou posição;
- `product.id` precisa existir e ser um inteiro positivo; ausência/valor inválido falha fechado;
- slug e permalink são apenas metadata/proveniência e podem mudar sem criar outro produto;
- todas as imagens do produto ficam na mesma identidade de entrada;
- `NSFW`, `Bust`, `Premium`, `Extras` e similares são features/variantes, não categorias públicas novas;
- categoria pública é resolvida apenas por sinais configurados que apontem para o SSOT real;
- conflito ou ausência de categoria/franquia fica `review_*` e bloqueia materialização;
- URL do produto e URL de cada imagem ficam como proveniência.

### Identidade de imagem

Quando a origem fornece `image.id`, esse ID é a identidade estável da mídia. Quando não fornece, o pipeline usa `SHA-256(sourceUrl)` como fallback determinístico. A posição `1..N` representa somente a ordem da galeria e **nunca participa da identidade da imagem**.

O `targetPath` também não depende da ordem nem do slug do produto: ele usa o ID estável do produto e a identidade estável da imagem. Assim, renomear um produto ou reordenar sua galeria não cria cópias artificiais.

## Aquisição é uma etapa separada

Os scripts do repositório **não fazem bulk scraping nem download de rede**. Eles consomem somente export/API obtido com autorização e bytes de imagem já materializados por um meio autorizado.

Isso evita transformar aquisição em dependência arquitetural e mantém o pipeline compatível com fontes próprias, exports do fornecedor ou integrações formalmente autorizadas.

## 1. Normalizar um export WooCommerce autorizado

Exemplo:

```bash
python tools/import_woocommerce_catalog.py \
  "/caminho/products.json" \
  --source-id vendor-a \
  --config ".external-ingest/source.json" \
  --output ".external-ingest"
```

Saídas:

```text
.external-ingest/vendor-a/
  products.jsonl
  image-download-plan.jsonl
  summary.json
```

`products.jsonl` contém uma linha por produto e registra:

- `sourceProductId`: ID imutável normalizado da origem;
- `sourceProductKey`: chave namespaced `sourceId:product:<id>`;
- `incomingModel`: identidade `sourceId / product:<id>` usada pelo planner;
- `sourceSlug`, título original e `displayName` como metadata;
- candidato de categoria pública e franquia;
- features, contagem de imagens e estado de revisão.

`image-download-plan.jsonl` contém uma linha por imagem, com URL de origem, posição de galeria e `targetPath` determinístico. O arquivo é um **plano de materialização**, não um downloader.

Exemplo de caminho estável:

```text
vendor-a/product-42/1001.webp
```

Se `image.id` não existir, o nome usa a identidade derivada do hash da URL. O resumo sempre registra `networkDownloads=0`.

## 2. Materializar mídia por meio autorizado

Depois que os bytes forem obtidos por um canal autorizado, grave-os sob uma raiz local preservando os `targetPath` do plano. Nenhum arquivo precisa ser renomeado manualmente além do plano determinístico.

## 3. Gerar o manifesto técnico externo

```bash
python tools/build_external_image_manifest.py \
  ".external-ingest/vendor-a/products.jsonl" \
  ".external-ingest/vendor-a/image-download-plan.jsonl" \
  --images-root "/caminho/fonte-autorizada" \
  --output ".external-ingest/manifest.jsonl" \
  --summary ".external-ingest/manifest-summary.json"
```

Esse gate:

- recusa produto ainda em `review_*`;
- exige `imageCount` positivo;
- exige cobertura integral da galeria e posições contíguas `1..N` sem duplicação;
- recusa path traversal e arquivo ausente;
- abre/verifica cada imagem;
- recalcula SHA-256 e dHash;
- mede dimensões, nitidez e quality score;
- preserva uma mesma `public_model_key` para todas as vistas do produto;
- grava proveniência `AUTHORIZED_EXTERNAL_MEDIA`;
- só emite `completeGalleryCoverage=true` quando a galeria inteira foi comprovada;
- não acessa a rede.

Isso impede que um produto anunciado com, por exemplo, 7 imagens seja promovido silenciosamente com apenas 5 materializadas.

## 4. Reconciliar com o catálogo atual

Use o fluxo canônico:

```bash
python tools/plan_gallery_merge.py \
  ".catalog-ingest/manifest.jsonl" \
  ".external-ingest/manifest.jsonl" \
  --output ".external-ingest/gallery-merge-plan.json"
```

Produto externo que corresponda a ficha já existente deve usar `model-identity-map.csv` explícito. Nome, slug ou similaridade perceptual nunca autorizam fusão de identidade. Candidatos perceptuais continuam exigindo revisão por `resolve_gallery_merge.py` e manifesto de promoção auditável.

## Política NSFW

NSFW continua sendo uma feature/variante transversal. Não existe categoria pública paralela `NSFW`.

Se a fonte publicar produtos Normal e NSFW como produtos separados, eles permanecem identidades de entrada separadas enquanto tiverem IDs de produto distintos, até uma revisão explícita decidir a relação correta.
