# Ingestão do acervo

O importador foi desenhado para um acervo com 100 mil+ modelos/imagens e **nunca altera, move ou apaga os arquivos de origem**.

## Objetivo

Transformar a árvore de pastas auditada em um manifesto técnico reutilizável pelo backend e pelo futuro upload ao R2/D1.

```bash
python tools/ingest_catalog.py "<CATALOG_ROOT>" --output ".catalog-ingest" \
  --audit-registry "<CATALOG_ROOT>\00 - ESTATISTICAS - CATALOGO\REGISTRO-AUDITORIA-IMAGENS.csv"
```

Na primeira execução cada imagem elegível é aberta e analisada. Nas execuções seguintes, arquivos cujo caminho, tamanho e `mtime_ns` não mudaram são reaproveitados do checkpoint.

### SSOT das raízes públicas

A fronteira pública é definida em `config/public-catalog-roots.json`. Esse arquivo é o contrato único compartilhável entre o pipeline web e a automação local. Não manter outra lista independente das categorias públicas em scripts de ingestão/auditoria.

`tools/ingest_catalog.py` carrega e valida esse JSON em modo fail-closed: arquivo ausente, versão inválida, categoria vazia/duplicada ou nome inseguro interrompem a execução.

Quando a automação PowerShell do desktop for atualizada, ela deve consumir o mesmo JSON e aplicar a mesma normalização de prefixo `OK - ` e sufixo `[N]`, preservando referências, lotes, estatísticas e `Novos` fora do snapshot público.

### Fronteira de publicação

A raiz real também contém pastas operacionais de auditoria. Por segurança, a ingestão pública é **fail-closed**: somente as seis categorias canônicas entram no manifesto — `Pessoas`, `Animes & Desenhos`, `Filmes & Séries`, `Marvel & DC`, `Games` e `Tokusatsu & Cultura Japonesa`. O prefixo opcional `OK - ` e o sufixo de contagem `[N]` não alteram a identidade da categoria; assim a árvore histórica do Drive e as categorias já auditadas continuam compatíveis. Pastas como `00 - ESTATISTICAS...`, `Novos`, `99 - LOTES CONSOLIDADOS` e qualquer categoria desconhecida ficam fora mesmo que contenham imagens. Se nenhuma categoria canônica existir, o processo falha em vez de publicar conteúdo auxiliar.

Em produção, prefira `--audit-registry` apontando para `REGISTRO-AUDITORIA-IMAGENS.csv`. A raiz pode ser a cópia operacional local ou um espelho montado; a ingestão não depende de Google Drive nem de letra de unidade fixa. O registro é fail-closed: somente `status=OK_VISUAL` ou estados versionados no formato `OK_VISUAL|...` são publicáveis; `PENDENTE` e outros estados são rejeitados. Esse modo usa os caminhos já revisados como fonte de verdade, evita uma varredura recursiva lenta da raiz do acervo e ainda recalcula o SHA-256 de cada imagem: qualquer divergência entre o arquivo atual e o hash auditado interrompe a ingestão.

## Saídas

- `manifest.jsonl`: fonte técnica completa, uma linha por imagem;
- `manifest.csv`: visão humana para conferência;
- `duplicates.json`: grupos de duplicatas/candidatos visuais e a versão recomendada;
- `summary.json`: totais de imagens, erros, grupos, canônicas e modelos detectados.

## Integridade e identidade

Para cada arquivo são registrados:

- caminho relativo;
- tamanho e data técnica de modificação;
- SHA-256;
- dHash perceptual de 64 bits;
- largura, altura e megapixels;
- estimativa de nitidez;
- pontuação de qualidade;
- categoria, franquia e chave hierárquica do modelo.

Arquivos vazios, ilegíveis ou com formato inválido ficam com `status=ERRO` e permanecem intactos no acervo.

## Identidade de modelo e galerias

No modo `--audit-registry`, a coluna `modelo_publico` é a identidade estável do modelo dentro da sua hierarquia. **Todas as imagens que pertencem ao mesmo produto/modelo devem compartilhar o mesmo `modelo_publico`**. Assim, frente, costas, laterais e detalhes continuam como imagens distintas de uma única ficha pública.

O inverso também é obrigatório: **produtos 3D diferentes do mesmo personagem precisam ter `modelo_publico` diferentes**. Nome de personagem, pasta e franquia não são suficientes para agrupar. Isso evita, por exemplo, transformar dezenas de esculturas diferentes de um mesmo personagem em uma única galeria.

Quando `modelo_publico` estiver vazio, o importador mantém o comportamento legado e usa o nome-base do arquivo como identidade, preservando compatibilidade com o catálogo já publicado. Esse fallback não deve ser usado para novos lotes do fonte externa.

Antes de publicar um lote novo ou complementar, use o planejador de galeria:

```bash
python tools/plan_gallery_merge.py ".catalog-ingest/manifest.jsonl" ".external-ingest/manifest.jsonl" --output ".external-ingest/gallery-merge-plan.json"
```

Quando um produto da entrada já corresponde a um modelo público existente, use um **mapa explícito de identidade** em CSV, nunca heurística de nome:

```csv
incoming_model,target_model
fonte / produto-abc,Games / Saga / Heroi / modelo-publico-01
```

```bash
python tools/plan_gallery_merge.py ".catalog-ingest/manifest.jsonl" ".external-ingest/manifest.jsonl" \
  --mapping ".external-ingest/model-identity-map.csv" \
  --output ".external-ingest/gallery-merge-plan.json"
```

O mapa é fail-closed: origem inexistente, alvo inexistente, origem duplicada ou dois produtos diferentes apontando para o mesmo modelo público interrompem o planejamento.


### Gate de decisões visuais

`tools/plan_gallery_merge.py` nunca transforma similaridade perceptual em substituição automática. Quando o plano contém `review_visual_candidate`, gere um CSV de decisões com estas colunas:

```csv
incoming_path,incoming_sha256,decision,replace_sha256
fonte/frente-hq.png,<sha-da-entrada>,replace_existing,<sha-da-vista-atual>
```

Decisões permitidas:

- `keep_existing`: conserva a vista atual e não promove a candidata;
- `add_view`: trata a candidata como ângulo/vista distinta e adiciona à galeria;
- `replace_existing`: promove a candidata e marca qual SHA publicado deverá ser supersedido na publicação; não apaga o arquivo mestre.

Resolva o plano:

```bash
python tools/resolve_gallery_merge.py ".external-ingest/gallery-merge-plan.json" \
  --decisions ".external-ingest/gallery-decisions.csv" \
  --output ".external-ingest/gallery-merge-resolution.json"
```

O resolvedor é fail-closed: candidato visual sem decisão, decisão extra, decisão duplicada ou `replace_sha256` fora dos candidatos interrompem a execução. O JSON de resolução informa apenas promoções autorizadas; ele não move, apaga nem reescreve imagens.


### Manifesto de promoção controlada

Depois de o resolvedor retornar `ready=true`, gere um CSV auditável para a automação local:

```bash
python tools/build_gallery_promotion_manifest.py ".external-ingest/gallery-merge-resolution.json" \
  --output ".external-ingest/gallery-promotions.csv" \
  --summary ".external-ingest/gallery-promotions-summary.json"
```

O CSV contém:

- `authorization_id`: identificador determinístico da autorização;
- `resolution_sha256`: SHA-256 da resolução completa que originou a autorização; qualquer mudança de revisão produz novas autorizações;
- `source_path` e `source_sha256`: origem e hash que devem ser conferidos novamente antes da cópia;
- `target_model`: identidade pública lógica do produto;
- `source_model`: identidade do produto na fonte de entrada;
- `mode`: `add_view` ou `replace_existing`;
- `replace_sha256`: somente quando uma vista publicada deverá ser supersedida.

Esse manifesto **não contém caminho físico de destino por projeto** e não altera arquivos. O PowerShell local deve resolver o destino usando a árvore auditada atual, verificar o SHA da origem imediatamente antes de copiar e falhar se houver divergência, ambiguidade ou colisão. `replace_existing` significa superseder na publicação; não autoriza deletar o mestre antigo.


Antes de qualquer cópia local, valide o CSV contra a resolução e contra os bytes atuais da fonte:

```bash
python tools/verify_gallery_promotion_manifest.py \
  ".external-ingest/gallery-merge-resolution.json" \
  ".external-ingest/gallery-promotions.csv" \
  --source-root "/caminho/real/da/fonte" \
  --existing-manifest ".catalog-ingest/manifest.jsonl"
```

O verificador exige correspondência exata das autorizações com a resolução, confere `resolution_sha256`, bloqueia caminhos que escapem da raiz autorizada, recalcula o SHA-256 de cada arquivo e, para `replace_existing`, confirma que o SHA a superseder ainda pertence ao mesmo modelo no manifesto atual. Só um resultado com `ready=true` autoriza a etapa de cópia.

A política é fail-closed:

- SHA-256 igual: não republicar a mesma imagem;
- sem correspondência exata/perceptual: adicionar como nova vista;
- semelhança perceptual: apenas candidato de revisão, nunca remoção automática;
- quando o fonte externa tiver maior `quality_score`, o plano recomenda a imagem do fonte externa como vencedora da revisão;
- nenhuma decisão do planejador apaga ou move os mestres.

## Duplicatas exatas

Mesmo SHA-256 significa conteúdo binário idêntico. O grupo recebe `kind=exact` e somente um membro é marcado como `canonical=true`.

Nenhum arquivo é removido automaticamente.

## Imagens visualmente equivalentes

O dHash permite encontrar versões visualmente muito próximas mesmo quando resolução, compressão ou formato mudaram. A comparação é feita **somente dentro da mesma hierarquia de modelo**, evitando misturar personagens diferentes por semelhança de silhueta.

O padrão usa distância de Hamming `<= 6`. Esses grupos são `visual_candidate`, pois devem continuar auditáveis. O limiar pode ser alterado:

```bash
python tools/ingest_catalog.py "CAMINHO" --visual-threshold 4
```

Quanto menor o número, mais conservadora a comparação.

## Escolha da melhor versão

Dentro de um grupo equivalente, o arquivo recomendado considera:

1. resolução útil;
2. nitidez estimada;
3. formato da fonte;
4. dimensões e tamanho como desempate.

A versão vencedora vira a candidata a capa/original canônico. As variantes web (`thumb`, `card`, `detail`) serão geradas depois, sem substituir o original.

## Regra de segurança

O pipeline **não deve usar o resultado perceptual como autorização para deletar arquivos**. Deduplicação física, caso seja desejada no futuro, é uma operação separada e exige confirmação/relatório próprio.

## Próxima integração

Quando o acesso ao acervo local estiver disponível novamente, o fluxo será:

`Catálogo auditado → ingestão/checkpoint → revisão de duplicatas → variantes web → R2 → D1 → catálogo público`
