# External source 001 — batch 004 release evidence

Status: **published and publicly verified**.

## Scope

- source: `external-source-001`
- descriptor: `config/external-batches/external-source-001-batch-004.json`
- product: `11876` — DigiDestined Diorama
- canonical model id: `mdl_9813b8c1eb0a33cdf7f4`
- slug: `digimon-digidestined-diorama`
- code: `TS-9813B8C1EB0A`
- category/franchise: `Animes & Desenhos / Digimon`
- source images: 6
- generated public variants: 18
- gallery manifests: 1
- production R2 objects: 19 total
- existing models updated: 0
- retirements: 0
- deletes: 0

## Review gates

- staging coverage: 6/6
- source SHA verification: 6/6
- visual review: 6/6 clear
- watermark/promotional branding: 0/6
- production SHA collisions before release: 0/6
- identity decision: `new_model`

## Release workflow

Official promotion was triggered through the `production-release` PR label after CI and read-only preflight were green.

- CI #1157: success on `6c48d69efe793fb6c7fb61499025052e642cf597`
- External catalog batch release #47: read-only preflight success
- External catalog batch release #48: promotion success
- release artifact: `11942970101`
- release artifact digest: `sha256:2e73c246207862ccb6ffb0dcf0c69c7a9ee27ae1205995eab877cb6d567601fd`

The production run re-materialized the reviewed bytes, rebuilt the delta, re-asserted exact identity, re-ran the D1 new-only gate immediately before the R2 write, verified the R2 proof, applied the D1 transaction atomically, ran general catalog health, and verified the released model/gallery publicly.

## Live production proof

D1 contains exactly one published model with:

- id: `mdl_9813b8c1eb0a33cdf7f4`
- slug: `digimon-digidestined-diorama`
- code: `TS-9813B8C1EB0A`
- image_count: 6
- physical gallery version: `217164108664112`
- public gallery revision: `1`
- franchise slug: `digimon`
- category slug: `animes-desenhos`

`model_image_sources` contains exactly 6 rows for this model, positions 0..5, with one cover and five gallery images, all bound to physical gallery version `217164108664112`.

Production R2 contains:

- 18 objects under `media/mdl_9813b8c1eb0a33cdf7f4/`
- 1 object under `gallery/mdl_9813b8c1eb0a33cdf7f4/`

The `production-release` label was removed after the successful promotion so the draft PR is not left armed for another release.
