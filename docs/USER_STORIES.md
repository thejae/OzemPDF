# OzemPDF — User Stories

**Product:** OzemPDF v0.3.3 — cross-platform surgical lossless & visually-lossless PDF slimmer
(HTML/CSS/JS interface served by the Python engine, wrapped by an Electron desktop shell, plus the
`ozempdf` CLI)
**Document status:** v2 · derived from the shipped implementation
**Last updated:** 2026-11-24

---

## 1. About this document

These stories describe what OzemPDF does **today** (traced through `python/src/ozempdf/`, `python/src/ozempdf/locales/en.json`, `README.md` and `python/tests/`) plus the small number of user-facing gaps that the implementation leaves open. Every story is written so it can be tested without reading the source.

| Field | Meaning |
| --- | --- |
| **Epic** | Feature area (E1–E11) |
| **Persona** | The primary user the story is for |
| **Priority** | MoSCoW: **Must** = the product is broken without it; **Should** = expected; **Could** = nice to have |
| **Status** | **Shipped** = exists in the current release (0.3.3); a version in parentheses marks where it landed · **Partial** = exists but incomplete · **Proposed** = not built yet |

Stories are numbered `US-001 … US-058`. IDs are stable — reference them in commits, issues, and the changelog.

> **Architecture note (v0.3.3).** These stories are the durable contract and remain authoritative;
> the notes under each story were rewritten to name what implements it today. The interface is plain
> HTML, CSS and JavaScript in `python/src/ozempdf/web/`, served over a loopback HTTP API by
> `python/src/ozempdf/server.py` and wrapped by the Electron shell in `electron/`. The engine, the CLI and the
> invariants are unchanged. Every story is Shipped, including the five that were Proposed/Partial in
> v0.1.0 (US-004, US-036, US-051, US-052, US-053) and four added at v0.3.x for behaviour that had
> shipped without a written story (US-055 … US-058).

---

## 2. Personas

| Persona | Who they are | What they care about most |
| --- | --- | --- |
| **Nadia — Office admin** | HR/finance assistant, non-technical, lives in email | Getting a 40 MB scan under an attachment limit in under a minute; never being asked to configure anything |
| **Devon — Design & engineering** | Brand designer, architect, CAD drafter | Vectors, line art, and text must stay razor-sharp; distrusts "compressors" that blur or rasterize |
| **Priya — Legal & compliance ops** | Contract administrator handling signed, permission-restricted PDFs | Signature locks handled predictably, originals never damaged, audit trail of what changed |
| **Sam — Automation engineer** | Builds document pipelines | A scriptable CLI, deterministic exit codes, machine-readable reports, recursive folders, parallel workers |
| **Wei — Localization contributor** | Translates open-source tools on the side | Adding a language by dropping in one JSON file — no compile step, no build toolchain |

---

## 3. Story map at a glance

| Epic | Stories | Headline |
| --- | --- | --- |
| **E1 · Getting files in** | US-001 – US-004 | Drag, browse, de-duplicate; explain what was ignored |
| **E2 · Managing the queue** | US-005 – US-008 | Thumbnails, live status, remove, retry |
| **E3 · Choosing how hard to squeeze** | US-009 – US-012 | Three presets + an advanced drawer that becomes "Custom" |
| **E4 · Where the results go** | US-013 – US-016 | Suffix, custom folder tree, atomic overwrite |
| **E5 · Running a batch** | US-017 – US-022 | Progress, stop, per-file errors, where you land |
| **E6 · Understanding the savings** | US-023 – US-027 | Totals, "where bytes were saved", drill-in, incremental queue |
| **E7 · Verifying quality** | US-028 – US-032 | Wipe-slider inspector, zoom, pages, external viewer |
| **E8 · Making it mine** | US-033 – US-037 | Dark/light, live language switch, new locale files, remembered settings |
| **E9 · Command line & automation** | US-038 – US-041 | One command, folders, flags, JSON report, exit codes |
| **E10 · The engine's promises** | US-042 – US-048, US-055, US-056 | Vectors intact, never-grow, lossless, dedup, fonts, locks, re-signing |
| **E11 · Trust, reliability & access** | US-049 – US-054, US-057, US-058 | Offline, cross-platform, crash-safe writes, portable folder, browser fallback |

---

## E1 · Getting files in

### US-001 · Drop PDFs or folders and land on the configure step
**Epic:** E1 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want to drag my PDFs (or a whole folder) straight onto the window, so that I never have to learn a file dialog before I can start.

**Acceptance criteria**
- **Given** I am on Step 1 and drag one or more PDFs over the window, **when** I drop them, **then** every PDF is queued and the app advances to Step 2 with the files listed.
- **Given** I drop a folder, **when** it contains PDFs (including in nested subfolders), **then** every PDF inside is queued in a stable sorted order.
- **Given** I am already on Step 2 or Step 3, **when** I drag files over the window, **then** the drop target shows an active drag state and the new files are appended to the queue without disturbing existing rows.
- **Given** the folder I drop contains no PDFs at all, **when** the drop completes, **then** nothing is added and the app stays where it was.

**Notes** — `add_paths_to_queue()`; folder walk is recursive and sorted (`discover_pdf_files()`).

### US-002 · Pick files or a folder with the mouse or a shortcut
**Epic:** E1 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want explicit "Select PDF Files" and "Select Folder" buttons (and keyboard shortcuts), so that I can work without drag-and-drop.

**Acceptance criteria**
- **Given** I am on Step 1, **when** I click *Select PDF Files*, **then** I can multi-select PDFs from a dialog filtered to PDF documents.
- **Given** I am on Step 1, **when** I click *Select Folder*, **then** I pick a directory and all PDFs beneath it are queued.
- **Given** I am on Step 2, **when** I use the queue header's *Add More PDFs* / *Add Folder*, **then** new files are appended to the existing queue rather than replacing it.
- **Given** I press `Ctrl+O` or `Ctrl+Shift+F` anywhere in the window, **then** the corresponding picker opens (except while a batch is running, where no picker opens from the shortcut).

**Notes** — One global `keydown` handler in `app.js` (Ctrl/Cmd+O, Ctrl/Cmd+Shift+O); the same actions are reachable from the queue header toolbar.

### US-003 · Adding the same file twice never duplicates it
**Epic:** E1 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want re-adding a file I already queued to be a no-op, so that my queue and my totals stay trustworthy.

**Acceptance criteria**
- **Given** a PDF is already in the queue, **when** I add it again (by any route), **then** the queue still contains exactly one row for it.
- **Given** I previously ran a suffix-mode batch, **when** I re-scan the same folder, **then** the `_compressed` outputs from that run are not queued as new inputs.
- **Given** OzemPDF is writing a temporary file, **when** a folder scan runs, **then** `.ozempdf_*` temp files are never picked up.

**Notes** — De-duplication is by resolved absolute path; folder scans skip files whose stem already ends with the active suffix.

### US-004 · Tell me why my drop did nothing
**Epic:** E1 · **Persona:** Nadia · **Priority:** Could · **Status:** Shipped (v0.3.0)

**Story**
> As Nadia, when I drop a file that OzemPDF cannot use, I want to be told so, so that I stop retrying the same thing.

**Acceptance criteria**
- **Given** I drop only non-PDF files, **when** the drop completes, **then** a single non-blocking notice names what was skipped and why.
- **Given** I drop a mix of PDFs and non-PDFs, **when** the drop completes, **then** the PDFs are queued and the notice counts only the skipped items.
- **Given** I drop a file that no longer exists or cannot be read, **when** it is inspected, **then** it is either excluded from the queue or shown as a failed row with a readable reason — never as a silent blank row.

**Notes** — The queue API returns `skipped[]` with a localized reason per item and the interface shows one notice with the count and reasons. Electron drops pass real paths; a plain browser stages uploaded files and explains why.

---

## E2 · Managing the queue

### US-005 · See what is queued, at a glance
**Epic:** E2 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want each queued file to show a page-1 thumbnail, page count, and size, so that I can confirm I queued the right documents before running anything.

**Acceptance criteria**
- **Given** files are queued, **when** Step 2 appears, **then** each row shows a page-1 thumbnail, the document name, its relative folder, page count, original size, status, savings, and an action.
- **Given** thumbnails are still rendering, **then** a neutral placeholder is shown and the page count displays a loading indicator rather than a wrong number.
- **Given** a long filename or deep path, **then** text is ellipsized and the full value is available on hover.
- **Given** I resize the window, **then** the queue grows with it, columns keep stable minimum widths, and the column headers stay visible above the scrolling rows.
- **Given** the queue has files, **then** the header summarises them as a count plus total pages and total size.

**Notes** — Rows are 60 px; thumbnail rendering and metadata reading happen off the UI thread (`ThumbnailLoaderThread`).

### US-006 · Watch each file's status change in the row
**Epic:** E2 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want each row to tell me whether it is waiting, working, done, or failed, so that I can see progress without reading a log.

**Acceptance criteria**
- **Given** a row is waiting, **then** it reads *Queued — Ready to Slim* and its savings column shows a dash.
- **Given** a row is actively compressing, **then** it reads *Slimming…* with an indeterminate progress indicator (no invented percentage).
- **Given** a row finishes successfully, **then** it shows the new size in green and a savings pill such as `-73.4%`.
- **Given** a row fails, **then** it shows *Error: …* in the danger color, truncated in the row with the full message on hover.
- **Given** files were added after a completed run, **then** they carry a *New — Queued* badge until they run.

### US-007 · Take files back out of the queue
**Epic:** E2 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want to remove a file I queued by mistake — or clear the whole list — so that I do not pay to compress something I did not mean to.

**Acceptance criteria**
- **Given** a non-running row, **when** I click *Remove*, **then** only that row disappears.
- **Given** any queue, **when** I click *Clear All*, **then** the queue empties and the app returns to Step 1.
- **Given** removing that row empties the queue, **then** the app returns to Step 1 rather than showing an empty table.
- **Given** a batch is running, **when** I try to remove a row or clear the queue, **then** nothing is removed.

### US-008 · Retry a file that failed without re-adding it
**Epic:** E2 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, when one file fails, I want to retry just that file in place, so that I do not have to rebuild my queue.

**Acceptance criteria**
- **Given** a row is in the error state, **then** it offers *Retry* and *Remove*.
- **Given** I click *Retry*, **then** the row returns to the queue with its previous error and result cleared, and the app shows Step 2 ready to run.
- **Given** the retry succeeds, **then** the row shows the new size and savings and the old error is gone.
- **Given** the retry fails again, **then** the row returns to the error state with the new message — the previous message is not shown.

---

## E3 · Choosing how hard to squeeze

### US-009 · Pick one of three presets
**Epic:** E3 · **Persona:** Devon · **Priority:** Must · **Status:** Shipped

**Story**
> As Devon, I want to choose between *Strict Lossless*, *Visually Lossless*, and *Max Slim* with a one-line explanation of each, so that I can pick the right trade-off without a manual.

**Acceptance criteria**
- **Given** Step 2, **then** a *Compression Mode* card shows the three presets as segmented pills with the active one visually selected and a description updating to the chosen preset.
- **Given** a first run or no stored choice, **then** *Visually Lossless* (Balanced) is selected.
- **Given** any preset, **then** hovering it explains the exact trade-off (lossless: pixels bit-identical, no resampling; balanced: 150 DPI / Q85; max: 110 DPI / Q75 / 25 passes).
- **Given** I switch preset, **then** the advanced knobs reset to that preset's values while my output destination is left untouched.

**Notes** — Preset values live in `CompressionConfig.from_preset()`.

### US-010 · Look under the hood only when I want to
**Epic:** E3 · **Persona:** Sam · **Priority:** Should · **Status:** Shipped

**Story**
> As Sam, I want an advanced drawer with three clearly grouped sections, so that I can tune the engine when I need to and ignore it when I do not.

**Acceptance criteria**
- **Given** Step 2, **then** the *Advanced* control is collapsed by default and opens with an animation to the drawer's natural height (no clipped controls).
- **Given** the drawer is open, **then** it shows three sections: *1. Raster Images* (DPI cap, photo quality, screenshot→palette), *2. Stream & Deflate Engine* (Zopfli passes, strip redundant SMask, dedupe images & ICCs, pack object streams), and *3. Fonts, Metadata & Locks* (subset fonts, strip app bloat, flatten locks & signatures, strip XMP & Doc Info).
- **Given** I move a slider, **then** its value label updates immediately and states the effective meaning, e.g. `Max 150 DPI (> 220 DPI)` or `15 passes`.
- **Given** I choose *Strict Lossless*, **then** the DPI and quality labels read as lossless/no-downsampling rather than showing meaningless numbers.
- **Given** I reopen the app, **then** the drawer remembers whether it was open.

### US-011 · Any custom knob change becomes "Custom"
**Epic:** E3 · **Persona:** Sam · **Priority:** Should · **Status:** Shipped

**Story**
> As Sam, when I tweak a single knob, I want the app to stop claiming I am on a preset, so that I always know which configuration actually ran.

**Acceptance criteria**
- **Given** a preset is active, **when** I change any knob, **then** no preset pill remains selected and the description switches to the custom wording.
- **Given** the configuration becomes custom, **then** my output destination and suffix are preserved.
- **Given** the values land on a preset by coincidence, **then** the app still reports custom (it does not silently claim a preset).

### US-012 · Reset back to the preset defaults
**Epic:** E3 · **Persona:** Sam · **Priority:** Should · **Status:** Shipped

**Story**
> As Sam, I want a one-click reset, so that I can put the knobs back where a preset says they belong.

**Acceptance criteria**
- **Given** a preset is active, **when** I click *Reset to Preset Defaults*, **then** every knob returns to that preset's values.
- **Given** the configuration is custom, **when** I reset, **then** the knobs return to *Visually Lossless* defaults and that preset becomes active.
- **Given** the reset happens, **then** the drawer stays open and the DPI/quality labels are refreshed.

---

## E4 · Where the results go

### US-013 · Save beside the original with a suffix I can edit
**Epic:** E4 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want slimmed copies saved next to the originals with a predictable suffix, so that I always keep the file I sent to my colleagues untouched.

**Acceptance criteria**
- **Given** the suffix mode is selected, **then** the output name is `<original stem><suffix>.pdf` in the original's own folder.
- **Given** the default suffix, **then** it is `_compressed`; **and** I can edit it inline, with the `.pdf` extension fixed and visible.
- **Given** I clear the suffix, **then** the app falls back to `_compressed` rather than writing over the original name.
- **Given** I edit the suffix, **then** the destination summary and the next run's filenames both reflect it.
- **Given** the same file is compressed twice with the same suffix, **then** the previous output is replaced — never duplicated with a numbered name.

### US-014 · Send everything into a folder tree of my choosing
**Epic:** E4 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want to collect a whole batch into one output folder and keep its subfolder structure, so that the slimmed set mirrors the source archive.

**Acceptance criteria**
- **Given** I choose *Custom output folder…*, **then** a folder picker opens immediately and the chosen path becomes the destination.
- **Given** a file came from `invoices/2024/q3/x.pdf` and I set the output root, **then** the result is written to `<root>/2024/q3/x.pdf`.
- **Given** a file was added individually (no base folder), **then** it lands directly at `<root>/<name>.pdf`.
- **Given** the destination folder does not exist, **then** it is created as needed.
- **Given** the custom folder is set, **then** the destination control shows that mode and path, not the suffix option.

### US-015 · Overwrite originals only after I confirm
**Epic:** E4 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want an explicit, unambiguous confirmation before anything replaces my originals, so that a careless click cannot destroy source documents.

**Acceptance criteria**
- **Given** *Overwrite original files* is selected, **when** I press the primary action, **then** a warning dialog names how many original files will be replaced and states that it cannot be undone.
- **Given** that dialog, **then** the safe choice is the default button and cancelling writes nothing at all.
- **Given** I confirm, **then** each original is replaced only after its slimmed output has been produced and verified — never by writing in place directly.
- **Given** a file's new output is not smaller (see US-043), **then** its original bytes are left exactly as they were.
- **Given** the overwrite dialog is dismissed, **then** the queue, configuration, and any results already on screen are unaffected.

### US-016 · Open the folder the results went to
**Epic:** E4 · **Persona:** Nadia · **Priority:** Should · **Status:** Shipped

**Story**
> As Nadia, I want one button that opens the output location, so that I do not have to remember where I pointed the last run.

**Acceptance criteria**
- **Given** a run has finished, **then** *Open Output* is available in the action dock.
- **Given** a custom output folder is configured and exists, **when** I click it, **then** that folder opens in the system file manager.
- **Given** no custom folder is configured, **when** I click it, **then** the folder of a completed result opens (falling back to the input folder).
- **Given** the path no longer exists, **then** nothing is opened and no error dialog interrupts me.

---

## E5 · Running a batch

### US-017 · Start the run with a button that tells me the cost
**Epic:** E5 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want the primary button to state how many files and how much data I am about to process, so that I know what I am committing to.

**Acceptance criteria**
- **Given** one queued file, **then** the button reads `Slim 1 PDF (<size>)`; **given** several, **then** `Slim N PDFs (<total size>)`.
- **Given** some files are already done and new ones are pending, **then** the button counts and sizes only the pending files.
- **Given** a batch is running, **then** the button is disabled and shows `Slimming (done/total)…`.
- **Given** focus is not in a text field, **when** I press `Enter`, **then** the batch starts.
- **Given** I am typing in the suffix field, **when** I press `Enter`, **then** no batch starts.

### US-018 · The window stays usable while it works
**Epic:** E5 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want the window to keep repainting and responding while files compress, so that I never face a frozen app that I might kill.

**Acceptance criteria**
- **Given** a batch is running, **then** rows update as each file completes and the primary button's counter advances.
- **Given** a batch is running, **then** the window still repaints, scrolls, and answers clicks; it does not enter a "Not responding" state.
- **Given** multiple queued files, **then** they are processed in parallel on multiple CPU cores and rows may finish out of order.
- **Given** a single file, **then** it is processed without the overhead of worker processes.

**Notes** — Batch work runs in the server's run thread, which dispatches files to a `ProcessPoolExecutor`; the engine reports per-file completion only, which is why per-row progress is indeterminate.

### US-019 · Stop a long run without losing finished work
**Epic:** E5 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want to stop a batch that is taking too long, so that I keep the files that already finished and get my machine back.

**Acceptance criteria**
- **Given** a batch is running, **then** *Stop* is visible; **when** I click it, **then** it becomes *Stopping…* and cannot be clicked twice.
- **Given** I stop, **then** the file currently being compressed is allowed to finish (it is not interrupted mid-write) and the remaining files do not start.
- **Given** the stop completes, **then** completed rows keep their results, unstarted rows return to the plain queued state without results, and the primary button is re-enabled for another run.
- **Given** I stopped, **then** I stay in the current step and no result is fabricated for a file that never ran.

### US-020 · One bad file must not sink the batch
**Epic:** E5 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, when a corrupt or unreadable PDF is in the batch, I want the rest to still process and the bad one clearly flagged, so that one problem costs me one file, not the whole queue.

**Acceptance criteria**
- **Given** a file cannot be opened or processed, **then** that row enters the error state with a human-readable reason (full text on hover) and no output file is left behind.
- **Given** a failure occurs, **then** every other file in the batch still runs to completion.
- **Given** failures occurred, **then** the batch summary counts them separately from successes and the aggregate savings exclude them.
- **Given** the user opens Step 3 after a partially failed run, **then** the banner states the failure count instead of implying a clean success.

### US-021 · Land on the results when everything worked
**Epic:** E5 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, when a run fully succeeds, I want to be taken straight to the results, so that there is no ambiguity about whether it finished.

**Acceptance criteria**
- **Given** every file in the run succeeded, **when** the batch finishes, **then** the app advances to Step 3 and shows the totals banner.
- **Given** the run went to Step 3, **then** the queue heading changes to processed documents and the configure controls are hidden.
- **Given** the run finished, **then** the step indicator shows Step 3 as current and Steps 1–2 as completed.

### US-022 · Stay on the queue when something failed
**Epic:** E5 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, when files failed, I want to remain on the queue with the failed rows in front of me, so that I can fix or retry them immediately.

**Acceptance criteria**
- **Given** at least one file failed, **when** the batch finishes, **then** the app stays on Step 2 with failed rows visible and their *Retry* actions available.
- **Given** failures occurred, **then** the app does not auto-advance to Step 3 and does not show a "complete" banner where it would be read as success.
- **Given** the user then opens Step 3 manually, **then** the results shown are consistent with what actually succeeded.

---

## E6 · Understanding the savings

### US-023 · See the total I saved across the batch
**Epic:** E6 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want one headline number for the whole batch, so that I can report back what I achieved.

**Acceptance criteria**
- **Given** a successful run, **then** a banner states the bytes saved and the percentage, e.g. *Batch Slimming Complete — Saved 38.4 MB (-71.2%)*.
- **Given** the banner, **then** a subtitle restates it as *from X to Y across N files* and confirms vectors and text were preserved.
- **Given** a partially failed run viewed on Step 3, **then** the banner uses warning styling and includes the failure count.
- **Given** files were already known to be optimal, **then** their zero saving is included honestly rather than dropped from the totals.

### US-024 · See which parts of the PDF gave up the bytes
**Epic:** E6 · **Persona:** Devon · **Priority:** Must · **Status:** Shipped

**Story**
> As Devon, I want a breakdown across images, fonts, vector/text streams, and metadata, so that I can confirm the savings came from the right places and my vectors were not sacrificed.

**Acceptance criteria**
- **Given** results exist, **then** a *Where Bytes Were Saved* card lists the four categories with the bytes removed in each and a proportional bar.
- **Given** the card first appears, **then** it shows the aggregate across **all** completed files, labelled with the number of files and the total before→after.
- **Given** a category saved nothing, **then** it is still listed with a minimal bar rather than hidden or negative.
- **Given** the card is shown, **then** a caption explains that clicking a completed file shows that file's detail.
- **Given** savings are counted for vectors, **then** they represent denser stream compression — never removed drawing content.

**Notes** — Category audit: `audit_pdf_bytes()`; categories are embedded images, embedded fonts, vector & text streams, metadata & `/PieceInfo`.

### US-025 · Drill into a single file, then get back to the total
**Epic:** E6 · **Persona:** Devon · **Priority:** Should · **Status:** Shipped

**Story**
> As Devon, I want to click one completed file and see only its breakdown, then return to the batch total, so that I can investigate an outlier without losing the overview.

**Acceptance criteria**
- **Given** Step 3 with completed files, **when** I click a completed row, **then** the card header switches to that file's name with its own before→after and the bars animate to its values.
- **Given** a file is selected, **when** I click the same row again, **then** the card returns to the aggregate total.
- **Given** a file is selected, **then** a *Show Total* action also returns to the aggregate.
- **Given** a row is not completed yet (queued, running, failed), **when** I click it, **then** the breakdown selection does not change.
- **Given** the app is on Step 2, **then** the breakdown card is not shown at all.

### US-026 · Add more files after a run without losing the results
**Epic:** E6 · **Persona:** Priya · **Priority:** Should · **Status:** Shipped

**Story**
> As Priya, after a run I want to drop in more PDFs and slim just those, so that I do not have to start over or lose the results already on screen.

**Acceptance criteria**
- **Given** I am on Step 3 with completed files, **when** I add new files, **then** they appear as *New — Queued* while completed rows and their results stay intact.
- **Given** the queue mixes completed and pending files, **then** the heading says *Batch Queue (N Slimmed, M New Pending)* and the configure controls reappear.
- **Given** I run again, **then** the primary action compresses only the pending files (`Slim M New PDFs (size)`), not the completed ones.
- **Given** the second run completes, **then** both runs' results are present and the aggregate total covers all completed files.

### US-027 · Start a clean batch
**Epic:** E6 · **Persona:** Nadia · **Priority:** Should · **Status:** Shipped

**Story**
> As Nadia, when I am done, I want to clear the results and begin a new batch, so that the next task starts from a clean slate.

**Acceptance criteria**
- **Given** Step 3, **when** I click *Start New Batch*, **then** the queue empties and the app returns to Step 1.
- **Given** I start a new batch, **then** no file on disk is deleted or moved — only the in-app list is cleared.
- **Given** a batch is running, **when** I try to start a new one, **then** the queue is not cleared.

---

## E7 · Verifying quality

### US-028 · Preview a page before I commit
**Epic:** E7 · **Persona:** Nadia · **Priority:** Should · **Status:** Shipped

**Story**
> As Nadia, I want to preview a document from the queue, so that I can check I picked the right file before spending time compressing it.

**Acceptance criteria**
- **Given** a queued file, **when** I click its thumbnail, **then** the inspector opens for that file.
- **Given** the file has not been compressed yet, **then** the inspector opens in preview-only mode showing a single page and a *Pre-Compression* badge, with no wipe control.
- **Given** the inspector is open, **then** it is modal — I cannot accidentally run a batch behind it — and *Done* closes it.

### US-029 · Compare before and after with a wipe slider
**Epic:** E7 · **Persona:** Devon · **Priority:** Must · **Status:** Shipped

**Story**
> As Devon, I want to drag a divider across the same page to reveal original versus slimmed versions, so that I can judge whether the compression is truly invisible.

**Acceptance criteria**
- **Given** a compressed file, **when** I click *Inspect*, **then** the dialog shows the original on the left and the slimmed version on the right at identical coordinates and zoom.
- **Given** the divider starts at the middle, **when** I drag the handle (or the footer slider), **then** the reveal moves smoothly between 2% and 98% and the percentage label tracks it.
- **Given** the comparison view, **then** a badge on the left states the original size and a badge on the right states the slimmed size, the percentage saved, and that vectors stay crisp.
- **Given** I drag on the page away from the divider, **then** I pan instead of moving the divider; the cursor makes the difference obvious.

### US-030 · Zoom in far enough to judge a hairline
**Epic:** E7 · **Persona:** Devon · **Priority:** Should · **Status:** Shipped

**Story**
> As Devon, I want to zoom up to 800% and pan around, so that I can check whether a thin logo stroke or small text survived.

**Acceptance criteria**
- **Given** the inspector, **then** zoom steps are 100%, 200%, 400%, and 800%, with the current step visibly selected and 200% as the default.
- **Given** I scroll the wheel over the page, **then** zoom steps up or down; `+` and `-` do the same.
- **Given** I am zoomed in, **when** I drag the canvas, **then** the page pans and both layers pan together (no drift between original and slimmed).
- **Given** I press `R`, or click *Reset*, **then** the page re-centers and the divider returns to 50%.

### US-031 · Move between pages inside the inspector
**Epic:** E7 · **Persona:** Devon · **Priority:** Should · **Status:** Shipped

**Story**
> As Devon, I want to check the same page number in both versions and jump to any page, so that I can verify a specific page rather than only page one.

**Acceptance criteria**
- **Given** the inspector, **then** a page control shows and accepts `n / total`, and jumping to a number renders that page in both layers.
- **Given** I click the previous/next chevrons or press `←`/`→`, **then** the page changes and the chevrons disable at the first and last page.
- **Given** the viewer changes page, **then** both layers stay on the same page and the wipe position is preserved.

### US-032 · Hand the result to my normal PDF viewer
**Epic:** E7 · **Persona:** Nadia · **Priority:** Could · **Status:** Shipped

**Story**
> As Nadia, I want to open the file in the viewer I already use, so that I can keep reading it after checking it.

**Acceptance criteria**
- **Given** a slimmed result exists, **when** I click *Open File*, **then** the system default PDF viewer opens the slimmed file.
- **Given** no slimmed file exists yet, **then** the original opens instead.
- **Given** the file is gone, **then** nothing opens and no error dialog interrupts the session.

---

## E8 · Making it mine

### US-033 · Switch between dark and light
**Epic:** E8 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want a dark/light toggle that recolors everything, so that the app suits my screen and my eyes.

**Acceptance criteria**
- **Given** the header, **then** a theme toggle is always available, with a tooltip naming the mode it will switch to.
- **Given** the very first launch with no stored preference, **then** the app follows the operating system's light/dark setting.
- **Given** I toggle the theme, **then** the window, queue table, preset card, drawer, banner, breakdown bars, footer, menus, dialogs and the inspector canvas all update — no widget keeps the other palette.
- **Given** I restart the app, **then** my chosen theme is restored.

### US-034 · Change the interface language without restarting
**Epic:** E8 · **Persona:** Wei · **Priority:** Should · **Status:** Shipped

**Story**
> As Wei, I want to switch languages from the header and see the UI update in place, so that I can review a translation while using the app.

**Acceptance criteria**
- **Given** the header, **then** a language control shows the current two-letter code and its menu lists every bundled locale by its own native name, with the active one checked.
- **Given** I choose a language, **then** the window title, header, step labels, buttons, tooltips, column headers, and result cards are retranslated immediately — no restart.
- **Given** the switch, **then** the language menu and control reflect the new choice.

**Notes** — Live switching is complete. Making the choice survive a restart belongs to US-036 (currently partial).

### US-035 · Add my language by dropping in one file
**Epic:** E8 · **Persona:** Wei · **Priority:** Should · **Status:** Shipped

**Story**
> As Wei, I want to contribute a translation by adding one JSON file, so that I do not need a compiler, a build, or a developer to ship my language.

**Acceptance criteria**
- **Given** I copy the English catalog to a new `<code>.json` with translated values and a `_meta` block naming the language, **when** the app starts, **then** the language appears in the menu automatically.
- **Given** my catalog is missing keys (e.g. newly added features), **then** those strings fall back to English and the UI never shows raw key names or blanks.
- **Given** the app itself, **then** every user-facing string comes from the catalog — no string is hardcoded in a widget.
- **Given** a locale is set through the environment, **then** that locale is used on start when a catalog for it exists.

**Notes** — No compilation step; catalogs live in `python/src/ozempdf/locales/` and are discovered at runtime; fallback is `en.json`.

### US-036 · Keep my setup between launches
**Epic:** E8 · **Persona:** Sam · **Priority:** Should · **Status:** Shipped (v0.3.0)

**Story**
> As Sam, I want my theme, preset, knobs, and output destination restored next time, so that I do not re-configure the app every day.

**Acceptance criteria**
- **Given** I changed the theme, preset, any advanced knob, the output mode, the suffix, the custom output folder, or the drawer state, **then** each is persisted when I change it (not only on exit).
- **Given** I relaunch the app, **then** all persisted choices are restored and the active preset/description match the restored configuration.
- **Given** the settings file is missing or corrupt, **then** the app starts with safe defaults instead of failing to open.
- **Given** the settings location is not writable, **then** the app keeps working and silently skips saving.
- **Given** I switch language, **then** that choice is restored on the next launch. *(Implemented in v0.3.0: the language joins the persisted payload and is restored by the server at startup.)*

**Notes** — A small JSON file under the OS app-config location; writes are atomic.

### US-037 · Respect my reduce-motion setting
**Epic:** E8 · **Persona:** Nadia · **Priority:** Could · **Status:** Shipped

**Story**
> As Nadia, who gets motion sickness from UI animation, I want the drawers and bars to stop animating, so that the app is comfortable to use.

**Acceptance criteria**
- **Given** reduced motion is requested, **when** I open the advanced drawer, **then** it appears at full height instantly with no slide.
- **Given** reduced motion is requested, **then** the savings bars jump to their target values instead of animating.
- **Given** reduced motion is not requested, **then** both animations play with a short, eased duration.

---

## E9 · Command line & automation

### US-038 · Slim a single file with one command
**Epic:** E9 · **Persona:** Sam · **Priority:** Must · **Status:** Shipped

**Story**
> As Sam, I want `ozempdf report.pdf` to produce a slimmed copy and tell me the result, so that I can use it without reading documentation first.

**Acceptance criteria**
- **Given** a PDF path, **when** I run `ozempdf <file>`, **then** it is compressed with the balanced default and written beside the original as `<stem>_compressed.pdf`.
- **Given** the run ends, **then** each file prints a line with original size, new size, percentage saved, and output name, followed by a summary with totals and a per-category savings breakdown.
- **Given** no arguments, **then** the desktop GUI launches instead of printing help.
- **Given** `--version`, **then** the tool prints its version and exits successfully.

### US-039 · Recurse a folder across all my cores
**Epic:** E9 · **Persona:** Sam · **Priority:** Must · **Status:** Shipped

**Story**
> As Sam, I want to point the CLI at a directory and have every PDF inside processed in parallel, so that a nightly batch finishes quickly.

**Acceptance criteria**
- **Given** a directory, **then** every PDF beneath it is discovered recursively and processed.
- **Given** `--output-dir`, **then** results are written under it preserving the nested structure; **given** no output directory, **then** suffix mode applies.
- **Given** `--workers N`, **then** at most N files are processed concurrently; **given** no flag, **then** the worker count follows the CPU count and never exceeds the number of files.
- **Given** a file fails, **then** the rest continue and the failure is reported per file.
- **Given** a directory with no PDFs, **then** the tool says so and exits non-zero rather than pretending to succeed.

### US-040 · Tune the engine from the CLI
**Epic:** E9 · **Persona:** Sam · **Priority:** Should · **Status:** Shipped

**Story**
> As Sam, I want the same surgical knobs on the command line as in the GUI, so that pipelines can dial the trade-off per job.

**Acceptance criteria**
- **Given** `--preset lossless|balanced|max`, **then** that preset's configuration is used.
- **Given** `--max-dpi`, `--jpeg-quality`, or `--zopfli-iterations`, **then** the value is clamped to a safe range rather than accepted blindly, and a visually-lossless override switches off strict-lossless behaviour as documented.
- **Given** `--no-subset-fonts`, `--strip-metadata`, or `--no-unlock-signatures`, **then** that stage is disabled for the run.
- **Given** `--suffix`, `--overwrite`, `--workers`, `--password`, or `--lang`, **then** the corresponding behaviour applies and the help text documents it.
- **Given** `--gui` (optionally with paths), **then** the GUI opens preloaded with those inputs instead of running headless.

### US-041 · Get a machine-readable report and trustworthy exit codes
**Epic:** E9 · **Persona:** Sam · **Priority:** Must · **Status:** Shipped

**Story**
> As Sam, I want JSON output and exit codes that reflect reality, so that a CI job or cron script can branch on the outcome.

**Acceptance criteria**
- **Given** `--json-report report.json`, **then** a JSON document is written containing totals, per-file results, the per-category breakdown, and elapsed time.
- **Given** `--json-report -`, **then** JSON is printed to stdout **and** the human-readable output is suppressed, so the stream is parseable.
- **Given** every file succeeded, **then** the exit code is 0; **given** any file failed, **then** it is 2; **given** no PDFs were found, **then** it is 1.
- **Given** the JSON report path has missing parent directories, **then** they are created rather than failing.
- **Given** a failed file, **then** its entry records the error message and no output path is implied.

---

## E10 · The engine's promises

### US-042 · Vector art and text come out untouched
**Epic:** E10 · **Persona:** Devon · **Priority:** Must · **Status:** Shipped

**Story**
> As Devon, I want a compressor that never re-distills, rasterizes, or reflows my page content, so that line art, Bezier logos, and small text stay as sharp as the original.

**Acceptance criteria**
- **Given** any preset, **then** page content streams, vector path operators, and text operators are never rasterized, re-laid-out, or replaced with images.
- **Given** any preset, **then** page geometry and the number of pages are preserved.
- **Given** the vector category in the savings breakdown shows savings, **then** those bytes come from denser stream compression of the same operations, not from deleting vector content.
- **Given** a PDF with no embedded rasters at all, **when** it is processed in lossless mode, **then** it is not converted to a rasterized or image-backed PDF.

### US-043 · The output is never bigger than the input
**Epic:** E10 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want a hard guarantee that "compression" cannot make a file larger, so that I can run it over an archive without inspecting every result.

**Acceptance criteria**
- **Given** any file, **when** the result would be equal to or larger than the input, **then** the original bytes are kept and no larger file is written.
- **Given** the guardrail triggers in suffix or folder mode, **then** the output is a faithful copy of the original at the intended output location (never a partial or temp file).
- **Given** an individual image stream would grow when re-encoded, **then** its original encoding is kept.
- **Given** the file was encrypted or carried signature locks **and** the user asked for those to be flattened, **then** this guardrail exception is the only case where output size may exceed the original size.
- **Given** the guardrail fired, **then** the run still counts as successful rather than as an error.

### US-044 · Strict Lossless really is bit-identical
**Epic:** E10 · **Persona:** Devon · **Priority:** Must · **Status:** Shipped

**Story**
> As Devon, when I choose *Strict Lossless*, I want a mathematical guarantee that every pixel is unchanged, so that I can hand the file to a client who audits quality.

**Acceptance criteria**
- **Given** *Strict Lossless*, **then** images are never resampled and photographs are never re-encoded with lossy JPEG.
- **Given** *Strict Lossless*, **then** only lossless transforms run: deflate/Zopfli recompression, lossless JPEG header/metadata stripping, RGB→gray compaction when channels are identical, indexed palettes when the image truly has few colours, opaque soft-mask removal, resource deduplication, and font subsetting.
- **Given** an image whose channels differ, **then** it is not converted to grayscale; **given** an image with more than the palette size of unique colours, **then** it is not converted to an indexed palette.
- **Given** a lossless run on a document whose savings come only from these transforms, **then** the rendered output is visually and pixel-wise identical to the input.

### US-045 · Visually Lossless shrinks photos and screenshots invisibly
**Epic:** E10 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, with the default preset, I want big scanned photos and screenshots to get much smaller while still looking the same on screen, so that attachments fit without looking degraded.

**Acceptance criteria**
- **Given** *Visually Lossless*, **then** raster images above the effective-DPI trigger are downsampled toward the 150 DPI target using a high-quality Lanczos filter.
- **Given** a flat UI/screenshot-style raster, **then** it is palette-quantized rather than JPEG-compressed, so UI text does not gain ringing artifacts.
- **Given** a continuous-tone photograph, **then** it is encoded as progressive JPEG at the preset's quality.
- **Given** an image already at or below the trigger resolution, **then** it is not downsampled merely because the preset allows it.
- **Given** *Max Slim*, **then** the same pipeline uses the more aggressive targets (110 DPI, lower quality, more deflate passes) and strips document metadata.

### US-046 · Duplicate resources, fonts, and stream bloat get removed
**Epic:** E10 · **Persona:** Priya · **Priority:** Should · **Status:** Shipped

**Story**
> As Priya, I want repeated logos, colour profiles and unused fonts cleaned up, so that long documents with repeated templates shrink the most.

**Acceptance criteria**
- **Given** byte-identical images, colour profiles, or font streams used on many pages, **then** a single shared object is stored and every page still references it.
- **Given** embedded TrueType/OpenType/CFF fonts, **then** only glyphs actually referenced by the document are retained.
- **Given** documents carrying editor-private editing dictionaries, unused page thumbnails, or fully opaque soft masks, **then** those are removed when the relevant option is enabled.
- **Given** non-stream PDF objects, **then** they are packed into compressed object streams when that option is enabled.
- **Given** the file is saved, **then** unreferenced objects are dropped.

### US-047 · Encrypted and signed PDFs can be slimmed deliberately
**Epic:** E10 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want permission-locked and signature-locked contracts to be compressible, with the visible signature kept and the lock handling under my control.

**Acceptance criteria**
- **Given** a PDF with owner/permission restrictions but no open password, **then** it can be opened and compressed without me supplying anything.
- **Given** a PDF with a valid digital signature, **when** signature handling is enabled, **then** the cryptographic signature byte-range locks and permission restrictions are removed while the visible signature appearance stays on the page.
- **Given** I turn signature/lock handling off, **then** the document is not unlocked or flattened and any resulting incompatibility is reported as a normal file error rather than silently ignored.
- **Given** I flattened locks or signatures, **then** the run does not hide that fact in the result metrics.
- **Given** a PDF that requires an open password, **then** the CLI accepts one via flag and the interface prompts once per run, retrying the failed rows (see US-051).
- **Given** a PDF whose only protection is permission restrictions (it opens in a reader without a password), **then** those restrictions are removed automatically with no prompt, and the result reports `protection_kind: permissions` and `protection_removed: true`.
- **Given** a certificate-encrypted PDF, **then** it is reported as requiring its private key; this engine cannot decrypt it and does not pretend to.

### US-048 · Metadata and editor bloat removal is my choice
**Epic:** E10 · **Persona:** Priya · **Priority:** Should · **Status:** Shipped

**Story**
> As Priya, I want control over stripping document metadata, so that I can remove it for privacy on some jobs and preserve it for document management on others.

**Acceptance criteria**
- **Given** the default presets (Lossless, Visually Lossless), **then** XMP metadata and Document Info are preserved.
- **Given** *Max Slim*, **then** metadata stripping is on by default.
- **Given** I toggle *Strip XMP & Doc Info* (or pass `--strip-metadata`), **then** the setting applies to the run and survives to the next launch.
- **Given** metadata is stripped, **then** page content, vectors, and text are untouched.

---

## E11 · Trust, reliability & access

### US-049 · Works offline, with nothing else to install
**Epic:** E11 · **Persona:** Nadia · **Priority:** Must · **Status:** Shipped

**Story**
> As Nadia, I want the app to work on a locked-down laptop with no internet and no Ghostscript, so that IT approval is trivial and my documents never leave the machine.

**Acceptance criteria**
- **Given** no network connection, **then** every feature still works.
- **Given** a clean machine, **then** no external PDF binaries (Ghostscript, Poppler, qpdf CLI) are required.
- **Given** the standalone build, **then** it launches without a Python installation.
- **Given** normal use, **then** the app sends no document content or telemetry anywhere.

### US-050 · Same behaviour on Windows, macOS, and Linux
**Epic:** E11 · **Persona:** Sam · **Priority:** Must · **Status:** Shipped

**Story**
> As Sam, I want identical behaviour across the three desktop platforms, so that our mixed-OS team shares one set of instructions.

**Acceptance criteria**
- **Given** each platform, **then** the GUI, CLI flags, and output naming behave identically.
- **Given** each platform, **then** settings are stored in that platform's standard per-user config location.
- **Given** each platform, **then** opening a file or folder hands off to the system handler rather than a hardcoded path.
- **Given** a path with non-ASCII characters or spaces, **then** discovery, output naming, and overwrite all work.

### US-051 · Enter a password for a protected PDF in the GUI
**Epic:** E11 · **Persona:** Priya · **Priority:** Should · **Status:** Shipped (v0.3.0)

**Story**
> As Priya, when a queued PDF needs an open password, I want to be asked for it in the app, so that I am not forced to the command line for locked contracts.

**Acceptance criteria**
- **Given** a queued file that requires an open password, **then** the row requests a password instead of failing blind.
- **Given** I enter the correct password, **then** the file compresses normally and the password is not written to the settings file or the JSON report.
- **Given** I enter a wrong password, **then** the row reports an authentication error and lets me retry without re-adding the file.
- **Given** several files share a password, **then** I am asked once per run, not once per file.

**Notes** — A run that fails with "password required" opens a prompt and retries the failed rows with the password entered; it lives in memory for that run only and never reaches settings or the report.

### US-052 · Explain when a file was already as small as it gets
**Epic:** E11 · **Persona:** Priya · **Priority:** Should · **Status:** Shipped (v0.3.0)

**Story**
> As Priya, when a file cannot shrink, I want to be told it was already optimal, so that a `0.0%` result does not look like a bug.

**Acceptance criteria**
- **Given** the never-grow guardrail preserved the original, **then** the row labels the outcome as *Already optimal — original kept* rather than only showing `-0.0%`.
- **Given** the batch summary includes such files, **then** they are counted and explained in the results banner.
- **Given** the JSON report, **then** that outcome is explicitly recorded per file.
- **Given** I inspect such a file, **then** the inspector explains that the two sides are byte-identical.

**Notes** — Surfaced in the row status, the results band, the inspector ("byte-identical"), the JSON `already_optimal` flag and a CLI summary line.

### US-053 · Warn me before closing mid-run
**Epic:** E11 · **Persona:** Nadia · **Priority:** Could · **Status:** Shipped (v0.3.0)

**Story**
> As Nadia, if I close the window while files are processing, I want a warning, so that I do not assume the batch completed.

**Acceptance criteria**
- **Given** a batch is running, **when** I close the window, **then** I am asked to confirm, naming how many files are unfinished.
- **Given** I confirm, **then** the app stops cleanly at a file boundary, leaves completed outputs intact, and writes no partial files.
- **Given** I cancel, **then** the window stays open and the batch continues.
- **Given** the app exits, **then** no worker process is orphaned and no temp file is left in the output folder.

### US-054 · A crash or a bad file never leaves me with a damaged document
**Epic:** E11 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped

**Story**
> As Priya, I want compression to be all-or-nothing per file, so that an interrupted or failed run can never leave a half-written PDF in my archive.

**Acceptance criteria**
- **Given** a file is being compressed, **then** its result is written to a temporary file in the destination folder first and only moved into place after it is complete and verified.
- **Given** the process fails, is stopped, or the machine stops mid-run, **then** the original file is untouched and no partially written PDF sits under the final output name.
- **Given** a run ends either way, **then** the temporary file is cleaned up and never picked up by a later folder scan.
- **Given** overwrite mode, **then** the original is replaced only by that verified, atomic move.
- **Given** any error, **then** it is reported as a per-file failure rather than as a corrupted or truncated output.

**Notes** — Temp file is written with an `.ozempdf_` prefix in the destination directory and promoted with an atomic replace.

---

### US-055 · Remove a PDF's restrictions without a password
**Epic:** E10 · **Persona:** Priya · **Priority:** Must · **Status:** Shipped (v0.3.0)

**Story**
> As Priya, when a supplier sends a PDF that forbids printing or copying, I want OzemPDF to hand me a
> normal PDF without asking me for a password I do not have, so that I can archive it with the rest.

**Acceptance criteria**
- **Given** a document that opens with the empty user password (owner restrictions only), **when** it
  is compressed, **then** no password is requested and the output carries no restrictions.
- **Given** a document that needs an open password, **when** I run without one, **then** it is
  reported as *password required* and nothing is guessed, tried or brute-forced.
- **Given** certificate or public-key encryption, **then** it is reported as unsupported with a
  readable reason instead of being silently skipped.
- **Given** documents were unlocked in a batch, **then** the results band counts them and the JSON
  records `protection_kind` and `protection_removed` per file.

**Notes** — `engine/unlock.py` classifies protection (`none`, `permissions`, `password`,
`unsupported`) and tries the empty user password first, then only the password the user supplied.
Removing permission restrictions is not decryption: every reader already opens the file with that
empty password.

### US-056 · Re-sign a document after slimming
**Epic:** E10 · **Persona:** Priya · **Priority:** Should · **Status:** Shipped (v0.3.0)

**Story**
> As Priya, when a signed contract has to be smaller, I want to replace the old signature with a new
> one I control, so that the archived copy is both slim and verifiably signed.

**Acceptance criteria**
- **Given** I enable flattening and provide a PKCS#12 file with its passphrase, **when** the run
  finishes, **then** the output carries a new signature over the compressed bytes.
- **Given** I supply the wrong passphrase, **then** the file fails with an error naming the
  certificate problem and no unsigned output is written.
- **Given** I supply a timestamp authority URL, **then** the new signature carries an RFC 3161
  timestamp.
- **Given** the run ends, **then** the passphrase is neither stored in settings nor printed in the
  report or the console.

**Notes** — `engine/signing.py` (pyhanko): `SimpleSigner.load_pkcs12`, SHA-256, optional
`HTTPTimeStamper`, verification that the field exists in `/AcroForm /Fields`, then an atomic promote.
Signing is off by default and never part of a preset.

### US-057 · Run from any folder and leave nothing behind
**Epic:** E11 · **Persona:** Sam · **Priority:** Should · **Status:** Shipped (v0.3.2)

**Story**
> As Sam, I want to unzip OzemPDF onto a USB stick or a locked-down machine and run it from there, so
> that I can work without an installer, admin rights, or traces left on the machine.

**Acceptance criteria**
- **Given** I extract the ZIP into any writable folder, **when** I double-click `OzemPDF.exe`,
  **then** the window opens with no flags, no console and no installer.
- **Given** the app has run, **then** everything it wrote sits in `data/` beside the executable —
  settings, caches, logs, temp files — and `%APPDATA%`, `~/.config` and the system temp directory
  hold nothing new from it.
- **Given** I delete the folder, **then** no part of OzemPDF remains on the machine.
- **Given** `OZEMPDF_DATA_DIR` is set, **then** that location wins, so a script can pin state
  anywhere.

**Notes** — `settings._portable_data_dir` (override → frozen-engine walk-up → platform location) and
`electron/main.js` (redirects Electron's `userData`, `sessionData`, `crashDumps` and the process temp
directory). Verified by extracting the ZIP outside the development workspace and launching it.

### US-058 · Use the browser when the desktop window cannot start
**Epic:** E11 · **Persona:** Nadia · **Priority:** Could · **Status:** Shipped (v0.3.1)

**Story**
> As Nadia, on a machine whose security policy blocks the desktop window, I want another way in that
> still gives me the whole interface.

**Acceptance criteria**
- **Given** the native window cannot start, **when** I run the browser launcher, **then** the full
  interface opens in my default browser with presets, queue, results and the inspector.
- **Given** I drop files there, **then** they are staged internally and compressed, and a notice
  explains that a copy is used because a browser cannot reveal real paths.
- **Given** the browser is serving the interface, **then** it is bound to `127.0.0.1`, every API call
  needs the session token, and nothing is reachable from my network.

**Notes** — `--serve --port N [--no-browser]`, `/api/upload` staging, loopback token and origin
check in `server.py`. Same UI files as the desktop app: there is no second implementation.

---

# 4. Release slices

| Slice | Scope | State |
| --- | --- | --- |
| **0.3.0** | HTML/CSS/JS interface over the Python engine, Electron shell, every `D-01 … D-21` closed, protection removal, re-signing; US-001 – US-056 | Shipped |
| **0.3.1** | Compatibility launcher for hosts that block the Chromium sandbox, ZIP distribution, startup diagnostics in `main.log`; US-058 | Shipped |
| **0.3.2** | Electron 38 (direct launch), read-only inputs (cloud/locked files), portable `data/` with no `%APPDATA%` writes; US-057 | Shipped |
| **0.3.3 (current)** | Ink-blue accent and matching icon, version chip rendered from the running version, documentation synchronised with the shipped behaviour | Shipped |

---

# 5. Traceability

| Epic | Interface | Engine / server |
| --- | --- | --- |
| E1 Intake | `web/index.html` drop mat, `app.js` (`bindDragAndDrop`, `openFiles`, `openFolder`) | `engine/batch.py` (`discover_pdf_files`), `/api/queue/add` |
| E2 Queue | `app.js` (`renderQueue`, row builder) | `/api/thumb`, `engine/batch.py` (`render_page_png`) |
| E3 Presets | `app.js` (`renderMode`, knob handlers) | `config.py` (`from_preset`, `clamped`), `/api/preset`, `/api/config` |
| E4 Output | `app.js` (destination dialog, suffix field) | `engine/batch.py` (`resolve_output_path`, `_unique_outputs`) |
| E5 Run | `app.js` (`startRun`, poll loop, dock) | `server.py` run thread, `engine/batch.py` (`run_batch_compression`) |
| E6 Savings | `app.js` (`renderBreakdown`, aggregate/single toggle) | `engine/analyzer.py` (`audit_pdf_bytes`) |
| E7 Inspector | `app.js` (inspector, wipe, zoom, page keys) | `/api/page`, `engine/batch.py` (`render_page_png`) |
| E8 Appearance | `web/styles.css` tokens, `app.js` (`applyI18n`) | `i18n.py`, `settings.py`, `locales/en.json`, `/api/ui` |
| E9 CLI | `cli.py` | `engine/batch.py` (`BatchSummary.to_dict`) |
| E10 Engine | drawer flags and consent dialogs | `engine/unlock.py`, `analyzer.py`, `deduplicator.py`, `image_optimizer.py`, `font_optimizer.py`, `stream_optimizer.py`, `signing.py`, `pipeline.py` |
| E11 Trust | `app.js` close guard, portable notice | `engine/pipeline.py` (atomic write + guardrail), `settings.py` (portable dir), `electron/main.js` |

Automated coverage maps to these stories: `python/tests/test_lossless.py` (US-042, US-043, US-044),
`python/tests/test_visually_lossless.py` (US-030, US-045), `python/tests/test_security_and_fonts.py` (US-046,
US-047, US-051, US-055, US-056), `python/tests/test_batch.py` (US-003, US-014, US-019, US-020, US-038,
US-039, US-041), `python/tests/test_settings.py` (US-036, US-057), `python/tests/test_i18n.py` (US-035, US-037),
`python/tests/test_web.py` (US-001, US-004, US-021, US-026, US-034, US-053, US-058).

---

# 6. Open questions

## 6.1 Settled in 0.3.x

| Question | Decision |
| --- | --- |
| Default preset on a cold start | **Visually Lossless** stays the default; the results breakdown and the wipe-slider inspector make the quality measurable, which is the counterweight a vector-heavy user needs. |
| Signature flattening default | **Off** everywhere, behind a consent dialog (D-14). Re-signing exists for users who must end up signed. |
| Overwrite safety | Atomic replace after confirmation, defaulting to No. No automatic backup/trash: documented rather than silently added. |
| "100 % vectors preserved" claim | Backed by a construction invariant plus tests that compare decoded content bytes and pixels; there is no per-run diff in the product. |
| Guardrail visibility | First-class: row status, results-band note, inspector note, JSON `already_optimal` and a CLI line. |
| Password scope | One prompt per run, applied to the remaining rows; the password lives only in the running process and is never persisted. |
| Browser vs desktop parity | One implementation: the browser serves the same three files and the same API, with upload staging as the only documented difference. |
| Portable storage | `data/` beside the executable; the platform config location is used only for a source install. |

## 6.2 Still open

1. **Per-file progress** — the engine reports per file only; a multi-minute Zopfli pass still shows an
   indeterminate bar. Stage-level progress needs engine callbacks.
2. **Backup of overwritten originals** — should overwrite keep a recoverable copy? It is deliberately
   not implemented, because a second full copy doubles the disk cost the product exists to reduce.
3. **PDF/A and PDF/X** — the app preserves what it does not touch but cannot promise conformance; a
   preservation mode would have to refuse some optimisations.
4. **Pixel-difference readout** — the inspector shows both renders side by side; a numeric MSE/PSNR
   would make "visually lossless" measurable in-product.
5. **Signed builds** — the ZIP is unsigned, so SmartScreen warns on first run. Code signing needs a
   certificate and a recurring cost decision.
6. **macOS and Linux verification** — configured and tested in CI, but no end-to-end packaged run has
   been performed by the maintainer.

---

# 7. Definition of Ready / Done (repo conventions)

**Ready** — the story has personas, measurable acceptance criteria, a priority, and an
owner-visible scope.

**Done** — additionally, per this repo's `CONTRIBUTING.md`:
- No user-facing string is hardcoded; every new key exists in `python/src/ozempdf/locales/en.json` and no
  orphan key remains (tested).
- Both themes are verified at desktop and mobile widths for any touched surface, and the layout
  survives the 1080/860/700/480 px breakpoints.
- The three-step progressive-disclosure flow, keyboard access and reduced motion still hold, and the
  vector/never-grow invariants are untouched.
- `python -m pytest -q` and `ruff check python/src python/tests` pass; `node --check` passes for touched scripts.
- `python/src/ozempdf/_version.py` and `docs/CHANGELOG.md` are updated, the portable Windows ZIP builds, and one atomic
  conventional, GPG-signed commit records the change.
