# LPS Intelligent QA — Understanding

**Scope:** This document records what the `lpsdemointelligentqa` solution is today, how the customer
will feed it, and how it consumes the output of `land_records_digitization-main`.
**Status:** Understanding / pre-build. **This is a demo scope** — the rules below are deliberately
permissive: anything ambiguous gets a sensible default instead of a validation error, and nothing blocks a
batch from being ingested unless it is completely unusable.

---

## 1. Purpose

`lpsdemointelligentqa` is the customer-side (LPS / Mobius QC) **Quality Assurance** workspace. It is used
to check the extraction produced by `land_records_digitization-main` — a Northern Ireland Land Registry
**folio capture** pipeline that turns folio PDFs/images into structured register entries (Transfer of Part,
Description of Land, Covenants, Easements, Leasehold Yearly Rent, Leasehold Particulars).

Division of responsibility:

| Solution | Owns |
|---|---|
| `land_records_digitization-main` | OCR + AI extraction, per-folio structured output, producer-side "review" (annotated boxes, field evidence) |
| `lpsdemointelligentqa` | Independent QA of that output: sample selection, per-entry/per-field verification, accept/reject, notes, escalation, and QA analytics |

The two are **separate tools**. QA does not re-extract; it verifies and records a verdict.

---

## 2. What exists today (as-is)

Three standalone static pages, no backend, no build step. Sample/mock data is hard-coded; user actions
live in memory plus `localStorage`.

| File | Role |
|---|---|
| [LPS_QA_Allocation.html](LPS_QA_Allocation.html) | Choose QA mode + scope, set sampling %, preview sample size, allocate to QA experts |
| [LPS_QA_Review.html](LPS_QA_Review.html) | The QA workspace: document (or OCR text) beside extracted fields; note / accept / reject / query / escalate |
| [LPS_QA_Analytics.html](LPS_QA_Analytics.html) | QA roll-up dashboards, batch scores, folio detail, expert and analyst breakdowns |

Observed characteristics that constrain any build:

- **No ingestion path exists.** `LPS_QA_Review.html` carries a hand-maintained constant
  `const IMPORTED=/*IMPORTED_START*/[]/*IMPORTED_END*/` — folios are injected by editing the HTML.
  (`LPS_QA_Builder.html` is **out of scope and ignored**; every reference to it has been removed from the
  pages and from this document.)
- **Data is mock.** `LPS_QA_Review.html` has a hard-coded `MOCK` folio (`DN-35846`), and
  `LPS_QA_Allocation.html` / `LPS_QA_Analytics.html` have hard-coded batch and statistic tables.
- **Allocation → Review hand-off is by URL query string** (`?mode=random|intelligent`, `?et=<entry types>`)
  and `localStorage` key `lpsQA.allocation`.
- Allocation/Analytics numbers are illustrative; only the Review page is interactive per folio.
- Sidebar nav (`Dashboard / Workflows / Jobs / Users / Mojo / Asset / Notifications / Settings`) is
  mostly placeholders; only Dashboard, Mojo (Allocation) and Review are wired.

---

## 3. Required input contract

> A **root folder** is the delivery point. **Each folder directly under the root is one batch**, and the
> **batch name is that folder's name**. A batch folder contains **one or more ZIP archives**, which hold the
> actual files. Where a container holds `{filename}_1`, `{filename}_2`, …, **only the biggest suffix is
> considered.**

```
root/                          <- delivery point, not itself a batch
  23548_Q3/                    <- batch (name = folder name)
    delivery.zip               <- one or more ZIPs
    delivery_1.zip             <- rerun of the same delivery -> superseded
  Batch_7302_Rerun/            <- another batch
    output.zip
    sources.zip
```

Consequences to build to:

1. **Batch folders are discovered one level below the root.** Every direct child folder of the root is a
   batch. The root itself is not a batch, and its own loose files are not part of any batch.
2. **Batch name = batch folder name**, used verbatim (folder `Batch_23548_Rerun` → batch
   `Batch_23548_Rerun`). It is taken from the directory name, never from a file or ZIP inside it.
3. **ZIPs are the file containers.** A folio's artefacts live inside the batch folder's ZIPs, not loose in
   the folder. All ZIPs in a batch are read and their contents **unioned** — a folio's source document and
   its extracted output may legitimately arrive in separate ZIPs.
4. **Files → folios.** After unzipping and unioning, each distinct file base is one folio of the batch.
   Three kinds of file make up a folio (see §3.2).
5. **Version suffixes are dropped to the highest number**, applied at *every* level — see §4.

### 3.1 Why the highest suffix wins

Repeated runs of the same source produce another artefact set rather than replacing the previous one
(this is the same duplication the producer tool already disambiguates — see §5.4). QA must assess **one
current version per folio**; older `_1`, `_2` … versions are superseded and must not be counted as
extra folios, extra entries, or extra errors in the analytics denominators.

The same reasoning applies to a rerun **delivery**: `delivery_1.zip` supersedes `delivery.zip`.

### 3.2 What a batch folder contains

Inside the batch folder's ZIP(s), each folio base is made up of up to three kinds of file:

| Kind | Example | Purpose in QA |
|---|---|---|
| **Source document** | `23548.pdf` (or `23548_p1.jpg`, `23548_p2.jpg`, …) | the original register page(s) shown in the Review page |
| **Extracted output** | `23548.xlsx` / `23548.csv` | the captured entries and field values under test |
| **Coordinate CSV** | `23548-coordinates.csv` | per-field source geometry (`page`, `x0_pt,y0_pt,x1_pt,y1_pt`, `value`, `quote`, `basis`) used to draw the field highlight over the page |

The coordinate CSV is the file produced by `land_records_digitization-main` as
`{folio-filename-stem}-coordinates.csv`, in **PDF points**.

**The source document is optional.** When neither ZIP contains it, QA falls back to the producer tool —
see §5.5.

**ZIP layout.** Directory depth inside a ZIP carries no meaning: files are read at any depth and grouped by
**base name**, not by path. A producer batch ZIP is already flat, but a hand-built ZIP of a folder will
usually have a sub-directory — both are handled the same way.

**`manifest.json`.** The producer's ZIP includes one. It is not needed for grouping, but it is the cleanest
source of the `source_id` → filename mapping used by the fallback in §5.5, so it should be captured when
present.

### 3.3 Artefact families — the producer's naming convention

Grouping follows the **producer's own naming**, not a new convention. `land_records_digitization-main`
emits exactly these per-folio families:

| Family | Naming | Produced by |
|---|---|---|
| **Source document** | `{base}.pdf`; image folios `{base}_p{n}.jpg` | upload / `_p{page}` page naming |
| **Values** | `{base}.csv` (the manual workbook is `{base}.xlsx`) | single-folio download (`{filename-stem}.csv`) |
| **Geometry** | `{base}-coordinates.csv` | single-folio download, and inside the batch ZIP |
| **Manifest** | `manifest.json` | batch ZIP, one per batch |

Grouping rule: **one folio = all files whose base name matches**, where the base is the filename stem with
the producer's `-coordinates` qualifier removed. So `23548.pdf` + `23548.csv` + `23548-coordinates.csv` are
one folio, and `23548` is its base.

The `_N` version suffix is handled on top of this by the latest-suffix rule (§4): it selects the newest run
of the whole folio, so all three families are taken from the same run.

**Two naming conventions turn up — tolerate both.** The batch ZIP keys its entries by `source_id`
(`{source_id}.csv`), which looks like `23548-8a1c3a9384d3`, while the single-folio download uses the plain
filename stem (`23548.csv`). A `source_id` base will not match a source document called `23548.pdf`.

For the demo, don't force one convention — **normalise the base** instead: if a base ends in a
`-<12 hex characters>` suffix (the producer's content hash), strip it before grouping. Then a ZIP delivered
straight from the producer groups correctly with its source documents, and no customer-side renaming is
needed. Folio names ending in a 12-character hex string are vanishingly unlikely, so the tolerance is safe.

---

## 4. The latest-suffix rule (definition)

One rule, applied at **every container level** — batch folders are discovered, then the ZIPs inside a
batch, then the files inside those ZIPs. Containers are compared to siblings in the same parent only.

```
version(name) -> N or none
    stem = filename without extension
    if stem matches  ^(.+)_(\d+)$   ->   base = group 1, N = int(group 2)
    else                                base = stem,     N = none

latest(names):                       # applies to any set of sibling names
    group by base
    in each group, keep only the name(s) with the greatest N
      (N = none counts as lower than any N)
    everything else in that group is ignored
```

Evaluation order for a delivery:

1. **Root → batches.** Every direct child folder of the root is a batch (§3, step 1). Folder names are
   distinct, so nothing is dropped here. **Folders are never version-deduplicated.**
2. **Batch folder → ZIPs.** Apply `latest()` to the ZIP names in the batch folder.
3. **ZIPs → files.** Extract the surviving ZIPs, **union** all their files, then apply `latest()` to the
   file names — this is where folio versions (`23548_1.csv` vs `23548.csv`) are resolved, across the whole
   batch rather than per ZIP.

Step 3 runs **after** the union on purpose: a folio's artefacts may be split across ZIPs, so a per-ZIP
decision would resolve each family independently and could pair a v2 output with a v1 coordinate file.

`_p<digits>` is **not** a version suffix — it is a **page** suffix used by the producer output naming
(`{folio}_p{page}.pdf`). Only a bare `_<digits>` is a version.

### 4.1 Decision table

Applied to the union of a batch's ZIPs (step 3):

| Files in the batch | In scope for QA | Reason |
|---|---|---|
| `23548.pdf` | `23548.pdf` | no suffix |
| `23548_1.pdf`, `23548_2.pdf` | `23548_2.pdf` | highest suffix |
| `23548_1.pdf`, `23548_2.pdf`, `23548_3.pdf` | `23548_3.pdf` | highest suffix |
| `23548_2.pdf` only | `23548_2.pdf` | suffixes need not be contiguous |
| `23548.pdf`, `23548_1.pdf` | `23548_1.pdf` | highest suffix wins — see §4.3 |
| `7302.pdf`, `7303.pdf` | both | different folios |
| `35846_p1.pdf`, `35846_p2.pdf` | both | `_pN` is a page, not a version |
| `23548.csv` in `output.zip`, `23548.pdf` in `sources.zip` | one folio | union first, then group |
| `23548_2.pdf` (duplicated byte-for-byte twice) | one | same base, same N — keep first, warn (§4.4) |

Applied to the ZIPs in a batch folder (step 2):

| ZIPs in the batch folder | In scope | Reason |
|---|---|---|
| `output.zip`, `sources.zip` | both | different bases — they are the two halves of one delivery |
| `delivery.zip`, `delivery_1.zip` | `delivery_1.zip` | rerun of the same delivery — see §4.3 |
| `delivery.zip`, `delivery_1.zip`, `delivery_2.zip` | `delivery_2.zip` | highest suffix |
| `delivery_1.zip` only | `delivery_1.zip` | suffixes need not be contiguous |

### 4.2 Grouping — resolved

**Grouping follows the producer's naming convention** (§3.3). One folio is the set of files sharing a base
name, across the producer's families (source document, values, geometry). The `_N` suffix is evaluated on
that shared base, so one version is chosen for the whole folio rather than per file.

Example — one ZIP, or two ZIPs unioned: `23548.pdf` + `23548.csv` + `23548-coordinates.csv` +
`23548_1.csv` + `23548_1-coordinates.csv` → base `23548`, highest N = 1 → QA takes `23548_1.csv` +
`23548_1-coordinates.csv`, and uses the source document `23548.pdf` (the only one present).

### 4.3 Edge cases — demo defaults

No decision needed for these; the defaults below are already applied, and any of them can be revisited
later without changing the rest of the design.

| Case | Default behaviour |
|---|---|
| Plain name and suffixed name both present (`23548.pdf` + `23548_1.pdf`) | **Highest suffix wins** — treat the plain name as the original and the suffixed one as the rerun. |
| A suffixed **ZIP** (`delivery.zip` + `delivery_1.zip`) | Treat the suffixed ZIP as a **replacement** delivery. A `_2` ZIP alongside a `_1` simply means a further rerun. |
| A ZIP that looks like an *addition* rather than a rerun | Not handled separately — if it matters later, drop ZIP-level dedup and keep only file-level dedup, using the ZIP version as the tie-breaker. Same result for true reruns. |
| File base ends in `-<12 hex>` (a `source_id`) | Strip that suffix before grouping, so producer ZIPs and source documents group together (§3.3). |

### 4.4 Handling problems (demo posture)

**Best effort — warn and carry on.** A delivery is only rejected when there is nothing usable in it.
Malformed or unexpected content is skipped with a warning so the rest of the batch still ingests.

- Nothing usable at all in a batch folder (no readable ZIP, or no recognisable files) → the batch is
  empty; surface a clear message rather than failing silently.
- A ZIP that is unreadable or encrypted → skip it, note it, ingest the other ZIPs.
- Two files with the same base **and** the same highest N → keep the first, warn, and list the duplicate.
- Unrecognised file extensions, and non-ZIP files loose in the batch folder → ignore, and report the count.

---

## 5. How QA consumes the producer output

### 5.1 Producer artefacts available per folio

From `land_records_digitization-main` (see `folio_capture/export.py`, `folio_capture/api.py`):

| Artefact | Content |
|---|---|
| `{base}.csv` | Flat per-folio export: metadata columns + every schema table/field for every entry |
| `{base}-coordinates.csv` | Per-field source geometry — `page`, `x0_pt,y0_pt,x1_pt,y1_pt` (PDF points), `quote`, `basis` |
| batch ZIP | `{source_id}.csv` + `{source_id}-coordinates.csv` per folio, plus `manifest.json` |
| `extracted.json` | Lossless full folio held in the folio's artifact directory (metadata, entries, `field_evidence`, `regions`, per-page OCR words). **Not shipped in the ZIP.** |
| page images | Rendered page PNGs, served at `/api/batches/{id}/folios/{sid}/pages/{n}` |

### 5.2 Mapping producer output → QA page entities

| QA entity (as used by the pages) | Producer source |
|---|---|
| Batch name | **Folder name** (new contract, §3) |
| Folio `id` | `county-code + '-' + metadata.folio_number` (e.g. `DN-35846`) |
| Folio `file` | `source_filename` stem (e.g. `35846`) |
| Folio `county` | `metadata.county` |
| Page count / page tabs | `pages` length |
| Page image | rendered page PNG (or `_p{page}.pdf` per producer naming) |
| Entry `type` | `entry_type` |
| Entry `no` / `order` | `page_entry_number` + `regions[].page` ordering |
| Entry `box` | `regions[].box` (0–1000 normalized) → QA uses 0–1 fractions |
| Field `label` / `value` | schema table + field name / captured value |
| Field `box` (locate on image) | **coordinates CSV**, PDF points → 0–1 fractions (divide by page width/height in points) |
| Field `onImage` | whether the field has a source box at all |
| Entry verdict (Pending / Accepted / Rejected) | **QA-owned**, not produced by the pipeline |
| QA notes / query / escalation | **QA-owned** |

### 5.3 Decisions taken

| Topic | Decision |
|---|---|
| **Field flags and labels** | Review displays `Field Names Per LPS` matched by `TABLE_NAME` and internal `FIELD_NAME`. Its High/Med/Low flag is a conservative mapping from extraction reason codes, not a calibrated probability; `basis` remains available in ingested data. |
| **Entry-type taxonomy** | Read from the actual delivery, including inactive entries, and used consistently for allocation counts and Review focus. |
| **County codes** | Use exactly what the pipeline provides — all **six** counties, including **Antrim**. QA's 5-prefix list is extended to cover all six. |
| **Batch folder contents** | A **root folder** holds one folder per batch; **each batch folder holds one or more ZIPs**, and the folio artefacts are inside those ZIPs (§3). All ZIPs in a batch are unioned before grouping. |
| **Artefact-family grouping** | Follows the producer's naming convention (§3.3): one folio = files sharing a base name, with `_N` evaluated on that shared base. |
| **QA verdicts** | Saved in this browser's `localStorage` with field corrections, notes, and folio submission state. This is not shared across reviewers or devices and is not a server audit trail. |
| **`LPS_QA_Builder.html`** | Out of scope and ignored; all references removed from the pages and from this document. Ingestion still enters through the `/*IMPORTED_START*/ … /*IMPORTED_END*/` markers. |
| **Raw OCR text** | Not needed and not requested as an export. Review defaults to the source document image with extracted-entry and field boxes; its OCR view renders captured entries and values — see §5.6. |

### 5.4 Relationship to producer-side duplicate handling

The producer tool already names repeated runs with an incrementing `_N` postfix so users can tell which is
latest. The QA latest-suffix rule (§4) is the consumer-side counterpart of exactly that: within a delivered
folder, only the newest run of a folio is QA'd.

### 5.5 Fallback: sourcing the document from the producer tool

**Question:** when the batch folder has no source document, can QA refer to `land_records_digitization-main`
to pick it up?

**Answer: yes — the producer retains what is needed — but three conditions must be met.** It is feasible,
with the caveats below.

**What the producer keeps.** Every ingest publishes a `sources` record (table `sources`, keyed by `id`) that
holds:

| Field | Meaning |
|---|---|
| `id` | `{sanitised folio name}-{content-hash[:12]}` — derived from content, so identical files share an id |
| `filename` | the uploaded filename (e.g. `23548.pdf`) |
| `pdf_path` | **the original uploaded PDF** |
| `pages_path` | the PDF exploded into per-page files `page-*.pdf` |
| `page_manifest` | for image folios (JPEG pages): each page's `original_filename`, `path`, `hash`, `width`, `height` |
| `hash`, `page_count`, `kind` | integrity and shape |

Rendered page PNGs (what the QA overlay actually needs) live under the folio's artifact directory and are
served over HTTP.

**How QA would resolve it**

1. Look up the source: `GET /api/sources` returns `id`, `filename`, `folio_name`, `kind`, `hash`,
   `page_count`, `uploaded_at`, `numbering_gaps`.
2. Match the QA folio base name (e.g. `23548`) against `filename` / `folio_name`.
3. Take the page image: `GET /api/batches/{batch_id}/folios/{source_id}/pages/{n}` → PNG of page `n`.

**Why a match key is needed at all.** The fallback is a lookup into the producer's `sources` table, which
is **global** — one row per source ever ingested, keyed by `id`. `filename` alone is not unique (the same
folio name recurs across runs), and `id` is content-derived (`{name}-{hash12}`), so the same filename with
changed content is two different rows. A key is therefore required to pick the right row. This is not an
extra decision: **the producer already supplies one.** The batch ZIP names its entries by `source_id`
(`{source_id}.csv`, `{source_id}-coordinates.csv`), and `manifest.json` carries it too. So:

- If the delivered output is named by **`source_id`**, the key is free — no lookup ambiguity.
- If it is named by **filename stem** (§3.3, which is what the single-folio download produces), then
  resolve by `filename` **plus** `hash` (or `page_count`) and treat more than one match as ambiguous.

**The three conditions**

1. **A match key must exist** — as above: prefer the `source_id` from the producer's output naming.
2. **Auth and reachability.** Every endpoint is behind Basic Auth, and the producer must be running and
   reachable from QA (Modal workspace `lpsdigitization`, or the VM). If either is down, ingestion stalls.
3. **The source must not have been purged.** Sources live in the artifacts volume/`runtime/artifacts/sources`.
   Clearing QA or producer state breaks the link.

**One gap to close.** Over HTTP only the **rendered PNG** is exposed today. The **original** document
(`pdf_path` / image-folio originals) is retained on disk and recorded in the `sources` payload, but there is
**no endpoint to download it** — that would need a small addition, or a read from the artifacts volume.

**Approach — left for later.** The fallback is not needed for the demo to run; when it is built, the
simplest first cut is to resolve live at review time, since a demo batch is small. The more robust option,
worth doing if the fallback becomes load-bearing:

- At batch ingest, resolve the fallback once and record `source_id` + `batch_id` (and `hash`) on the QA
  folio.
- Better, **copy the page PNGs into the QA batch** at ingest, so a batch is self-contained and the
  "document present" and "document referenced" cases behave identically downstream. This also immunises QA
  against reruns, purges, and producer downtime.
- Failing both, the batch should ingest with the document flagged **missing**, and the Review page should say
  so rather than silently showing nothing.

### 5.6 OCR text — what exists and what is needed

**Correcting an earlier assumption: raw OCR text is not needed, and it is not in the flat output.**

| What | Where it lives | In the delivered output? |
|---|---|---|
| Captured entry text and field values | `{base}.csv` / `{base}.xlsx` | **Yes** |
| The OCR snippet behind a single field | `{base}-coordinates.csv`, column `quote` | **Yes** |
| Full per-page OCR words (with boxes) | the folio's `extracted.json` artifact only | **No** |

The Review page's text view is labelled **"Captured text"** and renders the folio's captured entries and
field values (used for image folios, which have no page PDF to display) — it is not a raw OCR pane.
Everything that view needs therefore already arrives with the extracted output, and the per-field `quote`
in the coordinate CSV covers "show me the text behind this value".

Conclusion: **no raw-OCR export is required.** If QA later wants a whole-page OCR pane, that is a new
export from the producer's folio payload, not something the current output provides.

---

## 6. Data model the pages expect

Shapes actually used by the pages today (names as they appear in the code):

**Batch / allocation**
```
Batch  : { v, folios, entries, items:[{ id, entryTypes, entryTypeCounts, inactiveEntries }] }
Scope  : { folioIds:[], entryTypes:[], analystByFolio:{}, experts:[], sampling }
mode   : 'random' | 'intelligent'
pct    : 1..100
```
Allocation uses actual per-folio counts for the selected entry types; inactive entries remain in scope.
Older indexes without those counts are enriched from their folio JSON at page load.

**Folio (Review page)**
```
F : { id, file, county, batch, analyst, kind:'mock'|'image',
      pages : [ {head} | {img} ],
      E     : [ Entry ] }
Entry : { type, page, order, box, heading?, margin?, text?,
          fields : [ { label, value, flag, box, onImage } ],
          n, no, sib, inactive?, cancellationStatus? }
```
- Entries are sorted by `page`, then `order`.
- Entries sharing a page/order (one paragraph captured as several types) are numbered `4a`, `4b`, `4c`.
- Inactive entries have no extracted fields and remain reviewable as not captured.
- Per-folio UI state and submission progress are saved in browser-local storage.

---

## 7. Workflow and rules as encoded today

**Allocation → Review → Analytics**

1. **Mode.** Random QA samples eligible folios; Intelligent QA uses selected entry types to calculate
   eligible folios and exact entry counts.
2. **Sampling %.** Default 10, presets 5/10/25/50/100; the copy states LPS starts at 100% and lowers it
   only on delegated authority.
3. **Allocation.** Save exact selected folio IDs and entry-type focus, and divide the sample across selected
   QA experts; empty result or no expert selected is an error.
4. **Review.** Per entry: `Accept Entry` / `Reject Entry`. Active-entry rejection requires a field-level QA
   note; inactive entries can be resolved without fields. Field values and notes persist locally. `Submit Folio`
   stays disabled until all in-scope entries are resolved, then advances through the queue.
5. **Sampling-eligibility signals (Analytics).** Accepted at **≥98%**; Accepted w/ Feedback **96–97.9%**;
   Rejected (sent to rework) **<96%**; entry types at **≥99%** are flagged "Sampling Eligible".
6. **Intelligent-only reporting.** Folio-wise detail, expert completion, per-analyst accuracy and stratum,
   entry-type acceptance, error categories — hidden for Random QA.

---

## 8. Decisions and defaults in force

**No blocking questions.** Everything below has a demo-appropriate default applied, so the input contract
can be implemented as written. Nothing here prevents a delivered batch from ingesting.

| Topic | In force |
|---|---|
| Field labels and flags | Uses workbook `Field Names Per LPS` labels in table context and reason-code-derived High/Med/Low flags. |
| Entry-type taxonomy | Uses types in the ingested delivery, with inactive entries included. |
| County codes | All 6 from the pipeline, including Antrim. |
| Delivery structure | Root folder → one folder per batch → one or more ZIPs per batch; all ZIPs unioned. |
| Batch name | The batch folder's name, verbatim. |
| Nesting | One folder level below the root; directory depth inside a ZIP is ignored. |
| Grouping | By base name, following the producer's naming; a `-<12 hex>` suffix is stripped so both naming conventions group. |
| Version selection | Highest `_N` wins, applied at ZIP level and file level. |
| Naming conventions | Both the filename-stem and the `source_id` form are accepted; no customer-side renaming required. |
| Problem handling | Best effort — skip malformed content with a warning; never reject a batch that has usable files. |
| Fallback source document | The producer supplies `source_id`; page images can be resolved from it (§5.5). |
| Raw OCR text | Not needed — the text view renders captured values. |
| QA verdicts | Persisted in browser-local storage for the current allocation; not shared across users/devices. |
| `LPS_QA_Builder.html` | Ignored; references removed. |

**Left for later (not needed for the demo)**

| # | Item | Note |
|---|---|---|
| 1 | Whether the fallback references the producer live or snapshots the page images at ingest (§5.5) | Snapshotting is the more robust option whenever it becomes worth doing. |
| 2 | How the `IMPORTED` markers get populated now that the builder is dropped (§2) | **Done** — `qa_ingest.py` ingests the root folder into `qa_data/`; the pages read `qa_data/index.json` at load time (§10). The `/*IMPORTED_START*/ … /*IMPORTED_END*/` markers remain as a hand-edit escape hatch. |

---

## 9. Assumptions and non-goals

**Assumed**
- A **root folder** is the delivery point; each folder directly beneath it is one batch, and the batch name
  is that folder's name. No manifest file is required.
- A batch folder contains **one or more ZIPs**, and folio artefacts live inside them; loose files in the
  batch folder are ignored.
- All ZIPs in a batch are unioned, and a folio's artefacts may be split across them.
- Directory depth inside a ZIP carries no meaning — grouping is by base name, not by path.
- The latest-suffix rule is applied on ingest at each container level, so downstream (Allocation, Review,
  Analytics) only ever sees one version per folio.
- A folio's artefacts (source document, values, coordinate CSV) share one base name, following the
  producer's naming convention, so they group without a manifest — and the latest-suffix rule selects the
  whole folio together.
- QA verdicts, edits, notes, and folio submissions persist in browser-local storage; shared/server-side audit history remains out of scope.
- The producer tool remains the system of record for extraction and for the original source documents.
- Review displays source page images with entry and field geometry when available; the OCR view shows captured values.

**Not in scope for this understanding**
- Implementation of ingestion/back-end, authentication, persistence, or deployment.
- Changes to `land_records_digitization-main`. The optional ones flagged earlier (an endpoint to download
  the original source document, §5.5; ZIP naming alignment, §3.3) are no longer required, because the demo
  tolerates both naming conventions instead.
- **Strict validation of customer deliveries.** Malformed or unexpected content is skipped with a warning
  rather than rejected (§4.4); no conformance gate, rejection report, or re-delivery workflow.
- Customer-specific policy values beyond the thresholds already encoded in Analytics.

---

## 10. Running the demo

Ingest, then serve — the pages fetch `qa_data/` over HTTP, so a plain static server is enough.

```bash
# 1. Set BATCH_ROOT_FOLDER in .env, then ingest. The default .env points to sample_delivery.
python qa_ingest.py                          # writes ./qa_data (index.json, warnings.json, batches/…)

# 2. Serve the QA folder and open the pages.
python -m http.server 8080                   # then open http://localhost:8080/LPS_QA_Allocation.html
```

Set `BATCH_ROOT_FOLDER` in `.env` to the delivery root. It should contain one folder per batch, with one or
more ZIPs inside each batch folder. Relative paths are resolved from the project folder; absolute paths are
also supported. You can still pass the root directly (`python qa_ingest.py "<root folder>"`), which
overrides `.env`. The shell environment variable `BATCH_ROOT_FOLDER`, when set, takes precedence over `.env`.

The ingester also reads `23548 Output.xlsx` for display labels. It searches the sibling
`land_records_digitization` / `land_records_digitization-main` project folders; set
`FIELD_NAMES_WORKBOOK` in `.env` or pass `--field-names-workbook <path>` when the workbook is elsewhere.
`Field Names Per LPS` is selected by `TABLE_NAME` and `FIELD_NAME`, so the displayed label can vary by table
without changing the internal field key used for values or geometry. The UI's High/Med/Low field flag is
derived from extraction reason codes: no reasons → High, structural/evidence/validation reasons → Low, and
other or unknown reasons → Med. These are review flags, not calibrated probabilities.

Optional flags: `--out <dir>` (where the data is written), `--public <prefix>` (the URL prefix baked into
the folio JSON; defaults to the `--out` folder name), and `--field-names-workbook <path>`.

The run installs no state anywhere else. `qa_ingest.py` deletes and rewrites `qa_data/` on every run, so a
re-delivery is just a re-run.

**Reset.** Re-run ingestion to replace `qa_data/`. Allocation settings are stored in
`localStorage.lpsQA.allocation`; per-entry verdicts, edits, notes, and folio submissions are saved in this
browser under `localStorage.lpsQA.review.v1`. They are not shared between reviewers or devices.

**What each page reads**

| Page | Source |
|---|---|
| `LPS_QA_Allocation.html` | `qa_data/index.json` — batches, folios, entry types and per-folio counts, including inactive entries |
| `LPS_QA_Review.html` | `qa_data/index.json` + one `batches/<batch>/<folio>.json` per folio, filtered to the exact allocated folio IDs and entry types in `localStorage.lpsQA.allocation` |
| `LPS_QA_Analytics.html` | nothing — fixed sample data (§7) |

If `qa_data/` is absent the Review page falls back to the built-in sample folio and both pages say so,
rather than rendering blank.
