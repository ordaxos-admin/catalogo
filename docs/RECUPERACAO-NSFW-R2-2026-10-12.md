# Recuperação NSFW via R2 — 2026-10-12

## Objetivo

Registrar de forma auditável as três fontes históricas NSFW ausentes do SSOT físico atual que ainda possuem derivados publicados no R2.

Este documento **não** declara os derivados como equivalentes byte a byte ao arquivo-fonte histórico. O SHA histórico continua sendo apenas linhagem. Qualquer arquivo recuperado de `detail.webp` deve receber o SHA-256 real do objeto recuperado e a proveniência `RECOVERED_FROM_R2_DETAIL`.

## Política

- NSFW não é categoria separada.
- Versões Normal + NSFW permanecem na mesma categoria, franquia e coleção como variantes distintas.
- Ausência temporária da fonte física não autoriza aposentadoria do modelo publicado.
- `thumb.webp`, `card.webp` e `detail.webp` são derivados; nunca devem herdar falsamente o SHA do original histórico.
- Entre os derivados disponíveis, `detail.webp` é o candidato de recuperação por preservar a maior resolução publicada.
- Nenhuma escrita em D1/R2 é necessária para esta etapa.

## Objetos confirmados no R2

Bucket: `tonecos-catalogo-media`

| Modelo | model_id | Chave `detail.webp` | Tamanho | SHA-256 do derivado |
| --- | --- | --- | ---: | --- |
| Ada Wong — NSFW | `mdl_4178950210474bea1aa9` | `media/mdl_4178950210474bea1aa9/img_ae9129b021756df4eb14/detail.webp` | 345868 bytes | `a9fd2c092a3edae7a0e4f665efc42368aef66d63668da7d70d973e25ba02e104` |
| Cammy — NSFW | `mdl_dc75a8c0af5b0d852743` | `media/mdl_dc75a8c0af5b0d852743/img_79a5b334fa9235bdc79f/detail.webp` | 158950 bytes | `e1fec873b4d42bc72b84e77b893c3ffc2795467d75e155fbcf3834eff93981c2` |
| Lady Vashj — NSFW | `mdl_ebb783318941d6abc94e` | `media/mdl_ebb783318941d6abc94e/img_4162595d15c5c5494eba/detail.webp` | 198702 bytes | `04d90d0ab4fc14ca11d50eaf2418ac6c7a07210d5b0eafafd78b5362281c58a9` |

A mesma informação também existe em formato machine-readable em `config/catalog-r2-recovery-manifest.json`. Esse arquivo é o SSOT do verifier e da materialização; o quadro acima é documentação humana.

## Identidade publicada confirmada no D1

- Ada Wong NSFW: slug histórico `resident-evil-ada-wong-resident-evil-ada-wong-stl-4-nsfw-resident-evil-principal`.
- Cammy NSFW: slug histórico `street-fighter-cammy-street-fighter-cammy-stl-5-street-wear-bust-nsfw-principal`.
- Lady Vashj NSFW: slug histórico `warcraft-lady-vashj-warcraft-lady-vashj-stl-nsfw-warcraft-principal`.

Esses registros permanecem nas franquias originais (`Resident Evil`, `Street Fighter`, `Warcraft`) e na categoria `Games`.

## Procedimento canônico de recuperação

1. baixar somente o `detail.webp` correspondente ao `model_id` auditado;
2. salvar os três downloads em uma pasta de entrada isolada, fora do SSOT, usando exatamente `<modelId>.webp` como nome de arquivo;
3. usar `tools/materialize_r2_recovery.py` primeiro sem `--apply` para validar integralmente os três downloads e os destinos do mapping;
4. somente após o dry-run verde executar o mesmo comando com `--apply`;
5. o materializador recusa overwrite divergente e aceita idempotentemente destino já existente com SHA/tamanho corretos;
6. executar `tools/verify_r2_recovery.py` contra o SSOT materializado e exigir cobertura integral dos 3 IDs;
7. preservar o SHA histórico anterior apenas como lineage/ancestralidade, nunca como checksum do arquivo recuperado;
8. regenerar ingestão e bundle sob `MEDIA_METADATA_VERSION=8`;
9. comprovar coexistência Normal + NSFW como variantes distintas;
10. executar `publish_r2.py --dry-run` e o preflight D1 sem escrita de produção;
11. somente depois dos gates verdes avaliar os dois reparos monotônicos de galeria e a saída da PR de draft.

## Mapping local fail-closed

O caminho físico não é hardcoded no repositório porque o destino pertence ao SSOT operacional local e deve vir do relatório físico auditado. Crie um JSON local, fora do Git, contendo exatamente os três destinos relativos à raiz do catálogo:

```json
{
  "version": 1,
  "entries": [
    {
      "modelId": "mdl_4178950210474bea1aa9",
      "relativePath": "Games/Resident Evil/.../arquivo-recuperado.webp"
    },
    {
      "modelId": "mdl_dc75a8c0af5b0d852743",
      "relativePath": "Games/Street Fighter/.../arquivo-recuperado.webp"
    },
    {
      "modelId": "mdl_ebb783318941d6abc94e",
      "relativePath": "Games/Warcraft/.../arquivo-recuperado.webp"
    }
  ]
}
```

Os `...` acima são apenas ilustrativos na documentação. O mapping real deve usar os caminhos exatos do relatório físico; não é permitido inferir ou inventar diretório.

## Materialização segura

A pasta de entrada deve conter exatamente os bytes baixados do R2, nomeados pelo ID publicado:

```text
incoming/
  mdl_4178950210474bea1aa9.webp
  mdl_dc75a8c0af5b0d852743.webp
  mdl_ebb783318941d6abc94e.webp
```

Primeiro faça o preflight sem escrita:

```bash
python tools/materialize_r2_recovery.py \
  --manifest config/catalog-r2-recovery-manifest.json \
  --mapping "/caminho/local/recovery-mapping.json" \
  --incoming-root "/caminho/local/incoming" \
  --root "/raiz/do/Catalogo"
```

O dry-run exige que **todos** os downloads já existam e tenham tamanho/SHA corretos. Se um único arquivo estiver ausente ou divergente, nenhum destino é escrito.

Com o preflight verde, aplique:

```bash
python tools/materialize_r2_recovery.py \
  --manifest config/catalog-r2-recovery-manifest.json \
  --mapping "/caminho/local/recovery-mapping.json" \
  --incoming-root "/caminho/local/incoming" \
  --root "/raiz/do/Catalogo" \
  --provenance-output "/raiz/do/Catalogo/.catalog-ingest/recovery-provenance.jsonl" \
  --apply
```

O materializador é deliberadamente sem rede. Ele:

- verifica integralmente os arquivos de entrada **antes de qualquer cópia**;
- usa o mesmo manifesto e mapping do verifier;
- recusa target existente com bytes diferentes, sem overwrite;
- aceita target já idêntico como estado idempotente;
- grava arquivos faltantes de forma monotônica, sem apagar ou alterar masters existentes;
- reexecuta a verificação integral do SSOT após as cópias;
- só grava provenance depois de `completeCoverage=true`.

## Verificação pós-materialização

Execute novamente o verifier independente:

```bash
python tools/verify_r2_recovery.py \
  --manifest config/catalog-r2-recovery-manifest.json \
  --mapping "/caminho/local/recovery-mapping.json" \
  --root "/raiz/do/Catalogo" \
  --output "/raiz/do/Catalogo/.catalog-ingest/recovery-provenance.jsonl"
```

O verifier é deliberadamente sem rede e sem cópia. Ele:

- exige que mapping e manifesto contenham exatamente os mesmos `modelId`;
- recusa caminho absoluto, traversal ou escape da raiz autorizada;
- exige o tamanho exato do objeto auditado;
- recalcula e exige o SHA-256 exato do `detail.webp` recuperado;
- exige `RECOVERED_FROM_R2_DETAIL` como provenance;
- grava provenance somente depois da validação integral;
- informa `networkReads=0` e `writesToSourceRoot=0` no resumo.

A existência do arquivo de provenance não substitui a ingestão. Ela prova apenas que os bytes materializados correspondem exatamente aos objetos R2 auditados e que os três destinos físicos foram cobertos.

## Estado

Objetos R2 reconfirmados em leitura em 2026-10-12 na conta Tonecos Studio; tamanhos permanecem 345868, 158950 e 198702 bytes. O D1 ativo permanece com 2596 modelos / 2564 publicados, os três registros históricos NSFW ainda publicados e `variant_name` ainda vazio no dataset antigo. Nenhum upload R2 e nenhuma escrita D1 foram realizados durante esta validação.
