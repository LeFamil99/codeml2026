# DA parsers: strategy and architecture

This directory contains **one parser per element type and project/fabricator layout**.
The current parsers are `colonne_clp.py`, `dalle_clp.py`, `semelle_clp.py` and
`poutre_clp.py` and `radier_clp.py`. They run both as standalone command-line tools and through the
DA dashboard's dedicated runner, `l2c.da.dashboard`.

The project-wide context is in [DA_PLAN.md](../../../../DA_PLAN.md) and
[PLAN.md](../../../../PLAN.md). Those documents include older text/vector extraction
strategies; the modules in this directory implement the newer **image-based** strategy.

## Dashboard integration

`app/streamlit_app.py` attaches to `l2c.da.jobs.JobManager`. Its independent worker
calls `l2c.da.dashboard.run_da`, which opens each configured
PDF, normalizes rotation, selects the configured pages, and invokes its dedicated parser.
It never calls the old generic `da.pipeline.run_da`, reads unrelated DA folders, reads
hidden text for validation, or accesses the original plan. The old pipeline remains
a historical CLI baseline. Shared `ProjectResult`/`SheetReport` objects connect
these readers to all existing UI views and downloads.

The configured paths are relative to the selected **CLP** project folder:

| Parser | Source |
|---|---|
| `colonne_clp` | Whole `DA/Colonnes/CLP_COLONNES Partie 3.pdf` plus page 5 of `CLP_COLONNES Partie 1.pdf` |
| `dalle_clp` | Every PDF directly inside `DA/Dalles/` |
| `semelle_clp` | `DA/Fondations/CLP_SEMELLES FND.pdf` |
| `poutre_clp` | `DA/Poutres/CLP_POUTRES.pdf` |
| `radier_clp` | Whole `DA/Fondations/CLP_RADIERS.pdf` |

The column parser reads every page of Partie 3 and every strip, plus the distinct
basement schedule on Partie 1's fifth page. Radiers read the whole file and save
a checkpoint after each completed page. Slabs, footings and beams keep
their existing last-page selection and read every applicable support/elevation.
Slabs currently include **six PDFs**: RDC, Tréfond and Niveaux 2, 3, 4 and 5.
Together with the column supplement and the other configured files, the DA tab processes **eleven
files / fourteen selected pages**. PDF discovery is case-insensitive, ignores non-PDF files and directories,
and uses a stable filename order. Separate files and levels remain separate in
exports, even when their coordinates and reinforcement match.
The column adapter passes `max_strips=0` to disable the CLI's two-strip test limit.
`colonne_clp.records()` and its review JSON share the same selected, deduplicated
cells; the other adapters reuse their existing `records()` converters. Coordinates,
levels, layers, beam positions, bar roles, duplicate conflicts and known partial
reinforcement remain attached to records. Raw candidates and duplicate evidence
remain in each page's diagnostics. All page reports identify image/OCR reading.

### Identical plan/DA record meanings (required for future parsers)

`l2c.record_formats.align_records()` is the shared final adapter for UI tables,
comparison and Appendix-A JSON. `align_result()` also upgrades saved typed results;
the job/file-cache loaders persist that upgrade without invalidating extraction
checkpoints or reopening PDFs. Individual parser review/debug JSON remains source
evidence; the paired final datasets must pass through this shared adapter.

Both sides emit `ElementRecord` with the same fields and an `armature` array of
`repere`, `diametre`, `quantite`, `espacement_mm`, `longueur_mm`. Measurements are mm.
Unknown optional attributes remain `null` in this fixed schema; do not invent values
or create empty placeholder rows. Reinforcement roles align with entries one-to-one
in debug metadata and use the same vocabulary and ordering on both sides:

| Type | Shared record unit | Role order / primary meaning |
|---|---|---|
| Colonne | Coordinate + storey | `VERT`, `ETRI`: count/diameter of parallel verticals, diameter/spacing of ties |
| Semelle | Coordinate + foundation | `LONG`, `TRAN`: two independent count/diameter requirements, even if equal |
| Dalle | Located observation + storey + known layer | `NUM`, `ALP`: separate numeric/alphabetic directions; never implicitly sum them |
| Poutre | One elevation view / beam mark | `longitudinale`, `peau`, `étriers`: retain every independent bar annotation and zone |
| Radier | Located bar group + foundation + rang + drawn direction | `horizontal`, `vertical`: diameter/spacing for spaced reinforcement; preserve count-only requirements |

Normalize level aliases (`FONDATION`/`FONDATIONS`, `RDC`/`REZ-DE-CHAUSSÉE`) and
known slab layers (`INTEGRITE`, `HAUT`, `BAS`). Printed text orientation alone does
not establish a slab reinforcement direction; unknown direction/layer remains
`INCONNU` and requires review. Missing directions are explicit in `missing_roles`;
never copy a known directional value to fill the other one. A partial footing with
only a transverse label stays `TRAN`, rather than acquiring a longitudinal role.

Fabricator bar marks are retained in `debug.reinforcement_details`. Spaced beam
reinforcement uses diameter/spacing as its requirement; the number of fabricated
pieces stays in evidence, rather than being mistaken for longitudinal quantity.
Explicit physical lengths keep their mm meaning and still participate in comparison;
missing plan lengths stay unknown. Beam zones and equal independent annotations
are preserved; normalization does not establish spatial correspondence or conformity.
Direction/layer/quantity/diameter/spacing/length differences and unresolved readings
remain visible. Radier fabrication counts with known spacing stay in evidence;
count-only plan requirements retain quantity. Both sides use `FONDATION` when the
foundation level is otherwise unspecified, the printed rang as `layer`, and the
drawn bar direction as its role. Unread rang/direction stays `INCONNU` and requires
review; opposite directions never match merely because their coordinates agree.
Independent bar strokes stay separate. Exact repeated observations and empty or
unlocated rows are removed from final radier JSON while diagnostics retain them.

Shared-contract regressions are in `tests/test_record_formats.py` and
`tests/test_column_records.py`. Future parsers must use this adapter and extend these
tests when adding a type or a new role, rather than introducing another output shape.

Radier integration verification (2026-10-04): the actual `CLP_RADIERS.pdf` yielded
**93 records**; the original plan reader yielded **77**. Canonical paired JSON is
`out/CLP/radiers_plan.json` / `out/CLP/radiers_atelier.json`, with source evidence and
differences in `out/CLP/radiers_comparison.json`. Formatting does not force grid
aliases or equal reinforcement values. The full DA export is
`out/CLP/elements_atelier.json`: **995 records / 14 pages**, assembled from all
**11 cached files** without further OCR. Adding the radier source preserves the
other files' fingerprints. Tests cover cache reuse, page recovery after a radier
crash, sanitation, shared roles/units, and navigation/downloads with radiers.

**Radiers are connected** and counted in the same UI, comparison and
`CLP_elements_atelier.json` download as the other four types. Other projects are unavailable in this DA UI until their readers
are connected. Missing configured files appear as unread; there is no generic
reader fallback. The first visit starts a background worker; subsequent
renders reattach to its job or completed result. The job key includes the dedicated parser version plus the
selected paths, nanosecond modification times and sizes. Adding or removing a slab
PDF changes the cache key; unrelated files do not. Regeneration clears
only the selected project's completed DA result and reruns the five parsers.

`jobs.py` launches a separate Python process, isolating PDF/OCR engines from
Streamlit and concurrent original-plan parsing. The worker snapshots selected input
paths, writes atomic progress JSON and a trusted local result pickle (retaining
debug metadata) inside **`.cache/da_jobs/<job-id>/`**. Requests include their input
stamp and process identities are stored in `process.json`. On module reload or
server restart, the manager recovers live workers and completed results from disk.
A filesystem lock serializes discovery/start across managers and servers, preventing
duplicate processes. It never calls Streamlit and is independent of `st.cache_data`.
A one-second Streamlit
fragment polls the current filename and elapsed time and displays the result when
ready. Navigation does not cancel or submit another job. At most one active run
per project exists, even if inputs change or regeneration is requested mid-run.

Completed-file checkpoints live in `.cache/da_jobs/file_results/`. Each key includes
parser version, type, absolute source path, nanosecond timestamp and size. Checkpoints
preserve records and full page diagnostics; atomic writes are flushed to disk.
An interrupted run retries only unfinished/changed files and merges the saved
records with newly parsed files. A failed run's explicit retry reuses checkpoints;
all new jobs resume by default, including a rebuilt job after registry invalidation.
Only explicit full regeneration (`reparse=True`) bypasses them. Regenerating a
completed run from its clear-cache button deliberately reparses all files. Cache clearing marks
completed jobs invalid without deleting their saved artifacts or interrupting workers.
An unfinished file may need parsing again after its worker stops; completed files
and results remain on disk. Existing pre-checkpoint workers cannot gain file
checkpoints retroactively, but their process identities can be recovered.

**Required for future dashboard parsers:** use the shared `dashboard.run_da`
checkpoint lifecycle. Save a successful PDF immediately, before starting the next;
do not wait for the project to finish. The worker persists `saved_files` and
`checkpoint_hits` in `state.json` only after the file result is committed. A failed
run's **Reprendre la génération** button keeps completed files, checks their
fingerprints, and parses only missing/changed/corrupt entries. Bump `PARSER_VERSION`
when extraction logic changes. A PDF that fails before completion restarts at its
selected page; intermediate OCR tiles are not checkpoints. Columns save each page
in `file_results/pages/` before continuing; retry resumes completed column pages
even if the file aggregate was never saved. Empty but successfully
parsed pages are cached with their explicit `no_callouts` report.

The **Comparaison** section reads already loaded results without invoking any
parser. `CLP_comparaison.json` contains one row per type/level/layer/element group:
`status`, `reason`, `plan`, `atelier`, unmatched reinforcement on each side, and
`plan_sources`/`atelier_sources` with original filenames/pages/coordinates/debug.
Statuses are `same`, `changed`, `missing_plan`, `missing_da`, `review`, and
`out_of_scope`. Normalization aligns foundation/RDC aliases and accents; no data
from one parser fills gaps in the other. Unsupported levels/layers remain visible
as out of scope, and beam agreement still requires spatial review.

Regeneration is disabled during a run; clearing caches preserves active jobs.
Completed results can be regenerated explicitly. Worker errors appear in the UI
and remain attached to the job until explicit retry, preventing restart loops.
Do not put long generation back in a Streamlit cached function, call Streamlit
from a worker, or pass UI layout closures to a background callback. Only the UI
polling fragment may render progress.

**UI final output:** the `elements_atelier.json` download, named
`CLP_elements_atelier.json`, contains the combined Appendix-A records. Optional
unknown bar attributes may be null under that contract; blank/unlocated rows are
excluded. Individual standalone review JSON formats below keep optional unknown
fields omitted. The ZIP includes records, CSV tables and the parser/input manifest.

Future uploads should provide `run_da(project_dir, inputs={type: PDF_path, ...})`;
each type can also receive a sequence, e.g. `inputs={"dalle": [PDF_path, ...]}`.
The explicit mapping replaces file selection without changing layout parsers or
the record contract. Keep each new project/type parser explicitly registered;
never route an unfamiliar format through a CLP reader because its folder matches.

Integration verification: **51 focused tests passed**, including all four actual
record adapters, UI tables/download serialization, last-page selection, complete
column-strip selection and section cache regeneration. A separate live run on
**one** semelle PDF through the dashboard runner produced **80 unique footings /
160 directional entries**, removed five repeats and passed Appendix-A validation
in 20.54 seconds. Hidden-text/vector extraction was forbidden during that run.
This verifies one real input and all adapter wiring, not a fresh OCR run of all four
files together. The smoke artifact is `out/CLP/elements_atelier_semelle_smoke.json`.

Background-job verification: **57 focused tests passed**, including blocked-run
navigation, active-job preservation during cache clearing, retry behavior and a
real subprocess launch. One live semelle file run in that independent worker
produced **80 footings / 160 directional entries** in 37.11 seconds while tests
ran concurrently. Active and completed requests reattached to the same job.
Its schema-valid artifact is `out/CLP/elements_atelier_semelle_background_smoke.json`.

## Shared strategy

**Defensive review policy:** prefer detecting potential issues to suppressing uncertain
findings. Flag missing plan counterparts, absent specifications, ambiguous matches
and delegated external references for human review. Explain the uncertainty and
distinguish confirmed value differences from potential discrepancies, but do not
automatically clear a flag because an external drawing might explain it. In CLP,
K-9/K-10/J-9/J-10 remain flagged despite the crane-base subcontractor note; L-13
remains a confirmed reinforcement difference. Only proven repeated observations
are removed, with their evidence retained in diagnostics.

Read the drawing as an image, identify the layout, read small relevant regions with
local OCR, and use deterministic code to parse the resulting text. Each parser knows
its own layout; shared helpers handle rendering, OCR and reinforcement notation.

```text
PDF page
  │ remove page rotation; retain PDF-point coordinates
  ▼
Rendered image / reusable display list
  │ OpenCV detects the layout and element anchors
  ▼
Small regions with known structural meaning
  │ local RapidOCR detects text; higher-resolution crops recognise it
  ▼
Text lines + boxes + OCR confidence
  │ common.parse_bar_line interprets the fabricator's notation
  ▼
Reinforcement entries linked to an element, level and source region
  │
  ├─ saved records / count–diameter summaries
  └─ diagnostics, unread reasons and optional visual review
```

The reader must stay **blind to the structural plan**. A plan's expected count or
diameter must never influence transcription. The Canadian bar-designator vocabulary
is shared domain knowledge; a particular expected value at `J-15` is not.

Normal parsing uses rendered pixels, not the PDF's text or vector paths. With
`--check`, an explicit oracle function reads the PDF's hidden text **after the OCR
result has been computed**. This is a validation path, not a fallback reader.

## CLP beam elevations: `poutre_clp.py`

Read **one PDF, last page only**, default `CLP/DA/Poutres/CLP_POUTRES.pdf`.
The long outlined elevation rectangles are beams; grey blocks are supports and
small squares below the elevations are stirrup-zone symbols. An elevation title
such as `P100` identifies a beam. Its own circles supply positions such as
`17 → 16 → 15` or `L → K`; preserve fractional labels and separate repeated views.
The original S-300 parser previously produced 177 reinforcement callouts, not
177 beams. The corrected right-margin handling produces 187 callouts in 27 beams.

**Shared saved representation (plan and DA): one record per beam view**, identified
by source, file, sheet/page, level, view and beam mark. `armature` contains all
reinforcement entries for that beam using the same Appendix-A fields on both
sides (`quantite`, `diametre`, `espacement_mm`, `longueur_mm`, `repere`). Counts
therefore mean **27 plan beams / 187 entries** and **20 DA beams / 162 entries**.
The UI displays beam and reinforcement-entry counts separately. Never sum equal
entries across zones: two identical bar specifications at distinct positions
must stay as two entries.

`l2c.beam_records.align_beam_records()` provides the shared normalization. In
internal/debug data, `roles` and `annotations` align one-to-one with `armature`.
Each annotation retains its role, raw label, source position/bounding box and
confidence; plan grouping preserves each original callout's ID. Beam-level data
retains the section, circled axes and position string. Unknown locations are not
combined, and different pages/levels/views remain separate. Debug evidence is
available in comparison JSON; canonical downloads keep the Appendix-A schema.

Existing trusted result/checkpoint pickles are upgraded atomically without OCR.
The DA extraction version/cache keys stay unchanged because this is a storage
change. Legacy grouped DA records that lack individual bar positions leave those
positions unknown rather than borrowing the whole beam's location. New DA reads
retain the observed bar boxes. The original plan pipeline version advances so
fresh plan exports and UI counts use grouped beams.

Aligned canonical review files: `out/CLP/poutres_plan.json` and
`out/CLP/poutres_atelier.json`; both use the same record structure. The standard
downloads remain `CLP_elements_plan.json` / `CLP_elements_atelier.json`.

Detect text in overlapping image tiles, join fragments before recognition, and
retry incomplete bar labels in small horizontal crops. Short numeric zone counts
must be recognised upright: rotating a narrow `6` can produce an incorrect `9`.
Read quantities, Canadian diameters, fabrication marks, lengths and spacings with
the shared grammar. Preserve longitudinal, skin and stirrup roles, distinct zones,
and separate inside/outside face bars. Shared support labels can be relevant to
neighbouring beam comparisons without changing their blind source assignment.

Some stirrup definitions supply a **group total**, such as `29 10M 10TT21X35 @18"`
for P101/P102 together. Detect the tiny square zone symbols and read their local
quantities. Associate them with an observed definition using the leader baseline
and stirrup dimensions relative to the beam section. Keep the group total and its
source box in diagnostics; never assign that total to each beam. Unresolved
symbols and unread spacings remain review issues rather than invented values.

```bash
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.poutre_clp \
  --check --annotated out/poutre_clp_review.pdf \
  --compare-plan "$HOME/Downloads/l2c-participants/CLP/L2C_PLAN_STR_CLP.pdf"
```

**Final DA file to review: `out/poutre_clp_output.json`.** A JSON array with one
populated beam per mark/page, `position`, observed `axes`, `section`, reinforcement
entries with roles and count–diameter `formatted` values, and status/confidence.
Null/empty rows are removed; exact repeated observations are deduplicated with
provenance retained. Equal bar specifications in distinct zones stay separate.
Current results remain partial while coverage/leader ownership requires review.

Other artifacts:

- `out/poutre_clp_plan_output.json`: grouped original beams, positions and specs.
- `out/poutre_clp.json`: Appendix-A DA records; beam marks remain the identifiers.
- `out/poutre_clp_diagnostics.json`: raw OCR, circles, source regions, shared
  stirrup definitions, zone counts, unresolved symbols, timing and deduplication.
  `--check` adds hidden-text checks **after** extraction; it never fills output.
- `out/poutre_clp_comparison.json`: original count/diameter/spacing coverage
  against spatially aligned DA annotations. Separate face bars may form a pair;
  shared support labels may live under a neighbouring beam. These are coverage
  checks, not a complete engineering conformity verdict. Missing beams, unknown
  zones, extra steel and dimension revisions require human review.
- `out/poutre_clp_review.pdf`: annotated **last page only**.

The original has 27 titled beams and this DA sheet has 20. P200/P201/P300/P301/
P400/P401/P500 are absent from this file and remain flagged. Never manufacture
those entries to make the counts equal. The DA dashboard uses this reader;
original-plan and DA axis positions appear in their detail columns.

Verified with a fresh full run on that one DA file's last page: **20 beams / 162
reinforcement entries**, including **38 resolved generic stirrup zones**, with no
unresolved zone symbols. The post-OCR source checks pass **162/162** for their
documented count/diameter/spacing scope; the full run took **243.58 seconds**.
The comparison locates **150/152** original specifications across the 20 shared
beams. P112 and P116 retain unresolved spatial matches, nine DA annotations
remain unmatched, and the seven absent beams stay flagged. P112 has a 2-25M
support annotation where the original shows 2-20M; P116 has the same 2-20M value
at a shifted label position. Follow the actual leaders before clearing either.
See `out/poutre_clp_comparison.md` for the readable report. Forty-six focused
tests pass; the original plan still passes all six answer-key checks.

The dashboard's plan cache includes the pipeline version, so this parser revision
regenerates its plan data. The per-section clear-cache buttons remain available.

## CLP isolated footings: `semelle_clp.py`

Read **one PDF, last page only**. The default file is
`CLP/DA/Fondations/CLP_SEMELLES FND.pdf`; radiers and continuous wall footings are
separate element types and are not extracted by this module.

The drawing supplies a type schedule headed **Nomenclature des semelles isolées**.
Read its type column and its **LONGITUDINALE / TRANSVERSALE** columns once. Detect
hexagonal type markers inside the grey footing squares, then link each marker to
that schedule. Values are read from the drawing, never hardcoded by type. Keep both
directions even when they match: type D reads `7-25M · 7-25M`, while type F reads
`8-25M · 10-25M`. Goujons and column ties are not footing directional reinforcement.

Reuse the slab parser's grid bubbles and independent viewports. Recover interrupted
circle arcs from pixels and OCR their labels; fractional axes such as `12.7` and
`G.5` stay intact. The detached foundation inset has one numeric strip and a stepped
letter strip. Its coordinates retain a separate `view` identifier.

The marker sits below/right of its footing centre. Locate an isolated grey square
or a compact column symbol, and associate its measured position with the grid.
Large grey radiers are excluded from the square detector. Narrow wall connections
are removed before detecting isolated squares. Missing labels, unresolved geometry,
conflicting definitions and duplicate typed associations remain in diagnostics.
Never copy one directional value to fill a missing direction.

Some dimensions erase part of a hexagon with an opaque white text background.
The detector also recognises its remaining chevron and caps, excluding dense
hatching. A label crossing a grid line gets a bounded crop retry. Long pedestal
segments supply independent anchors at every grid row they cross; lower-left
markers are supported as well as lower-right markers.

```bash
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.semelle_clp \
  --check --annotated out/semelle_clp_review.pdf
# Small experiment on the same file's last page:
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.semelle_clp \
  --coordinate M-14.4 --output-json out/semelle_clp_sample.json \
  --diagnostics out/semelle_clp_sample_diagnostics.json \
  --json out/semelle_clp_sample_records.json
```

**Final file to review: `out/semelle_clp_output.json`.** This is a JSON array with
one populated footing per viewport/grid coordinate. Empty, unlocated and unresolved
duplicate rows are excluded; optional unknown fields are omitted. Known partial
reinforcement can be exported with `status="partial"`. Example:

```json
{
  "fichier": "CLP_SEMELLES FND.pdf",
  "page": 1,
  "niveau": "FONDATION",
  "view": "view-1",
  "coordinate": "M-14.4",
  "type": "D",
  "summary": "7-25M · 7-25M",
  "reinforcement": [
    {"role": "LONG", "formatted": "7-25M", "quantite": 7, "diametre": "25M"},
    {"role": "TRAN", "formatted": "7-25M", "quantite": 7, "diametre": "25M"}
  ],
  "status": "read",
  "confidence": 0.99
}
```

Other files: `out/semelle_clp.json` holds Appendix-A `type_element="semelle"`
records; `out/semelle_clp_diagnostics.json` holds every candidate, the type catalog,
raw OCR, source boxes, timing and unread reasons. `--check` adds type-label and
schedule count/diameter comparisons using hidden text **after** blind extraction.
Its diagnostic `validation` object also reports marker-label coverage and unmatched
source labels; those labels never supply missing output values. These checks validate
type transcription and schedule values, not an independent coordinate truth set.
The optional `out/semelle_clp_review.pdf` contains only the source's last page, with
marker-to-anchor links. The dashboard uses this same reader for semelles.

Verified on that single file's last page: **85 populated annotations / 170 directional
entries**, all 85 source type labels located and all 14 schedule quantity/diameter
cells matching the optional validation oracle. The final run took 35.56 seconds
while tests ran concurrently; 32 focused tests passed. Eight extra image candidates
are excluded from the final JSON and retained in diagnostics.

Cross-referencing original S-100 shows that **five annotations are repeated inset
details**, so there are 80 distinct footing locations after view correspondence.
Inset `Q-5` repeats main `G-5` despite using a literal Q label. Seven additional
coordinate differences refer to the same marker positions across the drawings.
Keep these correspondences in comparison results; never use original-plan values
to change the blind DA extraction. Five DA locations are absent from the original
parser's 75 records: `K-9`, `K-10`, `J-9`, `J-10`, `A-6`. The first four occupy the
original crane-base radier area; A-6 is an original-parser grid-bubble false positive.
L-13 also differs in reinforcement: original `9-25M · 9-25M`, DA
`11-25M · 11-25M`. The generated comparison is `out/semelle_clp_comparison.md`,
with full JSON evidence and an annotated one-page PDF beside it.

## Shared building blocks

### Required output rule: remove repeated entries

**Every current and future parser must deduplicate all final exports**, including
review JSON, Appendix-A records and saved summaries. Columns, slabs and isolated
footings share `output.py`; diagnostics keep every source cell/marker/callout.

An element's identity includes project, source (`plan`/`atelier`), element type,
source file, grid coordinate, storey/level and
reinforcement layer. Identical reinforcement means the same roles, counts,
diameters, spacing, lengths and marks; OCR wording, confidence, page/view and
bounding boxes do not make a repeated observation a new element. Keep different
coordinates, storeys, layers and independent drawings separate. Equal longitudinal
and transverse bars remain **two directional entries** inside the retained element.
Project/source/type are implicit in the current CLP standalone modules; any shared
cross-project exporter must include them explicitly. Never deduplicate a plan
record against a DA record: they are the two sides of the comparison.

Repeated viewports require geometric evidence: at least three shared numeric axes
and three shared letter axes must agree on the view translation. Match alternative
axis labels by their observed positions only after establishing that alignment.
For example, the footing inset's literal `Q-5` matches main `G-5`. This is inferred
from the DA's axes, without reading the original structural plan or hardcoding Q/G.
Unaligned viewports remain independent; never remove a different element just
because its reinforcement is the same.

Prefer a main-view observation for aligned grid details; otherwise prefer the
complete/highest-confidence read (columns also prefer observed storey labels).
Every suppressed occurrence is recorded under diagnostic `deduplication.duplicates`,
with the kept and suppressed coordinates/views. `removed_rows` reports the count;
`view_correspondences` records the geometric mapping where applicable. Filtering
happens at export time, so unread/raw evidence and validation coverage survive.

**Different reinforcement at one identity is a conflict, not a duplicate.** Preserve
the alternatives, set `duplicate_conflict=true`, explain the conflict, and mark a
previously read record partial. The conflict also appears under diagnostic
`deduplication.conflicts`. Never choose an expected original-plan value to erase
a mismatch: L-13's original `9-25M · 9-25M` and DA `11-25M · 11-25M` remain distinct
source records and a real comparison discrepancy.

The final CLP footing JSON now has **80 unique entries / 160 directional entries**,
down from 85 annotation rows. The five repeated inset observations remain in
diagnostics; a coordinate-limited inset run retains its literal coordinate if no
main-view occurrence is present.

| Component | Responsibility |
|---|---|
| `colonne_clp._ocr()` | Cached local RapidOCR engine and configurable CPU thread budget. Reused by all three parsers. |
| `colonne_clp.render()` | Display-list rendering cached for the column parser. |
| `imageread.PageImage` | Reusable page renderer used by the slab and footing parsers. |
| `imageread.TextLine` | Text, bounding box, orientation and confidence. |
| `imageread._join()` | Joins overlapping detector fragments before whole-grid recognition. |
| `imageread.remove_rules()` | Removes long drawing strokes from recognition crops; text strokes remain. |
| `da.common.parse_bar_line()` | Quantity, diameter, mark, spacing and length grammar, including bounded OCR repairs. |
| `units.py` | Closed bar vocabulary and conversion to millimetres. |
| `model.py` | Validated reinforcement entries and Appendix-A output records. |
| `parsers.output` | Shared repeated-entry filtering, reinforcement signatures, viewport correspondence and duplicate/conflict provenance. |

Detection runs at a moderate resolution. Recognition re-renders each line at a
higher resolution, rather than running expensive OCR over a huge high-resolution
sheet. Recognition can compare crops with and without long rules removed. The slab
parser also tries both quarter turns for vertical text.

These helpers are currently shared through the existing modules. If more parsers
need the same OCR helpers, move them into a dedicated shared module while preserving
the tested behaviour; keep layout-specific rules in their respective parsers.

## CLP columns: `colonne_clp.py`

The column drawing is a schedule:

- Each vertical table strip represents one building column.
- Its bottom cell supplies a coordinate such as `K-6`.
- Each horizontal band represents a storey, labelled beside the table.
- A data cell contains reinforcement for that coordinate and storey.

Both original-plan and DA column records already use the same unit: one coordinate
and storey. The shared `l2c.column_records` adapter stores the same primary
specification on both sides: vertical quantity/diameter and tie diameter/spacing.
Fabrication piece counts, marks, lengths, dowels and unspaced slab ties remain in
`debug.reinforcement_details` and the source-cell diagnostics, rather than appearing
as differences against a plan which never specifies those details. Explicit parallel
vertical groups of one diameter are summed; a separately labelled `MURET` in the
same cell is excluded from the column's primary specification and retained in evidence.
The conversion is idempotent and upgrades trusted saved records without PDF/OCR.
Appendix-A JSON downloads use this adapter too. A column's primary `armature`
therefore has the same field meanings for `plan` and `atelier`.

The original last-page result had 92 DA records. Reading all four pages initially
gave 327, but missed nine I-labelled strips (45 cells) and misread floor boundaries.
After the fixes Partie 3 supplies **371** records. Partie 1's fifth page supplies
**23 additional basement columns**, giving **394 DA records versus 395 plan records**.
Partie 1's first four pages and Partie 2 overlap the newer schedules; only the
distinct fifth-page supplement is connected. Current-release coordinates take
precedence over supplemental ones, with suppressed observations retained in diagnostics.
Each selected column page has a durable checkpoint. The column scope marker changes
without invalidating other DA file caches; old completed cell checkpoints are upgraded
by re-reading only cells whose row boundaries changed.
Paired current exports: `out/CLP/colonnes_plan.json` and
`out/CLP/colonnes_atelier.json`; `out/CLP/colonnes_coverage.json` retains the
scope/counts, level distributions and source-backed comparison findings.

The parser finds families of long vertical rules with common extents, then horizontal
rules crossing those families. It reads bottom coordinate cells, the storey-label
strip, and finally the data cells.

Storey names define row ends independently of elevation-name pairing. The last
observed `NIVEAU 5` label wins over a nearby `NIVEAU 5-TOIT` offset. `SOUS-SOL`
defines the basement row even if its horizontal rule is missing; `TRÉFONDS`, `RADIER`
and `EMPATTEMENT` reference lines do not split that building column into extra storeys.
Foundation reinforcement below the lowest observed storey is excluded from column
specifications. Grid-band OCR repairs a narrow capital I read as `1` and retries
ambiguous I/L labels at larger glyph resolution. It prefers a standalone repeated
coordinate over text merged with a section dimension. These rules read the DA's
own pixels; original-plan data never supplies missing coordinates or specifications.
Missing storeys can be
inferred from neighbouring observed storeys and the row count; inferred values are
marked. An incomplete reinforcement read gets a second OCR pass at higher detection
resolution.

A saved column summary currently requires both vertical bars and spaced ties, for
example `4-25M · 10M@152mm`. Cells that do not satisfy that requirement have a reason
and can be exported through `--all-cells-json`.

```bash
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.colonne_clp \
  FILE.pdf --page 4 --check --max-strips 0
```

The standalone CLI default input is CLP Partie 3, page 1. **Its default strip limit
is currently 2** for quick experiments; use `--max-strips 0` for every strip.
The dashboard reads all pages and all strips. `--all-pages` reads every
column-schedule page in the standalone CLI. Optional worker processes each open their
own PDF and OCR engine.

## CLP slabs: `dalle_clp.py`

**Read only the last page of each slab PDF.** A folder run applies this rule separately
to every PDF. There is no all-pages option for this parser.

CLP's numbers identify columns and its letters identify rows. The slab parser:

1. Detects circular grid-label candidates and OCRs their small interior regions.
2. Pairs top/bottom numeric strips to establish independent viewports. It finds the
   matching letter strips inside each viewport, including strips that step sideways.
   Repeated labels in inset details remain separate from the main drawing.
3. Detects compact grey support rectangles. In grey background areas, it additionally
   looks for four short sides of outlined support rectangles; this handles Niveau 5's
   dashed column outlines.
4. Links support rectangles to every grid intersection they touch. A tall wall can
   span two rows; its centre must not replace those two grid coordinates.
   Also checks every remaining labelled grid intersection, including where the
   support detector missed a symbol. These have `detection="grid_intersection"`.
5. OCRs regions anchored at the grid intersection: 100 PDF points above, 40 below,
   100 left and 130 right. Close row spacing does not shrink away raised callouts.
6. Assigns text to the nearest grid anchor shifted 30 points upward, in the same
   view, reflecting CLP's raised callout placement. A crop containing a
   neighbour's text does not automatically own that text. Close-distance ties are
   excluded from the assignment. A visible grey/outlined support has priority over
   an unmarked grid intersection within 40 points; this prevents nearby fractional
   axes from stealing a callout placed to the right of a visible support.
7. Full runs detect text in overlapping tiles and join boxes before recognition,
   so crop boundaries do not truncate neighbouring callouts. Coordinate-limited
   experiments use local crops. Parses reinforcement lines, attaches spacing labels, and retries incomplete
   reads at higher resolution.

The drawing title supplies the layer and level where readable; the filename is a
fallback for the level. Do not infer layer from page order: CLP's layer order varies.

Quantities and diameters are formatted as **`22-15M`**. Preserve separate directional
entries, for example:

```text
NUM: 3-15M · ALP: 3-15M
```

Do not sum `NUM` and `ALP` into a single quantity without an explicitly defined
aggregation rule. They describe different reinforcement directions.

### Corresponding labels in the original CLP plan

Circled `A` and `B` on the original S-600 plans identify **column integrity
reinforcement**. Detail **#101**, found on **S-003 (page 1)** in the supplied CLP
plan PDF, defines `A = 2-15M CH. DIR.` and `B = 3-15M CH. DIR.`: two or three 15M
bars **in each direction**, respectively. The S-600 legend references S-002, but
S-002 is absent from this supplied PDF; the actual detail is on S-003.

For matching the current DA integrity layer, read the circled type at the support,
resolve it through this detail table, and preserve separate NUM/ALP requirements.
Ordinary slab callouts such as `11(5)` describe a different reinforcement group.
The original-plan `parse/slab_integrity.py` reader now resolves these circled labels
from the document’s own detail table. `parse/slabs.py` emits separate integrity
records with `reinforcement_kind="integrity"`, `integrity_type`, the detail source and
NUM/ALP roles in debug metadata. Ordinary numeric records carry
`reinforcement_kind="slab"` and retain their parenthesized count. Missing/conflicting
definitions are reported without inventing quantities.

```bash
# One coordinate on the default Niveau 3 file, last page only:
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.dalle_clp \
  --coordinate J-15 --check --annotated out/dalle_clp_review.pdf

# Every detected support on the last page of every CLP slab file:
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.dalle_clp \
  ~/Downloads/l2c-participants/CLP/DA/Dalles \
  --check --annotated out/dalle_reviews
```

`--coordinate` is repeatable. `--max-supports N` limits an exploratory run;
`--threads N` controls the OCR CPU budget.

## CLP radiers: `radier_clp.py`

One sheet holds several **plan views**, one per radier, each with its **own grid**:
numbers along x, letters along y, in bubbles on the view's edge. The elevations between
them carry a single bubble strip and are skipped. Every page is read.

A radier bar is a heavy long stroke, horizontal or vertical, and bars come in pairs
(`HAUT` over `BAS`). Each bar carries one spec line that **starts at a ringed rang
number** (1–4):

```text
(2) TRAN: 24 30M 30RU19-09 @11"BAS       count, size, mark, spacing, face
(4) LONG: 2x22 25M 25RL22-00 @8"HAUT     2x22 = two lapped bars at 22 positions
(3) LONG: 10 25M 26-03 @8"HAUT           26-03 is a length, 26'-3"
(4) L: 5 25RU8-04 @8"HAUT                the size is carried by the mark only
```

The parser:

1. Finds grid bubbles by **Hough transform**, not by contour: a long label (`14.4`,
   `12.7`) touches the outline and the circle is then no closed round shape.
2. Builds a view from two numeric strips, top and bottom, repeating the same columns at
   the same x. Two columns are enough (`7.3`, `7`); either strip may add an intermediate
   axis. Side-by-side views share a bubble height, so a row of bubbles is split at wide
   gaps. Row letters go to the view whose **columns** are nearest; a row may be labelled
   on one side only, and a view may have a single row (`I`).
3. Reads the view's title (`PLAN FONDATION - RADIER #n`) below it; details may sit
   between the view and its title.
4. Finds rang rings by template correlation: CLP draws the ring as a ragged hatched
   band around an empty gap. Rings crossed by a bar, a revision cloud or a wall fill
   need looser tests (partly inked gap, Hough votes); those candidates are only tried
   off text already read and where a bar stroke runs beside them.
5. Reads the spec **from the ring onward** — rightward, or upward for vertical text —
   so the crop never includes the ring and never starts mid-line. The crop is centred
   on the inked rows and cut at the first wide blank.
6. Reads that crop at **three resolutions and requires agreement**. One recognition of a
   line crossed by drawing strokes is right about 95 % of the time: a dashed grid line
   through `16` read as `1` at full confidence. The rule-erased crop only breaks a
   three-way split, because erasing a line along a digit's stem erases the digit too.
   A reading without a second vote is kept with a note and a reduced confidence.
7. Reads the digit in the ring. An unread ring takes the view's own unanimous reading
   for that direction and face (`rang_source: "view"`), otherwise stays unknown.
8. Links the spec to the heavy stroke on its baseline side and reports the axes that
   stroke crosses (`span`).

The coordinate is the grid intersection nearest the spec text, as on the plan side
(`parse/radier.py`), in that view's own grid.

`issues` list what could not be read and make the row `partial`. `notes` are things read
correctly that deserve a look and are **never corrected**: a mark whose size differs from
the stated size (`30M 35RU22-07`), a rang that disagrees with the face (ring 1 beside
`HAUT`). A mark must end in a whole length: `25RL16 06` and `25RU21-0.0` are rejected,
not shortened.

```bash
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.radier_clp \
  ~/Downloads/l2c-participants/CLP/DA/Fondations/CLP_RADIERS.pdf \
  --check --annotated out/radier_clp_review.pdf
```

Measured on `CLP_RADIERS.pdf` (6 views, 95 specs in the hidden text): 93 read, all 93
equal to the hidden text on every parsed field, in about two minutes. The 2 unread specs
are one pair in radier #5 whose text is overprinted by a lap dimension inside a revision
cloud. Without `--check` such a pair is invisible to the run: nothing reports a bar
whose spec was never found.

## Required output convention for all future parsers

The final review artifact must be **JSON**, with a clear default filename documented
here and printed by the CLI. Sanitize it before saving: remove blank/null rows,
unlocated rows when coordinates are required, and rows without meaningful parsed
reinforcement. Use the element type's required fields; slab count–size entries need
both quantity and diameter. Preserve legitimate spacing-only records for formats
where quantity is not part of the notation.

Keep partial known data and explicit status/issues. Omit optional unknown fields in
the review JSON; never replace them with guessed values or zero. Preserve valid
`false` and zero values. Keep every unread candidate, raw text and failure reason in
a separate diagnostics file. Appendix-A exports remain a separate contract whose
unknown attributes can explicitly be null. CSV and annotated PDFs are optional
review aids, not the canonical final output.

## Output contracts

### Slabs

**Final file to review: `out/dalle_clp_output.json`.** It is generated automatically
at the end of extraction, before optional PDF overlays. Use `--output-json PATH` to
choose a different location. It contains only located supports with valid parsed
reinforcement; blank, null and unread rows are excluded. Full evidence remains in
`out/dalle_clp_diagnostics.json`.

The final JSON is an array, with one object per populated coordinate/view:

```json
[
  {
    "fichier": "CLP_DALLE NIV 3.pdf",
    "page": 3,
    "niveau": "NIVEAU 3",
    "layer": "intégrité",
    "view": "view-1",
    "coordinate": "J-15",
    "summary": "NUM: 3-15M · ALP: 3-15M",
    "reinforcement": [
      {"role": "NUM", "formatted": "3-15M", "quantite": 3, "diametre": "15M", "longueur_mm": 3429.0},
      {"role": "ALP", "formatted": "3-15M", "quantite": 3, "diametre": "15M", "longueur_mm": 3429.0}
    ],
    "status": "read",
    "confidence": 0.95
  }
]
```

The example shows the core fields. Reinforcement entries can also contain spacing,
fabrication mark, raw OCR and issues. The object includes detection method and, when
`--check` is used, boolean `count_size_check` and `full_check`. A false check remains
false; zero confidence remains zero. Optional unknown fields are omitted, not invented.
Partial reads with valid quantity and diameter remain, with their status and reason.
NUM and ALP stay separate and quantities are never summed implicitly.

`--csv PATH` optionally exports the detailed CSV, including empty diagnostic rows.
CSV is no longer the final/default review format. Old preview CSVs are snapshots.

| Default output | Contents |
|---|---|
| **`out/dalle_clp_output.json`** | **Final sanitized review JSON**: populated coordinates with formatted reinforcement, no null rows or optional null fields. |
| `out/dalle_clp.json` | Appendix-A records with `source="atelier"`, `type_element="dalle"` and nested reinforcement values. |
| `out/dalle_clp_summaries.json` | Populated-coordinate summaries with count–diameter strings and roles. |
| `out/dalle_clp_diagnostics.json` | Views and axes, detected supports, source boxes, raw OCR, parsed values, warnings, timings and optional oracle checks. |
| `--annotated PATH` | Review PDF with grid axes, support boxes, callout boxes and association links; only the source's last page is included. A folder run writes one review PDF per input. |

Each support retains a status:

- `read`: reinforcement fields were parsed without a detected mark/length problem.
- `partial`: quantity and diameter were read, but a length or mark remains invalid
  or unread. Known values are retained; the unknown value is null and the issue is
  recorded. Confidence is reduced.
- `unread`: no reinforcement line was parsed, or the support could not be placed on
  a readable grid. Keep the support and its reason in diagnostics.

`read` is a parsing status, **not a conformity verdict or a guarantee of OCR accuracy**.
With `--check`, `check_equal` compares all locally parsed reinforcement attributes;
`check_count_size_equal` compares quantities and diameters. These checks validate
transcription inside the selected regions, not complete engineering correspondence.

### Radiers

**Final file to review: `out/radier_clp_output.json`** — an array with one object per
spec, i.e. per bar group, as the plan side emits one record per radier callout:

```json
{
  "fichier": "CLP_RADIERS.pdf",
  "page": 1,
  "view": "view-1",
  "radier": "RADIER #1",
  "coordinate": "J-13",
  "direction": "vertical",
  "rang": 2,
  "rang_source": "circle",
  "face": "BAS",
  "summary": "RANG 2: 30M@11\"",
  "reinforcement": [
    {"label": "TRAN", "formatted": "30M@11\"", "diametre": "30M", "espacement_mm": 279.4,
     "quantite": 24, "repere": "30RU19-09", "raw": "TRAN: 24 30M 30RU19-09 @11\"BAS"}
  ],
  "span": ["J.5", "J"],
  "status": "read",
  "confidence": 0.97
}
```

`summary` uses the plan's own radier grammar (`RANG 2: 30M@11"`). `quantite` is the
total bar count: `2x22` gives 44, with `"sets": 2` kept beside it. `issues`, `notes`,
`sets`, `longueur_mm` and `full_check` appear only when they apply.

| Default output | Contents |
|---|---|
| **`out/radier_clp_output.json`** | **Final review JSON**, one object per spec. |
| `out/radier_clp.json` | Appendix-A records: `source="atelier"`, `type_element="radier"`, `element` the coordinate, one canonical `armature` entry with size/spacing (or count when unspaced) and explicit length. Fabrication marks/counts stay in `debug.reinforcement_details`; rang is debug `layer`, drawn direction is the shared role, and level is `FONDATION`. |
| `out/radier_clp_diagnostics.json` | Views and axes, every spec with its text box, bar stroke and oracle text, plus unread candidates and their reason. |
| `--annotated PATH` | Review PDF: grid axes, spec boxes, the linked bar stroke, unread candidates in red. |

### Columns

`out/colonne_clp.json` contains saved cell summaries with coordinate, storey, elevation,
raw lines and confidence. `--all-cells-json` adds every cell and its diagnostic state.
Final summaries are sanitized and deduplicated per coordinate/storey. The default
`out/colonne_clp_diagnostics.json` retains all cells plus duplicate/conflict evidence.
This review output differs from Appendix-A records. The dashboard adapter uses
`colonne_clp.records()` to export the same retained cells through Appendix A.

Coordinates and boxes use PDF points from the top-left after rotation normalisation.
For Appendix-A records, `x` and `y` identify the annotation centre; the support's
rectangle is retained separately in debug/diagnostic data. Spacing and length are
converted to millimetres, and unknown values remain null.

## Testing and adding another parser

Install the local OCR dependencies with `.venv/bin/pip install -e '.[da]'`. Corpus
tests use `L2C_CORPUS`, defaulting to `~/Downloads/l2c-participants`.

The slab guards are in `tests/test_dalle_clp.py`: separate main/detail grids,
ambiguous bubble readings, support ownership, grey/outlined rectangle detection,
invalid lengths, image-only parsing on a real J-15 callout, and last-page-only folder
processing. The older shared image-reader tests live in `tests/test_da_image.py`.

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_dalle_clp.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

For a new format:

1. Inspect representative pages from that project's DA folder and identify its
   layout, element anchors, notation, units and level convention.
2. Create a module named for the type and project, such as `dalle_wp2.py`. Reuse
   rendering, OCR, notation and model helpers; implement that layout's geometry
   and association rules locally.
3. Start with one region whose values can be independently verified. On a text-layer
   page, use hidden text only as a validation oracle. For outlined or raster pages,
   use manually verified fixtures.
4. Test the actual failure cases: inset views, overlapping callouts, fractional axes,
   vertical text, missing labels, partial reads and duplicate associations.
5. Run the complete intended file set without exploratory limits. Report extraction
   coverage, unread cases and measured transcription agreement separately.
6. Integrate the proven reader into `da.dashboard` behind its existing record/diagnostic
   contract and add its explicit project/file configuration. The five current CLP
   parsers already use this dashboard path.

Keep drawings and generated evidence out of version control. Runtime processing is
local; do not add cloud OCR or external model APIs.
