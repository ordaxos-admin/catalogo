# External source 001 — reviewed media staging

## Purpose

This runbook is the tracked operational handoff for the external-media workstream. The provider is represented inside the repository by the neutral ID `external-source-001`; exact origin and legacy staging prefix are stored reversibly in the versioned batch descriptor as Base64 so competitor branding never becomes application/repository identity.

Production remains independent from this workstream. Staging is private and is not a public catalog surface.

## Storage contract

- private staging bucket: `tonecos-catalogo-staging`;
- public/canonical media bucket: `tonecos-catalogo-media`;
- the two buckets must never resolve to the same value;
- notebook storage is transient cache only, not the media archive;
- public promotion is incremental and requires explicit reviewed-batch authorization;
- no paid model/STL payload belongs to this media workflow;
- no direct public URL is assigned to staging.

The current release path is:

`reviewed evidence -> private staging -> runner temp -> SHA/size verification -> audited ingest -> media bundle -> public R2 proof -> atomic new-only D1 transaction -> public verification`

There is deliberately no release fallback from private staging back to the external website. The external path is provenance; bytes used for publication must already exist in staging and reproduce the committed SHA-256 and byte count exactly.

## Durable staging checkpoint

Legacy local acquisition previously proved that the notebook cannot be the definitive archive. The supported checkpoint contract is:

- legacy state file is baseline/read-only;
- new verified objects are appended to the journal;
- journal append is durable before local cache eviction;
- an OS-backed lease prevents concurrent checkpoint writers;
- a final partial JSONL line caused by a crash may be ignored; a complete corrupt line fails closed;
- cache files are removed only after remote verification is durable;
- the normal local floor remains 15 GiB free space.

`tools/staging_checkpoint.py` owns this contract. Do not reintroduce replace-on-live-state as the primary persistence mechanism on Windows.

## Reconciliation batch 001

The first reviewed identity batch resolved ten products to existing catalog models. Sixty images were audited and all sixty were watermarked, therefore:

- identities remain useful for reconciliation/deduplication;
- promotable media: 0;
- public writes: 0.

Do not promote those images merely because identity was resolved.

## Reconciliation batch 002

Versioned SSOT:

- descriptor: `config/external-batches/external-source-001-batch-002.json`;
- evidence: `docs/evidence/external-source-001-batch-002-media.jsonl`;
- source ID: `external-source-001`.

Reviewed scope:

- Shae Vizla — 12 images;
- Rukia Kuchiki — 5 images;
- Doomguy 2 — 6 images;
- Gemma 3 — 6 images;
- total: 4 products / 29 images / 139,465,606 source bytes;
- watermark review: clear=29, watermarked=0, unknown=0;
- production exact-SHA matches before release: 0;
- identity resolution: 4 explicit `new_model`, 0 `map_existing`, 0 automatic mappings.

A prior full local ingest reached 2,733 images / 2,678 detected models / 0 errors. That local archive was subsequently removed and must not be recreated just to release this delta.

### Pinned technical identities

The first green GitHub runner preflight generated these identities; the descriptor now pins them and every later preflight/apply must reproduce them exactly:

| Model | ID | Slug | Code | Images |
| --- | --- | --- | --- | ---: |
| Shae Vizla | `mdl_23dd3630f28aac37578f` | `star-wars-shae-vizla` | `TS-23DD3630F28A` | 12 |
| Rukia Kuchiki | `mdl_11beeac150d4cfc7f7b3` | `bleach-rukia-kuchiki` | `TS-11BEEAC150D4` | 5 |
| Doomguy 2 | `mdl_54f58525ac72af9187c3` | `doom-doomguy-2` | `TS-54F58525AC72` | 6 |
| Gemma 3 | `mdl_03087f436ba0fb2ac272` | `monster-hunter-gemma-3` | `TS-03087F436BA0` | 6 |

Identity drift is a release blocker. Never accept a new ID/slug/code from a rebuild just because display names still match.

## Release tooling

`.github/workflows/external-batch-release.yml` is the only CI release path for this reviewed small delta.

Preflight always performs:

1. checkout and dependency installation;
2. canonical Cloudflare/D1 binding resolution;
3. focused release tests;
4. materialization from private staging into runner temporary storage;
5. SHA-256 and byte-count verification against committed evidence;
6. audited ingest;
7. four-model media-bundle build;
8. exact ID/identityKey/slug/code/imageCount assertions against the descriptor;
9. R2 delta dry-run;
10. D1 new-only read-only production compatibility/SHA checks;
11. short-lived preflight artifact upload.

Publication is allowed only by explicit manual `workflow_dispatch` with `apply=true` and the production environment gate.

Apply repeats materialization/build/identity assertions, then:

1. re-checks D1 new-only state immediately before any production media write;
2. uploads only the public R2 delta;
3. reads every uploaded object back through the canonical media gateway and verifies SHA+size;
4. emits an exact R2 proof bound to the expected public bucket;
5. commits taxonomy + four models + image-source rows in one D1 batch transaction;
6. verifies all four models and all galleries publicly;
7. runs general public-catalog health verification.

`tools/publish_d1_new_models.py` intentionally has no snapshot/retirement semantics. It rejects existing ID/slug/code collisions and pre-existing source SHA matches. Model inserts have no conflict-update handler. The complete delta must fit inside one bounded transaction; otherwise the release fails and must be split by a new explicit authorization rather than partially committed.

## Brand/source isolation

Repository brand gates prohibit competitor/provider marks in tracked paths and contents. Preserve provenance without weakening that gate:

- tracked identifier: `external-source-001`;
- exact origin: `sourceOriginB64` in the descriptor;
- legacy staging prefix: `stagingPrefixB64` in the descriptor;
- each evidence row stores only `sourcePath`, not a full external URL;
- private staging object key is derived from decoded prefix + committed SHA + extension;
- never add a provider name to tracked filenames, fixture values, UI strings or application branding.

This is source isolation, not loss of provenance: `sourceOriginB64 + sourcePath` reconstructs the exact source reference when an authorized tool needs it.

## Current release state

Batch 002 is not considered public until both R2 and D1 apply gates have completed and public verification succeeds. A green PR preflight proves reconstructability and compatibility only; it is not publication.

If another chat continues this work, it must first re-read the current PR head, descriptor, workflow and latest CI. Multiple workstreams may legitimately update the same PR branch; never force-push or discard an unfamiliar commit.

## Do not do

- do not rebuild the full historical media archive on the notebook;
- do not redownload all source media for a four-model release;
- do not publish directly from an acquisition/review directory;
- do not turn staging into a public media origin;
- do not bypass the source-brand gate;
- do not convert name/SHA/visual similarity into automatic model identity;
- do not update or retire an existing model through the `new-only` publisher;
- do not split the authorized four-model D1 delta into independent partial commits;
- do not delete legacy/public media as part of this additive batch.
