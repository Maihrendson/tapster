"""Cores e fontes da interface."""

from typing import NamedTuple

from tapster.ui.state import AppState

BG = "#101218"
PANEL = "#1b1f2a"
FIELD = "#2a3040"
FIELD_HOVER = "#363d50"
TEXT = "#f4f6fb"
MUTED = "#a2abbd"
ACCENT = "#c8f31d"
ACCENT_HOVER = "#dbff4d"
ON_ACCENT = "#101218"
STOP = "#ff4757"
STOP_HOVER = "#ff6b78"
WARN = "#ffb627"
WARN_HOVER = "#ffc857"
ERROR = "#ff8a8a"

FONT_CANDIDATES = ("Ubuntu Sans", "Ubuntu", "Noto Sans", "DejaVu Sans")
FALLBACK_FONT = "TkDefaultFont"


class StateLook(NamedTuple):
    pill_text: str
    pill_bg: str
    pill_fg: str
    strip: str
    counter: str
    caption: str
    action_text: str
    action_bg: str
    action_hover: str


STATE_LOOKS = {
    AppState.IDLE: StateLook("PRONTO", FIELD, TEXT, FIELD, TEXT, "CLIQUES", "INICIAR", ACCENT, ACCENT_HOVER),
    AppState.CONNECTING: StateLook("PERMISSÃO", WARN, ON_ACCENT, WARN, WARN, "CLIQUES", "CANCELAR", WARN, WARN_HOVER),
    AppState.COUNTDOWN: StateLook("PREPARE-SE", WARN, ON_ACCENT, WARN, WARN, "SEGUNDOS", "CANCELAR", WARN, WARN_HOVER),
    AppState.RUNNING: StateLook("CLICANDO", ACCENT, ON_ACCENT, ACCENT, ACCENT, "CLIQUES", "PARAR", STOP, STOP_HOVER),
}
