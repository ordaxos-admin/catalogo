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

## Checkpoint medido — 2026-10-14

- inventário bruto preservado: **1.626 produtos / 12.535 referências lógicas**;
- plano físico bruto: **12.499 caminhos únicos**;
- export saneado para modelos individuais: **1.625 produtos / 12.486 imagens**;
- 36 URLs repetidas dentro da mesma galeria foram removidas apenas do export saneado; 0 conflitos `image.id -> URL`;
- `product:27152` (Master Vault 1500+ HQ Models) permanece no staging bruto, mas não é tratado como modelo individual;
- normalização canônica: **1.307 readyForMaterialization / 318 requiresReview**;
- categoria: 1.471 resolved / 30 conflict / 124 missing;
- franquia: 1.361 resolved / 21 ambiguous / 243 missing;
- checkpoint histórico que disparou a mudança: **6.310 arquivos / 9,071 GiB** locais e **5,962 GiB livres** no C:;
- esse checkpoint não é mais o desenho operacional: a aquisição integral local foi substituída pelo staging privado + cache transitório;
- checkpoint após a migração parcial desta sessão: **1.208 objetos `remoteVerified`**, **5.667 arquivos / 3,896 GiB** ainda no cache local e **0 arquivos de imagem** em `Revisao`;
- o espaço livre global do C: oscila porque outros workstreams simultâneos também escrevem no disco; o catálogo deve manter seu próprio cache decrescente e nunca tentar compensar isso reduzindo o gate de segurança;
- árvore humana de revisão: `C:\Users\TONECOS\Documents\catalogo\Revisao\STLForge.cl`;
- histórico pré-revisão: a árvore de revisão usava hardlinks NTFS; esse desenho foi substituído abaixo por índice somente de metadata;
- PR de handoff: **#174**, mantida draft;
- produção continua com `productionWrites=0` e `canonicalCatalogMutations=0`.

## Arquitetura revisada para armazenamento pequeno — 2026-10-14

A mídia externa não deve permanecer integralmente no notebook. O disco local é somente cache transitório e limitado.

- bucket privado de staging: `tonecos-catalogo-staging`;
- produção continua separada em `tonecos-catalogo-media`; staging nunca é origem pública do frontend;
- objetos externos são content-addressed por SHA-256 em `stlforge-cl/sha256/<prefix>/<sha256>.<ext>`;
- uploader temporário: Worker `catalog-staging-uploader`, restrito ao IP operacional atual e ao bucket de staging;
- todo upload exige SHA-256 declarado, o Worker recalcula o hash, grava metadata `sha256` e a ferramenta local confirma novamente via `HEAD` tamanho + hash;
- somente após o `HEAD` remoto confirmado o cache local pode ser removido;
- `staging-offload-state.json` é o checkpoint resumível e liga `targetPath`, produto, imagem, URL de origem, SHA-256 e `remoteKey`;
- `offload_to_staging.py` esvazia cache local com concorrência limitada, mas serializa checkpoint e deleção;
- `stream_to_staging.py` transmite itens ainda não materializados `stlforge.cl -> memória -> validação PIL/SHA -> R2 staging`, sem escrever mídia no disco;
- as ferramentas operacionais se localizam pelo próprio diretório de aquisição, sem caminho absoluto da máquina;
- o endpoint temporário de upload é injetado por `CATALOG_STAGING_UPLOAD_URL`; o streaming exige também `CATALOG_STAGING_SOURCE_ORIGIN`, sem URL operacional embutida no código;
- `acquire.py` reconhece `remoteVerified` e mantém piso de **15 GiB livres**, impedindo redownload dos objetos já no staging;
- `Revisao/STLForge.cl` passa a ser somente árvore de metadata; não mantém hardlinks nem fallback `copy2` de mídia;
- o rebuild de `Revisao/STLForge.cl` valida pai/nome e rejeita symlink antes de qualquer `rmtree`, impedindo deleção fora da árvore derivada autorizada;
- limite operacional do uploader: 90 MiB, abaixo do limite de request de 100 MB aplicável a Free/Pro; o cache medido não tinha objetos acima desse limite;
- o Worker temporário deve ser desativado/removido após a migração de staging.

A promoção pública continua sendo outra etapa: `staging -> reconciliação explícita -> qualidade/watermark -> map_existing|new_model -> delta aprovado -> tonecos-catalogo-media/D1`. Subir para staging **não publica** nada ao usuário.

## Próximo gate

A conclusão do download total **não bloqueia** a correção incremental. Enquanto o gate de disco estiver ativo:

1. offload do cache legado até restabelecer pelo menos **15 GiB livres**;
2. transmitir a mídia ainda ausente diretamente para staging, sem persistência local;
3. reconstruir `Revisao/STLForge.cl` somente com metadata e referências `remoteKey`;
4. selecionar produtos `readyForMaterialization` cuja galeria esteja 100% verificada no staging;
5. auditar visualmente watermark/qualidade em lotes sob demanda e registrar a decisão por SHA;
6. gerar candidatos contra o catálogo atual;
7. resolver identidade somente por decisão explícita `map_existing` ou `new_model`;
8. publicar apenas deltas aprovados e validar produção após cada lote;
9. manter cache local pequeno e nunca usar o notebook como arquivo definitivo da fonte externa.

Nunca promover a árvore de aquisição ou `Revisao` diretamente.
