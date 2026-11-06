"""Locais dos arquivos que o Tapster cria fora do projeto."""

import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tapster"
TOKEN_PATH = CONFIG_DIR / "restore_token"

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or CONFIG_DIR)
SOCKET_PATH = RUNTIME_DIR / "tapster.sock"
LOCK_PATH = RUNTIME_DIR / "tapster.lock"
