# Publicação de mídia no R2

O bundle de publicação transforma somente imagens `OK` e `canonical=true` do manifesto de ingestão. A origem continua somente leitura.

## Gerar o bundle

```bash
python tools/build_media_bundle.py "/caminho/do/catalogo" ".catalog-ingest/manifest.jsonl" --output ".publish-bundle"
```

A geração mantém `media-build-state.json` no diretório de saída. Cada modelo concluído recebe um fingerprint que inclui SHA/metadados da origem, variantes, opção de original e a regra de taxonomia relevante. Em nova execução, o modelo só é reutilizado se o fingerprint coincidir **e** galeria/capa/variantes ainda existirem e forem legíveis. Isso permite retomar após queda de Drive ou interrupção sem aceitar saída parcial como válida.

O diretório de saída é **single-writer**: `.media-build.lock` usa um lock exclusivo do sistema operacional e uma segunda execução apontando para o mesmo bundle falha imediatamente. O arquivo de lock pode permanecer no diretório sem criar bloqueio obsoleto, porque a posse é determinada pelo processo/handle do sistema operacional. A troca atômica de `media-build-state.json` também faz retry curto e limitado apenas para `PermissionError` transitório (por exemplo, antivírus ou Google Drive no Windows); se o bloqueio persistir, a execução aborta em modo fail-closed.

Para forçar uma recomposição completa, ignorando o checkpoint:

```bash
python tools/build_media_bundle.py "/caminho/do/catalogo" ".catalog-ingest/manifest.jsonl" --output ".publish-bundle" --no-resume
```

Por padrão o bundle **não copia o original** para o R2. O arquivo mestre continua preservado no acervo de origem. Para incluir também o original:

```bash
python tools/build_media_bundle.py "/caminho/do/catalogo" ".catalog-ingest/manifest.jsonl" --output ".publish-bundle" --include-original
```

## Variantes

Cada imagem canônica gera:

- `thumb.webp`: lado máximo 360 px;
- `card.webp`: lado máximo 800 px;
- `detail.webp`: lado máximo 1600 px.

Imagens menores nunca são ampliadas artificialmente.

## Estrutura

```text
.publish-bundle/
  models.jsonl
  publish-summary.json
  media-build-state.json
  r2-publish-state.json
  r2/
    gallery/<model-id>/<hash-do-manifesto>.json
    media/<model-id>/<image-id>/thumb.webp
    media/<model-id>/<image-id>/card.webp
    media/<model-id>/<image-id>/detail.webp
```

O manifesto da galeria é **content-addressed**: se a galeria não muda, a chave é idêntica; se uma imagem entra, sai ou muda de posição/metadados, uma nova chave é gerada. Isso permite cache longo sem sobrescrever um manifesto antigo que ainda esteja sendo usado.

`models.jsonl` é a ponte para alimentar o D1. Cada linha contém:

- ID determinístico do produto/modelo 3D;
- slug público e código estável `TS-*`;
- categoria/franquia com nome e slug;
- coleção intermediária, quando existir;
- hierarquia completa de origem;
- `searchText` para indexação;
- número de imagens;
- capa;
- chave e versão do manifesto de galeria.

Slugs colidentes recebem um sufixo determinístico derivado do ID do modelo. Portanto dois personagens diferentes nunca disputam a mesma URL por acidente.

### Catálogo auditado e galerias

No modo `--audit-registry`, a identidade pública vem de `modelo_publico` quando essa coluna está preenchida. Registros com a **mesma hierarquia + mesmo `modelo_publico`** são agrupados em uma única ficha e cada registro canônico vira uma imagem da galeria.

Exemplo: se 17 vistas auditadas de um mesmo produto em `Dragon Ball/Androides/Androide 18` compartilham `modelo_publico=androide18-modelo-01`, o bundle produz **1 modelo com 17 imagens**, não 17 modelos.

Para compatibilidade com o acervo histórico, registros sem `modelo_publico` ainda usam a identidade baseada no arquivo e podem continuar aparecendo como entradas individuais. Novos lotes — especialmente fonte externa — não devem depender desse fallback.

Antes de gerar o bundle após integrar uma nova fonte, `tools/plan_gallery_merge.py` deve separar duplicatas exatas, novas vistas e candidatos visuais. Similaridade perceptual nunca elimina automaticamente uma imagem; ela apenas recomenda qual fonte possui maior qualidade para revisão.

### Taxonomia pública sem mover a origem

`config/catalog-taxonomy.json` permite reorganizar a navegação pública sem renomear ou mover a árvore auditada no Drive. As regras são explícitas por franquia e caminho; não existe classificação automática baseada apenas no nome do personagem.

O bundle grava `folderPath` e `folderPathKey` em cada modelo. Por exemplo, a origem `As Tartarugas Ninja/Destruidor` pode continuar intacta enquanto a navegação web publica `Vilões/Destruidor`. Alterar essa pasta pública não altera o ID do modelo nem exige reescrever o arquivo mestre.

## IDs determinísticos

O ID do modelo deriva de uma chave pública determinística. No modo de galeria legado, essa chave é a hierarquia do modelo; no catálogo auditado, ela inclui também a entrada pública representativa. O ID da imagem deriva do SHA-256. Portanto uma nova execução não gera IDs aleatórios para conteúdo que não mudou.

## Capa

A primeira capa automática é a imagem canônica de maior `quality_score`. No futuro o painel administrativo poderá sobrescrever essa escolha sem alterar o arquivo original.

## Upload incremental

O publicador usa a API S3 compatível do R2 e mantém checkpoint local. Depois da primeira publicação, arquivos sem alteração são ignorados sem precisar executar `HEAD` remoto para cada objeto.

Variáveis necessárias:

```text
CLOUDFLARE_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=tonecos-catalogo-media   # opcional; esse já é o padrão
```

Publicar:

```bash
python tools/publish_r2.py ".publish-bundle/r2"
```

Simular o delta sem enviar nada:

```bash
python tools/publish_r2.py ".publish-bundle/r2" --dry-run
```

Fazer uma auditoria remota dos objetos que o checkpoint consideraria prontos:

```bash
python tools/publish_r2.py ".publish-bundle/r2" --verify-remote
```

O modo `--verify-remote` é propositalmente opcional: em um acervo com milhões de arquivos, consultar cada objeto a cada execução seria caro e lento. O checkpoint local guarda tamanho, `mtime`, SHA-256 publicado e metadados HTTP; ele é salvo atomicamente durante a execução para permitir retomada após interrupção.

O publicador grava o SHA-256 como metadata de cada objeto. Galerias content-addressed recebem cache `immutable`; variantes de mídia recebem cache longo, mas não `immutable`, permitindo futura evolução do renderizador sem deixar uma URL permanentemente presa a uma versão antiga.

## Consistência e cache da galeria

A versão do manifesto (`gallery_version`) faz parte do contrato público do modelo. O Worker devolve essa versão nos cards/detalhes e o frontend a inclui nas URLs do detalhe e da galeria. Quando as imagens mudam, a versão muda e a URL de cache também muda; assim uma galeria nova não herda uma resposta antiga do edge ou do cache local.

O Worker valida em runtime, antes de responder a uma galeria:

- `manifest.modelId === models.id`;
- `manifest.version === models.gallery_version`;
- número de imagens igual a `models.image_count`;
- IDs de imagem únicos;
- exatamente uma capa, na primeira posição;
- SHA-256 fonte válido;
- variantes `thumb/card/detail` sob `media/<modelId>/...`, sem traversal ou backslashes;
- `cover_storage_key` do D1 igual à variante `card` da primeira imagem/capa.

Divergência entre D1 e R2 responde `gallery_manifest_invalid` em vez de servir dados parciais.

## Publicação do índice no D1

`tools/publish_d1.py` valida `models.jsonl` e prepara upserts idempotentes para categorias, franquias, árvore de pastas e modelos. Nós pais são publicados antes dos filhos e os modelos recebem `folder_id` somente após a pasta existir.

Sem `--apply`, o comando é somente leitura e imprime o plano:

```bash
python tools/publish_d1.py ".publish-bundle/models.jsonl"
```


### Redução de galeria é fail-closed

Para modelos já publicados, `tools/publish_d1.py` compara o bundle com o D1 antes do R2 e novamente antes do upsert.

- aumentar `imageCount` é permitido quando manifesto/versão representam a nova galeria;
- substituir vistas mantendo a mesma contagem é permitido quando `galleryManifestKey` e `galleryVersion` mudam coerentemente;
- reduzir `imageCount` é bloqueado por padrão;
- o mesmo manifesto content-addressed não pode ser reutilizado com contagem, capa ou versão diferentes;
- mudança de galeria sem nova `galleryVersion` é bloqueada.

Uma redução intencional exige CSV por modelo:

```csv
model_id,current_image_count,new_image_count,current_gallery_version,new_gallery_version,reason
mdl_xxx,4,3,123,456,vista incorreta removida após revisão
```

E o preflight/publicação deve receber:

```bash
python tools/publish_d1.py ".publish-bundle/models.jsonl" --check-production \
  --gallery-shrink-approvals ".external-ingest/gallery-shrink-approvals.csv"
```

A autorização só vale para o delta exato registrado. Se o D1 ou o bundle mudarem, o gate falha e exige nova revisão.

Antes de enviar qualquer mídia nova ao R2, valide também a identidade do bundle contra o D1 atual em modo **somente leitura**:

```bash
python tools/publish_d1.py ".publish-bundle/models.jsonl" --check-production
```

Esse preflight consulta apenas os IDs, slugs e códigos presentes no bundle candidato. Ele bloqueia:

- um novo ID tentando reutilizar slug ou código já publicado;
- um ID existente mudando slug ou código;
- mudança de categoria/franquia de um modelo existente sem migração explícita.

O mesmo gate é executado novamente dentro de `--apply`, imediatamente antes dos upserts. Assim uma validação antiga não autoriza uma escrita posterior se a produção tiver mudado.

Antes do D1, o gate R2 também valida o contrato completo do manifesto: versão, quantidade, IDs únicos, exatamente uma capa na primeira posição, SHA-256 de origem, chaves pertencentes ao mesmo modelo e correspondência da capa do D1 com o manifesto. Qualquer divergência falha antes de escrever o índice.

Para aplicar em produção:

```text
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_D1_DATABASE_ID=
CLOUDFLARE_API_TOKEN=
```

```bash
python tools/publish_d1.py ".publish-bundle/models.jsonl" --apply
```

Os statements são enviados em batches transacionais. O publicador não executa `DELETE`, não despublica itens ausentes e não tenta reconciliar remoções automaticamente; qualquer política destrutiva futura deverá ter fluxo e revisão próprios.

### Aposentadoria explícita de modelos ausentes — fail-closed

Modelos publicados que deixaram de existir no SSOT físico só podem ser despublicados por aprovação explícita. O registro canônico do repositório é `config/catalog-model-retirement-approvals.csv`, com `model_id`, `slug`, `code` e `reason` do estado real de produção.

O preflight deve validar o snapshot e as aprovações contra o D1 atual:

```bash
python tools/publish_d1.py ".publish-bundle/models.jsonl" --check-production \
  --retire-absent-approvals "config/catalog-model-retirement-approvals.csv"
```

Na aplicação, use o mesmo arquivo:

```bash
python tools/publish_d1.py ".publish-bundle/models.jsonl" --apply \
  --retire-absent-approvals "config/catalog-model-retirement-approvals.csv"
```

A aprovação é vinculada ao `id/slug/code` atual e falha se o estado de produção mudar. A aposentadoria altera apenas `published=0`; as mídias no R2 não são apagadas e continuam disponíveis para recuperação. Reparos de galeria que absorvem fichas-fonte devem ser aplicados antes desse gate, para que apenas ausências realmente intencionais sejam aprovadas.

## Segurança e exclusões

- O publicador **não apaga objetos do R2**.
- Uma publicação parcial mantém checkpoint dos uploads já concluídos e pode ser retomada.
- Credenciais R2 nunca entram no repositório.
- Gere credenciais S3 com acesso somente ao bucket do catálogo quando possível.
- `.publish-bundle/` é ignorado pelo Git e nunca deve ser commitado.
