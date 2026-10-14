# External source 001 — batch 002 — production release evidence

Date: 2026-10-14

## Scope

This evidence closes the production promotion of `config/external-batches/external-source-001-batch-002.json`.

Released delta:

- 4 strictly new models;
- 29 reviewed canonical source images;
- 87 public WebP variants (`thumb`, `card`, `detail`);
- 4 private gallery manifests;
- 91 R2 objects total;
- no retirements;
- no deletes;
- no updates to pre-existing catalog models.

Production storage remains split by contract:

- private staging bucket: `tonecos-catalogo-staging`;
- canonical production media bucket: `tonecos-catalogo-media`;
- public media is served only through the canonical Worker `/media/*` gateway.

## Released models

| Model | Model ID | Slug | Images | Physical gallery version | Public gallery revision |
| --- | --- | --- | ---: | ---: | ---: |
| Gemma 3 | `mdl_03087f436ba0fb2ac272` | `monster-hunter-gemma-3` | 6 | `198588110645181` | 1 |
| Rukia Kuchiki | `mdl_11beeac150d4cfc7f7b3` | `bleach-rukia-kuchiki` | 5 | `276678391971089` | 2 |
| Shae Vizla | `mdl_23dd3630f28aac37578f` | `star-wars-shae-vizla` | 12 | `261025309397459` | 1 |
| Doomguy 2 | `mdl_54f58525ac72af9187c3` | `doom-doomguy-2` | 6 | `82272420992164` | 1 |

`gallery_version` in the bundle/D1 is the physical content-addressed manifest version. The Worker API exposes `public_gallery_version` as its public `gallery_version`; that value is a separate monotonic cache/publication revision.

Rukia is at public revision 2 because its cover pointer was corrected to the canonical first image after the initial publication verification exposed the mismatch. The physical manifest itself did not change.

Canonical Rukia cover after correction:

`media/mdl_11beeac150d4cfc7f7b3/img_d04dbfae4287f9e6c7c7/card.webp`

## R2 verification

The first publication attempt uploaded the complete object set but failed its verification stage because the verifier incorrectly constructed `/media/media/...` and also attempted to expose private `gallery/...` objects through the public media gateway.

The verifier was corrected so that:

1. every object is read back directly from the target R2 bucket and checked by SHA-256 + byte length;
2. `media/...` objects are additionally verified through the canonical public `/media/*` Worker route;
3. `gallery/...` manifests remain private and are verified directly from R2;
4. no delete path exists in this release transport.

Production inventory for this delta is exactly 91 objects: 87 WebP files + 4 gallery manifests.

## D1 promotion

Before mutation, production was rechecked and showed:

- 0 collisions by model ID, slug or code;
- 0 existing candidate source SHA-256 values;
- 0 pre-existing `model_image_sources` rows for the four candidate model IDs.

The D1 promotion preserved the `new-only` contract:

- taxonomy/folder inserts were idempotent (`DO NOTHING`);
- the four model rows were inserted without an update-on-conflict path;
- the 29 image identity rows were committed with the model publication;
- the release did not retire or delete anything.

Post-write validation confirmed:

- 4/4 models published;
- image counts `6 / 5 / 12 / 6`;
- 29 image source rows;
- 29 distinct source SHA-256 values;
- one canonical cover per model;
- image-source ordering aligned with the canonical gallery manifests.

## Public verification

The canonical Worker health endpoint reported the production schema ready.

All four models were found through the public catalog API and every gallery returned its complete expected image count:

- Gemma 3: 6/6;
- Rukia Kuchiki: 5/5;
- Shae Vizla: 12/12;
- Doomguy 2: 6/6.

Each canonical cover was also fetched through the public media gateway using a byte-range request and returned `206` with `image/webp`.

## Post-release idempotency gate

The `new-only` publisher remains strict during `--apply`: any existing model or candidate SHA still blocks a second publication.

Read-only preflight is now intentionally idempotent. A previously released batch passes only when production contains the complete, exact release:

- all candidate models already exist;
- all model identity/taxonomy/gallery metadata is compatible;
- no candidate model remains new;
- all galleries are unchanged;
- every candidate SHA exists;
- every SHA resolves to the same effective model;
- no identity drift or legacy owner move is present.

Partial or divergent publication remains fail-closed.

## Final automated proof

PR: `#174`

Validated head before this evidence file:

`f62a3937dd8ab51fdafba3d8e8e1b4675b8cb82d`

CI:

- workflow `CI` run **#1142** — success;
- workflow `External catalog batch release` run **#30** — success.

Focused release regression set on run #30:

- 17 tests executed;
- 17 passed.

The final read-only production preflight reported:

```json
{
  "releaseState": "already-released",
  "models": 4,
  "images": 29,
  "existingModels": 4,
  "newModels": 0,
  "unchangedGalleries": 4,
  "historicalMatches": 29,
  "productionShaMatches": 29,
  "identityDrift": 0,
  "updatesExistingModels": 0,
  "retirements": 0,
  "deletes": 0
}
```

Preflight R2 dry-run on the same head found exactly:

- 91 objects;
- 8,306,245 transformed production bytes;
- 0 uploads in dry-run;
- 0 deletes.

Run #30 preflight artifact:

- artifact ID: `11931390618`.

## Relevant hardening commits

- `7e48df9` — verify R2 releases using canonical routes/read-back semantics;
- `79e6011` — regression coverage for canonical R2/public-media paths;
- `262eb81` — distinguish physical gallery versions from public gallery revisions;
- `91b21f9` — regression tests for external public release verification;
- `bca0869` — make post-release read-only preflight idempotent while preserving strict `--apply`;
- `f495832` — fail-closed tests for already-released/partial/SHA ownership states;
- `f62a393` — run the complete external-release regression set in the release workflow.

## Release state

`external-source-001-batch-002` is **published and publicly verified**.

The `production-release` label was removed after promotion so the PR is not left armed for another production action.

Future synchronization of PR #174 may audit this batch as `already-released`, but must not republish it. A future `--apply` against these same four models remains intentionally blocked by the strict new-only gate.
