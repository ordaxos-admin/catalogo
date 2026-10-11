# Deploy Cloudflare

O Catálogo usa `https://acheguese.com.br/tonecosstudios/` como URL pública canônica. O frontend é publicado no GitHub Pages e montado sob o Achegue-se. A Cloudflare é responsável pelo Worker, D1, R2 e Turnstile.

## Recursos canônicos de produção

Conta: **Tonecos Studio**.

- Worker: `tonecos-catalogo-api`
- D1: `tonecos-catalogo`
- D1 ID: `87006366-60f3-4937-af73-2ef5a5f901fb`
- R2 privado: `tonecos-catalogo-media`
- binding D1: `DB`
- binding R2: `MEDIA`
- entrypoint: `worker/entry.ts`
- segredo obrigatório: `TURNSTILE_SECRET_KEY`

`wrangler.jsonc` é a SSOT versionada para Worker, D1, R2, CORS, compatibility, cache, observability e contrato de segredo. O UUID do D1 é infraestrutura versionada, não segredo.

## Origem pública única

O backend possui uma única origem pública configurável:

- `VITE_API_BASE_URL`

O mesmo Worker atende:

- `/api/*` — catálogo, busca, galeria e formulários;
- `/media/*` — objetos do bucket R2 privado pelo binding `MEDIA`.

Não existe `VITE_MEDIA_BASE_URL` como configuração de produção. Não existe dependência pública de `r2.dev`. O build do Pages falha se detectar `r2.dev` no artefato gerado.

A entrypoint `worker/entry.ts` intercepta `/media/*` antes do handler da API. Por isso uma leitura de mídia não consulta D1. O gateway preserva metadata HTTP, ETag e Range, rejeita traversal/chaves inválidas e usa cache público explícito.

## GitHub

### Repository Variables

- `CLOUDFLARE_ACCOUNT_ID`
- `VITE_API_BASE_URL`
- `VITE_TURNSTILE_SITE_KEY`

### Repository Secrets

- `CLOUDFLARE_API_TOKEN`
- `TURNSTILE_SECRET_KEY`

O workflow deriva `CLOUDFLARE_D1_DATABASE_ID` do `wrangler.jsonc` somente para o processo em execução. Manutenções que precisam de `CATALOG_MEDIA_URL` derivam o valor de `CATALOG_API_URL`; não existe segunda fonte de verdade.

## Token Cloudflare

O token de CI deve ser dedicado ao Catálogo, restrito à conta Tonecos e usar o menor privilégio suportado:

- Workers Editor no Worker `tonecos-catalogo-api`;
- D1 Edit;
- Workers R2 Storage Read.

Não conceder Workers Admin, Workers Routes Write, R2 Write, Billing ou administração de API Tokens apenas por conveniência.

## CORS

As origens de produção ficam no `wrangler.jsonc`:

- `https://washingtonmsdj.github.io`
- `https://acheguese.com.br`
- `https://www.acheguese.com.br`

`localhost` e `127.0.0.1` não pertencem ao SSOT de produção.

## Gate de drift remoto

Antes de qualquer write no Worker, `tools/check_remote_worker_config.mjs` compara o remoto com o SSOT versionado, incluindo compatibility, observability, bindings, CORS, D1, R2 e secret binding. Divergência falha fechado antes do upload.

O CI de PR continua executando dry-run estrito do Wrangler com segredo sintético. O deploy remoto usa o gate semântico porque metadados locais de bindings não são equivalentes ao payload persistido pela API Cloudflare.

## Fluxo de produção

`Deploy Cloudflare API` é fail-closed. A ordem é obrigatória:

1. validar Account ID, token, Turnstile e `VITE_API_BASE_URL`;
2. typecheck do Worker;
3. derivar D1 ID e renderizar a configuração de deploy;
4. validar drift remoto;
5. capturar o Version ID que serve 100% do tráfego;
6. validar o bucket R2 read-only;
7. criar uma versão staged sem tráfego, com código + secret juntos;
8. validar política migration-first;
9. aplicar migrations D1 somente quando o diff realmente altera schema;
10. registrar `previous@100% + staged@0%`;
11. executar smoke completo da versão staged por Version Override;
12. promover `staged@100%`;
13. confirmar bounded que o plano de controle aponta exatamente para a versão staged e que o contrato público convergiu;
14. **provar um objeto de mídia real em `/media/<storage-key>` pela mesma origem do Worker**;
15. provar `Workers Cache` com HIT real dentro da janela bounded;
16. se qualquer gate pós-stage falhar, executar rollback explícito para `PREVIOUS_WORKER_VERSION_ID`;
17. executar auditoria D1 somente quando o escopo de schema exigir.

Upload, migrations, deployment, promoção e rollback não são repetidos automaticamente. Retry bounded existe apenas para verificações read-only.

## Contrato de release

`tools/worker_release_smoke.mjs` valida o pacote de API:

- `/api/health` + schema contract;
- `/api/categories`;
- `/api/recent`;
- `/api/catalog` + galeria;
- shared collections.

`tools/verify_public_catalog.py` valida o contrato público API + mídia. Em produção a mídia usa a própria `api-base`; `--media-base` existe apenas como override diagnóstico/teste isolado.

Smoke rápido:

```powershell
python tools/verify_public_catalog.py --api-base https://<worker>
```

Auditoria completa:

```powershell
python tools/verify_public_catalog.py --api-base https://<worker> --exhaustive --all-galleries
```

## Workers Cache

O gate de cache exige `CF-Cache-Status: HIT` real. Como uma nova Worker Version começa com cache frio, o verificador aceita somente `MISS` transitório em uma janela limitada; `BYPASS`, `DYNAMIC`, ausência do header ou erro HTTP falham imediatamente. Isso evita tanto falso rollback quanto falso verde.

## R2 e conteúdo

O R2 é storage canônico, não endpoint público. O deploy da API só precisa de leitura do bucket. Publicação/mutação de mídia pertence ao pipeline de dados e não é motivo para conceder R2 Write ao token do Worker deploy.

O endpoint `/media/*` aceita apenas chaves sob o prefixo `media/`; traversal, segmentos vazios, backslashes, NUL e chaves excessivas são rejeitados. As respostas preservam `Content-Type`/metadata, ETag e Range e incluem cache explícito.

Depois que o gateway está validado em produção, a Public Development URL `r2.dev` deve permanecer desabilitada. Reativá-la recria uma segunda origem pública e viola o contrato arquitetural.

## Frontend e proxy público

- `/tonecosstudios/*` → origem do frontend do Catálogo;
- `/catalogo-api/*` → Worker do Catálogo na Tonecos;
- no domínio Achegue-se a API é first-party;
- no GitHub Pages o preview usa `VITE_API_BASE_URL` diretamente;
- mídia segue a mesma origem lógica do backend, nunca um endpoint R2 paralelo.

O frontend recebe somente valores públicos. O Turnstile secret nunca vai para o navegador.

## Segurança

- Segredos ficam somente em GitHub Secrets/Cloudflare.
- Nunca colocar token Cloudflare, Turnstile secret ou credencial em Git, chat, `.env.example` ou `wrangler.jsonc`.
- Não criar fallback de autenticação.
- Não enfraquecer gates para fazer deploy passar.
- Divergência entre dashboard e SSOT deve ser reconciliada conscientemente.
- Não reintroduzir `VITE_MEDIA_BASE_URL`, `r2.dev` ou custom domain R2 como segunda origem de mídia sem uma decisão arquitetural explícita.
