# DA_PLAN — the "Dessins d'atelier" tab

The dashboard today reads the **L2C plan** and shows its elements. This plan adds a
second, parallel section that does the same for the **shop drawings (DA)**: read every
DA file of the selected project, extract its reinforcement elements, and present them
with the same overview / table / diagnostics / downloads layout.

Design rationale and the measurements behind it live in [PLAN.md](PLAN.md) §5
(reading the shop drawings). This file is the **build plan and progress tracker**.

---

## Status

The **DA UI now uses the five CLP image parsers** through `l2c.da.dashboard`:
columns Partie 3 plus Partie 1's fifth-page basement supplement, **all six slab PDFs** in `DA/Dalles`, isolated footings FND and
beams and the complete `DA/Fondations/CLP_RADIERS.pdf`. The four fixed paths and slab folder are configured relative to the
selected CLP folder. Columns now read all pages of Partie 3, with the strip limit
disabled; the other readers retain their existing page selection. Deduplicated Appendix-A records
populate all tables and the `CLP_elements_atelier.json` download. Selected file
timestamps/sizes, slab folder membership and parser version control the DA cache;
the current folder supplies eleven files, with fourteen selected pages: the four-page
current column schedule and its separate basement supplement. Earlier repeated
column sheets stay superseded. Completed older column-cell checkpoints are upgraded
by re-reading only cells whose floor boundaries changed; other DA checkpoints are reused. Its regeneration button
reruns these readers without invalidating the plan cache. The radier parser is now connected, with page checkpoints and the same final
plan/DA format: foundation level, rang, horizontal/vertical role, diameter/spacing.
Fabrication counts and marks remain in debug evidence; count-only requirements are preserved. Other projects have no connected
DA readers yet. The runner accepts explicit input paths for future UI uploads.

All five types now use `l2c.record_formats` for final storage, UI and comparison.
Plan/DA records share role vocabulary/order, level aliases and reinforcement field
meanings. Fabrication marks and spaced-beam piece counts stay in debug evidence;
explicit lengths and independent directions/zones remain preserved. Existing typed
job/file caches upgrade in place without PDF/OCR. Unknown slab layer/direction and
partial directional reads stay flagged for review rather than being guessed.

DA generation now runs through `l2c.da.jobs` in an independent Python worker,
outside Streamlit page execution. Switching tabs/sections or refreshing reattaches
to the same active job and completed result. Requests, process identity, progress
and results persist under `.cache/da_jobs`, so code reloads and server restarts
also recover existing workers. A filesystem lock prevents duplicate starts across
managers. Completed PDFs are checkpointed individually: an interrupted worker's
explicit retry resumes unfinished files instead of reparsing completed ones. Progress is
saved after every file and polled once per second, with saved/reused counts.
New jobs reuse valid checkpoints by default; only an explicit full regeneration
bypasses them. Failed runs offer **Reprendre la génération** and a recoverable-file
count. An abrupt process-exit test verifies that the next worker reuses the saved
PDF after a manager reload. Active work survives cache clearing; regeneration is
disabled until completion. Failures remain visible until explicit retry. Separate
processes isolate PDF/OCR state from original-plan parsing during navigation.

The **Comparaison** section now reads loaded plan/DA results without submitting
work. It highlights changed armatures, one-sided elements and ambiguous readings;
filters, source evidence and `CLP_comparaison.json` support parser refinement.
Groups use type/level/slab-layer/identifier. Unsupported coverage stays listed
separately; beam agreement still requires spatial review. Verification using the
saved 75-plan/80-DA footing results gives 67 identical groups, the genuine L-13
9-versus-11 difference, and 19 one-sided coordinate groups (including seven known
coordinate alias pairs). No new OCR was needed for this comparison.

Current standalone beam work: `poutre_clp.py` reads one CLP beam DA PDF, last page
only, through pixels. Circle labels locate each titled elevation; small squares
are stirrup-zone symbols. Shared notes give group totals, so resolve local zone
counts against their observed shape/spacing definitions. Keep longitudinal,
skin and stirrup roles and source evidence; do not invent missing quantities.
Original S-300 now exports 187 callouts in 27 beams after fixing the right-margin
cutoff and retaining numeric/letter axis positions. The DA sheet has 20 beams;
both sides now save one beam record with its armature list (27/187 plan, 20/162
DA). The shared representation preserves per-annotation roles and coordinates;
existing result/checkpoint caches upgrade without OCR or extraction invalidation.
The seven omitted cantilever elevations stay flagged. Review JSON files and the
coverage-comparison limits are documented in `src/l2c/da/parsers/README.md`.
Fresh last-page verification: 20 DA beams, 162 reinforcement readings, all 162
post-OCR source checks passing; 150 of 152 original specifications found in the
shared beams. P112/P116 spatial matches, nine unmatched DA annotations and seven
missing beam elevations remain review findings. Generic zone recovery resolves
38 symbols; the full run takes approximately four minutes. Forty-six focused
tests pass and the original answer key remains 6/6.

| Phase | What | Status | Result |
|---|---|---|---|
| 0 | Tab shell, shared components, DA inventory | ✅ done | both sections render from `app/views.py`; 137 files / 421 pages inventoried with type + tier |
| 1 | Text-layer DA reader (CLP + LIGREP columns, 81 pages) | ⚠️ done, gaps | CLP 7,751 records (5 types), LIGREP 324 columns; 3/6 answer-key DA values found, the other 3 explained |
| 2 | **AI reader**: local OCR model on rendered line crops (340 outlined pages, and scans) | 🟡 in progress | template decoder kept as a measured baseline |
| 3 | DA locators on decoded pages (bubbles, schedules) | ⬜ todo | |
| 4 | Other fabricators' dialects (WP2, LIGREP, EspCa3B) | ⬜ todo | |
| 5 | **AI matching** plan ↔ DA: Jev-style choice with probabilities, local LLM; code decides the verdict | ⬜ todo | |
| 6 | **AI report**: local LLM writes each finding's explanation from code-computed facts | ⬜ todo | |
| 7 | Downloads parity, tests, packaging of models | ⬜ todo | |

Legend: ⬜ todo · 🟡 in progress · ✅ done · ⚠️ done with known gaps

---

## Ground rules (from the consignes and PLAN.md — not negotiable)

1. **Local only.** No cloud service, no external AI API. Every reader runs on the laptop.
2. **The DA reader parses blind.** It never sees the plan's expected value for a cell
   (PLAN §5.13). It emits records; comparison is plain code, later, in a separate stage.
   Asserted by a test: the DA package does not import the plan parsers' outputs.
3. **Closed vocabulary.** A bar designator is one of `10M 15M 20M 25M 30M 35M 45M 55M`.
   An `M`→`H` misread must be unrepresentable (the `Armature` validator already enforces it).
4. **Unread is reported, never hidden.** A page we cannot read is listed with its reason
   and tier; it never silently counts as zero elements.
5. **Same contract as the plan side.** DA records are Appendix-A `ElementRecord`s with
   `source="atelier"`, so the later comparison joins like with like.
6. **AI where the variability is, code where correctness is checkable.** Plan extraction,
   coordinates and grid geometry stay deterministic (standardised, 6/6 answer key). AI
   reads the DA (whose appearance changes per fabricator), matches records, and writes
   the report. **The verdict "same / different" is always plain code.**
7. **Models: local, open licence, CPU-sized.** Weights downloaded once, pinned by version
   and checksum, run offline. Budget measured on the dev laptop (i7-12700H, 15 GB RAM,
   ~4 GB free, no NVIDIA GPU): **≤ 4 GB RAM per model**, no CUDA. Inference deterministic
   (greedy / temperature 0, fixed seed) so two runs give the same JSON.
8. **Chosen by measurement, not reputation.** Every model is scored on the same ground
   truth before it is adopted (bake-off, Phase 2); the numbers go in the progress log.

---

## Review policy — prefer detection over suppression

The user explicitly prefers a defensive approach: flag more potential discrepancies
and let a human dismiss false positives. Missing extracted plan counterparts,
unresolved specifications, ambiguous matches and references delegated to other
drawings remain flagged for human review. Context qualifies the finding; it does
not suppress it or imply conformity. Keep confirmed reinforcement differences and
potential discrepancies distinguishable, with their source evidence.

CLP K-9, K-10, J-9 and J-10 therefore remain **discrepancies requiring human review**:
their isolated-footing reinforcement has no identified counterpart in the original
plan set. The crane-base subcontractor reference is explanatory context only.
L-13 remains a confirmed reinforcement difference. A-6 remains a review finding
with an identified original-parser omission. Suppress only proven duplicate
observations of the same finding; never hide uncertainty to reduce the issue count.

## What the DA corpus is (measured 2026-10-03)

| Project | Folders (fabricator's own) | Files | Pages | Text layer | Outlined glyphs |
|---|---|---|---|---|---|
| CLP | Colonnes, Dalles, Fondations, Poutres | 12 | 34 | **34** | 0 |
| WP2 | Colonnes, Dalles, Murs refends, Semelles et radiers | 29 | 72 | 0 | **72** |
| LIGREP | Colonnes, Dalles, Fondations, Poutres, Refends | 42 | 191 | **47** | 144 |
| EspCa3B | Colonnes, Dalles, Fondations, Murs cisaillements | 54 | 124 | 0 | **124** |
| **Total** | | **137** | **421** | **81** | **340** |

CLP's DA is fully text and covers four element types, and it is the project with the
answer key — so it is the first end-to-end target. CLP grammar, sampled:

| Folder | Lines that carry the information |
|---|---|
| Colonnes | explicit grid label `D-2`, `16"X24"`, `VERT: 4 25M 25Z12-01`, `ÉTRI: 6 10M 10ET13X21 @6"`, `NIVEAU 2` |
| Dalles | `2 15M 17-06` (count size length), `@17"`, `3 15J19-06` (count mark), `RANG 2`, grid bubbles |
| Fondations | `EMPATTEMENT TYPE-B:`, `LONG: 11 25M 25U12-00`, `TRAN: …`, `GOUJ: …`, radier `TRAN: 44 25M 25RU24-10 @8"BAS` |
| Poutres | `POUTRE P103`, `P108 - 16" x 40 3/8"`, `2 25M 19-03`, `15 10M 10TT16X35 @18"` |

The fabricator's title block is not L2C's: the plan-side sheet-number reader returns
`UNKNOWN` on most DA pages, so DA sheet identity needs its own reader (Phase 1).

---

## UI design — same layout, shared components

- **Top-level switch** under the title: `Plan L2C` | `Dessins d'atelier`. The sidebar
  project picker is shared: one project is chosen once, both sections show it.
- **Same five sub-tabs** in both sections, built from the same functions:

  | Sub-tab | Plan L2C | Dessins d'atelier |
  |---|---|---|
  | Vue d'ensemble | records per sheet, confidence histogram | records per **file**, confidence histogram, **reading tier** per page |
  | Éléments | filterable table | same table, plus `repère` (bar mark) and `fichier` filter |
  | Feuillets / Fichiers | one row per sheet | one row per **DA file / page**: type, tier, records, status, reason |
  | Diagnostics | warnings, unit system | warnings, **dialect** (labels seen), unread pages |
  | Téléchargements | `elements_plan.json`, CSV, ZIP | `elements_atelier.json`, CSV, ZIP |

- **Reuse:** the view functions move from `app/streamlit_app.py` into `app/views.py`
  and take a small `Dataset` spec (labels, the "unit" column — sheet vs file — and the
  JSON file name). Both sections call the same code; nothing is copy-pasted.
- KPI row: same hero number + metric strip; DA adds "pages lues / pages totales".

---

## Phases

### Current work — CLP parsers by element type

The current implementation uses image-only local OCR in modules under
`src/l2c/da/parsers/`, connected to the dashboard through `l2c.da.dashboard`.
The earlier vector/text readers in `da.pipeline` remain a historical CLI baseline;
the phase descriptions below document that earlier implementation.

- `colonne_clp.py`: ruled schedule cells, bottom grid labels and storey strips.
- `semelle_clp.py`: isolated grey footing squares and hexagonal type markers,
  linked to the drawing's **Nomenclature des semelles isolées** schedule. Reads
  **one PDF, its last page only**; current verification uses
  `CLP/DA/Fondations/CLP_SEMELLES FND.pdf`, page 1/1. The clean final review file is
  `out/semelle_clp_output.json`, with coordinates, types and two separate directions
  formatted as `7-25M · 7-25M`. Supports fractional axes, the single-strip inset,
  lower-left markers, long pedestal segments and partly erased hexagons.
  Verification: 85 annotations / 170 directional entries; all 85 marker-sized source
  type labels were located, and all 14 schedule quantity/diameter cells matched.
  Extraction took 35.56 seconds during the final concurrent test run. The type and
  schedule checks do not independently validate grid-coordinate assignment; the
  annotated last-page PDF supports that review. 32 focused tests passed, including
  an image-only read with PDF text/vector extraction forbidden. Diagnostics retain
  all 93 candidates, including eight excluded non-footing/unread shapes.
  Cross-reference with the 75 original S-100 parser records: five annotations repeat
  main-view footings in the inset (including inset Q-5 = main G-5), leaving 80 distinct
  locations. Seven further raw coordinate differences align to the same physical
  markers. Five locations are extra relative to the original parser: K-9, K-10,
  J-9, J-10 and A-6. The first four replace the original crane-base radier's
  representation; A-6 is present in the original drawing but is wrongly filtered
  as a grid bubble. All 75 original records have counterparts; 74/75 reinforcement
  pairs agree, with L-13 reading 9-25M per direction in the original output versus
  11-25M per direction in the DA. Full evidence: `out/semelle_clp_comparison.json`,
  `.md` and an annotated last-page `.pdf`. Raw parser outputs remain unchanged.
- `dalle_clp.py`: **last page only for each slab PDF**, as requested; circular grid
  labels, independent main/inset grids, grey support rectangles and raised callouts.
  Checks every labelled intersection, with crops anchored to grid coordinates rather
  than wall centres. Full scans join overlapping text detections before recognition.
  Accepts a PDF or a folder; development verification currently runs **Niveau 3 only,
  last page (3/3)** to keep iteration short. The final review output is
  `out/dalle_clp_output.json`; its sanitized JSON format and architecture are documented in
  [the parsers README](src/l2c/da/parsers/README.md). Also writes Appendix-A JSON, diagnostics including unread
  supports and partial marks/lengths, count–diameter summaries (`22-15M`), and an
  optional annotated last-page PDF. Detects Niveau 5's dashed column outlines inside
  grey backgrounds and accommodates RDC's stepped row-label strip.
  The DA read is blind; `--check` consults the hidden text only for validation.
  The J-15 / Niveau 3 integrity callout reads both `NUM` and `ALP`, each
  `3 15M 11-03`. Dense regions and off-grid rectangles remain explicit limitations.
  Current verification: Niveau 3 page 3/3, 380 labelled intersections checked,
  75 records / 142 reinforcement entries in 218.8 seconds; 24 focused tests passed.
  Local quantity/diameter checks match at 61/75 populated locations, all-field
  checks at 56/75. Two empty reads have bars in their compared crops; 21 locations
  are flagged for review in total. The full six-file run was stopped at the user's
  request to iterate on one file.

Run examples and OCR dependencies are in README's “CLP slab image parser” section.

Original-plan integrity is now extracted by `parse/slab_integrity.py`: the source
document's detail table defines the circled A–I types, with two separate directional
entries. On CLP S-601 and S-602, 70 integrity labels per sheet resolve without missing
definitions. Numeric slab annotations remain separate and retain parenthesized
counts. The dashboard can regenerate either section with its own cache button.
The final standalone DA review artifact is sanitized JSON; blank/unread rows stay
in diagnostics, and optional unknown review fields are omitted.
All three current standalone parsers now share `parsers/output.py` for removing
identical repeated observations from final exports. Footings export 80 unique
records / 160 directional entries; their five repeated inset annotations, including
Q-5 = main G-5, remain in diagnostics with axis-alignment evidence. Columns deduplicate
per coordinate/storey; slabs per coordinate/level/layer with independent viewports
unless observed axes prove correspondence. Conflicting values survive with
`duplicate_conflict=true` and a review reason. The regenerated Niveau 3 slab output
has 75 rows, no identical repeats, and three repeated locations with conflicting
OCR reads flagged for review. L-13 remains a genuine original-plan/DA discrepancy.
The original S-100 crane-base note explicitly delegates its design to subcontractor
DA; other original-plan crane references concern temporary slab openings (detail
116). K/J-9/10 remain flagged as potential discrepancies requiring human review,
with that external crane-base reference attached as context. The reference does
not clear or suppress their flags under the user's defensive review policy.

### Phase 0 — Tab shell, shared components, DA inventory

- [x] Move view functions to `app/views.py`, parameterised by a `Dataset` spec
- [x] Top-level `Plan L2C` / `Dessins d'atelier` switch; plan section unchanged in behaviour
- [x] `l2c.da.inventory`: walk `<project>/DA/**.pdf` → file, folder, element type
      (from the folder name: Colonnes→colonne, Dalles→dalle, Fondations / Semelles et
      radiers→semelle|radier, Poutres→poutre, Refends / Murs …→mur_refend), pages,
      reading tier per page (1 = text layer, 2 = outlined glyphs, 3 = raster, 4 = unread)
- [x] DA section shows the inventory (Pages tab + overview) even before any reader exists

**Exit:** the DA tab lists all 137 files / 421 pages with type and tier for the four
projects; `test_app` covers the new section; plan section tests unchanged.

### Phase 1 — Text-layer DA reader (tier 1)

Targets the 81 text pages (CLP 34, LIGREP 47) with the existing `PreparedPage`, so the
coordinate fixes are inherited.

- [x] DA sheet identity: `DESSIN NO.` when filled in — **blank on every CLP DA page** —
      else `<file stem> p<n>`; drawing title kept in diagnostics
- [x] Dialect lexicon counted per page and shown in Diagnostics (CLP: `ÉTRI` 1429,
      `VERT` 842, `NUM` 460, `ALP` 447, `GOUJ` 349 …; LIGREP: `VERT`, `ET`, `GOUJ`, `A.ET`)
- [x] Bar-line grammar shared by all types (`l2c.da.common`): label? count size mark?
      @spacing? suffix; `repere` = mark, `longueur_mm` from `17-06`; `3 15J19-06` reads
      the size from the mark (flagged, lower confidence); colon-less `NUM.` accepted
- [x] Locators: grid-label strips (CLP columns), `COL : DD - 12` panels (LIGREP columns),
      drawn grid lines (plan-view DA), beam titles + drawn spans (beams)
- [x] Parsers: columns (2 layouts), slabs, footings, radier, beams — walls: no text-layer
      wall DA exists in the corpus (CLP has no wall folder; the others are outlined)
- [x] Records `source="atelier"`, Appendix-A valid, unique ids, storey from the strip
      (CLP) or the file name (LIGREP `NIV-2@3`)

**Exit:** the DA tab shows CLP's DA elements; each answer-key row has a DA record at its
locator carrying the key's `Dessin d'atelier` value (`4-25M` at K-6, `10M@6"` at I-13,
`11-25M` at L-13, `20(8)` at J-15, `RANG 2: 25M@11"` at J-10.8, `6-30M` on wall B);
LIGREP column schedules read.

**Result (2026-10-03):**

| Answer-key row | DA value | Found? | Why |
|---|---|---|---|
| S-502 K-6 | `4-25M` | ✅ | `VERT: 4 25M 25Z11-04`, CLP_COLONNES Partie 2/3, segment RDC @ 2 |
| S-504 I-13 | `10M@6"` | ✅ | `ÉTRI: … 10M 10ET13X21 @6"` on every I-13 segment |
| S-100 L-13 | `11-25M` | ✅ | `LONG: 11 25M 25U12-00`, CLP_SEMELLES FND |
| S-400 élévation B | `6-30M` | ❌ | **no wall DA for CLP in the corpus** — no `6 30M` anywhere in its DA |
| S-603 J-15 | `20(8)` | ❌ | the DA lists bar **bands** (`14 15M 12-00`, `15 15M 12-00` …), not `n(m)`; the total is an aggregation for the comparison stage |
| S-050 J-10.8 | `RANG 2: 25M@11"` | ❌ | the fabricator's radier sheet has **no 10.8 grid line and no 25M@11" bar** (every `@11"` is 30M) — needs a human look |

Open points carried forward:
- **Revisions.** CLP's column schedule exists in three parts that are successive releases
  (Partie 3 changes `25L9-05`→`25L10-05`, stirrups 19→20, and is the only one with dated
  notes). All are read and kept; choosing which wins is a comparison-stage decision
  (PLAN §5.16 pre-flight).
- **Two blocks on one cell.** A footing intersection can receive a neighbour's block
  (L-13 also gets a `17-25M` block); the comparison must use the block nearest the
  footing's own outline, not just the cell name.
- **Run time.** CLP's DA takes ~47 s (grid building on heavy slab pages); cached in the app.

### Phase 2 — AI reader for the DA (vision OCR)

**Why change course.** A first decoder matched each vector glyph against font templates
(PLAN §5.2). It works on the fonts it was tuned on, but every gain came from rules about
how one fabricator's CAD draws text (CLP draws Times condensed to 36 % width) — the
next fabricator breaks it. Reading **rendered pixels** with an OCR model removes that
dependency: text layer, outlined glyphs or a scan all become the same image.

**Design — split so it fits a laptop CPU.** Full-page OCR is too slow: a CLP sheet at the
resolution 3-pt text needs is ~21,000 × 14,000 px (~300 tiles). Instead:
1. **Where the text is** — from geometry when the page is vector (glyphs are small filled
   paths in every fabricator's PDF; grouping them into lines is font-agnostic); from an
   OCR *detection* model when the page is a scan.
2. **What it says** — an OCR *recognition* model on a high-resolution crop of each line
   (a few hundred lines per page, ~10–50 ms each on CPU).
3. **Closed-vocabulary check** — a token in a bar-designator slot outside
   `10M…55M` is flagged (never silently "corrected" to a plan value).

**Ground truth for the bake-off (already built).** Text-layer pages re-exported with text
converted to outlines: the decoder sees outlined glyphs, the original text layer gives
the exact answer. Three fonts: CLP's fabricator, LIGREP's fabricator, L2C's plans.

- [x] Ground-truth harness (outlined copies + word / line / bar-line parity metrics)
- [x] Baseline: template glyph decoder (`l2c.da.glyphs`, `l2c.da.decode`) measured
- [ ] Line detection from vector geometry, crops rendered at fixed cap height
- [ ] Bake-off on recognition: PaddleOCR (PP-OCR, Apache-2.0), docTR (Apache-2.0),
      TrOCR-small-printed (MIT); Florence-2 (MIT) on a sample for full-page reading
- [ ] Adopt the winner behind one interface (`read_lines(page) -> Line[]`), so Phase 1
      parsers run unchanged on decoded pages
- [ ] Scanned-page path: detection model + the same recogniser
- [ ] Per-line confidence carried to every record

**Exit:** on the ground truth, bar-line parity (same quantity, size, mark, spacing as
the text layer) ≥ 95 % on all three fonts; zero designators outside the closed set
emitted unflagged; WP2 / EspCa3B pages produce records.

### Phase 3 — Locators on decoded pages

- [ ] Grid lines + bubbles on decoded DA (the line-grid already handles patterned dashes)
- [ ] Axis convention per project (letters = rows on CLP, columns elsewhere)
- [ ] Schedule pages with no grid (WP2 / EspCa3B columns): bind on attributes —
      section dimensions, level, fabrication mark — `locator_kind="attribute"`

**Exit:** ≥ 95 % of decoded plan-view DA records carry a grid locator; schedule records
carry their binding attributes.

### Phase 4 — Other fabricators' dialects

- [ ] LIGREP (`DIM:`, `VERT:`, no `ÉTRI:`), EspCa3B (`ET.:`, `DIM: 400x750`, mm), WP2
- [ ] Unknown label → reported in Diagnostics, never silently dropped

**Exit:** every DA folder of the four projects yields records or an explicit reason.

### Phase 5 — AI matching, Jev-style (plan ↔ DA)

The pattern of TypeSafe's Jev (a decision model that **chooses among given options and
returns a probability for each**), rebuilt locally — Jev itself is API-only, which the
consignes forbid. A small local LLM (3–4B, quantised, llama.cpp) scores a fixed set of
options by likelihood; it never writes a value.

1. **Code proposes candidates** for each DA record: same element type, compatible
   locator and storey (e.g. `NIVEAU 2` plan sheet vs `RDC @ 2` DA segment).
2. **The model chooses**: "which plan element is this DA block? `K-6 RDC@2` / `K-6 2@3`
   / `K-7 RDC@2` / none" → probability per option. It also answers yes/no questions
   that rules handle badly (is this `TRAN` layer the plan's `RANG 2`? do these bands
   form the plan's `16(8)`?).
3. **Code decides the verdict** on the matched pair — `35M ≠ 25M` — and classifies
   (conforme / différent / manquant / ajouté).
4. **Probability = triage.** A confident match is automatic; a close call (e.g. 0.51 vs
   0.43) goes to the engineer's review queue in the dashboard.

Why this shape: a model asked "do these match?" tends to reconcile them, hiding the
discrepancy recall is scored on (PLAN §5.13). Choosing an *identity* from a list cannot
invent or soften a bar size.

- [ ] Model choice by measurement (licence checked at install: Qwen3-4B, Phi-4-mini,
      Llama 3.2 3B candidates), RAM ≤ 4 GB, deterministic
- [ ] Option scoring via log-likelihood of each option (one batched pass per question)
- [ ] Candidate generator + verdict + classification in code
- [ ] Measured on CLP's answer key: all 6 rows classified, with probabilities

**Exit:** the 6 CLP answer-key discrepancies are found as `différent`, each with a match
probability; no conforme verdict comes from the model.

### Phase 6 — AI report

- [ ] The local LLM writes each finding's explanation for the engineer, **from facts the
      code computed** (element, sheets, plan value, DA value, confidence) — it cannot
      change them; a check rejects any text that states a value not in the facts
- [ ] Executive summary of the run (counts by type and class, unread pages, limits)
- [ ] PDF report + `comparison.json`; the report tab in the dashboard

**Exit:** a report a reviewer can read cold, where every number traces to a record.

### Phase 7 — Downloads parity, tests, packaging

- [ ] `elements_atelier.json` + CSV + ZIP + manifest (models + versions + checksums,
      tiers, dialect, unread pages); `l2c run` writes it next to `elements_plan.json`
- [ ] `make models` downloads and verifies the weights; everything runs offline after
- [ ] Tests: census, answer-key DA values, blind-reader invariant, closed vocabulary,
      determinism (same JSON twice, models included)

### Out of scope here

Nothing of the core flow: matching, classification and the report are now Phases 5–6.

---

## Progress log

Newest first. Each entry: date, phase, what changed, what was measured.

- **2026-10-03** — **Plan revised: AI where the variability is.** Phases 2/5/6 now use local
  models: vision OCR for reading, a Jev-style local decision model for matching, a local
  LLM for the report (decided with the user). Jev itself (TypeSafe AI, Sept 2026) is
  API-only, so only its *pattern* is used; OpenJev has no licence and was rejected.
  Template glyph decoder kept as the baseline. Its ground-truth numbers (word-level not
  yet measured; per-glyph top-1): CLP fabricator 0.77–0.79, LIGREP fabricator 0.77,
  L2C plans 0.82 (0/O, case and multi-part characters dominate the errors); each gain
  came from a font-specific rule (width factors, condensed spacing) — the reason for
  the change of course.

- **2026-10-03** — **Phase 1 done with gaps** (see the result table under Phase 1).
  CLP DA: 31/34 pages read (3 are Partie 1 pages with no callouts), 7,751 records —
  colonne 896, dalle 6,457, semelle 143, radier 125, poutre 130 — 7,698 placed on the
  grid. LIGREP: 324 column records from 325 panels, each with verticals + ties.
  Fixes found on the way, all measured: (1) the fabricator draws grid lines as ONE
  segment with a dash pattern `[7.92 3 2.04 3]`, not the plan's pre-split dashes —
  the line-grid detector now accepts both (plan answer key still 6/6, plan beam counts
  unchanged); (2) a page's grid is chosen by explicit preference (own lines → file's
  grid if labels agree → own labels), because "most bars placed" always favours the
  label grid, which shifted a column of top bars from J-15 to J-14.4; (3) LIGREP panel
  ids `C-01` look like grid labels, so `COL :` panels take precedence; (4) beam titles
  are chosen by RELATIVE size (plan 18 pt, CLP fabricator 9 pt). `l2c truth` now also
  scores the DA side: 3/6, the other three explained.

- **2026-10-03** — **Phase 0 done.** `app/views.py` holds every component; both sections
  call `views.render(spec, result)` with a `Dataset` spec (labels, grouping column, JSON
  name). DA side returns the same `ProjectResult` (one `SheetReport` per DA page, with
  `fichier` and `tier`). Top-level switch is a `segmented_control`, so only the chosen
  section runs. Inventory: tiers match the census exactly (81 text / 340 glyphs); every
  file typed from its folder name (file name refines mixed foundation folders). Tier
  detection counts fill operators in the content stream: 25 s for all four projects
  vs ~100 s with `get_drawings()`. DA units come from DA text only (CLP: imperial,
  6144 vs 2506 tokens) — the reader stays blind to the plan. Tier palette: one blue
  ramp + gray (ordinal), validated adjacent ΔE 18.9 CVD / 19.2 normal.
- **2026-10-03** — Plan written. DA corpus census: 137 files, 421 pages, 81 text / 340
  outlined. CLP DA grammar sampled per folder.
