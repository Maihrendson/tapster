"""Comunicação com a janela aberta e garantia de uma única instância.

O Wayland não permite que um app comum ouça teclas globalmente. Por isso o F8 é
um atalho do GNOME que executa `bin/tapster --toggle`, e esse comando avisa a
janela aberta por um socket Unix.
"""

import fcntl
import os
import socket
import threading
from pathlib import Path

from tapster.paths import LOCK_PATH, SOCKET_PATH


class CommandServer:
    """Recebe comandos de outros processos, como o do atalho F8."""

    def __init__(self, path, on_command):
        self._path = Path(path)
        self._on_command = on_command
        self._socket = None

    def start(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Quem chega aqui tem a trava de instância, então um socket existente é resto antigo.
        self._path.unlink(missing_ok=True)
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(str(self._path))
        self._path.chmod(0o600)
        self._socket.listen()
        threading.Thread(target=self._serve, args=(self._socket,), daemon=True).start()

    def _serve(self, server):
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with connection:
                try:
                    connection.settimeout(1)
                    command = connection.recv(32).decode(errors="ignore").strip()
                except OSError:
                    continue
            self._on_command(command)

    def close(self):
        if self._socket is None:
            return
        try:
            self._socket.shutdown(socket.SHUT_RDWR)  # Acorda a thread parada no accept.
        except OSError:
            pass
        self._socket.close()
        self._path.unlink(missing_ok=True)
        self._socket = None


def send_command(command, path=SOCKET_PATH):
    """Envia um comando à janela aberta. Devolve False se não houver nenhuma."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(path))
            client.sendall(command.encode())
    except OSError:
        return False
    return True


def acquire_instance_lock(path=LOCK_PATH):
    """Garante uma única janela. O sistema libera a trava quando o processo termina."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd
