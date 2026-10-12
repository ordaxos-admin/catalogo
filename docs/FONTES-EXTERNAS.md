# Fontes externas autorizadas

## Objetivo

O catálogo aceita lotes externos sem misturar aquisição de terceiros com a identidade pública do produto. A fronteira é explícita:

`fonte autorizada -> inventário normalizado -> mídia materializada -> manifest externo -> revisão de galeria -> promoção`

A origem externa nunca substitui o SSOT do catálogo e nunca autoriza remoção automática de mídia já publicada.

## Configuração da fonte

A identidade, domínio e regras particulares de um fornecedor ficam fora do repositório, em arquivo operacional ignorado pelo Git, por exemplo:

`.external-ingest/source.json`

Contrato esperado:

```json
{
  "version": 1,
  "sources": {
    "vendor-a": {
      "origin": "https://vendor.example",
      "productPathPrefixes": ["/product/"],
      "allowedImageHosts": ["vendor.example", "cdn.vendor.example"],
      "taxonomySignals": {
        "Anime": "Animes & Desenhos",
        "Videogames": "Games",
        "Movies": "Filmes & Séries",
        "Series": "Filmes & Séries",
        "Marvel": "Marvel & DC",
        "DC": "Marvel & DC"
      },
      "nonFranchiseTerms": ["Anime", "Videogames", "Premium", "NSFW"],
      "featureTerms": {
        "nsfw": "NSFW",
        "bust": "Bust",
        "premium": "Premium"
      }
    }
  }
}
```

O repositório versiona o **contrato de ingestão**, não a marca ou endpoint de terceiros. Isso preserva o gate de identidade comercial e permite trocar/adicionar fontes sem hardcode.

O contrato preserva:

- **1 produto externo = 1 identidade de entrada**;
- todas as imagens desse produto ficam na mesma galeria de entrada;
- `NSFW`, `Bust`, `Premium`, `Extras` e similares são recursos/variantes, não categorias públicas novas;
- a categoria pública é resolvida somente por sinais configurados que apontem para o SSOT real;
- conflito ou ausência de categoria/franquia fica `review_*` e bloqueia materialização;
- preço, texto comercial e descrição da loja externa não entram na identidade canônica;
- URL do produto e URL de cada imagem ficam apenas como proveniência.

### Aquisição é uma etapa separada

Os scripts do repositório **não fazem bulk scraping nem download de rede**. Eles consomem somente um export/API obtido com autorização e bytes de imagem já materializados por um meio autorizado.

Isso evita transformar um detalhe de aquisição em dependência arquitetural e mantém o pipeline compatível com fontes próprias, exports do fornecedor ou integrações formalmente autorizadas.

## 1. Normalizar um export WooCommerce autorizado

Exporte a resposta de produtos da WooCommerce Store API para JSON e execute:

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

- `sourceProductKey` estável e namespaced;
- `incomingModel`, usado como identidade do lote externo;
- título original e `displayName` normalizado;
- candidato de categoria pública;
- candidato de franquia;
- features;
- contagem de imagens;
- estado de revisão.

`image-download-plan.jsonl` contém uma linha por imagem, com URL de origem, posição e `targetPath` determinístico. O arquivo é um **plano de materialização**, não um downloader.

O resumo sempre registra `networkDownloads=0`.

## 2. Materializar mídia por meio autorizado

Depois que os bytes forem obtidos por um canal autorizado, grave-os sob uma raiz local preservando os `targetPath` do plano.

Exemplo:

```text
/caminho/fonte-autorizada/
  vendor-a/
    personagem-modelo/
      001-12345.webp
      002-12346.webp
      ...
```

Nenhum arquivo precisa ser renomeado manualmente além do plano determinístico.

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

- recusa produto que ainda esteja em `review_*`;
- recusa path traversal e arquivo ausente;
- abre/verifica cada imagem;
- recalcula SHA-256 e dHash;
- mede dimensões, nitidez e quality score;
- preserva uma mesma `public_model_key` para todas as vistas do produto;
- grava proveniência `AUTHORIZED_EXTERNAL_MEDIA`;
- não acessa a rede.

## 4. Reconciliar com o catálogo atual

Use o fluxo já canônico:

```bash
python tools/plan_gallery_merge.py \
  ".catalog-ingest/manifest.jsonl" \
  ".external-ingest/manifest.jsonl" \
  --output ".external-ingest/gallery-merge-plan.json"
```

Produto externo que corresponda a ficha já existente deve usar `model-identity-map.csv` explícito. Nome parecido nunca é autorização para mesclar.

Depois, candidatos perceptuais continuam exigindo `resolve_gallery_merge.py` e o manifesto de promoção auditável.

## Política NSFW

NSFW continua sendo uma variante/feature transversal. Não existe uma categoria pública paralela `NSFW`.

Um título externo como `Personagem STL + Bust + NSFW – Franquia` pode ser normalizado como:

- display name: `Personagem`;
- franquia candidata: `Franquia`;
- categoria pública candidata: resolvida pelo SSOT configurado;
- features: `Bust`, `NSFW` e demais sinais presentes;
- todas as imagens do mesmo produto permanecem na mesma identidade de entrada.

Se a fonte publicar produtos Normal e NSFW como produtos separados, eles permanecem identidades de entrada separadas até uma revisão explícita decidir a relação correta.
