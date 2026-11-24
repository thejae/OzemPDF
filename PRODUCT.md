# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Electron shell (Windows/macOS/Linux) rendering a plain HTML/CSS/JS frontend, with a Python engine + local HTTP API as the single backend. Chosen by the user; Tauri was rejected because the engine's runtime (pikepdf, Pillow, fontTools) is Python and would need a Rust rewrite or a sidecar plus a Rust toolchain. The frontend has no build step and also runs in a plain browser against the same API. Assumption: the Electron shell is the product surface; the browser path is a development and fallback surface.

## Users

Nadia (office admin, non-technical: needs a 40 MB scan under an attachment limit in under a minute), Devon (designer/architect/CAD: vectors and text must stay razor-sharp, distrusts blurring compressors), Priya (legal/compliance ops: signed and permission-restricted contracts, originals never damaged, an audit trail), Sam (automation engineer: scriptable CLI, deterministic exit codes, JSON reports, recursive folders, parallel workers), Wei (localization contributor: one JSON file per language, no build toolchain).

## Product Purpose

OzemPDF reduces PDF file size without re-distilling pages. It never rasterizes page content and never rewrites vector or text operators; it performs object-level surgery on embedded rasters, font programs, deflate streams, metadata and cross-reference structures. Success is a file that is much smaller, pixel-identical in Strict Lossless, visually identical in the default preset, and never larger than its input.

## Positioning

The mechanism is surgical, object-level compression with two hard guarantees: vectors and text are untouched by construction, and the output is never larger than the input. Neighbouring "PDF compressors" re-render pages through Ghostscript, which blurs line art, rasterizes text and cannot honour either guarantee. OzemPDF also runs fully offline with no external PDF binaries.

## Operating Context

Used on a locked-down work laptop or a pipeline machine; documents are invoices, contracts, scans, CAD exports, decks and reports. Batches arrive as folders on shared drives; legal documents carry owner passwords and digital signatures. The desktop workflow is a three-step progressive-disclosure flow (add files → configure and queue → results and verification). The automation workflow is `ozempdf <path>` with JSON reporting. Signature locks are an evidence artefact: flattening invalidates the cryptographic signature, so re-signing with the user's own certificate is an explicit, opt-in step.

## Capabilities and Constraints

In scope: single-file and recursive compression; image re-encoding (lossless and perceptual) with downsampling, palette quantisation, JPEG; `/SMask` handling; content-hash deduplication of images, ICC profiles and fonts; font subsetting; structural bloat removal (`/PieceInfo`, `/Thumb`, optional XMP/`/Info`); Zopfli recompression; object-stream packing; owner-encryption unlock; signature flattening; PKCS#12 re-signing after compression; four-category byte accounting; three-step UI; CLI with JSON reports and stable exit codes; JSON localization.

Out of scope: OCR, text extraction or editing, page manipulation, cryptographic signature *preservation*, PDF/A or PDF/X conformance, encrypting outputs, network services, telemetry.

Constraints: Python ≥3.10 engine; no external PDF binaries; outputs are plain unencrypted PDFs unless the user signs them; atomic writes (temp file in destination + atomic replace); never-grow guardrail except for the explicitly opted-in unlock/flatten/sign exception; outputs are unencrypted and unwatermarked.

## Brand Commitments

Name OzemPDF, version line v0.3.3. The product's voice is precise and quiet: it states measured facts ("38.4 MB saved", "vectors preserved"), never marketing adjectives. No emoji as UI iconography.

## Evidence on Hand

`docs/USER_STORIES.md` (54 stories, 11 epics, five personas) and `docs/TECHNICAL_SPECIFICATION.md` (pipeline, invariants, 21 recorded discrepancies) are the normative evidence. No customer logos, testimonials, benchmarks or press assets exist; none may be fabricated. There are no brand fonts or logo files on hand — typography and marks are established by the design system.

## Product Principles

1. **Evidence over adjectives.** Every claim on screen is a measured value (bytes, percentage, page count, stage counts); nothing is asserted that the run did not observe.
2. **Progressive disclosure.** Show only what the current step needs; advanced knobs exist but never front-run the decision.
3. **Never damage the original.** Atomic writes, a never-grow guardrail, and explicit consent before anything irreversible.
4. **The interface disappears into the task.** Familiar desktop-document conventions, precise density, no decoration competing with the queue and the numbers.
5. **Honest about trade-offs.** Lossless, visually lossless and max-slim are explained by what they do, not by what they promise.

## Accessibility & Inclusion

WCAG 2.1 AA contrast for body and placeholder text; full keyboard operation for adding files, running, inspecting and every inspector action; reduced-motion honoured through the OS media query; icon-only controls carry accessible names; all user-facing strings come from the locale catalog so the interface can be fully translated.
