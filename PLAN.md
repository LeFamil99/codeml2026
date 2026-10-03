# L2C Hackathon — "Du plan aux dessins d'atelier" — Implementation Plan

**Status:** plan only, no code written.
**Author:** prepared with Claude Code, 2026-10-03.
**Evidence base:** every number in this document was measured on the real corpus in
`~/Downloads/l2c-participants` during planning. Measurements are collected in
§2 and Appendix A. Nothing here is assumed from experience with "PDFs in general".

> **Confidentiality note.** The consignes forbid uploading the documents to cloud
> services or external AI APIs. This plan is deliberately a **local file**, not a
> published page. The runtime pipeline described here is 100% local and offline.
> See §13 for the compliance checklist, including an issue you need to decide about.

---

## 1. The challenge in one paragraph

Engineers currently review rebar shop drawings (*dessins d'atelier*, "DA") against
the structural plans by eye, sheet by sheet. We must build a local Python system that
reads both document sets, extracts every reinforcement item with its sheet and X/Y
position, writes a schema-conformant JSON database, matches each plan element to its
shop-drawing counterpart, classifies it (`conforme`, `non conforme`, `manquant`,
`ajouté`), and emits a PDF report per plan sheet with conformity counts and a detailed
discrepancy list. Five projects exist; four are ours for development, the fifth is
held by the jury and contains known documented non-conformities.

**Scoring reality that drives every decision below:** 30 pts detection
(*"recall is weighted more heavily than precision"*), 20 pts extraction/JSON quality
incl. X/Y accuracy, 15 pts report usefulness, 15 pts code quality, 10 pts
robustness across an unseen project **and all five element types**, 10 pts
presentation. Detection is only 30 of 100. **Extraction quality + code quality +
report = 50 points and they are far more controllable.** Plan accordingly.

---

## 2. What the data actually is (measured, not assumed)

### 2.1 Corpus census

142 PDFs, 611 drawing pages, 290 MB, plus the two consignes PDFs.

| Project | Plan sheets | DA pages | DA pages with a real text layer |
|---|---|---|---|
| CLP | 28 | 34 | **34 / 34 (100%)** |
| WP2 | 50 | 72 | **0 / 72 (0%)** |
| LIGREP | 40 | 191 | **47 / 191 (25%)** |
| EspCa3B | 72 | 124 | **0 / 124 (0%)** |
| **Total** | **190** | **421** | 81 / 421 |

### 2.2 The single most important finding

**All 190 plan sheets have a clean, extractable text layer. 340 of the 421 shop-drawing
pages have ZERO extractable characters.**

The split is perfectly bimodal — no page has between 1 and 199 characters. A file is
either fully text or fully not; there are **no mixed files** (0 of 143).

Critically, the 340 text-less pages are **not scans**:

- `/Font` resources on those pages: **zero**. Text operators `BT` / `ET` / `Tj` / `TJ`: **zero**.
- Raster images cover only **~1.6%** of page area (title-block logos and the revision stamp).
- Page content lives in Form XObjects as **filled vector paths**.
- Rendered at 400 dpi the lettering is **perfectly crisp** — uniform Helvetica/Arial-like
  glyphs, no skew, no noise, no JPEG artefacts.

The CAD export converted all text to outlines. Measured glyph geometry:
**one filled path per glyph**, median height 16.7 pt, median width 10.2 pt, 4–56 path
items each, 831–1,300 glyph paths on a column sheet and 2,700–6,000 on a slab sheet.

This is enormously better than a scan, and it is why §5 proposes a deterministic
glyph decoder instead of OCR.

### 2.3 Sheet series, as actually numbered

| Series | Consignes say | Measured reality |
|---|---|---|
| S-000…S-004 | — | General notes, typical details |
| **S-050 / S-060** | *not documented* | **Radier (mat foundation) reinforcement, `RANG 1..4` layers** |
| S-100 | foundation | S-100 itself = footing schedule (`TYPE A..G`); **S-101…S-124 are dimensional/GA plans with zero rebar callouts** |
| S-300 | beam | Beam elevations. **LIGREP has no S-300 sheets at all** (but has a `DA/Poutres` folder) |
| S-400 | shear wall | Shear-wall elevations |
| S-500 | column | Column plans, one sheet per level |
| S-600 | slab | Slab sheets, dominated by `n(m)` notation, title keyword often `CISAILLEMENT` (punching-shear stud rails) |

Two consequences:

1. **The series list in the consignes is incomplete and partly inaccurate.** S-050/S-060
   carry real reinforcement and are in the answer key. Do not hardcode the documented list.
2. **Roughly 16 sheets per project carry no reinforcement at all** (WP2 S-101…S-114 etc.).
   For those the report must legitimately say `0 conformités / 0 non-conformités` —
   do not treat them as extraction failures.

Also measured: WP2 S-505…S-512 have *identical* word counts (720) and identical callout
histograms — these are repeated typical sheets. S-605…S-610 likewise. Expect near-duplicate
sheets; they are an opportunity for caching and a trap for "we found 8× more errors" bugs.

### 2.4 Callout grammar per element type

| Element | Plan notation | Shop-drawing notation |
|---|---|---|
| Column (S-500) | `COL. 16"x24"` / `ARM.: 4-25M` / `LIG.: 10M@6" c/c` / `BÉTON: 25MPa` | `16"X24"` / `VERT: 4 25M 25Z12-01` / `ÉTRI: 25 10M 10ET13X21 @6"` / `4 VERTICALES` |
| Beam (S-300) | `ARM.: n-SIZE`, bare `SIZE`, `LIG.: SIZE@sp` | mark + count + size + shape code |
| Shear wall (S-400) | `ARM.: n-SIZE` + `LIG.: SIZE@sp`, paired 1:1 | same shape as columns |
| Footing (S-100) | `TYPE A` row: dims + `17-25M` each way | schedule table |
| Radier (S-050) | `RANG 2: 25M@11" c/c` (4 layers) | `RANG n` layers |
| Slab / shear studs (S-600) | `16(8)` — count(spacing) | count(spacing) |

The plan gives **spacing**; the shop drawing gives **absolute count plus a fabrication
mark**. Normalisation must reconcile these (§6.3).

### 2.5 The answer key's "Localisation" column is heterogeneous

From `CLP/CLP_dismatch.xlsx`:

| Feuillet | Localisation | Form |
|---|---|---|
| S-050 | `J-10.8` | grid intersection |
| S-100 | `L-13` | grid intersection |
| S-400 | `élévation B - RDC @ 2` | **elevation label + level range** |
| S-502 | `K-6` | grid intersection |
| S-504 | `I-13` | grid intersection |
| S-603 | `J-15` | grid intersection |

So the locator is a grid intersection for plan-view elements and an
**elevation name + level span** for walls. The data model must carry both (§4).

---

## 3. Tooling decision — with benchmark evidence

### 3.1 Head-to-head on a real plan sheet (CLP S-502)

| Operation | PyMuPDF 1.28.2 | pdfplumber 0.11.10 | Verdict |
|---|---|---|---|
| `extract_words` result | 879 tokens | 879 tokens | **token multiset identical** |
| words, time | 0.009 s | 0.49 s | PyMuPDF **58× faster** |
| vector geometry | 6,442 paths, 0.04 s | 209 rects + 6,137 lines + 146 curves, 0.49 s | PyMuPDF **12.5× faster** |
| outlined DA page | 0 words | 0 words | **neither** gets text; both expose outlines |

**Decision: PyMuPDF (`pymupdf`) is the primary engine.** pdfplumber extracts exactly the
same text but is one to two orders of magnitude slower and — decisively — gets the
coordinate system wrong (next section). Keep pdfplumber only as an optional
cross-check in tests, since it is named in the consignes' recommended list.

### 3.2 Two coordinate traps that will silently destroy 20 rubric points

**Trap 1 — non-zero MediaBox origin. 118 of 624 pages (19%).**
CLP's plan sheets declare `MediaBox (-1727.7, -1295.4, 1727.7, 1295.4)`.

- PyMuPDF normalises to `Rect(0, 0, 3455.4, 2590.8)` — origin top-left, y growing
  downward. **This is exactly what Appendix A of the consignes requires.**
- pdfplumber returns **raw** MediaBox coordinates. Measured offset against PyMuPDF on
  uniquely-matched tokens: **dx = −1727.70, dy = +1298.71**.

A team that extracts with pdfplumber and writes `x0`/`top` straight into the JSON has
every coordinate on 19% of pages wrong by ~1,700 pt. The rubric scores
"accuracy of the X, Y coordinates" inside a 20-point criterion.

**Trap 2 — `/Rotate 90` on 406 of 624 pages (65%).**
On a rotated page, `get_text()` returns coordinates in the **rotated** space
(`page.rect` = 2592×1728) while `get_drawings()` returns them in the **unrotated**
MediaBox space (y up to 2592). Mixing them silently breaks every geometric
text↔symbol association on two thirds of the corpus.

**Mitigation (mandatory, first line of every page handler): call `remove_rotation()`
on the page before extracting anything.** Verified: afterwards both text and drawings
report a consistent `0..2592 × 0..1728` space.

**Also required:** the consignes ask for the **centre** of the annotation, in points,
from the **top-left**. PyMuPDF's native space is already top-left/y-down, so the
conversion is only `((x0+x1)/2, (y0+y1)/2)`. Add a golden test that asserts the
origin convention on one page from each project, and assert y-down by checking that
the title block (sheet number `S-502`, measured at y≈2531 of 2590.8) has a **larger**
y than the top grid bubbles (y≈219).

### 3.3 Performance — a non-issue, which is strategically important

Measured over an 18-file / 95-page random sample:

| Operation | Per page | Extrapolated to 611 pages |
|---|---|---|
| `remove_rotation` + `get_text("words")` | 0.024 s | 15 s |
| `get_drawings()` | 0.212 s | 130 s |
| 400 dpi render (outlined pages only) | ~0.13 s | ~45 s for 340 pages |

**Whole corpus: under 5 minutes single-threaded**, trivially parallel per page with
`multiprocessing`. Consequence: **you can afford a live full-project run during the
10-minute demo.** Build the CLI so that it does exactly that — it is worth more than
any slide.

### 3.4 Dependency list (all open-source, all jury-runnable)

Core: `pymupdf`, `numpy`, `pillow`, `pydantic` (schema validation, named in consignes),
`reportlab` (PDF report, named in consignes), `streamlit` (triage UI, named in consignes),
`pandas` (scoring notebook), `pytest`.

Deliberately **not** required: `opencv`, `paddleocr`, `tesseract`, `torch`, `YOLO`,
any VLM. §5 explains why the recommended OCR/detection stack is unnecessary here —
and §14 keeps a thin OCR fallback behind an interface in case the jury project differs.
Pin everything in `pyproject.toml`; the jury must be able to `pip install -e .` and run.

---

## 4. Architecture

Seven stages, each a pure function with a typed boundary, each independently testable.

```
 (1) DISCOVER   → file inventory, project/source/element-type classification
 (2) PAGE PREP  → remove_rotation, coordinate normalisation, sheet-ID + title-block parse
 (3) TEXTIFY    → real text layer  OR  glyph decoder (§5) → tokens with boxes
 (4) GEOMETRY   → grid axes, element symbols, scale, level bands
 (5) PARSE      → tokens + geometry → canonical reinforcement records (§6)
 (6) MATCH      → plan record ↔ DA record (§7)
 (7) COMPARE    → classify + confidence → JSON + PDF report (§8, §9)
```

Design rules:

- **Stage 3 returns the same token type regardless of source.** A token is
  `{text, bbox, confidence, source_kind}` where `source_kind` is `text_layer` or
  `glyph_decoded`. Every downstream stage is then identical for both page families.
  This is the key abstraction; it is what makes 340 outlined pages cost almost nothing
  architecturally.
- **Never discard geometry.** Every record keeps its page, bbox, grid locator, and the
  raw source string it was parsed from. The report's "ease for the engineer to find the
  element on the sheet" (15 pts) and the annotated-PDF bonus both depend on this.
- **Cache per page** to a content-addressed store keyed by file hash + page index, so
  re-runs during the hackathon are instant and the demo cannot fail on a cold cache.

### 4.1 Canonical data model

The consignes' Appendix A schema is the **output contract**, not the internal model.
Internally carry richer fields and project down at serialisation time.

Internal record (one per identified reinforcement item):

| Field | Notes |
|---|---|
| `id` | `{feuillet}_{element}_{source}`, matching the consignes' example `S-500_C-12_plan` |
| `source` | `plan` \| `atelier` |
| `fichier`, `feuillet`, `page` | provenance |
| `x`, `y` | **centre**, PDF points, top-left origin (§3.2) |
| `type_element` | `colonne` \| `poutre` \| `mur_refend` \| `semelle` \| `radier` \| `dalle` |
| `element` | grid locator (`K-6`) or elevation locator (`élévation B`) |
| `niveau` | normalised level token (§7.1) — **internal, needed for matching** |
| `armature[]` | `{repere, diametre, quantite, espacement_mm, longueur_mm}` per the schema |
| `_raw` | source strings, token bboxes, symbol bbox, confidence, decode path |
| `_locator_kind` | `grid` \| `elevation` — drives report wording |

Serialise to the consignes' exact schema (drop `_`-prefixed fields); validate every
record with Pydantic and **fail the build** on a schema violation. Units: the schema
asks for `espacement_mm` / `longueur_mm` in **millimetres** while the drawings are
imperial (`10M@6" c/c`, `EL.: 157'-9"`). Centralise conversion in one module with
unit tests, and keep `null` where a value genuinely is not stated — the schema permits it.

---

## 5. Reading the shop drawings — the technical core

340 pages (56% of DA pages, including **100% of WP2 and EspCa3B**) have no text.
This section is the difference between a working submission and a partial one.

### 5.1 What was ruled out, with evidence

**Exact path-hash fingerprinting: FAILS.** Hashing each glyph's translate-normalised
path geometry gave **860 distinct signatures for 1,013 glyphs on one page, 88.4%
singletons**, and the alphabet did **not saturate** across 34 pages — it grew
monotonically to 44,278 signatures over 65,627 glyph instances. Cause: the CAD export
emits jittered floating-point coordinates and many font sizes. Do not pursue this.

**Full-page OCR (Tesseract/PaddleOCR): unnecessary and lossy.** It would throw away the
exact glyph bounding boxes we already have, re-introduce a segmentation step that is
currently free and perfect, and inject an error source into the 20-point extraction
criterion.

### 5.2 What works

**Normalised-bitmap glyph classification.** Because each glyph is its own vector path,
**segmentation is already perfect and exact** — we know every character's bounding box
to sub-point precision before reading anything.

Validated clustering result: 1,846 glyphs from two pages → **55 clusters** at cosine
distance < 0.12 on 16×16 normalised bitmaps, only **3 singleton clusters**, **98.1% of
glyphs in clusters of size ≥ 5**, 100% in the top 60. Rendering the 60 largest cluster
centroids produced a clean, immediately legible alphabet:
`0 5 2 1 · 3 4 7 E 6 M X 9 R C 8 P S H U ' N @ a K B D V Q A ◇ Z L I t F e r R H / n # C M ? 1 F A`.
The alphabet is small and closed, exactly as expected for engineering annotation.

**Fully automatic decoding, no labelling step.** Classify each glyph against templates
rendered from PDF base-14 fonts (`helv`, `hebo`) at 20×20, cosine similarity.
Measured on a region whose true content I independently verified by rendering it:

| Ground truth | Decoded (v1) | Decoded (v2, case+gap aware) |
|---|---|---|
| `DALLE 10"` | `DALLE 1 0` | **`DALLE 10`** |
| `POUR BETON 35MPa` | `PoUR BEToN 35MPa` | `POUR BETON 35MP8H` |
| `CHEV. 15M =620` | `cHEV 1 5M 620` | **`CHEV- 15M -620`** |
| `CHEV. 20M = 830` | `CHEv 20M 830` | **`CHEV- 20M 830`** |
| `CHEV. 25M = 1290` | `CHEV 25M 1 290` | **`CHEV- 25M 1290`** |
| `CHEV. 30M = 1550` | `CHEV 30M 1 550` | **`CHEV- 30M - 1550`** |
| `CHEV. 35M = 1800` | `CHEV 35M 1 8o0` | **`CHEV- 35M 1800`** |

Mean per-line confidence **0.95–0.99**. The one junk line (a leader-line arrowhead read
as `/`) scored 0.805 — a confidence threshold removes it.

**All digits and all uppercase letters decode correctly in v2**, including every number
that matters (`10`, `15M`, `620`, `1290`, `1550`, `1800`). That is precisely the
content the task is scored on: quantities, bar sizes, spacings, counts.

### 5.3 Residual error classes and their fixes

| Error | Cause | Fix |
|---|---|---|
| Case confusion `O/o`, `C/c`, `V/v`, `S/s`, `X/x`, `Z/z` | bbox normalisation destroys absolute size | compare glyph height to the **line's cap-height**; shape-identical pairs are then decided by size alone, deterministically |
| `.` vs `-` vs `=` vs `"` | all are small marks; shape alone is ambiguous | decide by **vertical position relative to the line baseline**: `"` sits at cap height, `-`/`=` at mid height, `.`/`,` at baseline. We have exact bboxes, so this is exact |
| `1290` → `1 290` | naive per-glyph gap threshold | threshold on **median inter-glyph gap for that line**, scaled by cap-height, not by the previous glyph's width |
| dropped `.` and `"` | height filter floor was too high (periods are ~2 pt) | lower the floor and route sub-5 pt marks through the punctuation-by-baseline rule above |
| `a` forced to `A` | cap-height rule applied to an x-height letter | maintain an explicit x-height set (`acemnorsuvwxz`) exempt from the rule |

These are five bounded, deterministic rules. None requires a model.

### 5.4 Rotation — measured to be a minor edge case

Earlier corpus-wide stats showed 31% of text spans running vertically, which sounded
alarming. Broken down, that is almost entirely the **plan sheets**, which have a real
text layer where PyMuPDF reports direction natively — no work needed.

On the outlined DA pages, measured by neighbour chaining: **66.2% of glyphs have a
horizontal right-neighbour, only 3.5% have a vertical below-neighbour**, and **0% have
an aspect ratio > 1.5** (median w/h = 0.54, i.e. all glyphs upright). The CLP DA text
layer agrees: 601 horizontal spans vs 18 vertical (2.9%).

**Plan:** group glyphs into runs by nearest-neighbour chaining (not x-binning — naive
binning produced bogus 1,000 pt "lines" in testing), decide each run's orientation by
trying all four 90° rotations and taking the one with the **highest aggregate**
similarity over the whole run, not per glyph. Budget this as a small, late task.

### 5.5 Why this is the right engineering call

It is deterministic, auditable, offline, needs no training data, no GPU, no model
weights, and runs in milliseconds per page. For a submission judged partly on
"transparency about the limits of the solution", being able to say *"we do not OCR;
we decode the vector glyph outlines, and here is the per-character confidence"* is a
strong position. It also means the 20-point extraction criterion is earned by
construction rather than hoped for.

---

### 5.6 Dialect variation across providers — measured

The plans come from one engineering firm. **The shop drawings come from different
fabricators, and they do not share a standard.** This was measured across all four
projects, and it is the single biggest robustness risk in the task.

**Plan side — standardised, and that is the good news:**

| Project | Units | `ARM.:` | `LIG.:` | `COL.` | `RANG` | `TYPE` |
|---|---|---|---|---|---|---|
| CLP | **imperial** | 481 | 480 | 396 | 131 | 38 |
| WP2 | metric | 998 | 1067 | 1346 | 94 | 44 |
| LIGREP | metric | 724 | 761 | 678 | 77 | 60 |
| EspCa3B | metric | 886 | 965 | 733 | 81 | 109 |

**Identical grammar in all four**: `COL. WxH` / `ARM.: n-SIZE` / `LIG.: SIZE@spacing c/c`
/ `RANG n` / `TYPE x`. The only variable is the unit system — **CLP is imperial, the
other three are metric** (CLP 716 imperial spacing tokens vs 177 metric; the others
926–1441 metric vs 14–27 imperial). Unit system must therefore be **auto-detected per
project**, never configured, and never assumed from the one project that has an answer key.

**Shop-drawing side — three distinct dialects in four projects:**

| Project | Text layer | Dimension | Ties/stirrups | Verticals | Spacing |
|---|---|---|---|---|---|
| CLP | text | `16"X24"` | `ÉTRI: 25 10M 10ET13X21 @6"` | `VERT: 4 25M 25Z12-01` | imperial `@6"` |
| WP2 | **outlined** | (metric) | — | — | metric |
| LIGREP | mixed | `DIM:` | **no `ÉTRI:` at all** | `VERT:` | metric `@...` |
| EspCa3B | **outlined** | `DIM: 400x750` | `ET.: 11 10M 10T3267` | `VERT: 6 25M 25Z2711` | `9@400` mm, rotated |

Measured token counts over sampled DA files: CLP `VERT:` 968 / `ÉTRI:` 1579 / `GOUJ` 170
/ `VERTICALES` 507, all imperial. LIGREP `VERT:` 377 / `DIM:` 689 / `GOUJ` 44, all
metric, **zero `ÉTRI:`**. EspCa3B uses `ET.:` and `DIM:`. So the stirrup label alone
appears as `ÉTRI:`, `ET.:`, or is absent, depending on the fabricator.

**Conclusion: hardcoding DA keywords is guaranteed to fail on the jury's project.**
Plan for a dialect you have never seen.

### 5.7 Generalization test — the decoder survives, with one dangerous error

The glyph decoder was built and validated on WP2. I then ran it **unchanged** against
EspCa3B — a different fabricator, a different unit system, **8.5 pt glyphs instead of
19 pt**, and a **monospace** font for the values rather than the proportional sans it
was tuned on. Ground truth taken by rendering the region at 600 dpi and reading it.

| Ground truth | Decoded (untuned) |
|---|---|
| `DIM: 400x750` | `IDIMI 400x750 DI` |
| `ET.: 11 10M 10T3267` | `I 11 10M 10T3 267` |
| `11 10M 10P320` | `1 1 10H 10 P3 20` |
| `VERT: 6 25M 25Z2711` | `VERTI 6 25H 25 Z27 11` |

**Every digit is correct** — `400`, `750`, `11`, `10`, `3267`, `320`, `6`, `25`, `2711`.
Confidence 0.89–0.98. Expanding the template bank from 2 to 5 base-14 fonts
(`helv`, `hebo`, `cour`, `cobo`, `tiro` — 375 templates, 74 characters) was the **only**
change needed, and it is a one-line change that covers the sans/bold/mono/serif space
that CAD exports actually use.

**But two error classes appeared, and one of them is dangerous:**

- `:` decoded as `I` — cosmetic, fixed by the §5.3 baseline-position rule.
- **`25M` decoded as `25H`, `10M` as `10H`** — at 8.5 pt in a monospace font, `M` and `H`
  are genuinely similar. **This is the bar designator, the single most important field
  in the whole task.** A silent `M`→`H` on a bar size is exactly the class of error that
  would poison the output.

### 5.8 The fix is a lexicon, not a bigger model

Reinforcement bar designations in Canada are a **closed set**:
`10M, 15M, 20M, 25M, 30M, 35M, 45M, 55M`. `H` is not a bar designator and never will be.

So the decoder must not emit free text. It must emit **the best string consistent with a
grammar**, scoring candidates against the template similarities rather than taking a
per-glyph argmax. Concretely, a four-tier decode:

1. **Per-glyph posterior** — keep the top-k template similarities per glyph, not just the winner.
2. **Token grammar** — bar tokens match `\d{2}M` from the closed set; spacings match
   `@\d{1,3}("|mm)?`; quantities are bare integers; marks match the fabricator's
   observed mark pattern. Pick the highest-scoring grammar-conformant reading.
3. **Line grammar** — a DA line is `<label> <count> <size> <mark>` or
   `<label> <count> <size> <mark> @<spacing>`. Labels come from a learned
   per-file lexicon (§5.9), not a hardcoded list.
4. **Cross-validation against the plan** — the plan already tells us the expected bar
   size at that grid cell. A decode that disagrees with the plan is *either* the
   non-conformity we are hunting *or* a decode error; surface it as a finding with the
   decode confidence attached, and let the triage UI (§9.3.3f) separate the two.

This makes the decoder **more** accurate than any OCR engine or VLM, because it is the
only approach that can exploit the closed vocabulary. It is also deterministic, so it
stays testable.

> **Honest caveat:** I verified the closed-set reasoning but did **not** run the
> lexicon-constrained decoder end-to-end — it is correct by construction for a closed
> set, but it is unimplemented and untested. Treat §5.8 as the highest-priority
> implementation task in Phase 2, with `test_glyph_decoder` extended to assert that
> `H` can never be emitted in a bar-designator position.

### 5.9 The inversion that makes an unknown dialect tractable

This is the most important strategic idea in the plan, and it falls directly out of
§5.6: **the plan side is standardised and 100% readable; the shop-drawing side is not.**

So do not write a DA extractor that must understand an arbitrary fabricator's layout.
**Anchor on the plan and turn open-ended extraction into constrained verification.**

For each plan element we already know, deterministically and with high confidence:
its element type, its level, its grid locator, its dimensions, and **the rebar it
expects**. That is a very strong prior. The DA reader's job is then not
*"parse this unknown document"* but *"find the cell for K-6 at NIVEAU 2 and read it,
knowing it should look roughly like 4 bars of some designator in the closed set."*

Why this is much better:

- **Recall by construction.** Every plan element gets checked because the plan drives the
  loop. Nothing is missed because the DA parser failed to understand a layout — the worst
  failure mode under a rubric that weights recall over precision. An unreadable DA cell
  becomes a low-confidence finding, not a silent omission.
- **`manquant` falls out naturally.** We looked for it and it was not there.
- **The prior disambiguates the decode** (§5.8 tier 4).
- **It needs far less of the DA to be understood.** We need the row/column headers
  (grid labels, level anchors) and the cells — not the whole sheet's semantics.
- **It degrades gracefully.** If the DA layout is wholly unparseable, we still emit a
  complete plan-side JSON (earning most of the 20-pt extraction criterion) plus a report
  saying exactly which DA files could not be read and why. A partial honest answer scores;
  a crash does not.

Keep a **reverse pass** as well — scan the DA for elements the plan does not mention — to
catch `ajouté`. That pass is allowed to be lower-recall, since it is the rarer class.

### 5.10 Where AI genuinely earns its place — and where it must not be used

Your instinct to reach for vision is right about *which problems are hard*, but the
hard problems are not the transcription. Here is where I would and would not spend it.

**✅ Use it: view/region segmentation on the page.** A sheet contains several viewports,
schedules, and a title block. Detecting those regions is a real layout problem and your
step 1 is well posed. But reach for the **cheapest tool that works**, in this order:

1. **Vector geometry first.** The viewport borders and title-block frames are literal
   rectangles in the content stream, and we already read every path on the page
   (0.2 s). The title-block region heuristic (x > 0.62·W, y > 0.80·H) already recovers
   sheet IDs on ~95% of 190 plan pages. Exhaust this before training anything.
2. **A small detector if that fails.** YOLO (recommended in the consignes) or
   DocLayout-YOLO, trained on a few hundred hand-boxed regions. Runs in milliseconds
   on CPU, gives reproducible boxes, is a legitimate "trained model" deliverable
   (the consignes ask for weights + training script if a model was trained).
3. **A VLM last and probably never**, for this step — it gives imprecise boxes, and we
   need exact coordinates for the JSON.

**✅ Use it: correspondence between DA views and plan sheets.** Your step 2. This is
genuine matching under uncertainty: a DA view labelled `NIV-4@5` must bind to plan sheet
`S-504 / PLAN DES COLONNES - NIVEAU 4`. Do it as a **similarity matrix + Hungarian
assignment** over cheap features (normalised level tokens, grid-label set overlap,
element type, dimension multiset). Grid-label set overlap alone is a very strong
signal — if a DA view contains `{K-3, K-5, K-6, K-7, L-3, …}` and exactly one plan
sheet contains that set, the binding is certain. No neural model required; add
embeddings only if the cheap features leave real ambiguity.

**✅ Use it: semantic normalisation of an unseen dialect.** This is the real inference
problem you identified, and the one place a language model clearly pays. Given the short
label strings harvested from a new DA file — `ET.:`, `ÉTRI:`, `LIG.:`, `DIM:`, `VERT:`,
`GOUJ:` — map each onto our canonical vocabulary
(`vertical_bars`, `ties`, `dimension`, `dowels`). A **small local LLM** (7B class) does
this well because the input is **a few dozen short strings, not images**: one prompt per
new file, constrained to emit only canonical labels, with the mapping cached per
fabricator and shown to the engineer for confirmation in the intake screen (§9.3.1).
Cost is negligible and the output is validated against a closed vocabulary, so a
hallucination cannot escape. Fall back to fuzzy string distance against known labels
when no model is available — that alone would have got `ET.:` → `ÉTRI:`.

**❌ Do not use it: transcribing the numbers.** Your step 3. I would push back on this
specifically, for five concrete reasons:

1. **The failure mode is silent numeric hallucination.** A VLM that reads `4-25M` as
   `4-35M` returns a fluent, confident, wrong answer — and that is *precisely the
   artefact we are built to detect*. The tool would manufacture non-conformities
   indistinguishable from real ones, and an engineer cannot tell which is which. This
   alone disqualifies it.
2. **Resolution.** A sheet is 3455 × 2590 pt ≈ 155 MP at 300 dpi. Local open-source VLMs
   handle roughly 1–4 MP natively. You would tile into 50–150 crops per page × 611 pages
   = tens of thousands of inferences. On CPU: days. On one consumer GPU: many hours. The
   deterministic path does the whole corpus in **under 5 minutes** (§3.3).
3. **No usable coordinates.** The 20-point criterion grades X/Y accuracy. VLMs give
   poor, non-reproducible boxes; we already have **exact** glyph bounding boxes for free.
4. **Non-determinism breaks the deliverable.** `test_determinism` becomes impossible and
   the jury cannot reproduce our numbers from our JSON.
5. **There is nothing left to win.** The deterministic decoder already got **100% of
   digits** on a dialect it had never seen, at 8.5 pt, with a one-line change. A VLM
   cannot beat 100%, and it would trade auditability for it.

**The short version:** use ML for *where things are* and *what things mean*. Never for
*what the digits say*.

### 5.11 The reading ladder — automatic tier selection per page

Make the reader a ladder with an explicit, logged tier per page, so an unseen project
degrades instead of failing:

| Tier | Condition | Method | Confidence |
|---|---|---|---|
| **1** | page has a text layer | PyMuPDF `get_text("words")` | ~1.0 |
| **2** | no text, but filled glyph-shaped paths present | glyph decoder + lexicon constraint (§5.2–5.8) | 0.85–0.99 measured |
| **3** | no text, no vector glyphs, raster coverage high | local OCR (PaddleOCR/Tesseract) on a 400 dpi render, **cropped to detected regions** | 0.5–0.8, flag all |
| **4** | tier 3 confidence below threshold | **human-in-the-loop**: surface the crop in the dashboard for the engineer to read | marked `unread` |

Tier 4 is not a cop-out — it is the correct engineering answer for a review tool whose
own documentation says *"the final decision remains the engineer's"*, and it protects
recall: an unread cell is reported as unread, never as conforme.

Measured tier distribution on the four dev projects: tier 1 = 271 pages
(all 190 plan sheets + 81 DA pages), tier 2 = 340 pages, tier 3 = 0, tier 4 = 0.
**Tier 3 exists purely as insurance against the jury's project**, which is why it stays
behind an interface and is built last.

### 5.12 Local model shortlist and compute budget

Everything below runs locally, offline, open-weights. **My training data ends May 2026,
so verify current best-in-class before committing** — treat these as the category, not
the final answer.

| Job | Candidates | Size | Where it runs |
|---|---|---|---|
| Region/view detection | YOLO (ultralytics), DocLayout-YOLO | 10–50 M params | CPU, ms/page |
| Raster OCR (tier 3 only) | PaddleOCR, Tesseract, docTR, Surya | small | CPU |
| Dialect label normalisation | Qwen2.5-7B-Instruct, Mistral-7B, Phi-4, Gemma-class | 7–14 B, 4-bit | 1 GPU or CPU via llama.cpp; **a few dozen short strings per file** |
| Last-resort visual read | Qwen2.5-VL, InternVL, MiniCPM-V, Molmo | 3–8 B | GPU; **not in the main path** |

Licensing matters for a jury that must run it: prefer Apache-2.0/MIT weights, note any
non-commercial or custom licence in the README beside the PyMuPDF AGPL note (§13).

**Budget check.** The deterministic path needs **no GPU at all** and finishes the corpus
in under 5 minutes. Adding a 7B LLM for label normalisation adds one short prompt per DA
file (~140 files) — seconds to a couple of minutes total. Adding a VLM to the
transcription path adds **tens of thousands of inferences**. The asymmetry is the whole
argument.

### 5.13 Critique of the obvious pipeline — one step will quietly destroy recall

The natural pipeline, and the one worth writing down because it is *almost* right:

1. Analyse the L2C plan first, so we know everything to expect.
2. Walk the DA files; in each, separate the views.
3. In each view, extract every piece of text.
4. Judge whether each piece is useful.
5. Compute its locator (e.g. `J-10`).
6. **Hand it to an LLM to map it onto one of the expected values from the plan.**

Steps 1–5 are the right architecture — they are §5.9, and anchoring on the plan is
exactly the correct inversion. **Step 6 is a trap**, and it is worth being precise about
why, because it looks like the sensible way to use a model.

**The problem: it puts the comparison inside the LLM.** If you hand a model a DA string
plus a list of expected plan values and ask it to map one onto the other, it will
**always find a match** — that is what language models do. And the entire purpose of
this tool is to find the places where the DA *does not* match the plan.

Concretely, with real decoded output from EspCa3B:

- Plan at K-6 expects `4-35M`. DA cell decodes to `VERT- 4 35H 35 Z27 15`.
- Give the model the DA string **and** "expected: 4-35M" → it confidently returns
  `4-35M`, conforme. Correct here, by luck.
- Now the real non-conformity: plan expects `4-35M`, DA actually says `4 25M`.
  The model, primed with "expected 4-35M", has every incentive to reconcile them —
  decode noise, a mark-code coincidence, a plausible reading. It reports conforme.

**That is a false negative, and false negatives are the one thing this rubric punishes
hardest** (*"recall is weighted more heavily than precision"*). A model asked to
reconcile two values will reconcile them. You would be building a conformity-confirming
machine and calling it a discrepancy detector.

**The fix is a hard architectural boundary: the model parses blind; code compares.**

| Stage | Who does it | Sees the plan's expected value? |
|---|---|---|
| Decode glyphs → characters | deterministic (§5.2–5.8) | **no** |
| Characters → structured record | LLM *or* grammar, per dialect | **no** |
| Record ↔ record comparison | **plain code, exact** | yes, both sides |
| Classification + confidence | plain code | yes |

The model's output must be a **record**, never a verdict:
`{role: "ties", count: 11, diametre: "10M", repere: "10T3267", espacement_mm: 400}`.
It is scored on schema conformance, not on agreement with anything. Then
`compare.py` does `35M == 25M → False`, deterministically, reproducibly, and testably.

**The one legitimate use of the plan as a prior — and the precise line.** §5.8 tier 4
*does* use plan knowledge to fix decode errors, which sounds like the same thing. The
distinction that makes it safe:

> Use the plan to constrain the decode to the **closed vocabulary**
> (`{10M, 15M, 20M, 25M, 30M, 35M, 45M, 55M}` — project-independent, true of every
> Canadian drawing). **Never** to the **specific expected value at that cell.**

Constraining to "must be a valid bar designator" is domain knowledge and cannot hide a
discrepancy. Constraining to "should probably be 35M because the plan says so" is
circular and hides exactly the discrepancy we are hunting. Encode this as a code-level
invariant — the DA parser takes the vocabulary as an argument and **has no access to the
plan's per-cell values** — and assert it in a test.

### 5.14 Why the lexicon, not the LLM, is the keystone — measured

Full-page decode of `EspCa3B_COLONNES NIV 10.pdf` (2,618 glyphs, 170 text lines)
produced this on the single most important field:

| Bar designator decode | Count |
|---|---|
| correct `<nn>M` | 6 |
| **wrong `<nn>H`** | **30** |
| **error rate** | **83%** |

At 8.5 pt in a monospace font, `M` and `H` are near-indistinguishable, and the error is
not occasional — it is the *default*. Raw glyph decoding is therefore **not sufficient**
on this project, and no amount of LLM post-processing fixes it honestly: a model handed
`25H` has to guess, and if you tell it the plan says `25M` you are back in §5.13.

The lexicon constraint fixes it completely and for free: `H` is not a bar designator, so
a grammar-conformant decode **cannot emit it**. Keep the top-k template posteriors per
glyph and pick the best reading consistent with
`\d{2}(10|15|20|25|30|35|45|55)M` — the correct answer is already in the candidate set,
it is simply not the per-glyph argmax.

**This reorders the implementation priorities.** §5.8 is not a refinement, it is the
component the whole DA path rests on. Build it before anything else in Phase 2, and make
`test_glyph_decoder` assert that no designator slot can ever emit a non-`M` letter.

Other decoded confusions on the same page, all fixable the same way: `:` → `I`/`l`/`-`
(baseline-position rule), `O` ↔ `0` (closed vocabulary — marks and counts are digits),
`DIM:` → `IDIMl` (label lexicon, §5.10). Note the decoder also read genuinely useful
things correctly throughout: `400x750`, `400x600`, `400x800`, `DIAM500`,
`niv. 47855`, `10T3267`, `25Z2711`, `3950`.

### 5.15 Locator derivation on the DA side — grid bubbles are there, and detectable

**Correction to an earlier draft of this plan.** An earlier version claimed EspCa3B's
shop drawings carry no grid reference. That was wrong — it generalised from a single
*Colonnes* file, which is a schedule table and genuinely has no grid. **The plan-view DA
sheets (slabs, foundations, walls) do carry gridlines and bubbles**, exactly like the
plan sheets.

**Measured bubble-candidate counts** (curve-dominated, near-square, 12–70 pt paths):

| | Colonnes | Dalles | Fondations | Murs/Refends | Poutres |
|---|---|---|---|---|---|
| CLP | 3 | **122** | **97** | — | **48** |
| WP2 | 0 | **36** | **21** | **22** | — |
| LIGREP | **21** | **38** | **23** | **15** | 7 |
| EspCa3B | **29** | **33** | **54** | **22** | — |

Bubbles are present on essentially every plan-view DA sheet in all four projects. Only
WP2's column schedule has none (it has 11 vertical + 10 horizontal long gridlines
instead).

#### Validated algorithm

Tested end-to-end on `EspCa3B_DALLE NIV 10.pdf`:

1. Find **bubble circles** — paths with ≥3 curve items, aspect ratio 0.85–1.18.
2. Find the **glyph paths whose centre lies inside** each circle.
3. Decode those glyphs with the §5.7 template bank.
4. Group the results: labels sharing an x and spread over y mark a **column** axis;
   labels sharing a y and spread over x mark a **row** axis.

Result: recovered `B C D E F` along the top at cx = 190, 473, 755, 1037, 1319 and
`2 3 4 5` down the left at cy = 194, 476, 758, 1040 — a **perfectly uniform 282 pt grid**
(283, 282, 282, 282 / 282, 282, 282). Confidence 0.73–0.96. This is the locator
mechanism, working on an outlined shop drawing with no text layer.

#### Three detection artefacts, all with cheap fixes

1. **Round letters are false positives.** The detector matched the `O`, `D`, `R`, `E`,
   `A`, `U` glyphs in the sheet note *"ORDRE DE POSE D'ARMATURE"* — their outlines are
   curve-dominated and near-square. **Fix:** require bubble diameter ≥ 30 pt (real
   bubbles measured 49 pt, stray letters 13–20 pt), require the circle path to be
   **stroked rather than filled**, and require the contained glyph to be materially
   smaller than the circle.
2. **Duplicate circles.** Each bubble appeared twice at the same centre with different
   diameters (49 pt and 14 pt). **Fix:** dedupe by centre within a small radius, keep the
   largest.
3. **Double-stroked glyphs.** Every letter decoded doubled — `BB` for `B`, `CC` for `C` —
   because the glyph outline is emitted as two near-identical filled paths. **Fix:**
   dedupe glyph paths whose bboxes agree within ~0.5 pt. This also matters for the main
   decoder, since it would otherwise double every character on affected sheets.

#### Three locator regimes

| Sheet type | How an element is identified |
|---|---|
| Plan-view DA (slabs, foundations, walls) | **gridlines + bubbles**, computed geometrically as above |
| CLP column schedule | **explicit grid label as text** — `K-3 K-5 K-6 K-7 K-12 …`, 16 per page |
| EspCa3B / WP2 column schedule | **no grid at all** — bind on *(section dimensions, elevation/level, fabrication mark)*. Note this joins on attributes we are **not** testing, so it cannot contaminate the comparison (§5.13) |

Resolve in that order, cheapest first, and record which regime fired per sheet so the
diagnostics panel can show it.

#### The margin heuristic does not port

The plan-sheet shortcut — short tokens in the outer 10% of the page, required to appear
twice (§6.1) — **fails on DA sheets**; tested across CLP and LIGREP DA it recovered only
spurious `I`, `B`, `X`. A DA sheet is not laid out like a plan sheet, and the bubble is
often inside the drawing extent. Anchor on **gridline endpoints and circle geometry**,
never on position relative to the paper.

### 5.15b The axis convention is per-project, and CLP is the exception

Detected on the plan sheets by checking whether a repeated label's occurrences share an
x (marking a column) or a y (marking a row):

| Project | Letters label | Numbers label |
|---|---|---|
| **CLP** | **rows** — 13 left/right pairs | **columns** — 21 top/bottom pairs |
| WP2 | **columns** — 18 pairs | rows — 12 pairs |
| LIGREP | (not paired on the sampled sheet) | columns — 23 pairs |
| EspCa3B | **columns** | rows — 6 pairs |

**CLP is transposed relative to WP2 and EspCa3B.** EspCa3B's plan and its DA agree with
each other (letters across the top, numbers down the side), so this is a **project-level
convention, consistent across both document sets** — not a plan-vs-DA mismatch. That is
the good news; the bad news is the pattern it completes:

> **CLP is the exception three times over — it is the only imperial project (§5.6), it
> has a transposed grid convention, and it is the only project with an answer key.**
> Anything calibrated on CLP will be wrong on three of the four dev projects, and
> probably on the jury's.

**Mitigations:**

- **Auto-detect the axis convention per project** by the pair-orientation test above;
  never hardcode it. Expose it on the pre-flight screen (§5.16) for confirmation.
- **Keep the emitted locator string stable.** The answer key writes `K-6`, `J-10.8`,
  `L-13`, `I-13`, `J-15` — always letter-then-number. Emit `<letter>-<number>`
  regardless of which geometric axis each plays, and keep the axis roles internally.
  The string format is then stable across projects even as the geometry flips, so
  `comparison.json` and the report stay directly diffable against the key.
- Assert it: a test fixture per project pinning the detected convention, so a
  regression that transposes locators fails loudly rather than silently joining nothing.

### 5.16 Pre-flight declaration — ask the engineer what the machine should not guess

**Yes — asking up front is the right call, and it should be a designed feature rather
than a fallback.** For a tool whose own consignes state *"the final decision remains the
engineer's"*, asking the engineer to confirm a handful of document-level facts is
entirely appropriate, and it is far more robust than inferring them. It converts several
of the hardest inference problems into thirty seconds of human input.

The rule that keeps it unattended-capable: **auto-detect everything, pre-fill the form
with what was detected and the evidence for it, and let the engineer confirm or
override.** Never a blank form, never an unconfirmable guess.

Pre-flight screen, shown after intake classification (§9.3.1) and before extraction:

| Question | Auto-detected from | Why it must be confirmable |
|---|---|---|
| **Unit system — imperial or metric?** | spacing-token census (CLP 716 imperial vs 177 metric; the other three 926–1441 metric vs 14–27 imperial) | CLP is the **only** imperial project *and* the only one with an answer key — a tool calibrated on it will be wrong on three of four |
| **Fabricator / DA dialect** | label lexicon match against known dialects (CLP, LIGREP, EspCa3B patterns) | three dialects in four projects (§5.6); "unknown → learn it" is a valid answer |
| **Grid axis convention** — do letters label rows or columns? | pair-orientation test (§5.15b): CLP letters=rows, WP2/EspCa3B letters=columns | CLP is transposed relative to the others *and* is the only project with an answer key; hardcoding it transposes every locator and joins nothing |
| **Level convention** — does "PLAN DES COLONNES - NIVEAU 2" mean the segment above or below that slab? | calibrated against CLP's answer key | an off-by-one-storey error shifts every column and manufactures hundreds of false findings (§7.1) |
| **DA revision precedence** | revision-table dates (only `Partie 3` carries any; latest 29/05/2026) | choosing wrong fabricates ~11 false non-conformities per page (§7.4) |
| **Bar designator set** | default Canadian `10M…55M` | this is the lexicon that fixes the 83% error in §5.14 |
| **Element types to check** | sheet-series census | lets the engineer scope a quick run |
| **Tolerances** | config default | must be visible, not buried (§8.1) |

Every answer is recorded in `run_manifest.json`, so a report always states the
assumptions it was produced under. The same form doubles as the CLI's config file
(`--config project.toml`), so headless runs are reproducible and the dashboard and CLI
cannot diverge.

This is also a credibility moment in the demo: showing the jury that the tool *knows
what it does not know*, states its detected guess, and asks rather than assumes, is
precisely the *"transparency about the limits of the solution"* the rubric rewards.

## 6. Extraction specifications per element type

### 6.1 Grid system (shared foundation for all plan-view elements)

**Validated on CLP S-502.** Grid bubble labels are ordinary tokens at the sheet margins:

- **Row letters** appear as *pairs* at both left (x ≈ 227) and right (x ≈ 2507) margins.
  Recovered A–O (15 rows) with y positions.
- **Column numbers** appear as *pairs* at top (y ≈ 219) and bottom (y ≈ 2320).
  Recovered 22 axes including fractional ones: 1, 2, 3, 3.8, 4, 5, 5.6, 6, 6.5, 7, 7.3,
  8, 8.2, 9, 9.8, 10, 10.8, 11, 12, 12.7, 13, 14.4, 15, 16, 17.

**Algorithm:** collect short tokens matching a letter pattern near the left/right
margins and a number pattern near the top/bottom; require each label to appear
**at least twice** (both margins) to reject stray text; take the **bbox centre**, not
the left edge, as the axis position; store axes as sorted arrays.

Robustness requirements learned from the data: fractional grids (`10.8`, `14.4`) must be
supported; axis order is **not** monotonic in x in the intuitive direction (on S-502 x
*decreases* as the grid number increases) so never assume ascending; always derive from
the labels.

> **This margin-based variant works on plan sheets only.** Measured on CLP and LIGREP
> DA sheets it recovers essentially nothing (§5.15). The portable algorithm anchors on
> **gridline endpoints** rather than page margins; implement that version and use the
> margin heuristic only as a fast path for plan sheets.

### 6.2 Element symbol detection and callout association — the crux

This is where naive approaches fail. The callout **text is not at the element's
location**; it is placed adjacent to it.

**Worked example, the known S-502 / K-6 / `4-35M` non-conformity:**

- The `ARM.: 4-35M` token sits at x = 1821.8, y = 1089.0.
- Nearest row axis: **K** (y = 1083, Δ = 5.6) — unambiguous.
- Nearest column axis by text position: grid **7** (x = 1741, Δ = 80.6) or **6**
  (x = 1933, Δ = 111.4). **Both wrong or ambiguous — the text alone cannot resolve it.**

The resolution comes from vector geometry. Searching filled paths near the callout found
a **12 × 18 pt filled rectangle at (1933.3, 1084.1)** — exactly grid 6 ∩ row K.

And the geometry self-validates against the text:

| Filled rect size | Count | At 0.75 pt/inch | Callouts stating that size | Count |
|---|---|---|---|---|
| 12 × 18 pt | **67** | **16.0" × 24.0"** | `COL. 16"x24"` | **67** |
| 7 × 22 pt | **3** | 10" × 30" | `COL. 10"x30"` | **3** |

Perfect 1:1 on both populations, 70 = 70 total. The drawing scale derives to exactly
**0.75 pt per real inch = 1/8" = 1'-0"**, a standard architectural scale.

**Therefore the association algorithm is:**

1. Detect candidate element symbols: filled vector paths in a plausible size window.
2. Derive the sheet scale by fitting detected rectangle dimensions against the
   dimensions **stated in the callout text** (`16"x24"`). This is a self-calibrating
   cross-check; log a warning if the fit is poor, since a bad scale invalidates the sheet.
3. Assign each callout block to the **nearest symbol by edge-to-edge distance**, not
   centre-to-centre. In the worked example the callout's right edge is 27 pt from the
   K-6 symbol but its left edge is 63 pt from the K-7 symbol — edge distance picks
   K-6 correctly, centre distance does not.
4. Require assignment consistency: the symbol's aspect ratio must match the callout's
   stated dimensions. Mismatch ⇒ emit a **low-confidence** record rather than drop it
   (recall-first, §8.2).
5. Map the symbol's centroid to the nearest grid intersection → `element = "K-6"`.

Solve step 3 as a **global assignment** (Hungarian / min-cost bipartite matching) over
each sheet, not greedily — the counts are equal (70/70) so a global optimum exists and
greedy nearest-neighbour will mis-pair in dense bays.

### 6.3 Notation normalisation

The two sides speak different dialects. Normalise both into the canonical
`armature[]` entries:

| Concept | Plan form | DA form | Canonical |
|---|---|---|---|
| Vertical bars | `ARM.: 4-25M` | `VERT: 4 25M 25Z12-01` | `quantite=4, diametre="25M", repere="25Z12-01"` |
| Ties / stirrups | `LIG.: 10M@6" c/c` | `ÉTRI: 25 10M 10ET13X21 @6"` | `diametre="10M", espacement_mm=152.4, quantite=25` |
| Footing bars | `17-25M` | schedule row | `quantite=17, diametre="25M"` |
| Radier layer | `RANG 2: 25M@11" c/c` | `RANG 2` layer | `espacement_mm=279.4`, layer index kept in `_raw` |
| Shear studs | `16(8)` | `20(8)` | `quantite=16, espacement_mm=203.2` |

Note the asymmetry: **the plan states spacing, the DA states an absolute count.**
Comparing count-to-count requires knowing the member length. Where the length is not
reliably extractable, **compare on the dimensions both sides actually state**
(bar size, spacing, and count when present) and mark the others
`espacement_mm=null` / `quantite=null` rather than inventing a derived value.
All four answer-key rebar discrepancies are expressible this way:
count (`9-25M` vs `11-25M`, `8-30M` vs `6-30M`), size (`4-35M` vs `4-25M`),
spacing (`10M@12"` vs `10M@6"`), stud count (`16(8)` vs `20(8)`).

### 6.4 Per-type build order

| # | Type | Series | Why this order | Difficulty |
|---|---|---|---|---|
| 1 | **Column** | S-500 | densest, cleanest, 1:1 paired callouts, validated end-to-end, present in all 4 projects | Low |
| 2 | **Shear wall** | S-400 | same `ARM.`/`LIG.` grammar as columns; elevation locator instead of grid | Low–Med |
| 3 | **Footing / radier** | S-100, S-050 | tabular `TYPE` schedule + `RANG` layers; 2 of 6 answer-key rows | Medium |
| 4 | **Beam** | S-300 | elevations, more free-form; LIGREP has none | Medium |
| 5 | **Slab / studs** | S-600 | `n(m)` callouts scattered over plan geometry, 100–194 per sheet, no table structure | **High** |

Slabs are last but **must not be skipped** — 10 pts of robustness require all five types,
and recall is weighted. Even a low-confidence slab extractor that flags candidates beats
no slab coverage at all.

---

## 7. Matching strategy

### 7.1 The join key

Validated: **plan title blocks state the element type and the level directly.**
Parsed from the title-block region (x > 0.62·W, y > 0.80·H):

| Sheet | Title block yields |
|---|---|
| S-501 | `PLAN DES COLONNES - RDC` |
| S-502 | `PLAN DES COLONNES - NIVEAU 2` |
| S-504 | `PLAN DES COLONNES - NIVEAU 4` |
| S-505 | `PLAN DES COLONNES - NIVEAU 5` |

Sheet-ID extraction from the same region recovered the correct `S-xxx` on ~95% of the
190 plan pages; the handful of failures and one duplicate (LIGREP shows `S-110` twice)
need a fallback — search a widened title-block window and reconcile against the
page order. **Build a `sheet_id` resolver with an explicit `unknown` state** rather than
guessing; a wrong sheet ID poisons the whole report for that page.

On the DA side the level comes from the **left-margin level anchors**, validated on
`CLP_COLONNES Partie 2.pdf` p4: `TOIT APPENTIS` y=266, `TOIT` y=356, `NIVEAU 5` y=533,
`NIVEAU 4` y=693, `NIVEAU 3` y=853, `NIVEAU 2` y=1013, `REZ-DE-CHAUSSÉE` y=1214,
`SOUS-SOL` y=1404, `RADIER`/`EMPATTEMENT` y≈1428–1540. These define **level bands**;
a rebar entry's y position places it in a band.

Normalise all level tokens through one table
(`RDC` ≡ `REZ-DE-CHAUSSÉE`, `SS1` ≡ `SOUS-SOL`, `NIV 4` ≡ `NIVEAU 4`, `TRÉFOND`,
`EMPATTEMENT`, `TOIT`, `TOIT APPENTIS`) and keep the ordinal so ranges
(`FDN@SS1`, `RDC@2`, `4@5`) can be resolved. DA filenames encode exactly these
ranges (`WP2_COLONNE-NIV-4@5.pdf`) — use the filename as a **prior**, the in-document
anchors as the **authority**, and log a warning when they disagree.

**Beware the off-by-one-storey error.** A column sheet named "NIVEAU 2" and a DA band
labelled "NIVEAU 2" may refer to the segment *below* or *above* that slab depending on
convention. Calibrate this **once** against CLP's six known rows and then assert it in
a regression test. Getting this wrong shifts every column by one storey and produces
hundreds of false non-conformities — the single highest-impact bug available in this task.

### 7.2 End-to-end validation already achieved

The full chain was run manually against the ground truth:

1. Plan S-502 → title block → `colonne`, `NIVEAU 2`.
2. Callout `ARM.: 4-35M` at (1821.8, 1089.0) → nearest symbol (1933.3, 1084.1)
   → grid intersection **K-6**.
3. Shop drawing `CLP_COLONNES Partie 2/3.pdf` p4 → column labelled `K-6` at x = 730.2
   → NIVEAU 2 band (y 1013…1214) → entry at y = 1030.7 → **`VERT: 4 25M`**.
4. Normalise → plan `{4, 35M}` vs DA `{4, 25M}` → **non-conforme, diameter**.

This reproduces answer-key row 4 exactly: *S-502 / K-6 / `4-35M` / `4-25M`*.
**The architecture is proven on real ground truth before any code is written.**
Make this the first integration test in the repo.

### 7.3 Matching algorithm

Per `(project, type_element, niveau)` bucket:

1. Bucket plan and DA records by normalised level and element type.
2. Within a bucket, join on the **grid locator** (exact string after normalising
   `K-6` / `K6` / `k-6` and fractional axes).
3. Unjoined plan records → `manquant dans l'atelier`.
   Unjoined DA records → `ajouté dans l'atelier`.
4. For walls (elevation locator), join on elevation label + level range instead.
5. Emit a per-bucket diagnostic (counts joined / unjoined both ways). A bucket that
   joins 0% is a **level-mapping bug**, not 100 non-conformities — fail loudly.

Guard rail: if a bucket produces a non-conformity rate above a configurable threshold
(say 20%), the pipeline should emit a **bucket-level warning** in the report rather
than hundreds of individual findings. Given the answer key has 6 findings across a
28-sheet project, a sheet reporting 50 non-conformities is far more likely to be a
join failure than a real catastrophe. Say so in the report; that is exactly the
"transparency about limits" the rubric rewards.

### 7.4 Revision resolution — a necessity, not the bonus

**Measured problem.** `CLP/DA/Colonnes/` contains `Partie 1`, `Partie 2`, `Partie 3`.
Partie 2 and Partie 3 page 4 cover the **same grid columns at the same coordinates**
(K-3…L-16) but **11 of 46 comparable rows differ**: stirrup counts 19→20, 14→15,
28→29, 2→3; mark numbers `25L9-05`→`25L10-05`; shape codes `10ET7X35`→`10ET7X27`.
Only Partie 3 carries revision dates (latest **29/05/2026**). Partie 1 is sparser still.

**Comparing against the wrong file fabricates ~11 false non-conformities per page.**
Since recall is weighted over precision the scoring harm is bounded, but the
*credibility* harm in a demo is severe.

**Plan:** build a revision resolver that, for each `(project, element type, level, grid)`
cell, selects the record from the DA file with the **latest revision date** in its
revision table, falling back to the highest `Partie`/`REV` ordinal, and records the
alternatives in `_raw`. Then the "revision comparison" bonus is nearly free: the
resolver already holds both versions. **Ask the organizers** what `Partie n` means
before trusting this (§15).

---

## 8. Comparison, classification, and recall-first confidence

### 8.1 Classification

Per the consignes, each matched element is classified `conforme`,
`non conforme` (with the detailed delta), `manquant dans l'atelier`, or
`ajouté dans l'atelier`. Compare attribute by attribute (quantity, diameter, spacing,
length) and report **which attribute differs with both values** — the answer-key format
(`Plan L2C` vs `Dessin d'atelier`) is exactly this.

Tolerances must be explicit and configurable: imperial↔metric conversion means
`6"` → 152.4 mm, so an exact-equality test on millimetres will produce spurious
mismatches. Define per-attribute tolerance (diameter exact; spacing ±2 mm;
length ±5 mm) in one config file and document it in the README.

### 8.2 Confidence, and why it is the strategic centre of the design

The rubric says: *"recall is weighted more heavily than precision: a missed
non-conformity has more consequences than a false alarm."* The bonus asks for a
*"confidence score ... and an interface where the engineer validates the uncertain
cases."* Read together, these say: **the organizers expect false positives and want
them surfaced as low-confidence, not suppressed.**

So: **never drop an uncertain record. Emit it with a low confidence score.**

Propagate confidence multiplicatively through the pipeline:

| Stage | Confidence signal |
|---|---|
| Glyph decode | mean cosine similarity over the token's glyphs (measured 0.95–0.99 on good text, 0.805 on junk) |
| Symbol association | edge distance to the assigned symbol ÷ distance to the runner-up; aspect-ratio agreement with the stated dimensions |
| Grid mapping | distance to the nearest grid intersection, normalised by local bay spacing |
| Level mapping | margin to the band boundary |
| Notation parse | whether the string fully matched a known pattern or needed a fallback |

Then rank findings by `(is_non_conforme, 1 − confidence)` so the engineer sees
real problems first and uncertain ones next, and ship the Streamlit triage view from
the bonus list. This single design choice serves the 30-pt detection criterion, the
15-pt usefulness criterion, and the 10-pt transparency criterion simultaneously.

### 8.3 Anomaly detection as an independent recall channel

Measured on the plan sheets alone, **the planted errors are statistical outliers
within their own sheet**:

- S-502 `ARM.` value distribution: `4-25M` × 64, **`4-35M` × 1**, `6-20M` × 3, `8-20M` × 2.
- S-504 tie spacing distribution: `10M@6"` × 55, `10M@4"` × 5, `10M@8"` × 3, **`10M@12"` × 1**.

In both cases the answer-key discrepancy is the **singleton**. This is a legitimate
engineering heuristic (nominally identical members should carry identical
reinforcement), it needs **no cross-document matching at all**, and it is a cheap
independent recall channel that will catch findings the matcher misses.

**Ship it as a second detector** whose hits are unioned with the matcher's, flagged
with `detector: "anomaly"` vs `detector: "cross_document"`, and attributed in the
report. Agreement between the two detectors is a strong confidence boost; disagreement
is exactly what the triage UI is for. Do **not** let it replace matching — it cannot
tell you *what the DA says*, which the report must state.

---

## 9. Deliverables

### 9.1 JSON database — the second graded artifact

**Yes, this is a first-class deliverable, not a by-product.** The consignes require it
three separate times: as a specific objective (*"Build a JSON database that lists each
identified piece of information, with the plan sheet and the X, Y coordinates as a
reference point"*), as a mandatory technology (*"A JSON output that complies with the
provided schema"*), and in the deliverables list (*"Other: JSON files and PDF reports
generated for the provided projects"*). It carries the 20-point
"Quality of extraction and JSON" criterion — the second-largest block on the rubric and
the most controllable one.

**Files emitted per project:**

| File | Contents |
|---|---|
| `out/{project}/elements_plan.json` | every reinforcement item found in the plan set |
| `out/{project}/elements_atelier.json` | every reinforcement item found in the shop drawings |
| `out/{project}/comparison.json` | the matched pairs with classification, delta, confidence, detector |
| `out/{project}/report.pdf` | the PDF report of §9.2 |
| `out/{project}/run_manifest.json` | pipeline version, timestamp, config, tolerances, SHA-256 of every input file |

`elements_plan.json` and `elements_atelier.json` are **flat arrays of Appendix-A
records** — exactly the shape the consignes specify, nothing wrapped around it, so a
jury script can load them without reading our docs. One record per identified item:
`id`, `source`, `fichier`, `feuillet`, `page`, `x`, `y`, `type_element`, `element`,
and the nested `armature[]` list of `{repere, diametre, quantite, espacement_mm,
longueur_mm}`.

`comparison.json` is **ours to design** — the consignes do not specify it — so keep it
obviously derived: each entry references two record `id`s (or one, plus a null for
`manquant`/`ajouté`), the classification, the per-attribute delta with both values, the
confidence, and which detector fired. This is the file the PDF report and the dashboard
both render from, and the file a scoring script diffs against the answer key.

**Hard rules, enforced in CI:**

- Every record validates against the Appendix-A schema via Pydantic. A violation
  **fails the build** — never emitted and quietly shipped.
- `x`, `y` are the **centre of the annotation, in PDF points, from the top-left corner**,
  after the §3.2 rotation and origin normalisation. This is the single most likely place
  to silently lose points; `test_coords` guards it on a non-zero-MediaBox page and a
  `/Rotate 90` page.
- `espacement_mm` / `longueur_mm` in **millimetres**, converted through the single
  `units.py` module. `null` where the drawing genuinely does not state a value —
  never a fabricated or back-derived number.
- Output is **deterministic**: records sorted by `(feuillet, page, y, x)`, floats
  rounded to one decimal, so two runs are byte-identical and diffs are reviewable.
  `test_determinism` enforces this.
- A `validate` CLI subcommand re-checks any JSON file against the schema, so the jury
  can verify conformance without running the pipeline.

Internal records carry extra `_`-prefixed debug fields (§4.1); these are **stripped at
serialisation**. Ship a `--debug-json` flag that keeps them for our own triage, and keep
it off by default so the graded artifact stays exactly on-schema.

### 9.2 PDF report (ReportLab)

The main objective per the consignes: *per plan sheet, the number of conformities
and non-conformities, plus a detailed list of discrepancies.*

Structure:

1. **Cover** — project, run timestamp, corpus inventory, pipeline version, totals.
2. **Summary table** — one row per sheet: feuillet, element type, level, #conforme,
   #non conforme, #manquant, #ajouté, mean confidence. Sheets with no reinforcement
   shown explicitly as `0 / 0` with a `no reinforcement callouts` note, so the
   engineer can tell "nothing to check" from "we found nothing".
3. **Per-sheet detail** — one section per sheet with non-conformities first, each giving:
   feuillet, locator (`K-6` or `élévation B - RDC @ 2`), attribute, plan value, DA value,
   X/Y in points, DA file + page, confidence, detector. Mirror the answer-key column
   names (`Feuillet`, `Localisation`, `Plan L2C`, `Dessin d'atelier`) so a
   judge can diff it against their key at a glance.
4. **Diagnostics appendix** — pages skipped, buckets that failed to join, low-confidence
   decode regions, revision conflicts resolved and how. This is where the
   "transparency about the limits" points live.
5. **Bonus if time** — annotated crops: the plan region and the DA region side by side
   with the differing callout boxed. We already keep every bbox, so this is a render,
   not a new capability.

### 9.3 The review dashboard

This is the *"Application / prototype: interface or CLI command that takes a project's
PDFs as input and produces the JSON and the PDF report"* deliverable. It is also where
the **15-point "Usefulness of the report"** criterion is won or lost, and it delivers
two of the three optional bonuses (confidence triage, annotated findings) almost for free.

**Technology: Streamlit** (named in the recommended list). Runs locally via
`streamlit run`, binds to localhost, makes no outbound network calls. State that
explicitly in the README — it is a compliance claim, not just a detail.

#### 9.3.1 Intake — upload and classify

Three ways in, all equivalent downstream:

1. **Drag-and-drop a project ZIP** (the natural shape — this is how the corpus arrived).
2. **Drag-and-drop loose PDFs** for a quick single-sheet check.
3. **Point at a local directory** — the path-based route, best for the jury's own copy
   and for the live demo.

Measured sizing: project ZIPs are CLP 30 MB, WP2 79 MB, LIGREP 87 MB, EspCa3B 94 MB
(full corpus 292 MB). Streamlit's default upload cap is 200 MB, so set
`server.maxUploadSize = 1024` in `.streamlit/config.toml` — otherwise EspCa3B uploads
fine today and the jury's larger project fails on stage.

**Classification must be content-based, never path-based.** Measured reason: LIGREP's
shop drawings are prefixed `GP2_` rather than the project name, and the element
folders differ across projects (`Murs refends` vs `Murs cisaillements` vs
`Semelles et radiers`). The classifier therefore decides from evidence: page count and
the presence of a title block identify the plan set; the sheet-ID series (§2.3) and the
title-block keywords identify the element type; the presence or absence of a text layer
decides which stage-3 path each page takes.

**The intake confirmation table is mandatory, not a nicety.** After upload and before
any processing, show one row per detected file:

| File | Role | Element type | Pages | Text layer | Action |
|---|---|---|---|---|---|
| `L2C_PLAN_STR_WP2.pdf` | Plan | — | 50 | text | ▾ override |
| `WP2_COLONNE-NIV-4@5.pdf` | Shop drawing | colonne | 3 | **outlined → glyph decoder** | ▾ override |

Every role and type is a dropdown the engineer can correct. This single screen prevents
the most likely live-demo failure — a misclassified file producing an empty or absurd
report — and it doubles as a credibility moment: showing the jury that the tool *knows*
which pages have no text layer and how it will read them demonstrates the §5 work
better than any slide.

Uploaded bytes go to a **session-scoped temp directory** and nowhere else. A visible
**Purge data** button deletes the session directory and the page cache, backing the
consignes' "data must be deleted from participants' workstations" requirement (§13).

#### 9.3.2 Run — progress that survives a 5-minute pipeline

A full project takes ~4–5 minutes (§3.3), so a spinner is not acceptable feedback.

- Execute in a background process; the UI stays responsive and cancellable.
- `st.status` with one expandable line per pipeline stage (§4), each turning from
  running → done with its page count and elapsed time.
- A progress bar driven by **pages completed / total pages**, plus a live tail of the
  warning log so problems surface during the run, not after.
- **Per-page caching keyed by file hash + page index**, so a re-run after a parameter
  change is near-instant and an interrupted run resumes.
- A **Demo mode** toggle that loads a pre-computed cache for a known project. Rehearse
  with it. A live cold run is the better demo, but a one-click fallback means a slow
  laptop or a bad USB read cannot cost us the 10 presentation points.

#### 9.3.3 Results — layout and the specific visual encodings

Reading order, top to bottom: *how bad is it → which sheets → which findings → is it
trustworthy.*

**(a) Hero figure + KPI row.** The number the dashboard leads with is the
**non-conformity count**, as a hero figure (≥48 px). Beside it a row of stat tiles —
not a bar chart, because these are headline scalars, each a value with a small
secondary line:

`Éléments extraits` · `Conformes` · `Non conformes` · `Manquants` · `Ajoutés` ·
`Confiance moyenne` · `Pages traitées / ignorées`

**(b) Per-sheet overview — emphasis form, not a four-colour stack.** The engineer's
question is "which sheets need my attention", so one series is the point and the rest
is context: **non-conformities per sheet in the critical hue, everything else in the
de-emphasis gray.** Horizontal bars, since a project has 28–72 sheets with short labels.
This also validated cleanly as a colour palette (critical + gray: CVD ΔE 9.1,
normal-vision ΔE 18.9, contrast ≥ 3:1 — pass).

If a full four-way composition is wanted, it belongs in the table, not in colour. Should
the team still want a stacked bar, use the **validated ordering
`conforme → ajouté → non conforme → manquant`** (`#0ca30c`, `#fab219`, `#d03b3b`,
`#ec835a`): that order passes both the CVD-separation and normal-vision checks, whereas
the intuitive ordering fails — `warning #fab219` beside `serious #ec835a` scores a
normal-vision ΔE of 13.6, below the floor of 15, i.e. **hard to tell apart even with
full colour vision**. Yellow must never sit next to orange here. Segments get a 2 px
surface gap and direct labels.

**(c) Confidence distribution.** A histogram of per-record confidence, **one hue,
light→dark** (sequential — the job is magnitude, not identity). Its purpose is to let
the engineer choose a triage threshold, so make the threshold draggable and have it
filter the table live.

**(d) Findings table.** The core of the screen. Columns mirror the answer key so a judge
can diff by eye — `Feuillet`, `Localisation`, `Plan L2C`, `Dessin d'atelier` — plus
attribute, confidence, detector (`cross_document` / `anomaly`), and a state badge.

State uses the reserved status palette **with an icon and a text label, never colour
alone**: ✓ `conforme` good · ✗ `non conforme` critical · ◔ `manquant` serious ·
⊕ `ajouté` warning. The two lower-contrast status hues carry visible labels, which is
exactly the relief their contrast warning requires.

Default sort: non-conformities first, then **ascending confidence** — real problems at
the top, then the cases most in need of a human. Filters sit in a single row above the
charts: sheet, element type, level, state, detector, confidence threshold.

**(e) Finding detail.** Selecting a row opens the side-by-side: the **plan region** and
the **shop-drawing region**, each cropped and rendered from the source PDF with the
differing callout boxed, and a line connecting the claim to both sources. Beneath it:
X/Y in points, DA file and page, the raw source strings both sides were parsed from, the
per-attribute delta, and — for glyph-decoded pages — the per-character decode confidence.
Every bbox is already retained (§4), so this is a render, not new capability. This panel
*is* the "ease for the engineer to find the element on the sheet" criterion, and it
delivers the annotated-PDF bonus.

**(f) Triage.** Accept / Reject / Unsure per finding, persisted to a local SQLite
sidecar so decisions survive a restart. This is the bonus's *"interface where the
engineer validates the uncertain cases"*, and it produces a second, more valuable
artifact: a **reviewed** findings set distinct from the raw machine output.

**(g) Diagnostics tab.** Pages with no text layer and how they were read; buckets that
failed to join (the §7.3 level-mapping alarm); revision conflicts and which file won and
why (§7.4); sheets with zero reinforcement callouts; low-confidence decode regions.
Putting the system's own doubts on screen is what the rubric means by
*"transparency about the limits of the solution"*.

**Accessibility and chrome**, per the visualization rules: a legend whenever two or more
series are present and none when there is one; a table view always reachable for every
chart; text in ink tokens rather than series colours; recessive hairline grid and axes;
thin marks with 4 px rounded data-ends; **no dual-axis charts ever**; dark mode as
deliberately selected steps against the dark surface, not an automatic inversion.

#### 9.3.4 Download — every artifact, one click

Each artifact individually, plus a single bundle:

| Download | Format |
|---|---|
| `elements_plan.json`, `elements_atelier.json` | Appendix-A conformant JSON |
| `comparison.json` | classified pairs |
| `report.pdf` | the §9.2 report |
| `findings.csv` | the answer-key column shape, for spreadsheet comparison |
| `findings_reviewed.csv` | with the engineer's triage decisions applied |
| `annotated/*.pdf` | per-sheet annotated plan + DA crops (bonus) |
| `run_manifest.json` | version, timestamp, config, tolerances, input SHA-256s |
| **`{project}_l2c_review.zip`** | **all of the above, one button** |

The ZIP is the demo close: the jury watches a project go in, waits for the real
pipeline, and leaves with the full evidence package. Build it in memory and serve via
`st.download_button`; never write outside the session directory.

#### 9.3.5 Division of responsibility with the CLI

The CLI is the graded, scriptable artifact and must be able to do everything headlessly
— `l2c run <project_dir> --out out/` producing identical JSON and PDF. The dashboard is
a **view over the same pipeline**, importing the same functions, with no logic of its
own. If a behaviour exists only in the dashboard, it is in the wrong place. This keeps
the 15-point code-quality criterion intact and means the jury can reproduce every
dashboard number from the command line.

### 9.4 Notebook

`notebooks/01_exploration.ipynb` reproducing the key measurements in §2 (the census,
the text/outline split, the scale derivation, the grid extraction) and
`02_end_to_end.ipynb` running one project through all seven stages and scoring against
`CLP_dismatch.xlsx`. The consignes require a notebook with exploration **and** an
end-to-end demonstration on one project; this satisfies both and doubles as the
evidence that the measurements are real.

### 9.5 README

Architecture diagram, install, run commands, the **assumptions and known limitations**
list (the consignes explicitly ask for it — and §15's open questions belong there),
the tolerance configuration, and a short "why no OCR" section pointing at the
measured evidence in §5.

---

## 10. Repository layout

```
l2c-review/
  pyproject.toml            # pinned deps, console entry point
  README.md
  src/l2c/
    discover.py             # stage 1: inventory + classification
    page.py                 # stage 2: rotation/coords/sheet-id/title block
    textify/
      text_layer.py         # stage 3a: native text
      glyph_decoder.py      # stage 3b: outline decoding (§5)
      templates.py          # base-14 glyph template bank
    geometry/
      grid.py               # stage 4: grid axes
      symbols.py            # element symbol detection + scale calibration
      levels.py             # level bands
    parse/                  # stage 5: one module per element type
      columns.py  walls.py  footings.py  radier.py  beams.py  slabs.py
    model.py                # canonical records + Pydantic schema projection
    units.py                # imperial <-> mm, single source of truth
    match.py                # stage 6 (incl. revision resolver)
    compare.py              # stage 7 classification + confidence
    detect_anomaly.py       # independent recall channel (§8.3)
    report/pdf.py           # ReportLab
    report/annotate.py      # cropped side-by-side renders (bonus)
    bundle.py               # zip packaging of all artifacts
    cli.py
  app/
    streamlit_app.py        # entry; view-only, imports src/l2c
    intake.py               # upload, content-based classification, override table
    views_overview.py       # hero + KPI row + emphasis chart + confidence histogram
    views_findings.py       # findings table, filters, detail panel
    views_diagnostics.py    # join-rate alarms, revision conflicts, decode confidence
    triage_store.py         # SQLite sidecar for accept/reject
  .streamlit/config.toml    # server.maxUploadSize = 1024 (measured need, §9.3.1)
  tests/
    golden/                 # frozen expected records per sheet
    test_coords.py          # origin/rotation invariants (§3.2)
    test_known_mismatches.py# the 6 CLP answer-key rows
    test_schema.py          # Appendix A conformance
    test_glyph_decoder.py   # character-level accuracy on fixtures
  notebooks/
  out/                      # generated JSON + PDF (git-ignored)
```

Keep `src/` free of any path to the confidential data; take the corpus root as an
argument. That way the repo is publishable while the data never is.

---

## 11. Testing strategy

The 15-pt code-quality criterion names "tests" explicitly, and tests are also the only
defence against the silent-coordinate-bug class in §3.2.

| Test | Asserts |
|---|---|
| `test_coords` | top-left origin, y-down, centre-of-annotation, on one page per project; explicitly covers a non-zero-MediaBox page and a `/Rotate 90` page |
| `test_known_mismatches` | all 6 rows of `CLP_dismatch.xlsx` are detected, with the right feuillet, locator, and both values |
| `test_no_false_storm` | no CLP sheet reports more than N non-conformities (catches level-mapping regressions) |
| `test_schema` | every emitted record validates against Appendix A |
| `test_glyph_decoder` | character accuracy ≥ 99% on a frozen fixture set of glyph crops, including the `.`/`-`/`=`/`"` and case cases |
| `test_scale_calibration` | derived scale on CLP S-502 is 0.75 pt/inch ± 1% and rect/callout dimension populations agree 1:1 |
| `test_grid_extraction` | CLP S-502 yields rows A–O and the 22 numbered axes incl. fractional |
| `test_determinism` | two runs produce byte-identical JSON |
| `test_json_contract` | emitted arrays are flat, on-schema, `_`-prefixed fields stripped, records sorted `(feuillet, page, y, x)` |
| `test_intake_classifier` | correct role/type/text-layer verdict on all 142 corpus files, including LIGREP's `GP2_` prefix and the three folder taxonomies |
| `test_bundle` | the ZIP contains every artifact in §9.3.4 and nothing outside the session dir |

Scoring harness: a `score` subcommand that compares `comparison.json` against a
ground-truth spreadsheet and prints recall, precision, and per-row diagnostics.
Run it on CLP continuously. **This is also the harness you run live on the jury project.**

---

## 12. Work plan

Durations are relative effort, since I do not know the event length or team size
(§15). Sequenced so that a complete, demonstrable submission exists early and
improves monotonically — never a state where nothing runs.

**Phase 0 — Spine (first ~15% of time).** Repo, deps, Pydantic model, page prep with
the rotation/origin fixes, sheet-ID + title-block parser, CLI skeleton, caching,
CI running the coordinate tests. *Exit: `l2c run CLP` emits an empty but
schema-valid JSON and a PDF with the correct sheet inventory.*

**Phase 1 — Columns end-to-end (next ~25%).** Grid axes, symbol detection + scale
calibration, Hungarian callout association, column parser, level normalisation,
matcher, comparator, report sections. *Exit: the S-502 / K-6 row from the answer key
is detected by the pipeline, not by hand.* **This is the single most important
milestone — hit it before touching anything else.**

**Phase 2 — Glyph decoder + dialect layer (next ~20%).** Template bank across the five
base-14 fonts, classifier, the five §5.3 rules, **the §5.8 lexicon-constrained decode
(highest priority — it is the unimplemented piece that protects the bar designator)**,
run chaining and orientation, confidence propagation, the §5.9 plan-anchored
verification loop, the per-file label lexicon (§5.10), unit auto-detection, and the
§5.11 tier selector with tiers 1–2 live and 3–4 stubbed. Fixture tests per dialect.
*Exit: WP2 and EspCa3B columns extract and compare — i.e. 196 previously unreadable
pages come online — and a deliberately mangled dialect fixture still parses.*

**Phase 3 — Remaining element types (next ~25%).** Walls, footings, radier, beams,
then slabs. Each lands with its own golden tests. *Exit: all five types produce
records on all four projects.*

**Phase 4 — Dashboard and hardening (final ~15%).** Revision resolver, anomaly
detector, diagnostics appendix, annotated crops, README limitations. The dashboard
lands in two passes: a **thin vertical slice in Phase 1** (upload → run → findings
table → download JSON + PDF, no charts) so there is always something demonstrable,
then the full §9.3 treatment here — intake override table, the §5.16 pre-flight
declaration form, progress reporting, hero +
KPI row, emphasis chart, confidence histogram and threshold, detail panel, triage
store, ZIP bundle, demo mode. *Exit: `l2c run <unseen> --report` from a cold cache in
under 5 minutes, and the same project driven end-to-end through the dashboard,
both rehearsed twice.*

> Build the thin slice early. A dashboard started in the last phase is the classic
> hackathon failure: the pipeline works, nobody can see it, and the demo is a terminal.

**Parallelisation if you have 3–4 people:** one on geometry/grid/symbols, one on the
glyph decoder (it is cleanly separable behind the stage-3 token interface), one on
match/compare/report, one on element-type parsers + tests. The stage-3 token contract
and the canonical record are the two interfaces to agree on in hour one, in writing,
before anyone opens an editor.

---

## 13. Compliance checklist

| Requirement | How this plan satisfies it |
|---|---|
| Python core | Yes |
| Schema-conformant JSON | Pydantic-validated, `validate` subcommand |
| PDF report | ReportLab, per-sheet |
| No cloud / external AI APIs at runtime | Entire pipeline is local; **no model API is called**; no VLM, no hosted OCR |
| No commercially licensed software | PyMuPDF (AGPL), numpy/pillow/pydantic/reportlab/streamlit — all OSS |
| Documents not shared publicly | Repo contains no drawings; corpus path is a CLI argument; `out/` git-ignored; add the corpus glob to `.gitignore` on day one |
| Data deleted after the event | Add a documented `scripts/purge` step and run it; include the cache directory |

> **One compliance item needs your decision.** PyMuPDF is **AGPL-3.0**. It is
> open-source and the jury can run it, so it satisfies the stated restriction
> ("no commercially licensed software the jury could not run"). But AGPL is viral
> for network-served software. If L2C ever wants to host this internally as a web
> service, that matters. `pypdfium2` (BSD/Apache) is the usual alternative — but note
> it does **not** expose `get_drawings()`-equivalent vector path data, which §6.2
> depends on absolutely, so it is not a drop-in. My recommendation: use PyMuPDF,
> and state the licence explicitly in the README so L2C can make an informed choice.
> Mentioning this unprompted is also a credibility win with an engineering jury.

---

## 14. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Level mapping off by one storey** | Medium | **Severe** — hundreds of false findings | Calibrate on CLP's 6 known rows; `test_no_false_storm`; per-bucket join-rate diagnostic |
| **Jury project's DA are true scans, not outlines** | Low–Medium | High — decoder useless | The §5.11 reading ladder: tier 3 OCR behind an interface, tier 4 human-in-the-loop. Note 100% of WP2+EspCa3B and 75% of LIGREP DA are outlined, so outlines are the norm in this corpus |
| **Jury project's DA use an unseen dialect** | **High** | High | Measured: 3 dialects in 4 projects (§5.6). Plan-anchored constrained verification (§5.9) + learned per-file label lexicon (§5.10). Never hardcode DA keywords |
| **Jury project is imperial when CLP-tuned code assumes metric (or vice versa)** | Medium | High | Auto-detect unit system per project (§5.6); CLP is the *only* imperial project, and it is the only one with an answer key — do not calibrate units on it |
| `M`→`H` or similar bar-designator misread | **Measured at 83% on EspCa3B** | **Severe** — corrupts the key field | Lexicon-constrained decode (§5.8/§5.14) — the keystone of Phase 2, not a refinement; `test_glyph_decoder` asserts `H` is unemittable in a designator slot |
| **LLM asked to map DA values onto expected plan values** | High if unguarded | **Severe** — silently destroys recall | Hard boundary (§5.13): model parses blind into a record, code compares. DA parser has no access to per-cell plan values; enforced by a test |
| LLM called per record rather than per dialect | Medium | Medium — intractable runtime | One prompt per DA file to learn the lexicon, then apply deterministically (§5.10) |
| DA column schedules have no grid locator (EspCa3B, WP2) | **Measured** | Medium | Attribute-key binding on (dimensions, elevation, mark) — never on the rebar values under test (§5.15) |
| **Grid axis convention hardcoded from CLP** | **High** — CLP is transposed vs WP2/EspCa3B | **Severe** — every locator transposed, nothing joins | Auto-detect per project (§5.15b); per-project test fixture; stable `<letter>-<number>` output format |
| Round letters (`O`,`D`,`R`) detected as grid bubbles | **Measured** | Medium — phantom axes | Diameter ≥30 pt, stroked-not-filled, contained glyph materially smaller (§5.15) |
| Double-stroked glyph outlines double every character | **Measured** (`BB` for `B`) | Medium | Dedupe glyph paths by bbox within ~0.5 pt — affects the main decoder too |
| Slab extraction does not converge | Medium | Medium — 10 pts robustness | Ship a low-confidence candidate flagger rather than nothing; be explicit in the README |
| Revision ambiguity misread | Medium | Medium | Revision resolver + ask organizers (§15) |
| Grid labels absent/different on some sheets | Medium | Medium | Fall back to symbol-cluster ordering and emit `element` as a page-local index with low confidence, never crash |
| Sheet-ID extraction failures (~5% measured) | High | Low–Medium | Explicit `unknown` state; widened-window fallback; reconcile with page order |
| Near-duplicate sheets inflate counts | Medium | Low | Content hashing; dedupe diagnostics |
| Over-scoping, nothing runs at the deadline | Medium | **Severe** | Phase gating above; a complete narrow pipeline always beats five half-built extractors |
| Dashboard left to the last phase, demo is a terminal | Medium | High | Thin vertical slice in Phase 1 (§12) |
| Live demo fails on a cold/slow read | Low–Medium | High | Demo-mode cache toggle (§9.3.2), rehearsed twice |
| Upload cap rejects the jury's project | Medium | Medium | `maxUploadSize = 1024`; directory-path intake as the primary route (§9.3.1) |
| Misclassified intake produces an empty report on stage | Medium | High | Mandatory intake confirmation table with per-file overrides (§9.3.1) |

---

## 15. Questions — for you and for the organizers

### For you (please answer, two matter for the plan)

1. **Your pasted additional info did not reach me.** Your message shows
   `[Pasted text #1 +8 lines]` but the content was not included in what I received.
   Please paste it again — it may change this plan and I would rather revise than
   guess.
2. **Event length and team size?** The Phase percentages in §12 are relative; I will
   convert them to real hours and a per-person split once I know.
3. **Hardware?** If there is a GPU I would still not use a VLM for extraction, but it
   would change the fallback story in §14.

### For the organizers (ask in hour one — these change architecture)

1. **The `S-050 / J-10.8` row lists identical values on both sides**
   (`RANG 2: 25M@11"` vs `RANG 2: 25M@11"`). On that sheet every other RANG 2 callout
   reads `30M@11"`, so this looks like an error **in the engineer's own plan** that the
   fabricator faithfully copied. Is internal-consistency checking in scope? If yes, the
   data model needs a third comparison axis and §8.3's anomaly detector is promoted from
   a recall channel to a primary detector.
2. **What does `Partie 1/2/3` mean** in `CLP/DA/Colonnes/`? We measured that Partie 2
   and 3 cover the same grid cells with 11/46 rows differing, and only Partie 3 carries
   revision dates. Are these revisions (use the latest) or scope splits (use all)?
   This changes the false-positive rate materially.
3. **Is the jury project's DA set also outline-converted text, or true scans?** And
   **which fabricator produced them** — do they share a dialect with any of CLP, WP2,
   LIGREP or EspCa3B? We measured three distinct DA dialects across the four dev
   projects (§5.6), so this single answer could save a day of adaptation.
4. **Is the jury project imperial or metric?** CLP is imperial; the other three are
   metric. Since CLP is also the only project with an answer key, there is a real risk
   of calibrating on the exception.
5. **Is recall/precision computed per non-conformity row, or per (sheet, locator) pair?**
   It determines whether reporting the same discrepancy on two DA revisions counts twice.
6. **Confirm the `n(m)` notation on S-600** (e.g. `16(8)` → `20(8)`): count of shear
   studs at that spacing, or rails × studs?
7. **Level convention:** does "PLAN DES COLONNES - NIVEAU 2" describe the column segment
   *below* or *above* the NIVEAU 2 slab? One sentence from them saves hours.

---

## Appendix A — Measurement log

All measured on this corpus during planning, 2026-10-03.

**Corpus:** 142 PDFs / 611 drawing pages / 290 MB. Pages by rotation:
`/Rotate 0` 216, `90` 406, `180` 1, `270` 1. Non-zero MediaBox origin: 118/624 (19%).
Page sizes: 2592×1728 (447), 3455.4×2590.8 (68), 3456×2592 (50), and 5 smaller variants.
Fonts on text pages: Garamond, Garamond-Bold, Times-Roman, ArialMT, Helvetica.
Optional content groups: present on 34 files, absent on the rest — not usable as a
reliable annotation layer.

**Text layer:** 271 pages with text, 340 with zero characters, 0 pages in between,
0 mixed files. Plans 190/190 text. DA: CLP 34/34, LIGREP 47/191, WP2 0/72, EspCa3B 0/124.

**Outlined pages:** 0 `/Font` resources, 0 `BT`/`ET`/`Tj`/`TJ` operators, 12 `Do`
XObject invocations, image coverage 1.6% of area. One filled path per glyph;
median glyph 10.2 × 16.7 pt; 831–1,300 glyph paths per column sheet,
2,700–6,000 per slab sheet.

**Glyph clustering:** 1,846 glyphs → 55 clusters at cosine < 0.12 (16×16 normalised),
3 singletons, 98.1% in clusters ≥ 5, 100% in top 60. Exact path-hash alternative:
860 signatures / 1,013 glyphs, 88.4% singletons, no saturation over 34 pages
(44,278 signatures / 65,627 instances) — rejected.

**Template decoding:** base-14 `helv`+`hebo`, 20×20, cosine. Per-line confidence
0.95–0.99 on real annotation, 0.805 on a misread leader arrowhead. All digits and
uppercase correct after the case/gap fixes; residual errors confined to
`.`/`-`/`=`/`"` and x-height letters.

**Orientation (outlined DA page):** 66.2% of glyphs have a horizontal right-neighbour,
3.5% a vertical below-neighbour, 30.2% isolated/line-end; 0% aspect ratio > 1.5,
median w/h 0.54. CLP DA text layer: 601 horizontal spans vs 18 vertical.

**Grid (CLP S-502):** rows A–O (15) paired at x ≈ 227 / 2507; columns 22 axes paired at
y ≈ 219 / 2320, including 3.8, 5.6, 6.5, 7.3, 8.2, 9.8, 10.8, 12.7, 14.4. x decreases
as grid number increases.

**Symbols and scale (CLP S-502):** 67 filled rects of 12×18 pt ↔ 67 callouts
`COL. 16"x24"`; 3 rects of 7×22 pt ↔ 3 callouts `COL. 10"x30"`; 70 `COL.` callouts
total. Derived scale 0.75 pt/inch = 1/8" = 1'-0".

**Outlier distributions:** S-502 `ARM.` = {`4-25M`: 64, `4-35M`: 1, `6-20M`: 3,
`8-20M`: 2}. S-504 ties = {`10M@6"`: 55, `10M@4"`: 5, `10M@8"`: 3, `10M@12"`: 1}.
In both, the answer-key discrepancy is the singleton.

**Revisions:** `CLP_COLONNES Partie 2` vs `Partie 3`, page 4: 46 comparable rows,
**11 differ**; Partie 3 carries dates up to 29/05/2026, Partie 2 carries none.

**Library benchmark (CLP S-502):** PyMuPDF 879 words in 0.009 s, 6,442 paths in 0.04 s.
pdfplumber 879 words in 0.49 s (58×), geometry in 0.49 s (12.5×); token multiset
identical; coordinate offset dx = −1727.70, dy = +1298.71.

**Cross-provider generalization:** plan grammar identical in all four projects
(`ARM.:` 481/998/724/886, `LIG.:` 480/1067/761/965, `COL.` 396/1346/678/733 for
CLP/WP2/LIGREP/EspCa3B); unit system CLP imperial (716 imperial vs 177 metric spacing
tokens), other three metric (926–1441 metric vs 14–27 imperial). DA grammar differs:
CLP `VERT:`/`ÉTRI:`/`GOUJ`, LIGREP `VERT:`/`DIM:`/`GOUJ` with **zero** `ÉTRI:`,
EspCa3B `DIM:`/`ET.:`/`VERT:`. Glyph geometry consistent across providers
(one path per glyph; median height 19.7 pt WP2, 14.8 pt LIGREP, 8.5 pt EspCa3B,
6.2 pt CLP; median 13–18 path items). Decoder run untuned on EspCa3B with a 5-font
base-14 template bank (375 templates, 74 chars): **all digits correct**, confidence
0.89–0.98, with `M`→`H` and `:`→`I` substitutions.

**Designator error rate (the keystone measurement):** full-page decode of
`EspCa3B_COLONNES NIV 10.pdf` — 2,618 glyphs, 170 text lines — yielded 6 correct
`<nn>M` against 30 wrong `<nn>H`, an **83% error rate on the bar designator**.
Correctly decoded on the same page: `400x750`, `400x600`, `400x800`, `DIAM500`,
`niv. 47855`, `10T3267`, `25Z2711`, `3950`. Grid-label patterns found: **0**.
Identifier families present: `DIM` section size ×21, bar designators ×36,
fabrication marks ×7.

**Grid bubbles on DA sheets:** curve-dominated near-square path counts — CLP Dalles 122,
Fondations 97, Poutres 48; WP2 Dalles 36, Murs 22, Semelles 21; LIGREP Dalles 38,
Fondations 23, Colonnes 21, Refends 15; EspCa3B Fondations 54, Dalles 33, Colonnes 29,
Murs 22. Bubble decode on `EspCa3B_DALLE NIV 10.pdf`: recovered `B C D E F` at
cx = 190/473/755/1037/1319 and `2 3 4 5` at cy = 194/476/758/1040 — uniform 282 pt grid,
confidence 0.73–0.96. Artefacts: real bubbles 49 pt vs stray round letters 13–20 pt;
every bubble duplicated at two diameters; every glyph outline double-stroked.

**Axis convention (plan sheets):** CLP letters=rows (13 horizontal pairs) /
numbers=columns (21 vertical pairs); WP2 letters=columns (18) / numbers=rows (12);
LIGREP numbers=columns (23); EspCa3B numbers=rows (6). **CLP is transposed relative to
WP2 and EspCa3B**, and EspCa3B's plan and DA agree with each other.

**DA locator regimes:** CLP columns carry explicit grid labels as text (16 per page;
`K-6` at x = 730.2); EspCa3B columns carry none (0 grid-label matches on a full-page
decode) and are identified by section dimensions + `niv.` elevation + mark; DA
plan-view sheets carry gridlines (21–32 vertical, 15–22 horizontal on CLP DA). The
plan-sheet margin heuristic for bubbles recovers only spurious single letters
(`I`, `B`, `X`) on DA sheets.

**Throughput:** 0.024 s/page text, 0.212 s/page `get_drawings`, ~0.13 s/page 400 dpi
render. Full 611-page corpus ≈ 4–5 min single-threaded.
