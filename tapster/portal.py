"""Envio de cliques pelo portal RemoteDesktop (xdg-desktop-portal).

O portal funciona em Wayland sem root e pede permissão ao usuário na primeira vez.
"""

import os
import threading
import time
import uuid
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from tapster.errors import ClickerError  # noqa: E402
from tapster.paths import TOKEN_PATH  # noqa: E402

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
DEVICE_POINTER = 2
PERSIST_UNTIL_REVOKED = 2
PERMISSION_TIMEOUT_S = 120
CLICK_HOLD_S = 0.015


class PortalClicker:
    """Envia cliques do mouse por uma sessão do portal RemoteDesktop."""

    def __init__(self, token_path=TOKEN_PATH):
        self.on_closed = None
        self._token_path = Path(token_path)
        self._bus = None
        self._session = None

    @property
    def connected(self):
        return self._session is not None

    def connect(self):
        """Abre a sessão. Bloqueia enquanto o diálogo de permissão está aberto."""
        if self.connected:
            return
        try:
            if self._bus is None:
                self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
                # Os sinais do portal só chegam com um loop GLib em execução.
                threading.Thread(target=GLib.MainLoop().run, daemon=True).start()

            created = self._request(
                "CreateSession",
                lambda token: GLib.Variant(
                    "(a{sv})",
                    ({
                        "handle_token": GLib.Variant("s", token),
                        "session_handle_token": GLib.Variant("s", f"tapster_{uuid.uuid4().hex}"),
                    },),
                ),
            )
            session = created["session_handle"]

            def select_devices(token):
                options = {
                    "handle_token": GLib.Variant("s", token),
                    "types": GLib.Variant("u", DEVICE_POINTER),
                    "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
                }
                saved_token = self._load_token()
                if saved_token:
                    options["restore_token"] = GLib.Variant("s", saved_token)
                return GLib.Variant("(oa{sv})", (session, options))

            self._request("SelectDevices", select_devices)

            started = self._request(
                "Start",
                lambda token: GLib.Variant(
                    "(osa{sv})", (session, "", {"handle_token": GLib.Variant("s", token)})
                ),
            )
        except GLib.Error as error:
            raise ClickerError(f"Não foi possível usar o portal do sistema: {error.message}") from error

        if not started.get("devices", 0) & DEVICE_POINTER:
            raise ClickerError("O sistema não liberou o controle do mouse.")

        if started.get("restore_token"):
            self._save_token(started["restore_token"])

        self._bus.signal_subscribe(
            PORTAL_NAME,
            "org.freedesktop.portal.Session",
            "Closed",
            session,
            None,
            Gio.DBusSignalFlags.NONE,
            self._handle_session_closed,
        )
        self._session = session

    def click(self, button_code):
        session = self._session
        if session is None:
            raise ClickerError("A permissão do mouse foi encerrada.")
        try:
            self._send_button(session, button_code, pressed=True)
            try:
                time.sleep(CLICK_HOLD_S)
            finally:
                self._send_button(session, button_code, pressed=False)
        except GLib.Error as error:
            raise ClickerError(error.message) from error

    def _send_button(self, session, button_code, pressed):
        self._bus.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            REMOTE_DESKTOP,
            "NotifyPointerButton",
            GLib.Variant("(oa{sv}iu)", (session, {}, button_code, 1 if pressed else 0)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def _request(self, method, build_args):
        """Chama um método do portal e aguarda o sinal Response correspondente."""
        token = f"tapster_{uuid.uuid4().hex}"
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"{PORTAL_PATH}/request/{sender}/{token}"
        answered = threading.Event()
        answer = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params, *_):
            answer["code"], answer["results"] = params.unpack()
            answered.set()

        # A inscrição vem antes da chamada para não perder uma resposta imediata.
        subscription = self._bus.signal_subscribe(
            PORTAL_NAME,
            "org.freedesktop.portal.Request",
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )
        try:
            self._bus.call_sync(
                PORTAL_NAME,
                PORTAL_PATH,
                REMOTE_DESKTOP,
                method,
                build_args(token),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            if not answered.wait(PERMISSION_TIMEOUT_S):
                raise ClickerError("A permissão do sistema não foi respondida a tempo.")
        finally:
            self._bus.signal_unsubscribe(subscription)

        if answer["code"] == 1:
            raise ClickerError("Permissão negada. Clique em Iniciar para tentar de novo.")
        if answer["code"] != 0:
            raise ClickerError("O sistema recusou o controle do mouse.")
        return answer["results"]

    def _handle_session_closed(self, *_):
        self._session = None
        if self.on_closed:
            self.on_closed()

    def _load_token(self):
        try:
            return self._token_path.read_text().strip()
        except OSError:
            return ""

    def _save_token(self, token):
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as token_file:
                token_file.write(token)
        except OSError:
            pass  # Sem o token, o sistema só volta a pedir permissão na próxima vez.
