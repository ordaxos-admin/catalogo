# Continuidade operacional do Catálogo

> **SSOT de continuidade.** Leia este arquivo antes de continuar o projeto em outra conversa, máquina ou sessão.
>
> Atualizado em **2026-10-11**. Este arquivo registra apenas o estado operacional que precisa orientar a próxima execução. Histórico detalhado pertence aos runbooks e documentos de migração; não acumular aqui gates já encerrados, PRs antigas ou bloqueios expirados.

## Autoridade

- Repositório: `washingtonmsdj/catalogo`.
- Branch autoritativa: `main`.
- Antes de qualquer mutação, conferir novamente `main`, PRs/issues abertas, CI e estado Cloudflare. Nunca confiar em SHA antigo gravado em documentação como autoridade de execução.
- `wrangler.jsonc` é o SSOT do Worker, D1, R2, CORS e segredo obrigatório.
- `config/public-runtime.json` é o SSOT da superfície pública do frontend.
- Mudança de runtime só é considerada concluída depois de CI + staged smoke + promoção + smoke público.

## Estado validado de produção

A última linhagem de runtime validada antes desta reconciliação é o commit **`5134897ff4a387a5302e16d78124bf39e46ab56e`**, integrado pela PR **#161**.

Validações concluídas nessa linhagem:

- CI **#1005** verde;
- GitHub Pages **#734** verde;
- Deploy Cloudflare API **#117** verde;
- staged Worker smoke verde antes da promoção;
- promoção para 100% verde;
- smoke público verde;
- gateway de mídia real verde;
- Workers Cache verde;
- rollback não executado;
- D1 migrations e auditoria estrutural corretamente puladas porque não houve mudança de schema.

O Worker promovido nessa execução é **`ac611817-e85c-4cba-a4a0-87551cbf702f`**, a 100% de tráfego no momento da validação.

Não usar esses identificadores como substituto de medição atual. Eles são apenas a última evidência conhecida boa.

## Superfície pública

- URL pública canônica: `https://acheguese.com.br/tonecosstudios/`.
- Preview independente: `https://washingtonmsdj.github.io/catalogo/`.
- `/tonecosstudios/*` pertence à superfície pública do Catálogo dentro do Achegue-se.
- `/catalogo-api/*` é o boundary first-party usado pelo frontend no domínio Achegue-se.
- No GitHub Pages, o frontend usa `VITE_API_BASE_URL`.
- `/catalogo` permanece apenas como compatibilidade de rota para `/tonecosstudios/`.

O Catálogo não deve copiar o aplicativo Achegue-se nem assumir propriedade de `acheguese.com.br`.

## Cloudflare — recursos canônicos Tonecos Studio

- Account ID: `8827c547d6def5ee5b9ca550fdd5680a`.
- Worker: `tonecos-catalogo-api`.
- API: `https://tonecos-catalogo-api.tonecosstudio.workers.dev`.
- D1: `tonecos-catalogo` (`87006366-60f3-4937-af73-2ef5a5f901fb`).
- R2: `tonecos-catalogo-media`.
- Turnstile sitekey pública: `0x4AAAAAAFSpOhaAFp6Gsjqd`.
- `TURNSTILE_SECRET_KEY` é segredo obrigatório e não pertence ao Git.

A infraestrutura antiga da conta Washington é somente rollback histórico. Não mover DNS, zona ou recursos de outra conta para “facilitar” o Catálogo.

## Mídia — arquitetura atual obrigatória

A mídia **não possui origem pública separada**.

- O bucket `tonecos-catalogo-media` permanece privado.
- O Public Development URL do R2 deve permanecer **desabilitado**.
- Não existe `VITE_MEDIA_BASE_URL`.
- O frontend monta mídia a partir do mesmo `apiBase` usado pela API.
- O Worker serve objetos por `/media/*` usando o binding privado `MEDIA`.
- A entrypoint `worker/entry.ts` resolve `/media/*` antes do handler principal da API, portanto leitura de mídia não depende de D1.
- O gateway preserva metadata HTTP, ETag e Range, rejeita traversal/chaves inválidas e usa cache público explícito.

O deploy Cloudflare valida o estado remoto do Worker contra o SSOT e também consulta o estado managed do bucket. Se a exposição pública direta do R2 voltar a ser habilitada, o deploy falha **antes de qualquer write**.

O Pages também rejeita artefato que contenha endpoint público direto de R2.

## Deploy Cloudflare

Fluxo canônico de `.github/workflows/cloudflare.yml`:

1. preflight fail-closed;
2. detectar se houve mudança real de schema D1;
3. validar credenciais e configuração;
4. renderizar configuração de produção;
5. validar Worker remoto e estado privado do R2 contra o SSOT;
6. capturar a versão atualmente servindo produção;
7. validar o bucket R2;
8. enviar nova versão do Worker sem tráfego;
9. registrar staged em 0%;
10. aplicar somente migrations compatíveis quando houver mudança de schema;
11. executar staged smoke com version override;
12. promover para 100% somente após staged verde;
13. validar release público;
14. validar um objeto R2 real por `/media/*`;
15. exigir Workers Cache `HIT` dentro de janela bounded;
16. restaurar a versão anterior se smoke staged ou pós-promoção falhar;
17. executar auditoria D1 somente quando o escopo de schema exigir.

`wrangler secret put` não pertence a esse pipeline. Código e segredo obrigatório são enviados de forma controlada com arquivo efêmero e permissões restritas.

Runbook detalhado: `docs/CLOUDFLARE_DEPLOY.md`.

## Schema e migrations

Produção está aplicada até `0019_model_variant_name.sql`.

`config/catalog-schema-contract.json` está na versão de contrato usada pelo Worker e o health público deve validar todas as migrations/estruturas exigidas por esse arquivo.

Regras:

- migrations novas passam por `config/migration-deploy-policy.json`;
- nenhuma migration destrutiva é aplicada para “destravar” deploy;
- mudança runtime-only não deve provocar leitura/auditoria D1 desnecessária;
- manutenção de dados pertence ao workflow `.github/workflows/catalog-maintenance.yml`, não ao deploy comum.

## Identidade de produto e galeria

Regra principal: **modelo não é imagem**.

- frente, costas, laterais e detalhes do mesmo produto pertencem à mesma galeria;
- produtos diferentes do mesmo personagem continuam modelos diferentes;
- nome, pasta ou franquia não bastam para unir produtos;
- `modelo_publico` explícito tem maior autoridade quando presente;
- troca de capa não muda ID/nome/slug;
- R2, manifests e D1 precisam concordar em identidade, `imageCount`, `galleryVersion` e variantes;
- redução automática de galeria é proibida;
- similaridade visual gera revisão, não exclusão automática.

## Galerias legadas já reconciliadas

`config/catalog-legacy-gallery-overrides.json` continua sendo o registro canônico dos grupos revisados.

Estado estrutural consolidado:

- 21 canônicos preservados;
- 32 fontes aposentadas por relação canônico→fonte;
- 32 relações de galeria;
- nenhuma exclusão destrutiva como mecanismo de consolidação;
- aliases antigos continuam resolvendo para o canônico;
- mídia histórica permanece no R2;
- IDs/slugs aposentados não podem ser reciclados.

`tools/apply_legacy_gallery_repairs.py` é o único caminho explícito de write para essa família e não deve ser reaplicado sem nova evidência de drift.

## Índice SHA e integridade

`model_image_sources` guarda metadados de auditoria; R2/manifests continuam sendo a fonte da galeria.

O backfill inicial foi concluído e não deve ser refeito por rotina. Novos backfills devem processar apenas versões ainda não indexadas e continuar sem baixar binários quando os manifests já fornecem SHA suficiente.

Ferramentas centrais:

- `tools/backfill_image_source_index.py`;
- `tools/plan_gallery_merge.py`;
- `tools/resolve_gallery_merge.py`;
- `tools/build_gallery_promotion_manifest.py`;
- `tools/verify_gallery_promotion_manifest.py`;
- `tools/audit_d1_integrity.py`;
- `tools/verify_public_catalog.py`.

## Conteúdo local — checkpoint preservado

O catálogo mestre operacional permanece fora do repositório público. Antes de qualquer mutação local, localizar o caminho real no dispositivo e conferir o estado atual; não assumir que unidade sincronizada e cópia local são equivalentes.

Checkpoint preservado do último ciclo local conhecido:

- SHA V4: 3.649 / 3.649;
- `catalog_complete=true`;
- 0 caminhos duplicados no cache;
- 0 hashes inválidos;
- `Novos`: 56 imagens;
- fila: 40 promoção controlada + 16 quarentena privada;
- nenhuma das 16 de quarentena deve ser apagada.

`config/public-catalog-roots.json` é a fronteira pública canônica. Não voltar a auditar conteúdo privado/referência como se fosse catálogo publicável.

Não reiniciar SHA V4 por conveniência. Reaproveitar hashes válidos e migrar/revalidar estado existente.

## Próxima sequência de conteúdo

Quando houver acesso ao catálogo mestre/local, continuar nesta ordem:

1. confirmar que helpers locais ainda consomem `config/public-catalog-roots.json`;
2. revalidar o checkpoint SHA sem reset total;
3. revalidar 40 itens de promoção e 16 de quarentena;
4. auditar nova fonte por produto, com mapeamento explícito `incoming_model,target_model`;
5. usar `tools/plan_gallery_merge.py --mapping` e resolver revisões conscientemente;
6. gerar e validar manifesto de promoção;
7. preservar arquivos mestres e nunca apagar fonte antiga por substituição de vista;
8. gerar bundle/mídia;
9. executar `publish_d1.py --check-production` antes de writes;
10. publicar somente delta R2 necessário;
11. aplicar D1 de forma idempotente;
12. validar produção com `verify_public_catalog.py --exhaustive --all-galleries`;
13. atualizar este checkpoint apenas com fatos realmente medidos.

## O que não fazer

- não recomeçar o catálogo do zero;
- não reprocessar dados já concluídos sem evidência técnica;
- não criar segunda origem de mídia;
- não expor R2 diretamente ao público;
- não criar fallback de infraestrutura ou credencial hardcoded;
- não mover domínio de outra conta como atalho;
- não juntar produtos diferentes só por nome/personagem/pasta;
- não apagar STL/GLB/3MF/OBJ/fontes;
- não recriar árvore paralela por mojibake/prefixo/sufixo;
- não executar manutenção de dados automaticamente junto com mudança runtime-only.

## Regra para futuras sessões

Antes de trabalhar:

1. ler este arquivo;
2. conferir estado atual de `main`, PRs/issues e workflows;
3. medir Cloudflare/produção em vez de assumir que IDs deste checkpoint ainda são atuais;
4. localizar o próximo problema concreto;
5. corrigir causa raiz e adicionar gate quando houver risco real de regressão;
6. atualizar este arquivo somente quando mudar uma decisão operacional relevante.

Sem gambiarras, sem paliativos, sem hardcoded concorrente, sem duplicação de SSOT e sem manter instrução obsoleta “por segurança”. O estado medido e os contratos versionados prevalecem.
