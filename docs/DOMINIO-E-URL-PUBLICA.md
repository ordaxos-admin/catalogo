# Domínio e URL pública

O Catálogo é publicado como uma aplicação independente, mas a URL pública canônica pertence ao domínio do Achegue-se:

`https://acheguese.com.br/tonecosstudios/`

Os repositórios continuam independentes. O Achegue-se é responsável pelo mount público em `/tonecosstudios/` e o Catálogo continua responsável pelo seu frontend, API, mídia, dados e pipeline de publicação.

## Fonte única de verdade

A URL pública usada em SEO e compartilhamento é versionada em:

`config/public-runtime.json`

Esse arquivo é o SSOT do endereço canônico do Catálogo. O `vite.config.ts` lê `publicRuntime.publicSiteUrl` para gerar automaticamente:

- `rel=canonical`;
- `og:url`;
- os metadados públicos que dependem da origem canônica.

Não existe variável `VITE_PUBLIC_SITE_URL` no contrato de produção. Alterar o endereço público exige uma mudança versionada e revisável em `config/public-runtime.json`, evitando divergência silenciosa entre deploy, SEO e documentação.

## Portabilidade do frontend

O frontend usa caminhos relativos (`base: './'`). Por isso, JS, CSS, favicon e manifest continuam funcionando quando o build é servido pelo mount `/tonecosstudios/` do Achegue-se, sem copiar o código do Catálogo para o outro repositório.

O backend público possui uma única origem configurável:

- `VITE_API_BASE_URL` — origem canônica do Worker, responsável por `/api/*` e `/media/*`;
- `VITE_TURNSTILE_SITE_KEY` — chave pública do formulário protegido.

Não existe `VITE_MEDIA_BASE_URL` como configuração de produção. O build deriva internamente a origem de mídia da API canônica. O bucket `tonecos-catalogo-media` permanece privado e é acessado somente pelo binding `MEDIA` do Worker; `r2.dev` não faz parte do contrato público.

Quando o Catálogo roda sob `acheguese.com.br`, o frontend usa o proxy first-party `/catalogo-api` para a API pública. O upstream real continua pertencendo ao Catálogo. O mesmo contrato de backend serve a mídia, sem uma segunda origem externa.

## Contrato com o Achegue-se

No repositório Achegue-se, o mount público deve preservar esta ordem:

1. `/catalogo` permanece apenas como caminho legado e redireciona para `/tonecosstudios/`;
2. `/tonecosstudios/:path*` é encaminhado para a origem publicada do frontend do Catálogo;
3. `/catalogo-api/:path*` é encaminhado para o Worker público do Catálogo, incluindo API e mídia;
4. qualquer outra rota de visitante do Achegue-se é redirecionada para `/tonecosstudios/`; a SPA real permanece intacta atrás dessa camada temporária.

O owner desse roteamento no Achegue-se é `src/shared/config/publicExternalApps.config.ts`; o `vercel.json` é derivado/validado a partir desse contrato e não deve virar uma segunda fonte de verdade.

## Validação de publicação

O workflow do GitHub Pages lê `config/public-runtime.json`, rejeita qualquer endpoint `r2.dev` no build e faz smoke test do HTML publicado. O deploy Cloudflare valida separadamente que um objeto R2 real é servido por `/media/*` depois da promoção; falha nessa prova aciona rollback do Worker.

Assim, preview técnico, origem de hospedagem e URL pública canônica podem ser diferentes sem duplicar configuração nem acoplar os dois projetos.
