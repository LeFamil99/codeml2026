# DA parsers: strategy and architecture

This directory contains **one parser per element type and project/fabricator layout**.
The current parsers are `colonne_clp.py`, `dalle_clp.py` and `radier_clp.py`. They are standalone
command-line tools used to develop and verify a format before integrating it into
the main DA pipeline.

The project-wide context is in [DA_PLAN.md](../../../../DA_PLAN.md) and
[PLAN.md](../../../../PLAN.md). Those documents include older text/vector extraction
strategies; the modules in this directory implement the newer **image-based** strategy.

## Shared strategy

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

## Shared building blocks

| Component | Responsibility |
|---|---|
| `colonne_clp._ocr()` | Cached local RapidOCR engine and configurable CPU thread budget. Currently reused by both parsers. |
| `colonne_clp.render()` | Display-list rendering cached for the column parser. |
| `imageread.PageImage` | Reusable page renderer used by the slab parser. |
| `imageread.TextLine` | Text, bounding box, orientation and confidence. |
| `imageread._join()` | Joins overlapping detector fragments before whole-grid recognition. |
| `imageread.remove_rules()` | Removes long drawing strokes from recognition crops; text strokes remain. |
| `da.common.parse_bar_line()` | Quantity, diameter, mark, spacing and length grammar, including bounded OCR repairs. |
| `units.py` | Closed bar vocabulary and conversion to millimetres. |
| `model.py` | Validated reinforcement entries and Appendix-A output records. |

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

The parser finds families of long vertical rules with common extents, then horizontal
rules crossing those families. It reads bottom coordinate cells, the storey-label
strip, and finally the data cells.

Storey labels and elevations are linked to row boundaries. Missing storeys can be
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

The default input is CLP Partie 3, page 1. **The default strip limit is currently 2**
for quick experiments; use `--max-strips 0` for every strip. `--all-pages` reads every
column-schedule page. Optional worker processes each open their own PDF and OCR engine.

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
| `out/radier_clp.json` | Appendix-A records: `source="atelier"`, `type_element="radier"`, `element` the coordinate, one `armature` entry with mark, size, count, spacing and length. The rang is the debug `layer`, as in `parse/radier.py`. |
| `out/radier_clp_diagnostics.json` | Views and axes, every spec with its text box, bar stroke and oracle text, plus unread candidates and their reason. |
| `--annotated PATH` | Review PDF: grid axes, spec boxes, the linked bar stroke, unread candidates in red. |

### Columns

`out/colonne_clp.json` contains saved cell summaries with coordinate, storey, elevation,
raw lines and confidence. `--all-cells-json` adds every cell and its diagnostic state.
This experimental output differs from the slab parser's Appendix-A records.

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
6. Integrate the proven reader into `da.pipeline` behind its existing record/diagnostic
   contract. These standalone modules are **not yet the dashboard's reader path**.

Keep drawings and generated evidence out of version control. Runtime processing is
local; do not add cloud OCR or external model APIs.
