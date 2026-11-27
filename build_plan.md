# LPS Intelligent QA — Build Plan

**Companion to:** `understanding.md` (the input contract and decisions this plan implements)
**Scope:** Demo build. Optimise for a convincing end-to-end walkthrough, not production hardening.
**Status:** Proposed — no code written yet.

---

## 1. Objective

Turn the three static prototypes into a working demo that ingests a real
`land_records_digitization-main` delivery and lets an analyst QA it:

```
root folder ──► ingest ──► per-batch data ──► Allocation ──► Review ──► Analytics
   (folder/     (unzip,      (JSON + page        (pick scope)   (verify    (roll up)
    ZIP/batch)   version,     images)
                 group)
```

**Done means:** point the ingester at a root folder, open the pages, allocate a batch, review a real
folio against its real page image with real highlight boxes, and see the verdicts reflected.

---

## 2. What already exists

### 2.1 The three pages

| File | State | Nature of the mock |
|---|---|---|
| `LPS_QA_Allocation.html` | Working UI, mock data | `BATCHES` (L103), `PRE` counties (L104), `FOLIOS` map (L105), `ETS` entry types + weights (L106), `ANALYSTS` (L107), `EXPERTS` (L108) |
| `LPS_QA_Review.html` | Working UI, mock data | `MOCK` folio (L174), `IMPORTED` constant (L171), `FOLIOS = [MOCK].concat(IMPORTED)` (L196) |
| `LPS_QA_Analytics.html` | Working UI, mock data | Inline rollups and tables; no external input — **stays a fixed mock** (Phase 5) |

All three are standalone — no build step, no dependencies, no backend. Cross-page links work
(Allocation → Review → Analytics → back).

**The scope handoff is only half-built.** Allocation builds the Review URL in `reviewHref()`
(L160–164) and sets it on `#goReview` (L188), passing **only** `mode` and the entry-type focus:

```
LPS_QA_Review.html?mode=intelligent&et=Transfer%20of%20Part|Easement
```

Review consumes those as `PREVIEW` (L164–167). It also supports reading a `localStorage` record
(`STORE_KEY='lpsQA.allocation'`, L161–163) — but **nothing writes it**, and neither path carries the
selected batches, folios, or analysts. So Review currently always shows every folio. Wiring the full scope
through is a Phase 4 task.

### 2.2 The integration seam

This is the whole job: **populate `IMPORTED` instead of hand-editing it.**

`LPS_QA_Review.html` L196–207 already normalises any folio in `IMPORTED` that is shaped like this:

```js
{
  id:    'DN-35846',      // folio identifier shown in the header
  file:  '35846',         // base filename, rendered as "{file}_p{page}.pdf"
  county:'Down', batch:'B-234', analyst:'Analyst A03',
  kind:  'image',         // 'image' -> imagePath()/imagePage() is used
  pages: [ { img: '<url>' } ],                       // one entry per page; img goes into <img src>
  entries: [
    { type:'Easement', page:2, order:1, box:[.1,.2,.5,.03],   // fractions 0-1, may be null
      fields:[ { label:'Easement Type', value:'right of way', box:[.1,.2,.3,.03] } ] }
  ]
}
```

Everything downstream — entry numbering (4a/4b/4c), entry-type focus, the image overlay
(`imagePage()` L251), the text view (`textView()` L269, which needs only `no`/`type`/`fields`), accept/reject
— works off that shape unchanged.

### 2.3 Producer artefacts to consume

| Artefact | Supplies |
|---|---|
| `{base}.csv` | one row per entry: metadata (`source_id`, `source_filename`, `folio_name`, `folio_number`, `county`, `entry_id`, `parent_entry_id`, `record_kind`, `entry_type`, `source_pages`, `cancellation_status`, `review_status`, `revision`) plus one column per schema field |
| `{base}-coordinates.csv` | per-field `page`, `basis`, `quote`, `x0_pt,y0_pt,x1_pt,y1_pt` (PDF points) |
| source `{base}.pdf`, or `{base}_p{n}.jpg` | the page images |
| `manifest.json` (batch ZIP) | batch-level `source_id` → filename mapping |

Useful consequences of the real CSV columns:

- **`source_id` is in the output** — the fallback needs no filename matching (`understanding.md` §5.5).
- **`entry_id` groups rows into entries**, so entry structure is not guesswork.
- **`source_pages`** gives each entry's page numbers directly, rather than inferring from the coordinates.
- **`cancellation_status`** maps onto the Review page's inactive-entry display (the `inactive:true` entries in `MOCK`).
- **`record_kind`** separates entries from notes.
- **`county` and `folio_number`** come free, so the folio `id` needs no derivation.

`folio_capture` already exposes `RENDER_DPI = 200`, and the coordinate CSV is emitted in PDF points at
that DPI. The ingester must render at **the same 200 DPI** so points map cleanly to fractions.

---

## 3. Architecture

### 3.1 Recommended: ingester + static host (Option A)

One Python script processes the root folder into a plain folder of JSON + images; the pages are served
statically and fetch that folder.

```
lpsdemointelligentqa/
  qa_ingest.py            # the whole pipeline (uses PyMuPDF; already a producer dep)
  qa_data/                # generated - do not edit
    index.json            # batches, folios, entry types, counts
    warnings.json         # everything skipped or ambiguous
    batches/<batch>/<folio>.json      # one folio, exactly the shape in 2.2
    batches/<batch>/<folio>/p1.png …  # page images
  LPS_QA_*.html           # + a small loader
```

Run: `python qa_ingest.py "<root folder>"` then `python -m http.server 8080`.

*Why this shape:* no server to write or keep running, no auth, no database, easy to re-run, and the whole
pipeline is inspectable by reading `qa_data/`. Re-running is cheap, which matters when the customer
re-delivers.

### 3.2 Alternative: extend the producer's API (Option B)

Add QA endpoints to the existing FastAPI app (`serve_local.py` / `modal_app.py`), reusing its Basic Auth and
image serving.

Take this only if QA must run as a hosted service alongside the producer. It costs a server, auth handling,
and a deploy cycle for every change — all unnecessary for a demo.

**Plan assumes Option A.**

---

## 4. Data contract

### 4.1 `index.json`

```js
{
  "generated": "2026-11-18T10:12:00Z",
  "root": "…/deliveries",
  "counties": ["Antrim","Armagh","Down","Fermanagh","Londonderry","Tyrone"],
  "entryTypes": ["Transfer of Part","Description of Land","Covenants","Easements",
                 "Leasehold Yearly Rent","Leasehold Particulars"],
  "batches": [
    { "name":"23548_Q3", "zip":"delivery_1.zip", "folios":42, "entries":310,
      "counts":{"withSource":40,"withoutSource":2},
      "items":[ {"folio":"23548","file":"23548","county":"Down","entries":7,
                 "hasSource":true,"json":"batches/23548_Q3/23548.json"} ] }
  ]
}
```

This replaces every mock constant in Allocation: `BATCHES` (name + counts), `FOLIOS` (per batch),
`ETS` (with weights recomputed over the 6 real types), `PRE` (the 6 real counties).

Analyst and expert names stay local to the page — QA does not assign them.

### 4.2 `batches/<batch>/<folio>.json`

Exactly the shape in §2.2. Field mapping:

| Folio JSON | Source |
|---|---|
| `id` | `{county prefix}-{folio_number}`, e.g. `DN-23548` |
| `file` | the normalised base name |
| `county` | the `county` column |
| `batch` | the batch folder name |
| `sourceId` | the `source_id` column — kept for the fallback (§5, Phase 3) |
| `kind` | `"image"` always (drives the `<img>` path) |
| `pages[].img` | `batches/<batch>/<folio>/p{n}.png` |
| `entries[].type` | `entry_type` |
| `entries[].page` | first of `source_pages` (falls back to `page` from the coordinate CSV) |
| `entries[].order` | row order within the folio, keyed by `entry_id` |
| `entries[].inactive` | `cancellation_status` non-empty → renders as the inactive/struck-through style |
| `entries[].note` | `record_kind` other than `entry` → rendered as a note row |
| `entries[].box` | union of the entry's field boxes, or `null` |
| `fields[].label` / `value` | the schema column / its value |
| `fields[].box` | `x0_pt…y1_pt` → fractions (§4.3) |
| `fields[].basis` | `basis` — surfaced in place of High/Medium/Low (§5, Phase 4) |

### 4.3 Point → fraction

Boxes are stored normalised 0–1000 upstream and exported in PDF points at 200 DPI. Since the ingester
renders at the same 200 DPI:

```
fraction = pt / (px × 72 / 200)          # equivalently pt / (px × 0.36)
```

Verified against the producer: normalised `[120,210,800,250]` on a 1700×2338 page → `73.44, 176.75, 489.6,
210.42` pt → `/1700×0.36 = 612`, giving `0.120, 0.289, 0.800, 0.344` — i.e. the original normalised values,
as it must be.

Fields with no located source keep `box: null`; the page already renders these as
"has no marked position on the image" (L298).

---

## 5. Work breakdown

### Phase 0 — Sample data and freeze the contract
- Assemble a root folder with 2–3 batch folders and mixed ZIPs: one ZIP, several ZIPs, a rerun (`_1`), a
  folio with no source document, an image folio (`_p1.jpg`).
- Confirm the §4 JSON shapes against the pages.

**Done when:** a sample root folder exists and the folio JSON shape has been checked against `LPS_QA_Review.html` L196.

### Phase 1 — Discovery, unzip, versioning
`qa_ingest.py` steps 1–3, implementing `understanding.md` §3–§4.
- Discover batch folders (direct children of root).
- Apply latest-suffix to ZIP names, extract the survivors, union all files.
- Apply latest-suffix to file names across the union.
- Normalise bases: strip `-coordinates`, strip a trailing `-<12 hex>`.
- Group into folio families (source / values / geometry).
- Emit `warnings.json` for every skip or ambiguity (§4.4 of the understanding).

**Done when:** for the sample root, the resolved manifest lists exactly the expected folios, and the rerun
ZIP and older `_N` files are excluded with a warning recorded.

### Phase 2 — Folio builder
- Read `{base}.csv` into entries: group rows by `entry_id`, order by row position, one entry per group.
- Attach fields from the schema columns; keep `basis`, `cancellation_status`, `record_kind`, `source_pages`.
- Read `{base}-coordinates.csv` and attach boxes to fields by `entry_id` + `field`; entry box = union of its
  field boxes.
- Convert boxes to fractions (§4.3); `null` where absent.
- Emit `id`, `file`, `county`, `batch`, `sourceId`.

**Done when:** a folio JSON loads into the Review page, shows all its entries and fields (including the
inactive/note rows), and each located field highlights the correct text on the page image.

### Phase 3 — Page images
- Render each source PDF page with PyMuPDF at 200 DPI → `p{n}.png`.
- Image folios (`_pN.jpg`): copy as-is (already the page).
- **Fallback** (no source in the folder): resolve with the `source_id` read from the CSV —
  `GET /api/batches/{id}/folios/{sid}/pages/{n}` — at *ingest* time, not review time, so the batch is
  self-contained. Producer URL and Basic Auth from config. If the producer is unreachable, still emit the
  folio, mark `hasSource:false`, and warn.

**Done when:** a folio with no source document in its ZIP still opens in Review with its page image.

### Phase 4 — Wire the pages
- Add a loader that fetches `index.json` and the folio JSONs and populates `IMPORTED` (L171) — no other
  change to the Review logic.
- Allocation: replace `BATCHES`, `FOLIOS`, `ETS`, `PRE` from `index.json`; recompute entry-type weights over
  the 6 real types.
- Confidence: feed `basis` (`observed`/`human`/`derived`/`default`) into the per-field pill, replacing
  High/Medium/Low. Keep the existing `p-na` style for fields with no basis.
- **Complete the scope handoff** (§2.1): write the full selection (batches, folios, entry types, mode,
  analysts, experts) to the `lpsQA.allocation` localStorage record that Review already reads, and have
  Review filter `FOLIOS` to the allocated scope. This is the missing link between allocation and review.
- Handle "no data ingested yet" with a clear message rather than a blank page.

**Done when:** all three pages run off `qa_data/` with no mock constants left in the data paths, and the
folios Review shows are exactly the ones allocated.

### Phase 5 — Analytics (unchanged)
Analytics **stays a fixed mock**. Verdicts are not persisted (`understanding.md` §5.3), so rather than fake a
rollup from a session that evaporates on reload, the page keeps its existing dataset and is left alone.

- No session store, no wiring to Review's verdicts, no rollup logic.
- Confirm its cross-page links still resolve after the rail removal.
- Relabel the dataset as sample data, so the demo does not imply the figures are live.

**Done when:** Analytics renders its existing data and is clearly presented as sample output.

### Phase 6 — End-to-end run and packaging
- One documented command sequence (ingest + serve).
- `warnings.json` surfaced in the UI (a small banner: "3 items skipped — see warnings") so nothing is
  silently dropped.
- Reset path: delete `qa_data/`, wipe session storage.

**Done when:** a clean machine can go from a root folder to a completed QA walkthrough following the
documented steps.

---

## 6. Tasks and dependencies

| ID | Task | Phase | Depends on |
|---|---|---|---|
| T1 | Sample root folder (ZIP/batch/rerun/no-source/image-folio cases) | 0 | — |
| T2 | Folder + ZIP discovery, latest-suffix at both levels | 1 | T1 |
| T3 | Union, base normalisation, folio-family grouping, `warnings.json` | 1 | T2 |
| T4 | CSV + coordinate CSV → folio JSON | 2 | T3 |
| T5 | Point → fraction conversion, verified against a known box | 2 | T4 |
| T6 | Page rendering at 200 DPI | 3 | T3 |
| T7 | Producer fallback for missing sources (ingest-time) | 3 | T6 |
| T8 | Loader: `index.json` → `IMPORTED` | 4 | T4, T6 |
| T9 | Allocation data from `index.json` (batches, folios, 6 entry types, 6 counties) | 4 | T8 |
| T10 | Confidence pill ← `basis` | 4 | T8 |
| T11 | Empty-state handling | 4 | T8 |
| T12 | Scope handoff: write `lpsQA.allocation`, filter Review's `FOLIOS` | 4 | T9 |
| T13 | Analytics: keep the fixed mock, relabel as sample data | 5 | — |
| T14 | Run instructions + warnings banner + reset | 6 | all |

T2–T5 and T6–T7 are independent and can be done in parallel.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Coordinate boxes misalign on image folios (no true page geometry) | Validate visually in Phase 2 on one image folio before building further; the fallback is to derive fractions from the image's own pixel size at 200 DPI. |
| Coordinating `entry_id` rows with coordinate rows | Low — both carry `entry_id`, and the coordinates carry `field`; verify the join on a real export in T4. |
| Delivery ZIPs use the `source_id` base, so folios look source-less | Base normalisation (§4.2 of the understanding) strips the hash; confirm on the real ZIP in T1. |
| Producer unreachable during the demo | Fallback is best-effort; folios still ingest with `hasSource:false`. |
| Analytics figures not tracking the reviewed batch | Accepted by design — Analytics is a fixed mock (Phase 5); label it as sample data. |

---

## 8. Out of scope

Per `understanding.md` §9: authentication, persistence of verdicts, multi-analyst coordination, deployment
hosting, strict delivery validation, and any change to `land_records_digitization-main` beyond the
already-delivered coordinate CSV.

---

## 9. Demo script

1. Show the root folder — batch folders containing ZIPs.
2. Run the ingester; show the folio count and the warnings banner.
3. **Allocation** — pick a batch, note the real folios and the 6 real entry types; allocate; *Go to Review*.
4. **Review** — folio page beside extracted fields; click a field, see it highlight on the page; accept some,
   reject one with a note.
5. Show a folio whose source document was **not** in the ZIP, opened from the producer.
6. **Analytics** — show the roll-up views (fixed sample data, labelled as such).
