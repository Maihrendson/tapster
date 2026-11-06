"""Opções de clique disponíveis e validação do que o usuário digita."""

from typing import NamedTuple, Optional

# Códigos de botão do kernel Linux (BTN_LEFT, BTN_MIDDLE, BTN_RIGHT).
BUTTONS = {"Esquerdo": 0x110, "Meio": 0x112, "Direito": 0x111}
SPEED_PRESETS = (("Lento", 1000), ("Médio", 100), ("Rápido", 20))

MIN_INTERVAL_MS = 10
MAX_INTERVAL_MS = 3_600_000
MAX_DELAY_S = 60


class ClickSettings(NamedTuple):
    interval_s: float
    limit: Optional[int]
    delay_s: int


def parse_settings(interval_text, limit_enabled, limit_text, delay_text):
    """Valida os campos do formulário e devolve um ClickSettings."""
    try:
        interval_ms = int(interval_text)
    except ValueError:
        raise ValueError("O intervalo precisa ser um número inteiro.") from None
    if not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
        raise ValueError(f"O intervalo precisa ficar entre {MIN_INTERVAL_MS} e {MAX_INTERVAL_MS} ms.")

    limit = None
    if limit_enabled:
        try:
            limit = int(limit_text)
        except ValueError:
            raise ValueError("A quantidade de cliques precisa ser um número inteiro.") from None
        if limit < 1:
            raise ValueError("A quantidade de cliques precisa ser pelo menos 1.")

    try:
        delay_s = int(delay_text)
    except ValueError:
        raise ValueError("O atraso precisa ser um número inteiro.") from None
    if not 0 <= delay_s <= MAX_DELAY_S:
        raise ValueError(f"O atraso precisa ficar entre 0 e {MAX_DELAY_S} segundos.")

    return ClickSettings(interval_ms / 1000, limit, delay_s)
