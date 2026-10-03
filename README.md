# L2C Review — rebar shop-drawing verification

Design document: **[PLAN.md](PLAN.md)** (measured evidence, architecture, open questions).

## Status: Phase 1 thin vertical slice

| | |
|---|---|
| ✅ | Plan-side **column** extraction (S-500), all four dev projects |
| ✅ | Grid locator (`K-6`) from bubble labels + symbol geometry |
| ✅ | Appendix-A conformant **JSON** + run manifest, deterministic |
| ✅ | **Web dashboard**: upload / pick project → run → inspect → download |
| ⬜ | Shop-drawing reading (glyph decoder, PLAN §5) |
| ⬜ | Plan ↔ atelier matching and non-conformity classification |
| ⬜ | PDF report |

Sheets outside this slice are reported as `skipped` with a reason — never counted as zero findings.

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
make truth                    # assert the S-502 / K-6 / 4-35M answer-key row
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

## Measured results

```
project   sheets  columns  located        units      time
CLP        28/6       395  395 (100.0%)   imperial   1.0 s
WP2        50/22     1087  1087 (100.0%)  metric     4.8 s
LIGREP     40/11      677  655 (96.7%)    metric     1.5 s
EspCa3B    72/23      644  644 (100.0%)   metric     1.6 s
                     2803  2781 (99.2%)
```

Ground truth: answer-key row *S-502 / K-6 / `4-35M`* is derived by the pipeline, not
hardcoded — it requires grid axes, symbol detection, scale calibration (0.75 pt/inch =
1/8"=1'-0") and the global callout↔symbol assignment all to be correct.

## Tests

```bash
make test            # 53 tests, ~30 s, against the real corpus
make test-coords     # just the coordinate-trap guards
make test-e2e        # just the end-to-end corpus runs
make test-ui         # just the headless dashboard tests
```

What they guard:

- **`test_coords.py`** — the two coordinate traps: non-zero MediaBox origin (19% of
  pages) and `/Rotate 90` (65%). Includes a test showing pdfplumber would be off by
  `dx=-1727.7`.
- **`test_end_to_end.py`** — per-project volume, locator coverage, unit auto-detection,
  schema validity, byte-level determinism, the K-6 ground truth, scale calibration.
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

- 22 of 677 LIGREP elements still unlocated (incomplete letter axis on some sheets).
- Occasional duplicate locators (two callouts resolving to one grid cell) — see the
  Diagnostics tab.
- 5 CLP callouts have no dimension-matching symbol; they fall back to the callout
  position with reduced confidence.
