# Recuperação NSFW via R2 — 2026-10-12

## Objetivo

Recuperar de forma auditável três modelos históricos NSFW cuja fonte física original não existe mais no SSOT local, mas cujo maior derivado publicado (`detail.webp`) permanece íntegro no R2.

A recuperação **não** declara o derivado como byte a byte equivalente ao master perdido. O arquivo recuperado recebe seu SHA-256 real e a provenance `RECOVERED_FROM_R2_DETAIL`; caminho e SHA históricos permanecem somente como lineage.

## Política

- NSFW é variante/feature, nunca categoria separada.
- Normal + NSFW coexistem na mesma categoria, franquia e coleção.
- ausência da fonte física não autoriza aposentadoria do modelo publicado;
- `thumb.webp`, `card.webp` e `detail.webp` são derivados e nunca herdam falsamente o SHA do master;
- `detail.webp` é a fonte de recuperação por ser o maior derivado publicado disponível;
- um `detail.webp` recuperado continua sendo **WebP por extensão e por bytes**; ele não pode ser salvo como `.jpg`, `.png` ou outra extensão histórica;
- `REGISTRO-AUDITORIA-IMAGENS.csv` continua sendo o SSOT dos bytes físicos atuais;
- a ingestão normal continua usando `--audit-registry`; não existe bypass especial para recuperação;
- nenhum passo de recuperação local escreve em D1/R2.

## Objetos canônicos confirmados no R2

Bucket: `tonecos-catalogo-media`

| Modelo | model_id | `detail.webp` | Tamanho | SHA-256 recuperado |
| --- | --- | --- | ---: | --- |
| Ada Wong — NSFW | `mdl_4178950210474bea1aa9` | `media/mdl_4178950210474bea1aa9/img_ae9129b021756df4eb14/detail.webp` | 345868 | `a9fd2c092a3edae7a0e4f665efc42368aef66d63668da7d70d973e25ba02e104` |
| Cammy — NSFW | `mdl_dc75a8c0af5b0d852743` | `media/mdl_dc75a8c0af5b0d852743/img_79a5b334fa9235bdc79f/detail.webp` | 158950 | `e1fec873b4d42bc72b84e77b893c3ffc2795467d75e155fbcf3834eff93981c2` |
| Lady Vashj — NSFW | `mdl_ebb783318941d6abc94e` | `media/mdl_ebb783318941d6abc94e/img_4162595d15c5c5494eba/detail.webp` | 198702 | `04d90d0ab4fc14ca11d50eaf2418ac6c7a07210d5b0eafafd78b5362281c58a9` |

O SSOT machine-readable desses objetos é `config/catalog-r2-recovery-manifest.json`.

## Identidade publicada confirmada

- Ada Wong NSFW: `Games / Resident Evil / Ada Wong`;
- Cammy NSFW: `Games / Street Fighter / Cammy`;
- Lady Vashj NSFW: `Games / Warcraft / Lady Vashj`.

Os três continuam publicados e não possuem aprovação de aposentadoria.

## Entrada isolada

Baixe somente os três objetos auditados para uma pasta fora do SSOT e nomeie-os exatamente pelo `modelId`:

```text
incoming/
  mdl_4178950210474bea1aa9.webp
  mdl_dc75a8c0af5b0d852743.webp
  mdl_ebb783318941d6abc94e.webp
```

O materializador verifica os três arquivos antes de copiar qualquer um deles.

## Mapping local

O mapping é operacional e fica fora do Git. Cada entrada separa explicitamente:

- `relativePath`: novo caminho físico do **WebP recuperado**;
- `auditRow.historicalPath`: caminho exato do master histórico perdido;
- `auditRow.historicalSha256`: SHA-256 histórico do master perdido;
- `codigo`, `identificacao`, `modelo_publico` e `status`: metadata auditada, obtida do registro/relatório físico — nunca inferida por nome ou por D1.

Exemplo estrutural:

```json
{
  "version": 1,
  "entries": [
    {
      "modelId": "mdl_4178950210474bea1aa9",
      "relativePath": "Games/Resident Evil/.../ada-wong-nsfw.recovered-r2.webp",
      "auditRow": {
        "historicalPath": "Games/Resident Evil/.../arquivo-historico.ext",
        "historicalSha256": "<sha256-original-auditado>",
        "codigo": "<codigo-auditado>",
        "identificacao": "<identificacao-auditada>",
        "modelo_publico": "<modelo-publico-auditado>",
        "status": "<status-publicavel-auditado>"
      }
    }
  ]
}
```

Os `...` e valores entre `<...>` são apenas documentação. O mapping real deve ser preenchido com os dados exatos do relatório/registro físico.

### Regras do mapping

- `relativePath` precisa terminar em `.webp`;
- caminho absoluto, drive Windows, `..`, segmentos vazios e escape da raiz são recusados;
- os três `modelId` do manifesto precisam aparecer exatamente uma vez;
- `historicalPath` é lineage; ele pode ter `.jpg`, `.png`, `.webp` etc. conforme o master real;
- se a linha histórica ainda existir no audit registry, o reconciliador pode movê-la do `historicalPath` para o novo `relativePath`;
- se a linha histórica não existir mais, a inserção só é permitida com `auditRow` completo e explícito;
- se tanto o caminho histórico quanto o recuperado aparecerem como linhas distintas, o processo falha por ambiguidade;
- nenhum código/status/modelo público é inventado automaticamente.

## 1. Materialização — dry-run

```bash
python tools/materialize_r2_recovery.py \
  --manifest config/catalog-r2-recovery-manifest.json \
  --mapping "/caminho/local/recovery-mapping.json" \
  --incoming-root "/caminho/local/incoming" \
  --root "/raiz/do/Catalogo"
```

O dry-run exige cobertura 3/3, tamanho e SHA exatos. Se qualquer entrada divergir, o SSOT não é alterado.

## 2. Materialização local

Somente após o dry-run verde:

```bash
python tools/materialize_r2_recovery.py \
  --manifest config/catalog-r2-recovery-manifest.json \
  --mapping "/caminho/local/recovery-mapping.json" \
  --incoming-root "/caminho/local/incoming" \
  --root "/raiz/do/Catalogo" \
  --provenance-output "/raiz/do/Catalogo/.catalog-ingest/recovery-provenance.jsonl" \
  --apply
```

O materializador:

- verifica todos os incoming antes da primeira cópia;
- não sobrescreve destino com bytes divergentes;
- aceita destino já idêntico de forma idempotente;
- cria somente os arquivos faltantes;
- revalida o SSOT inteiro ao final;
- só emite provenance após `completeCoverage=true`.

## 3. Verifier independente

```bash
python tools/verify_r2_recovery.py \
  --manifest config/catalog-r2-recovery-manifest.json \
  --mapping "/caminho/local/recovery-mapping.json" \
  --root "/raiz/do/Catalogo" \
  --output "/raiz/do/Catalogo/.catalog-ingest/recovery-provenance.jsonl"
```

O verifier recalcula tamanho/SHA e exige 3/3. Ele também recusa materializar WebP com extensão falsa.

## 4. Reconciliação do audit registry — dry-run

```bash
python tools/apply_r2_recovery_registry.py \
  --registry "/raiz/do/Catalogo/00 - ESTATISTICAS - CATALOGO/REGISTRO-AUDITORIA-IMAGENS.csv" \
  --manifest config/catalog-r2-recovery-manifest.json \
  --provenance "/raiz/do/Catalogo/.catalog-ingest/recovery-provenance.jsonl" \
  --mapping "/caminho/local/recovery-mapping.json" \
  --root "/raiz/do/Catalogo"
```

O dry-run aceita dois estados legítimos:

1. **linha histórica presente**: valida metadata e SHA históricos; se o caminho mudou para `.webp`, planeja mover a referência preservando `historical_caminho`;
2. **linha histórica ausente**: exige `auditRow` completo e planeja uma única inserção auditada.

Qualquer estado parcial ou ambíguo falha fechado.

## 5. Reconciliação atômica do audit registry

```bash
python tools/apply_r2_recovery_registry.py \
  --registry "/raiz/do/Catalogo/00 - ESTATISTICAS - CATALOGO/REGISTRO-AUDITORIA-IMAGENS.csv" \
  --manifest config/catalog-r2-recovery-manifest.json \
  --provenance "/raiz/do/Catalogo/.catalog-ingest/recovery-provenance.jsonl" \
  --mapping "/caminho/local/recovery-mapping.json" \
  --root "/raiz/do/Catalogo" \
  --report "/raiz/do/Catalogo/.catalog-ingest/recovery-registry-report.json" \
  --apply
```

A escrita usa arquivo temporário + `fsync` + `os.replace`. Após a troca, o CSV é reaberto e precisa ficar idempotente.

Campos de lineage/provenance adicionados:

- `historical_caminho`;
- `historical_sha256`;
- `provenance`;
- `source_bucket`;
- `source_r2_key`;
- `recovery_model_id`;
- `recovery_size`.

O `sha256` principal passa a representar **os bytes físicos atuais**, como exige o SSOT. O SHA do master perdido permanece somente em `historical_sha256`.

## 6. Ingestão canônica fresca

Depois da reconciliação, não existe parâmetro especial de recovery:

```bash
python tools/ingest_catalog.py "/raiz/do/Catalogo" \
  --output ".catalog-ingest" \
  --audit-registry "/raiz/do/Catalogo/00 - ESTATISTICAS - CATALOGO/REGISTRO-AUDITORIA-IMAGENS.csv" \
  --no-resume
```

A ingestão precisa terminar com `errors=0` e `ok=images`.

## 7. Bundle metadata v8

```bash
python tools/build_media_bundle.py "/raiz/do/Catalogo" ".catalog-ingest/manifest.jsonl" \
  --output ".publish-bundle" \
  --no-resume
```

`MEDIA_METADATA_VERSION` precisa permanecer em 8 e as versões Normal + NSFW devem coexistir como variantes distintas.

## 8. Preflights sem escrita de produção

```bash
python tools/publish_r2.py ".publish-bundle/r2" --dry-run

python tools/publish_d1.py ".publish-bundle/models.jsonl" \
  --check-production \
  --retire-absent-approvals config/catalog-model-retirement-approvals.csv

python tools/apply_legacy_gallery_repairs.py
```

Nenhum desses três comandos acima deve usar `--apply` nesta fase.

## Closeout orquestrado

`tools/preflight_catalog_closeout.py` executa a mesma sequência e não possui caminho de escrita em Cloudflare.

Primeiro, somente leitura/local dry-run:

```bash
python tools/preflight_catalog_closeout.py \
  --root "/raiz/do/Catalogo" \
  --mapping "/caminho/local/recovery-mapping.json" \
  --incoming-root "/caminho/local/incoming"
```

Se houver mutação local pendente, o relatório retorna `status=local_changes_required`.

Depois de revisar o plano, autorize **somente** as duas mutações locais auditadas:

```bash
python tools/preflight_catalog_closeout.py \
  --root "/raiz/do/Catalogo" \
  --mapping "/caminho/local/recovery-mapping.json" \
  --incoming-root "/caminho/local/incoming" \
  --apply-local
```

Mesmo com `--apply-local`:

- R2 continua obrigatoriamente `--dry-run`;
- D1 continua obrigatoriamente `--check-production`;
- reparos de galeria continuam sem `--apply`;
- `productionWrites=0` é registrado em `.catalog-ingest/closeout-preflight.json`.

Somente um relatório final `status=preflight_green` permite avaliar separadamente a aplicação dos dois reparos monotônicos de galeria.

## Estado atual

Os três objetos R2 foram reconfirmados em leitura em 2026-10-12 na conta Tonecos Studio. O D1 ativo permanecia com 2596 modelos / 2564 publicados, e os três registros históricos NSFW continuavam publicados. Nenhum upload R2 e nenhuma escrita D1 foram realizados durante a preparação deste fluxo.
