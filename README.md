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
| 🟡 | **Dessins d'atelier** tab, same layout — progress in **[DA_PLAN.md](DA_PLAN.md)**: text-layer DA read (CLP 7,751 records, LIGREP columns 324); 340 outlined pages await the glyph decoder |
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

On Windows (no `make`; needs [uv](https://docs.astral.sh/uv/)):

```bat
install.cmd      :: create .venv (Python 3.13), install everything incl. OCR extras
ui.cmd           :: launch the dashboard on http://localhost:8501
ui.cmd 8520      :: dashboard on another port
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

## CLP slab image parser

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
still need review; this parser is standalone and not yet wired into the dashboard.

## Results on the four development projects (plan side)

```
PROJECT  SHEETS  RECORDS  radier semelle poutre mur_refend colonne dalle  UNITS
CLP          18     2468      77      75    187        119     395  1615  imperial
WP2          33     4681      35     124    283        212     912  3115  metric
LIGREP       26     3562      30     112    240        174     677  2329  metric
EspCa3B      45     2806      54      20    212        369     644  1507  metric
                   13517
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
- Walls: LIGREP S-400 and S-401 both title their views A, B, C, so a wall element
  (`élévation A - RDC @ 2`) is unique only together with its sheet.
- Legend and detail tables (épingle spacing tables, `ARM. ADD.` shear-reinforcement
  details, the pilaster detail beside the foundations plan) are not attached to a grid
  location and are not extracted.
- Radier layer in WP2/EspCa3B is inferred from each view's direction legend
  (`RANG 1 & 4` / `RANG 2 & 3`), at reduced confidence.
- Column callouts with no dimension-matching symbol within 100 pt (CLP 3, WP2 11 — the
  WP2 ones are columns drawn inside a wall) are placed from the callout position, using
  the sheet's usual callout-to-column offset when it is consistent, at reduced confidence.
- A few columns share a locator: no labelled grid line passes through the second one
  (WP2 S-512 `T.1-34..37`), or two columns stand at one intersection (EspCa3B `B-2`).
