# Fontes externas autorizadas

## Objetivo

O catálogo aceita lotes externos sem misturar aquisição de terceiros com a identidade pública do produto. A fronteira é explícita:

`fonte autorizada -> inventário normalizado -> mídia materializada -> manifest externo + auditoria visual -> revisão de identidade/galeria -> promoção`

A origem externa nunca substitui o SSOT do catálogo e nunca autoriza remoção automática de mídia já publicada.

## Configuração da fonte

A identidade comercial, domínio e regras particulares de um fornecedor ficam fora do repositório, em arquivo operacional ignorado pelo Git, por exemplo `.external-ingest/source.json`. O repositório versiona o contrato de ingestão, não a marca ou endpoint de terceiros.

O contrato preserva:

- **1 produto externo = 1 identidade de entrada**;
- `sourceId` é um namespace operacional explícito e path-safe: 1..64 caracteres ASCII minúsculos `[a-z0-9._-]`, começando e terminando por letra/número; qualquer valor com `/`, `\\`, `..`, espaço, Unicode ou capitalização fora do contrato falha fechado antes de gerar caminhos;
- a identidade de entrada deriva de `sourceId + product.id` da origem, nunca de slug, título, preço ou posição;
- `product.id` precisa existir e ser um inteiro positivo; ausência/valor inválido falha fechado;
- slug e permalink são apenas metadata/proveniência e podem mudar sem criar outro produto;
- todas as imagens do produto ficam na mesma identidade de entrada;
- `NSFW`, `Bust`, `Premium`, `Extras` e similares são features/variantes, não categorias públicas novas;
- categoria pública é resolvida apenas por sinais configurados que apontem para o SSOT real;
- conflito ou ausência de categoria/franquia fica `review_*` e bloqueia materialização;
- URL do produto e URL de cada imagem ficam como proveniência.

### Identidade de imagem

Quando a origem fornece `image.id`, esse ID é a identidade estável da mídia e precisa ser um inteiro positivo do provedor. O valor é normalizado para decimal canônico (`"0007"` e `7` viram `"7"`), evitando colisões artificiais de filename após sanitização. `image.id` explicitamente inválido falha fechado; somente ausência real do campo/valor permite fallback.

Quando `image.id` não existe, o pipeline usa `SHA-256(sourceUrl)` como fallback determinístico. A posição `1..N` representa somente a ordem da galeria e **nunca participa da identidade da imagem**.

Cada item do plano registra `sourceImageIdentityKind` (`provider_id` ou `url_sha256`), tornando auditável qual fonte de identidade foi usada sem inferir isso pelo nome do arquivo.

O `targetPath` também não depende da ordem nem do slug do produto: ele usa o namespace seguro da fonte, o ID estável do produto e a identidade estável da imagem. Assim, renomear um produto ou reordenar sua galeria não cria cópias artificiais nem permite path traversal na etapa anterior à materialização.

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
  --media-audit ".external-ingest/vendor-a/media-audit.jsonl" \
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
- mede dimensões, nitidez e `quality_score`;
- preserva uma mesma `public_model_key` para todas as vistas do produto;
- grava proveniência `AUTHORIZED_EXTERNAL_MEDIA`;
- liga a auditoria de marca d'água ao `sourceProductKey + sourceImageId + SHA-256` dos bytes realmente materializados;
- só emite `completeGalleryCoverage=true` quando a galeria inteira foi comprovada;
- não acessa a rede.

Isso impede que um produto anunciado com, por exemplo, 7 imagens seja promovido silenciosamente com apenas 5 materializadas.

### Auditoria de marca d'água

A ausência de marca d'água não é inferida por heurística frágil de pixels. A decisão vem de um artefato operacional auditável, mantido fora do Git, ligado ao hash exato da imagem:

```json
{"version":1,"sourceProductKey":"vendor-a:product:42","sourceImageId":"1001","sha256":"<64 hex>","watermarkStatus":"clear","reviewMethod":"manual_visual"}
```

`watermarkStatus` aceita somente `clear` ou `watermarked`. Sem linha correspondente, a imagem fica `watermark_status=unknown`; se o SHA mudou desde a revisão, o build falha fechado; auditoria sobrando ou apontando para outra mídia também falha. O resumo informa contagens `clear/watermarked/unknown` e `watermarkAuditComplete`.

Esse gate é proposital: mídia externa `watermarked` nunca é promovida, e `unknown` não pode avançar como nova vista nem substituir mídia existente até ser auditada.

## 4. Reconciliar com o catálogo atual

Antes de criar qualquer mapa de identidade, gere um relatório **somente de candidatos** usando o índice público do bundle e os dois manifests de imagem:

```bash
python tools/propose_external_identity_matches.py \
  ".catalog-bundle/models.jsonl" \
  ".catalog-ingest/manifest.jsonl" \
  ".external-ingest/manifest.jsonl" \
  --output ".external-ingest/identity-candidates.json"
```

Esse auditor exige o mesmo escopo de categoria + franquia para formar candidato e combina evidência de nome, SHA-256 e dHash. O resultado é sempre `reviewOnly=true` e `automaticMappings=0`: nome exato, similaridade visual e até SHA idêntico **não autorizam merge**, porque variantes distintas ou modelos que compartilham uma imagem podem coexistir. SHA igual encontrado fora da taxonomia esperada vira `scopeConflict`, não candidato silencioso.

Registre uma decisão explícita para **cada** identidade de entrada em `identity-decisions.csv`:

```csv
incoming_model,decision,target_model,reason
vendor-a / product:42,map_existing,Games / Street Fighter / Ryu / ryu,mesma escultura e vistas confirmadas
vendor-a / product:43,new_model,,variante escultórica distinta
```

`decision` aceita somente `map_existing` ou `new_model`. `map_existing` exige que o alvo esteja no relatório de candidatos; `new_model` exige alvo vazio; toda decisão exige justificativa. Dois produtos externos não podem ser resolvidos para o mesmo modelo público.

Gere o mapa auditável:

```bash
python tools/resolve_external_identity_matches.py \
  ".external-ingest/identity-candidates.json" \
  ".external-ingest/identity-decisions.csv" \
  --output ".external-ingest/model-identity-map.csv" \
  --summary ".external-ingest/identity-resolution.json"
```

O `model-identity-map.csv` registra também o SHA-256 canônico do relatório de candidatos e a justificativa da decisão. Se todas as entradas forem `new_model`, o mapa fica sem linhas e o `--mapping` deve ser omitido no passo seguinte.

Depois da resolução, use o fluxo canônico de galeria. Para lotes com pelo menos um `map_existing`:

```bash
python tools/plan_gallery_merge.py \
  ".catalog-ingest/manifest.jsonl" \
  ".external-ingest/manifest.jsonl" \
  --mapping ".external-ingest/model-identity-map.csv" \
  --output ".external-ingest/gallery-merge-plan.json"
```

Produto externo que corresponda a ficha já existente deve usar esse mapa explícito. Nome, slug ou similaridade perceptual nunca autorizam fusão de identidade. Candidatos perceptuais continuam exigindo revisão por `resolve_gallery_merge.py` e manifesto de promoção auditável.

Depois que a identidade do modelo é explicitamente reconciliada, a seleção de mídia segue esta ordem:

1. duplicata SHA-256 exata é ignorada, porque não acrescenta conteúdo;
2. mídia externa `watermarked` é rejeitada mesmo que tenha resolução ou `quality_score` maior;
3. mídia externa `unknown` bloqueia a promoção até existir auditoria ligada ao SHA atual;
4. entre vistas equivalentes elegíveis, dimensões, nitidez, formato e `quality_score` determinam a recomendação técnica;
5. uma externa comprovadamente `clear` é recomendada antes de uma vista equivalente `watermarked` **ou de legado sem auditoria específica de watermark**; entre duas mídias explicitamente elegíveis/limpas, vence a qualidade técnica;
6. uma vista realmente distinta e `clear` é adicionada à galeria, em vez de substituir outra vista;
7. qualquer substituição por similaridade perceptual continua exigindo decisão explícita; não existe delete automático do master.

Assim, duas fontes podem conter o mesmo personagem/modelo sem criar duas fichas públicas e sem fazer a fonte externa vencer apenas por ser nova: vence a mídia comprovadamente limpa e tecnicamente melhor para aquela vista.

## Política NSFW

NSFW continua sendo uma feature/variante transversal. Não existe categoria pública paralela `NSFW`.

Se a fonte publicar produtos Normal e NSFW como produtos separados, eles permanecem identidades de entrada separadas enquanto tiverem IDs de produto distintos, até uma revisão explícita decidir a relação correta.
