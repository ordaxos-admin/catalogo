# Catalog media quality, deduplication and watermark policy — 2026-10-15

## Purpose

This policy governs gallery enrichment and external-media reconciliation. The goal is to improve existing catalog galleries without creating duplicate public models, without laundering piracy/repost watermarks, and without allowing source preference to bypass identity or evidence gates.

## Identity first

Media similarity never authorizes model identity by itself.

- `map_existing` requires explicit reviewed identity evidence.
- `new_model` requires an explicit decision that the sculpture/variant is distinct.
- exact name, slug, SHA-256, dHash or visual similarity are candidate evidence only.
- two external products must not silently collapse into one public model.
- a staged cover that equals a production cover is a strong anchor for review, not automatic identity approval.

## Image deduplication

1. Exact SHA-256 duplicate: keep one logical image; do not add a duplicate gallery entry.
2. Perceptual/same-view candidate: review explicitly; never replace automatically.
3. Distinct eligible view: add to the gallery.
4. Existing source files and masters are never destructively deleted by the reconciliation step.
5. Source preference is not enough to win. Eligibility and technical quality decide the recommendation.

## Preferred external-source-001 media

`external-source-001` media is expected to often have stronger source renders than the legacy one-image catalog. Prefer the incoming source for an equivalent view only when:

- model identity has already been explicitly reconciled;
- the image passes the watermark gate;
- it is not an exact SHA duplicate;
- technical evidence (quality score, dimensions, sharpness and format) makes it the better representation, or the existing equivalent is blocked/unverified by the watermark policy;
- perceptual replacement still receives an explicit reviewer decision.

The source identity alone never overrides these gates.

## Watermark states

### `clear`

No visible watermark/branding requiring attribution review. Eligible for promotion.

### `creator_attribution`

A legitimate signature, logo or handle belonging to the actual modeler/sculptor/studio responsible for the model. Eligible for promotion after explicit visual audit.

Examples include the actual creator's signed render or creator handle when authorship is verified.

An `@handle` is **not** automatically creator attribution. The reviewer must be able to associate it with the actual modeler/studio for that sculpture.

### `watermarked`

Blocking watermark. Not promotable, even if the image has higher resolution or quality score.

Treat as blocking when the visible mark is associated with reposting, piracy, redistribution, reseller/source promotion or download groups, including patterns such as:

- Telegram/channel/group/invite references;
- `@specialstl` or similar redistribution/repost handles;
- membership/subscription promotion unrelated to the modeler;
- reseller/store/source branding;
- promotional URLs, QR codes or social handles not proven to be the creator;
- download/archive branding;
- any mark whose ownership is ambiguous and appears to advertise redistribution rather than authorship.

Do not crop, paint over or otherwise remove a blocking watermark to make an image promotable.

### `unknown`

The image has not received a conclusive visual audit. External `unknown` media remains fail-closed and cannot advance.

## Existing/legacy media

Legacy media without the new explicit watermark audit remains `legacy_accepted` for compatibility, but an audited eligible incoming equivalent may be recommended over it. The replacement still requires explicit perceptual review.

A known blocking-watermarked legacy image may be replaced by an audited `clear` or `creator_attribution` image after explicit review.

## Production/staging audit snapshot

Audit performed against the production D1 and private R2 staging on 2026-10-15:

- 2,568 published models;
- 2,540 published models currently expose only one logical image;
- 12 expose two images;
- 8 expose three images;
- 2 expose four images;
- 6 expose five or more images;
- private staging contains 1,440 content-addressed source objects, about 6.21 GB;
- 44 staged SHA-256 values already exist exactly in production;
- therefore 1,396 staged objects are not exact SHA duplicates of the production source-image index.

The 44 exact overlaps break down as:

- 40 images belonging to the six already enriched external releases: Shae Vizla (12), Gemma 3 (6), Doomguy 2 (6), DigiDestined Diorama (6), Rukia Kuchiki (5) and Yoruichi Shihōin 2 (5);
- 4 exact-cover anchors on still-single-image models: Jessica Cruz, Momo Ayase, Laura and Artemis.

Those four anchors are priority reconciliation candidates. They do **not** authorize an automatic merge; they should be used to recover the corresponding external product/gallery evidence and then run the normal `map_existing` + watermark + gallery-merge gates.

## Next content sequence

1. Recover product/gallery metadata for the four exact-cover anchors without rebuilding the deleted multi-GB local archive.
2. Bind each external product to its staged SHA inventory.
3. Audit every candidate image visually as `clear`, `creator_attribution`, `watermarked` or `unknown`.
4. Record blocking-mark reason/notes, especially repost/piracy/Telegram/source branding.
5. Run production SHA ownership globally before identity resolution.
6. Resolve identity explicitly as `map_existing` or `new_model`.
7. For `map_existing`, run gallery planning: exact duplicates skipped, visual candidates reviewed, distinct eligible views added.
8. Prefer technically superior eligible external-source-001 views where they improve an existing view.
9. Publish only the bounded approved delta.

No batch should be promoted merely to keep sequence numbers moving. Gallery enrichment of existing models is currently a higher-value content priority than adding more one-image models.
