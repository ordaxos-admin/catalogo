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

## Identidade publicada confirmada no D1

- Ada Wong NSFW: slug histórico `resident-evil-ada-wong-resident-evil-ada-wong-stl-4-nsfw-resident-evil-principal`.
- Cammy NSFW: slug histórico `street-fighter-cammy-street-fighter-cammy-stl-5-street-wear-bust-nsfw-principal`.
- Lady Vashj NSFW: slug histórico `warcraft-lady-vashj-warcraft-lady-vashj-stl-nsfw-warcraft-principal`.

Esses registros permanecem nas franquias originais (`Resident Evil`, `Street Fighter`, `Warcraft`) e na categoria `Games`.

## Procedimento canônico de recuperação

1. baixar somente o `detail.webp` correspondente ao `model_id` auditado;
2. verificar o SHA-256 contra a tabela acima antes de aceitar o arquivo;
3. salvar no SSOT físico com proveniência explícita `RECOVERED_FROM_R2_DETAIL`;
4. preservar o SHA histórico anterior apenas como lineage/ancestralidade, nunca como checksum do arquivo recuperado;
5. regenerar ingestão e bundle sob `MEDIA_METADATA_VERSION=8`;
6. comprovar coexistência Normal + NSFW como variantes distintas;
7. executar `publish_r2.py --dry-run` e o preflight D1 sem escrita de produção;
8. somente depois dos gates verdes avaliar os dois reparos monotônicos de galeria e a saída da PR de draft.

## Estado

Objetos R2 confirmados em leitura em 2026-10-12. Nenhum upload R2 e nenhuma escrita D1 foram realizados durante esta validação.
