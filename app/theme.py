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


# Interface chrome. Only type, spacing, radius and hairlines are set here: surfaces and text colours
# stay with Streamlit's own light or dark appearance, so the app never offers its own switch.
# The accent (for controls) is set in .streamlit/config.toml; the four status hues above keep their
# meaning and always ship with an icon and a label.
CSS = """
html, .stApp, .stApp button, .stApp input, .stApp textarea {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Inter, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.block-container { max-width: 1120px; padding-top: 2.25rem; padding-bottom: 4rem; }
h1 { font-size: 1.75rem !important; font-weight: 650 !important; letter-spacing: -0.02em; margin-bottom: .25rem; }
h2 { font-size: 1.25rem !important; font-weight: 600 !important; letter-spacing: -0.01em; margin-top: 2rem; }
h3 { font-size: 1.0625rem !important; font-weight: 600 !important; }
[data-testid="stCaptionContainer"] { font-size: .875rem; opacity: .72; }
[data-testid="stMetric"] {
  border: 1px solid rgba(127, 127, 127, .22); border-radius: 12px; padding: .9rem 1.05rem;
}
[data-testid="stMetricLabel"] p { font-size: .8125rem; font-weight: 500; opacity: .72; }
[data-testid="stMetricValue"] { font-size: 1.5rem; font-weight: 600; font-variant-numeric: tabular-nums; }
[data-testid="stSidebar"] { border-right: 1px solid rgba(127, 127, 127, .18); }
.stButton > button, [data-testid="stBaseButton-secondary"] { border-radius: 10px; font-weight: 500; }
[data-testid="stSegmentedControl"] button { border-radius: 8px; font-weight: 500; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
[data-testid="stExpander"] { border-radius: 12px; }
hr { opacity: .35; margin: 2rem 0; }
.l2c-figure { font-size: 2.5rem; line-height: 1.1; font-weight: 650; letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums; }
.l2c-figure-note { font-size: .9375rem; opacity: .72; margin: .25rem 0 1rem; }
"""


def apply():
    import streamlit as st

    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
