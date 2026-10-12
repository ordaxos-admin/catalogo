# Catálogo

Catálogo web escalável para 100 mil+ modelos/personagens, com navegação inspirada em seletores de personagens de fliperama e interface limpa/industrial.

**URL pública canônica:** https://acheguese.com.br/tonecosstudios/

**Origem/preview independente:** https://washingtonmsdj.github.io/catalogo/

> **Retomando o projeto em outra conversa/sessão?** Leia primeiro [`docs/CONTINUIDADE-OPERACIONAL.md`](docs/CONTINUIDADE-OPERACIONAL.md). Esse arquivo é o SSOT do checkpoint real, bloqueios, decisões já tomadas e ordem dos próximos passos. Não reinicie auditorias ou ingestões antes de conferir esse estado.

## Arquitetura

- Identidade comercial: `config/brand.json` é o SSOT; veja `docs/BRANDING.md`.
- React + TypeScript + Vite no frontend.
- `https://acheguese.com.br/tonecosstudios/` como URL pública canônica, montada pelo projeto Achegue-se.
- GitHub Pages como origem/preview independente do frontend.
- Cloudflare Worker como **única origem pública de backend**, servindo `/api/*` e `/media/*`.
- Cloudflare D1 privado para categorias, franquias, modelos e solicitações.
- Cloudflare R2 privado para variantes web e manifestos de galeria; objetos públicos passam pelo binding `MEDIA` do Worker e não dependem de `r2.dev`.
- Cloudflare Turnstile para proteger o formulário público sem expor telefone/WhatsApp.
- Paginação por cursor para 100 mil+ modelos.
- FTS5 trigram para busca de substring sem varrer a tabela inteira.
- Galeria carregada separadamente e paginada; dezenas de imagens de um personagem não pesam na seleção principal.

## Imagens e duplicatas

A origem é somente leitura durante a ingestão. SHA-256 identifica duplicatas exatas e hash perceptual aponta equivalentes visuais dentro do mesmo modelo. Entre imagens equivalentes, o pipeline prioriza a versão de maior qualidade e preserva os arquivos mestres.

São geradas variantes WebP de `thumb`, `card` e `detail`; o original pode ser preservado no armazenamento mestre sem ser servido na grade do catálogo.

Fontes externas entram por um boundary separado e auditável: aquisição autorizada, normalização, mídia materializada, manifesto externo, revisão de galeria e só então promoção. Veja [`docs/FONTES-EXTERNAS.md`](docs/FONTES-EXTERNAS.md). Um produto externo com várias vistas permanece uma única identidade de modelo com galeria multi-imagem.

## Runtime

Sem configuração Cloudflare, o frontend funciona em modo `DEMO`. Com `VITE_API_BASE_URL`, a mesma interface passa para `LIVE`. A origem de mídia não é configurável separadamente: ela é derivada da origem canônica da API e usa `/media/<storage-key>` no mesmo Worker.

Quando aberto em `acheguese.com.br`, a API continua acessada pelo proxy first-party `/catalogo-api`; no GitHub Pages, o preview usa `VITE_API_BASE_URL` diretamente. Em ambos os casos a mídia segue a mesma origem lógica do Catálogo, sem endpoint público R2 paralelo.

No modo LIVE, solicitações de orçamento exigem Turnstile validado no Worker, aceitam no máximo 50 modelos e conferem server-side se todos os IDs enviados correspondem a modelos publicados.

## Deploy

O frontend publica automaticamente a cada mudança na `main`. O backend possui workflow próprio para aplicar migrações D1 somente quando o schema muda, publicar o Worker por versão staged e validar API, mídia R2 privada e cache antes de manter a promoção. Falha em qualquer smoke pós-promoção aciona rollback explícito para a versão anterior.

Veja [`docs/CLOUDFLARE_DEPLOY.md`](docs/CLOUDFLARE_DEPLOY.md) para a configuração de produção.