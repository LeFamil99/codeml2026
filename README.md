# L2C Review — rebar shop-drawing verification

Design document: **[PLAN.md](PLAN.md)** (measured evidence, architecture, open questions).

## Status: plan side complete, shop drawings next

| | |
|---|---|
| ✅ | Plan-side extraction of **every element type**, all four dev projects |
| | radier (S-050/060) · semelles (S-100) · poutres (S-300) · murs de refend (S-400) · colonnes (S-500) · dalles (S-600) |
| ✅ | Grid locator from the drawn grid **lines** (`K-6`, `J-10.8`, `B.2-35`), correct on multi-view sheets |
| ✅ | Wall locator `élévation B - RDC @ 2`, beam locator = beam mark (`P108`) |
| ✅ | **6/6** answer-key rows (`CLP_dismatch.xlsx`) derived from the plan — `make truth` |
| ✅ | Appendix-A conformant **JSON** + run manifest, deterministic, unique ids |
| ✅ | **Web dashboard**: pick one project folder → run → inspect → download |
| 🟡 | **Dessins d'atelier** tab: four CLP image parsers connected; complete column schedule, existing last-page readers for other types; radiers and other projects pending. Details in **[DA_PLAN.md](DA_PLAN.md)** |
| ⬜ | Plan ↔ atelier matching and non-conformity classification |
| ⬜ | PDF report |

Sheets with no element reinforcement (typical details, general-arrangement plans) are
reported as `skipped` with the reason — never counted as zero findings.

## Quick start

```bash
make install     # create the venv, install everything
make doctor      # check the environment and that the corpus is visible
make ui          # launch the dashboard on http://localhost:8501
```

`make` on its own lists every target. Override any variable inline:

```bash
make run PROJECT=WP2          # extract one project -> JSON + manifest
make run-all                  # all four
make validate-all             # check every JSON against Appendix A
make summary                  # one-line extraction summary per project
make truth                    # check all 6 answer-key rows against the plan extraction
make ui PORT=8520             # dashboard on another port
make run CORPUS=/mnt/other    # a corpus somewhere else
make clean                    # drop generated output and caches
make purge                    # also remove any corpus copy under the repo (consignes S4)
```

Equivalent bare commands, if you prefer:

```bash
.venv/bin/l2c run ~/Downloads/l2c-participants/CLP --out out
.venv/bin/l2c validate out/CLP/elements_plan.json
.venv/bin/streamlit run app/streamlit_app.py
```

## CLP image parsers

The DA dashboard now runs the four format-specific parsers through
`src/l2c/da/dashboard.py`. The selected CLP project folder supplies these inputs:

| Type | File under the CLP project folder |
|---|---|
| Colonnes | `DA/Colonnes/CLP_COLONNES Partie 3.pdf` and the basement supplement on page 5 of `CLP_COLONNES Partie 1.pdf` |
| Dalles | Every PDF directly inside `DA/Dalles/` (currently RDC, Tréfond and Niveaux 2, 3, 4, 5) |
| Semelles | `DA/Fondations/CLP_SEMELLES FND.pdf` |
| Poutres | `DA/Poutres/CLP_POUTRES.pdf` |

Columns process **all pages of Partie 3**, with every strip included, plus the
23 basement-only columns on Partie 1's fifth page. Partie 1's repeated first four
pages are superseded by Partie 3. Other readers
retain their existing page scope. Results feed the tables and the **`elements_atelier.json`**
download (download filename: `CLP_elements_atelier.json`, Appendix A). Per-parser
review JSON files remain available through their standalone commands below.

Both datasets pass through the same final formatter for columns, footings, slabs
and beams. Roles, level aliases and primary reinforcement fields share the same
meaning; source fabrication details remain in evidence. Existing saved results are
upgraded without rerunning OCR. See the [shared format contract](src/l2c/da/parsers/README.md#identical-planda-record-meanings-required-for-future-parsers).
**Radiers are pending**, displayed as “À venir”, and are excluded from this extraction.
Other projects display an availability message instead of using CLP readers.

DA generation runs in an **independent background process**. Switching sections,
changing filters or refreshing the page reattaches to the same active job; completed
results remain saved on disk. Progress updates automatically once per second.
The durable registry in **`.cache/da_jobs/`** records worker identities, requests,
progress and results. Module reloads and server restarts reconnect to existing
workers instead of starting over. A filesystem lock prevents duplicate starts
from multiple servers or sessions.

Each completed PDF is checkpointed separately. If a worker stops, explicit retry
resumes from those completed files; only unfinished files need parsing again.
Checkpoints are reused only when source path, timestamp, size and parser version
match. Every new job resumes by default. The failed-run button **Reprendre la
génération** preserves these checkpoints and displays how many files can be
recovered. Progress records each file immediately after its checkpoint has been
flushed to disk, including the number reused. Invalid/truncated checkpoints are
reparsed. Full regeneration of a completed result deliberately reparses its files.
The current PDF restarts if it crashes before completion; previously completed
PDFs survive page refreshes and server restarts. Older workers started before
file checkpointing cannot recover partial results they never saved.
The complete column schedule also checkpoints each page under
`.cache/da_jobs/file_results/pages/`; an interruption within the PDF resumes from
finished pages. Column page scope has its own cache signature, so changing it
preserves all other file results. Selective column resets invalidate the aggregate
DA result as well, including an existing server's in-memory registry, while retaining
the other eight file checkpoints.

Completed DA results depend on all selected source paths, timestamps, sizes and the
dedicated parser version. Adding or removing a slab PDF also invalidates them. The current CLP
folder supplies **ten files: six slabs, the current column schedule and its basement
supplement, semelles and poutres**.
Its regeneration button runs these readers again after a run finishes; it is disabled
during generation. Clearing caches preserves active jobs. Failed runs show their
error and require explicit resume to retry. Missing files
appear explicitly as unread; the UI never falls back to the old generic readers.
Later PDF uploads can supply the runner's explicit `inputs` mapping.
The older DA runner remains for historical answer-key checks (`l2c truth`);
its results are not the DA dashboard's source.

After loading both sides, open **Comparaison**. It highlights changed armatures,
elements absent from either side and readings requiring review, with type/level
filters and the source annotations for each side. Matching uses the element
identifier, normalized level and slab layer; unsupported coverage is listed
separately, including radiers. Beam matches still require spatial review. Opening
this section never submits or reruns a DA job. Download
**`CLP_comparaison.json`** for all rows, including identical and out-of-scope rows;
each contains the status/reason, both reinforcement lists, unmatched bars and
source/debug evidence. This is a parser comparison, not a certified conformity report.

Both sides save **one beam record containing all its reinforcement entries**:
CLP has 27 plan beams with 187 entries and 20 DA beams with 162 entries. Counts are
shown separately, using the same JSON structure. Annotation roles and positions
remain in internal comparison evidence. Existing DA caches upgrade to this
structure without rerunning OCR; the seven beams absent from this DA stay absent.

CLP beam DA elevations now have a standalone reader, tested on the last page of
`CLP_POUTRES.pdf`. **Review `out/poutre_clp_output.json`**; the grouped original is
`out/poutre_clp_plan_output.json` and the comparison is `out/poutre_clp_comparison.json`.
Circle axes appear as positions (`17 → 16 → 15`, `L → K`); small squares are stirrup
zones, not additional beams. Original S-300 has **27 beams / 187 reinforcement
callouts** after restoring the right-edge annotations. This DA contains 20 beams.

```bash
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.poutre_clp \
  --check --annotated out/poutre_clp_review.pdf \
  --compare-plan "$HOME/Downloads/l2c-participants/CLP/L2C_PLAN_STR_CLP.pdf"
```

Shared stirrup totals are resolved through local zone quantities; unread zones,
missing beams and reinforcement/dimension differences remain review findings.
See [the parser architecture](src/l2c/da/parsers/README.md) for the JSON contract.
Fresh single-file verification: **162/162 source checks**, **150/152 original
specifications located**, 46 focused tests passing. P112/P116, nine unmatched DA
annotations and seven missing beams remain review findings. The readable report
is `out/poutre_clp_comparison.md`; source checks do not certify complete conformity.

The original-plan slab reader also resolves circled integrity types from the plan's
own detail table: CLP detail #101 on S-003 defines A as `2-15M` and B as `3-15M` in
each direction. Integrity records retain NUM/ALP roles and their detail source,
separately from ordinary numeric slab annotations.

Both dashboard sections have **Vider le cache et régénérer** buttons. Each clears
the selected project's cached result for that section and reruns its parser; the
other section's cached result remains available.

The standalone CLP slab parser reads **only the last page of each PDF**. It locates
grey support rectangles using separate grids for the main view and inset details,
then OCRs and associates nearby reinforcement callouts. It does not read the plan.

```bash
.venv/bin/pip install -e '.[da]'  # local OCR dependencies
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.dalle_clp \
  --coordinate J-15 --check --annotated out/dalle_clp_review.pdf
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.dalle_clp \
  ~/Downloads/l2c-participants/CLP/DA/Dalles \
  --annotated out/dalle_reviews
```

**Final file to review: `out/dalle_clp_output.json`**, sanitized to include only
populated grid locations, with directional reinforcement such as `22-15M`, lengths,
raw OCR and read status. Unread intersections remain in the diagnostics file. The exact format and
parser architecture are documented in [the parsers README](src/l2c/da/parsers/README.md).

Other defaults: `CLP_DALLE NIV 3.pdf`, `out/dalle_clp.json` (Appendix A records) and
`out/dalle_clp_diagnostics.json` (all detected supports, raw OCR, layer, direction,
confidence and unread reasons). `out/dalle_clp_summaries.json` provides count–diameter
strings such as `22-15M`; direction labels remain separate (`NUM: 3-15M · ALP: 3-15M`).
Niveau 5's dashed column outlines in grey backgrounds are also detected.
`--check` uses the hidden PDF text **only after**
the image read, for validation. `--max-supports N` limits an exploratory run.
Partial reads retain known values and flag unread lengths or marks. Dense callouts
still need review; the dashboard uses this same parser for the configured Niveau 3 file.

The CLP isolated-footing parser reads **one PDF, its last page only**. It links the
small hexagonal type labels inside footing squares to the drawing's type schedule,
then keeps longitudinal and transverse reinforcement separately.

```bash
PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.semelle_clp \
  --check --annotated out/semelle_clp_review.pdf
```

**Final semelle file to review: `out/semelle_clp_output.json`**, with coordinates,
type letters and summaries such as `7-25M · 7-25M`. Empty/unlocated rows stay in
`out/semelle_clp_diagnostics.json`; `out/semelle_clp.json` is the Appendix-A export.
The [parsers README](src/l2c/da/parsers/README.md) documents the strategy, JSON format
and other output files. The dashboard uses this same reader for its configured semelle file.

## Results on the four development projects (plan side)

```
PROJECT  SHEETS  RECORDS  radier semelle poutre mur_refend colonne dalle  UNITS
CLP          18     2027      74      75    177         80     395  1226  imperial
WP2          33     3659      31     124    244        134     912  2214  metric
LIGREP       26     2585       —     112    218        108     677  1470  metric
EspCa3B      45     2276      54      20    183        241     644  1134  metric
                   10547
```

`SHEETS` = sheets with element reinforcement; the rest (typical details, general
arrangement plans) are listed as skipped with the reason. Every record gets a locator,
but a locator is not proof of correctness. The checks that are:

- **Answer key** — `make truth` derives all 6 rows of `CLP_dismatch.xlsx` from the
  drawings (radier `J-10.8`, footing `L-13`, wall `élévation B - RDC @ 2`, columns
  `K-6` and `I-13`, slab `J-15`). The key is read at run time, never copied into code.
- **Grid geometry** — where the line grid and the old label-only grid disagreed on
  columns, the line grid puts the symbol 0.0–0.6 pt from its grid lines, the old one
  20–326 pt (fractional `B.2`, primed `F'`, doubled `CC` labels).

Only CLP has an answer key; the rules were each checked on all four projects.

## Tests

```bash
make test            # 74 tests, ~90 s, against the real corpus
make test-coords     # just the coordinate-trap guards
make test-e2e        # just the end-to-end corpus runs
make test-ui         # just the headless dashboard tests
```

What they guard:

- **`test_coords.py`** — the two coordinate traps: non-zero MediaBox origin (19% of
  pages) and `/Rotate 90` (65%). Includes a test showing pdfplumber would be off by
  `dx=-1727.7`.
- **`test_end_to_end.py`** — per-project and per-type volume, unique ids, locator
  coverage, unit auto-detection, schema validity, byte-level determinism, all 6
  answer-key rows, scale calibration.
- **`test_sheets.py`** — every page has a unique sheet number (incl. `S-600A`,
  `S-103.a`); sheet type agrees with the numbering series; multi-view grid (`J-10.8`).
- **`test_app.py`** — the dashboard driven headlessly by `streamlit.testing`.
- **`test_model.py`** — an `M`→`H` glyph misread is unrepresentable (`Armature(diametre="25H")`
  raises); see PLAN §5.14 for the measured 83% raw error rate.

Set `L2C_CORPUS` to point at the corpus; tests skip cleanly if it is absent.

## Confidentiality

The corpus is confidential (consignes §4). `.gitignore` excludes all drawing data,
`out/` and the cache; the corpus path is always a CLI argument, never baked in. The
pipeline is fully local — no cloud services and no external AI APIs at runtime.
`PLAN.md §13` has the full compliance checklist, including the PyMuPDF AGPL note.

## Known issues

- Only CLP has an answer key: on WP2, LIGREP and EspCa3B correctness is backed by
  geometry checks, not by labelled truth.
- Slab (`dalle`) callouts are located at their own position; a callout placed between
  two columns resolves to the nearer one (confidence reflects the distance).
- Beams: a few titled beams have no callout found (WP2 7, LIGREP 14, EspCa3B 3 — some
  are `POUTRE SUPPRIMÉ`); listed per sheet in the Diagnostics tab.
- Radier layer in WP2/EspCa3B is inferred from each view's direction legend
  (`RANG 1 & 4` / `RANG 2 & 3`), at reduced confidence.
- 5 CLP column callouts have no dimension-matching symbol; they fall back to the
  callout position with reduced confidence.
