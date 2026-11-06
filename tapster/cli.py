"""Ponto de entrada: encaminha o comando ou monta o aplicativo."""

import argparse
import tkinter as tk

from tapster.ipc import CommandServer, acquire_instance_lock, send_command
from tapster.paths import SOCKET_PATH
from tapster.portal import PortalClicker
from tapster.ui.app import TapsterApp


def main():
    parser = argparse.ArgumentParser(prog="tapster", description="Autoclicker simples para Linux.")
    parser.add_argument("--toggle", action="store_true", help="inicia ou para os cliques na janela aberta")
    args = parser.parse_args()

    # Sem janela aberta, --toggle só abre o app, para não clicar sem aviso.
    if send_command("toggle" if args.toggle else "show"):
        return
    lock = acquire_instance_lock()
    if lock is None:
        return  # Outra instância ainda está abrindo.

    root = tk.Tk(className="Tapster")
    clicker = PortalClicker()
    app = TapsterApp(root, clicker)
    clicker.on_closed = app.handle_session_closed

    server = CommandServer(SOCKET_PATH, app.handle_command)
    try:
        server.start()
    except OSError as error:
        app.show_error(f"O atalho F8 não está disponível: {error}")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.engine.stop(wait=True)
    finally:
        server.close()
