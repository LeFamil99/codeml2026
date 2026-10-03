"""Chart tokens. Status colours are reserved and always ship with an icon + label.

The four status hues must never sit adjacent in one chart: `warning #fab219` beside
`serious #ec835a` scores a normal-vision Delta-E of 13.6, below the floor of 15 - hard to
tell apart even with full colour vision. Composition therefore uses the EMPHASIS form
(one hue + de-emphasis gray), which validates clean.
"""

GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"

ACCENT = "#2a78d6"          # categorical slot 1 / sequential top step
MUTED = "#898781"           # de-emphasis gray
GRID = "#e1e0d9"
INK = "#0b0b0b"
INK_2 = "#52514e"

SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#184f95"]

# DA reading tier is ORDINAL (texte > glyphes > image), so it is encoded as one hue
# light->dark from the sequential ramp, with de-emphasis gray for "illisible".
# validate_palette.js: adjacent CVD dE 18.9, normal-vision 19.2 (PASS); its lightness /
# chroma checks are categorical-only. #9ec5f4 is below 3:1 on the surface, so the tier
# is always also stated in text (the Pages table's `lecture` column, legend, tooltip).
TIER_RANGE = ["#184f95", "#3987e5", "#9ec5f4", MUTED]

STATUS = {
    "extracted":   (GOOD, "✓", "extrait"),
    "no_callouts": (MUTED, "·", "aucun repère"),
    "skipped":     (WARNING, "◔", "hors périmètre"),
    "unread":      (CRITICAL, "✕", "non lu"),
}
