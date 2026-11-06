#!/usr/bin/env python3
"""Tapster: autoclicker simples para Linux, compatível com Wayland.

Os cliques passam pelo portal RemoteDesktop do sistema (xdg-desktop-portal),
que funciona sem root e pede permissão ao usuário na primeira vez.

O Wayland não permite que um app comum ouça teclas globalmente. Por isso o F8 é
um atalho do GNOME que executa `tapster.py --toggle`, e esse comando avisa a
janela aberta por um socket Unix.
"""

import argparse
import fcntl
import os
import queue
import socket
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import font as tkfont

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
DEVICE_POINTER = 2
PERSIST_UNTIL_REVOKED = 2
PERMISSION_TIMEOUT_S = 120

# Códigos de botão do kernel Linux (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE).
BUTTONS = {"Esquerdo": 0x110, "Meio": 0x112, "Direito": 0x111}
SPEED_PRESETS = (("Lento", 1000), ("Médio", 100), ("Rápido", 20))

CLICK_HOLD_S = 0.015
MIN_INTERVAL_MS = 10
MAX_INTERVAL_MS = 3_600_000
MAX_DELAY_S = 60
# Um clique em andamento pode cair no botão logo depois de parar. Sem esta
# pausa, ele acionaria "Iniciar" e o autoclick recomeçaria sozinho.
RESTART_COOLDOWN_S = 0.6
# Segurar a tecla não pode iniciar e parar várias vezes seguidas.
HOTKEY_DEBOUNCE_S = 0.3
# Ao autorizar, o cursor está sobre o diálogo do sistema. Este tempo mínimo
# permite levá-lo ao lugar certo antes do primeiro clique.
DELAY_AFTER_PERMISSION_S = 3

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tapster"
TOKEN_PATH = CONFIG_DIR / "restore_token"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or CONFIG_DIR)
SOCKET_PATH = RUNTIME_DIR / "tapster.sock"
LOCK_PATH = RUNTIME_DIR / "tapster.lock"


class PortalError(Exception):
    """Falha ou recusa ao usar o portal RemoteDesktop."""


class PortalClicker:
    """Envia cliques do mouse por uma sessão do portal RemoteDesktop."""

    def __init__(self, on_closed=None, token_path=TOKEN_PATH):
        self._on_closed = on_closed
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
            raise PortalError(f"Não foi possível usar o portal do sistema: {error.message}") from error

        if not started.get("devices", 0) & DEVICE_POINTER:
            raise PortalError("O sistema não liberou o controle do mouse.")

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
            raise PortalError("A permissão do mouse foi encerrada.")
        self._send_button(session, button_code, pressed=True)
        try:
            time.sleep(CLICK_HOLD_S)
        finally:
            self._send_button(session, button_code, pressed=False)

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
                raise PortalError("A permissão do sistema não foi respondida a tempo.")
        finally:
            self._bus.signal_unsubscribe(subscription)

        if answer["code"] == 1:
            raise PortalError("Permissão negada. Clique em Iniciar para tentar de novo.")
        if answer["code"] != 0:
            raise PortalError("O sistema recusou o controle do mouse.")
        return answer["results"]

    def _handle_session_closed(self, *_):
        self._session = None
        if self._on_closed:
            self._on_closed()

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


class ClickEngine:
    """Repete cliques em uma thread própria, sem acumular atrasos."""

    def __init__(self, click, on_progress, on_finished):
        self._click = click
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._stop = threading.Event()
        self._thread = None

    def start(self, interval_s, limit=None):
        self.stop(wait=True)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(interval_s, limit, self._stop), daemon=True
        )
        self._thread.start()

    def stop(self, wait=False):
        self._stop.set()
        if wait and self._thread is not None:
            # O limite evita travar a interface se o barramento não responder.
            self._thread.join(timeout=1)

    def _run(self, interval_s, limit, stop):
        count = 0
        error = None
        next_click = time.monotonic()
        while not stop.is_set() and (limit is None or count < limit):
            try:
                self._click()
            except (PortalError, GLib.Error) as exc:
                error = getattr(exc, "message", None) or str(exc)
                break
            count += 1
            self._on_progress(count)
            if limit is not None and count >= limit:
                break
            # Se um clique atrasar, segue em frente em vez de compensar com uma rajada.
            next_click = max(next_click + interval_s, time.monotonic())
            stop.wait(next_click - time.monotonic())
        self._on_finished(count, error)


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


def parse_settings(interval_text, limit_enabled, limit_text, delay_text):
    """Valida os campos e devolve (intervalo_s, limite, atraso_s)."""
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

    return interval_ms / 1000, limit, delay_s


class TapsterApp:
    IDLE, CONNECTING, COUNTDOWN, RUNNING = range(4)

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

    def __init__(self, root, clicker=None):
        self.root = root
        self.events = queue.Queue()
        self.clicker = clicker or PortalClicker(on_closed=lambda: self.events.put(("closed",)))
        self.engine = ClickEngine(
            self._click,
            lambda count: self.events.put(("progress", count)),
            lambda count, error: self.events.put(("finished", count, error)),
        )
        self.state = self.IDLE
        self.pointer_inside = False
        self.start_allowed_at = 0.0
        self.hotkey_allowed_at = 0.0
        self.countdown_job = None
        self.button_code = BUTTONS["Esquerdo"]
        self.settings = None
        self.connect_attempt = 0

        self._build_window()
        self._poll_events()

    def handle_command(self, command):
        """Recebe comandos externos. Pode ser chamado de outra thread."""
        if command in ("toggle", "show"):
            self.events.put((command,))

    def _build_window(self):
        root = self.root
        root.title("Tapster")
        root.resizable(False, False)
        root.configure(bg=self.BG)
        # Mantém a janela visível mesmo quando um clique ativa outro aplicativo.
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        families = set(tkfont.families(root))
        self.family = next((f for f in self.FONT_CANDIDATES if f in families), "TkDefaultFont")
        # Fontes crescem em telas HiDPI, mas medidas em pixels não. Esta escala as acompanha.
        self.scale = max(1.0, tkfont.Font(root=root, font=(self.family, 11)).metrics("linespace") / 30)

        self.interval_var = tk.StringVar(value="100")
        self.button_var = tk.StringVar(value="Esquerdo")
        self.limit_enabled_var = tk.BooleanVar(value=False)
        self.limit_var = tk.StringVar(value="100")
        self.delay_var = tk.StringVar(value="3")

        main = tk.Frame(root, bg=self.BG, padx=self._px(20), pady=self._px(14))
        main.grid()
        main.columnconfigure(0, weight=1)

        # A ordem acompanha o uso: ajustar, iniciar e acompanhar o resultado.
        self._build_header(main).grid(row=0, column=0, sticky="ew", pady=(0, self._px(10)))
        self._build_controls(main).grid(row=1, column=0, sticky="ew", pady=(0, self._px(10)))

        self.action_button = self._flat_button(
            main, font=(self.family, 12, "bold"), pady=self._px(8), command=self.toggle
        )
        self.action_button.grid(row=2, column=0, sticky="ew", pady=(0, self._px(10)))

        self._build_counter(main).grid(row=3, column=0, sticky="ew")

        # A largura vem do formulário; só então os textos longos quebram nela.
        self.interval_var.trace_add("write", lambda *_: self._sync_inputs())
        self.limit_enabled_var.trace_add("write", lambda *_: self._sync_inputs())
        self.button_var.trace_add("write", lambda *_: self._sync_inputs())
        self._paint_state()
        main.update_idletasks()
        text_width = main.winfo_reqwidth() - 2 * self._px(20)
        self.status_label.configure(wraplength=text_width - 2 * self._px(14))
        self._set_status("Aperte F8 para clicar onde o cursor estiver.")

        root.bind("<Enter>", self._on_pointer_enter)
        root.bind("<Leave>", self._on_pointer_leave)
        root.bind_all("<ButtonPress>", self._on_any_press, add=True)

    def _build_header(self, parent):
        header = tk.Frame(parent, bg=self.BG)
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="TAPSTER", bg=self.BG, fg=self.TEXT,
                 font=(self.family, 14, "bold")).grid(row=0, column=0, sticky="w")
        self.state_pill = tk.Label(header, font=(self.family, 9, "bold"),
                                   padx=self._px(10), pady=self._px(3))
        self.state_pill.grid(row=0, column=1, sticky="e")
        return header

    def _build_counter(self, parent):
        panel = tk.Frame(parent, bg=self.PANEL)
        panel.columnconfigure(0, weight=1)
        self.hero_strip = tk.Frame(panel, height=self._px(5))
        self.hero_strip.grid(row=0, column=0, sticky="ew")

        reading = tk.Frame(panel, bg=self.PANEL)
        reading.grid(row=1, column=0, pady=(self._px(4), 0))
        self.counter_label = tk.Label(reading, text="0", bg=self.PANEL, font=(self.family, 28, "bold"))
        self.counter_label.grid(row=0, column=0, sticky="s")
        self.counter_caption = tk.Label(reading, text="CLIQUES", bg=self.PANEL, fg=self.MUTED,
                                        font=(self.family, 9, "bold"))
        self.counter_caption.grid(row=0, column=1, sticky="s", padx=(self._px(8), 0), pady=(0, self._px(10)))

        # Duas linhas reservadas evitam que o layout pule a cada mensagem.
        self.status_label = tk.Label(panel, bg=self.PANEL, font=(self.family, 9), justify="center", height=2)
        self.status_label.grid(row=2, column=0, padx=self._px(14), pady=(0, self._px(8)))
        return panel

    def _build_controls(self, parent):
        # Uma única grade mantém rótulos e controles alinhados entre as linhas.
        card = tk.Frame(parent, bg=self.PANEL, padx=self._px(14), pady=self._px(10))
        for column in (1, 2, 3):
            card.columnconfigure(column, weight=1, uniform="control")

        self._row_label(card, 0, "Velocidade")
        self.speed_chips = {}
        for column, (name, interval_ms) in enumerate(SPEED_PRESETS, start=1):
            chip = self._chip(card, name, lambda value=interval_ms: self.interval_var.set(str(value)))
            self._place_control(chip, 0, column)
            self.speed_chips[interval_ms] = chip

        self._row_label(card, 1, "Intervalo")
        self.interval_entry = self._entry(card, self.interval_var)
        self._place_control(self.interval_entry, 1, 1, columnspan=2)
        self._unit_label(card, 1, "ms")

        self._row_label(card, 2, "Botão")
        self.button_chips = {}
        for column, name in enumerate(BUTTONS, start=1):
            chip = self._chip(card, name, lambda value=name: self.button_var.set(value))
            self._place_control(chip, 2, column)
            self.button_chips[name] = chip

        self._row_label(card, 3, "Atraso")
        self.delay_entry = self._entry(card, self.delay_var)
        self._place_control(self.delay_entry, 3, 1, columnspan=2)
        self._unit_label(card, 3, "segundos")

        self._row_label(card, 4, "Limite")
        self.limit_chip = self._chip(card, "Parar após",
                                     lambda: self.limit_enabled_var.set(not self.limit_enabled_var.get()))
        self._place_control(self.limit_chip, 4, 1)
        self.limit_entry = self._entry(card, self.limit_var)
        self._place_control(self.limit_entry, 4, 2)
        self._unit_label(card, 4, "cliques")
        return card

    def _row_label(self, parent, row, text):
        tk.Label(parent, text=text, bg=self.PANEL, fg=self.MUTED, font=(self.family, 10)).grid(
            row=row, column=0, sticky="w", padx=(0, self._px(16)))

    def _unit_label(self, parent, row, text):
        tk.Label(parent, text=text, bg=self.PANEL, fg=self.MUTED, font=(self.family, 10)).grid(
            row=row, column=3, sticky="w", padx=self._px(3))

    def _place_control(self, widget, row, column, columnspan=1):
        widget.grid(row=row, column=column, columnspan=columnspan, sticky="nsew",
                    padx=self._px(3), pady=self._px(3))

    def _px(self, value):
        return round(value * self.scale)

    def _flat_button(self, parent, **options):
        return tk.Button(parent, relief="flat", bd=0, highlightthickness=0, cursor="hand2", **options)

    def _chip(self, parent, text, command):
        return self._flat_button(parent, text=text, command=command, font=(self.family, 9, "bold"),
                                 pady=self._px(3), padx=self._px(8))

    def _entry(self, parent, variable):
        return tk.Entry(
            parent, textvariable=variable, width=1, justify="center", relief="flat", bd=0,
            font=(self.family, 10, "bold"), bg=self.FIELD, fg=self.TEXT,
            insertbackground=self.TEXT, disabledbackground=self.BG, disabledforeground=self.MUTED,
            highlightthickness=self._px(2), highlightbackground=self.FIELD, highlightcolor=self.ACCENT,
        )

    def _click(self):
        self.clicker.click(self.button_code)

    def toggle(self):
        if self.state != self.IDLE:
            self.stop()
        elif time.monotonic() >= self.start_allowed_at:
            self.start()

    def _toggle_from_hotkey(self):
        now = time.monotonic()
        if now < self.hotkey_allowed_at:
            return
        self.hotkey_allowed_at = now + HOTKEY_DEBOUNCE_S
        if self.state != self.IDLE:
            self.stop()
        elif now >= self.start_allowed_at:
            # Quem aperta F8 já está com o cursor no lugar, então não há o que esperar.
            self.start(skip_delay=True)

    def start(self, skip_delay=False):
        try:
            interval_s, limit, delay_s = parse_settings(
                self.interval_var.get().strip(),
                self.limit_enabled_var.get(),
                self.limit_var.get().strip(),
                self.delay_var.get().strip(),
            )
        except ValueError as error:
            self._set_status(str(error), error=True)
            return
        self.button_code = BUTTONS[self.button_var.get()]

        if self.clicker.connected:
            self.settings = (interval_s, limit, 0 if skip_delay else delay_s)
            self._begin_countdown()
            return

        self.settings = (interval_s, limit, max(delay_s, DELAY_AFTER_PERMISSION_S))
        self._set_state(self.CONNECTING)
        self._set_status("Autorize o controle do mouse no diálogo do sistema.")
        self.connect_attempt += 1
        threading.Thread(target=self._connect, args=(self.connect_attempt,), daemon=True).start()

    def _connect(self, attempt):
        try:
            self.clicker.connect()
        except PortalError as error:
            self.events.put(("connect_failed", attempt, str(error)))
        else:
            self.events.put(("connected", attempt))

    def _begin_countdown(self):
        self._set_state(self.COUNTDOWN)
        self._tick_countdown(self.settings[2])

    def _tick_countdown(self, remaining):
        if remaining > 0:
            self.counter_label.configure(text=str(remaining))
            self._set_status("Posicione o cursor fora desta janela.")
            self.countdown_job = self.root.after(1000, self._tick_countdown, remaining - 1)
            return
        self.countdown_job = None
        if self.pointer_inside:
            self.counter_label.configure(text="0")
            self._to_idle()
            self._set_status("Tire o mouse da janela do Tapster antes de começar.", error=True)
            return
        self.counter_label.configure(text="0")
        self._set_state(self.RUNNING)
        self._set_status("Para parar: F8 ou mouse sobre a janela.")
        interval_s, limit, _ = self.settings
        self.engine.start(interval_s, limit)

    def stop(self):
        if self.state == self.RUNNING:
            # A mensagem final chega no evento "finished" do motor.
            self.engine.stop()
            self._to_idle()
        elif self.state != self.IDLE:
            self.connect_attempt += 1  # Ignora a resposta de uma conexão cancelada.
            self.counter_label.configure(text="0")
            self._to_idle()
            self._set_status("Cancelado.")

    def _to_idle(self):
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None
        self.start_allowed_at = time.monotonic() + RESTART_COOLDOWN_S
        self._set_state(self.IDLE)

    def _set_state(self, state):
        self.state = state
        self._paint_state()

    def _paint_state(self):
        look = {
            self.IDLE: ("PRONTO", self.FIELD, self.TEXT, self.FIELD, self.TEXT,
                        "INICIAR", self.ACCENT, self.ACCENT_HOVER, self.ON_ACCENT, "CLIQUES"),
            self.CONNECTING: ("PERMISSÃO", self.WARN, self.ON_ACCENT, self.WARN, self.WARN,
                              "CANCELAR", self.WARN, self.WARN_HOVER, self.ON_ACCENT, "CLIQUES"),
            self.COUNTDOWN: ("PREPARE-SE", self.WARN, self.ON_ACCENT, self.WARN, self.WARN,
                             "CANCELAR", self.WARN, self.WARN_HOVER, self.ON_ACCENT, "SEGUNDOS"),
            self.RUNNING: ("CLICANDO", self.ACCENT, self.ON_ACCENT, self.ACCENT, self.ACCENT,
                           "PARAR", self.STOP, self.STOP_HOVER, self.ON_ACCENT, "CLIQUES"),
        }[self.state]
        pill_text, pill_bg, pill_fg, strip, counter, action, action_bg, action_hover, action_fg, caption = look
        self.state_pill.configure(text=pill_text, bg=pill_bg, fg=pill_fg)
        self.hero_strip.configure(bg=strip)
        self.counter_label.configure(fg=counter)
        self.counter_caption.configure(text=caption)
        self.action_button.configure(
            text=f"{action}  ·  F8", bg=action_bg, fg=action_fg,
            activebackground=action_hover, activeforeground=action_fg,
        )
        self._sync_inputs()

    def _sync_inputs(self):
        idle = self.state == self.IDLE
        interval = self.interval_var.get().strip()
        for interval_ms, chip in self.speed_chips.items():
            self._paint_chip(chip, interval == str(interval_ms), idle)
        for name, chip in self.button_chips.items():
            self._paint_chip(chip, self.button_var.get() == name, idle)
        limit_enabled = self.limit_enabled_var.get()
        self._paint_chip(self.limit_chip, limit_enabled, idle)
        self.interval_entry.configure(state="normal" if idle else "disabled")
        self.delay_entry.configure(state="normal" if idle else "disabled")
        self.limit_entry.configure(state="normal" if idle and limit_enabled else "disabled")

    def _paint_chip(self, chip, selected, enabled):
        background = self.ACCENT if selected else self.FIELD
        foreground = self.ON_ACCENT if selected else self.TEXT
        chip.configure(
            bg=background,
            fg=foreground,
            activebackground=self.ACCENT_HOVER if selected else self.FIELD_HOVER,
            activeforeground=foreground,
            disabledforeground=self.ON_ACCENT if selected else self.MUTED,
            state="normal" if enabled else "disabled",
        )

    def _set_status(self, text, error=False):
        self.status_label.configure(text=text, fg=self.ERROR if error else self.TEXT)
        self._fit_window()

    def _fit_window(self):
        # Janelas de tamanho fixo não crescem sozinhas se uma mensagem passar de duas linhas.
        lines = self._status_line_count()
        self.status_label.configure(height=max(2, lines))
        self.root.update_idletasks()
        self.root.geometry(f"{self.root.winfo_reqwidth()}x{self.root.winfo_reqheight()}")

    def _status_line_count(self):
        # Sem altura fixa, o rótulo informa de quantas linhas o texto precisa.
        self.status_label.configure(height=0)
        self.root.update_idletasks()
        line_height = tkfont.Font(root=self.root, font=self.status_label.cget("font")).metrics("linespace")
        border = 2 * int(self.status_label.cget("pady")) + 2 * int(self.status_label.cget("bd"))
        return round((self.status_label.winfo_reqheight() - border) / line_height)

    def _on_pointer_enter(self, _event):
        self.pointer_inside = True
        if self.state == self.RUNNING:
            self.stop()

    def _on_pointer_leave(self, _event):
        self.pointer_inside = False

    def _on_any_press(self, _event):
        self.pointer_inside = True
        if self.state == self.RUNNING:
            self.stop()

    def _poll_events(self):
        try:
            while True:
                self._handle_event(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.poll_job = self.root.after(30, self._poll_events)

    def _handle_event(self, kind, *data):
        if kind == "progress":
            if self.state == self.RUNNING:
                self.counter_label.configure(text=str(data[0]))
        elif kind == "finished":
            count, error = data
            self.counter_label.configure(text=str(count))
            if self.state == self.RUNNING:
                self._to_idle()
                if error:
                    self._set_status(f"Parado por erro: {error}", error=True)
                else:
                    self._set_status(f"Concluído: {_format_clicks(count)}.")
            elif error:
                self._set_status(f"Parado por erro: {error}", error=True)
            else:
                self._set_status(f"Parado: {_format_clicks(count)}.")
        elif kind == "connected":
            if data[0] == self.connect_attempt and self.state == self.CONNECTING:
                self._begin_countdown()
        elif kind == "connect_failed":
            attempt, message = data
            if attempt == self.connect_attempt and self.state == self.CONNECTING:
                self._to_idle()
                self._set_status(message, error=True)
        elif kind == "closed":
            self.engine.stop()
            if self.state != self.IDLE:
                self._to_idle()
            self._set_status("O sistema encerrou a permissão do mouse.", error=True)
        elif kind == "toggle":
            self._toggle_from_hotkey()
        elif kind == "show":
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def quit(self):
        # Espera o motor para não encerrar entre apertar e soltar o botão.
        self.engine.stop(wait=True)
        self.root.after_cancel(self.poll_job)
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
        self.root.destroy()


def _format_clicks(count):
    return "1 clique" if count == 1 else f"{count} cliques"


def main():
    parser = argparse.ArgumentParser(description="Autoclicker simples para Linux.")
    parser.add_argument("--toggle", action="store_true", help="inicia ou para os cliques na janela aberta")
    args = parser.parse_args()

    # Sem janela aberta, --toggle só abre o app, para não clicar sem aviso.
    if send_command("toggle" if args.toggle else "show"):
        return
    lock = acquire_instance_lock()
    if lock is None:
        return  # Outra instância ainda está abrindo.

    root = tk.Tk(className="Tapster")
    app = TapsterApp(root)
    server = CommandServer(SOCKET_PATH, app.handle_command)
    try:
        server.start()
    except OSError as error:
        app._set_status(f"O atalho F8 não está disponível: {error}", error=True)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.engine.stop(wait=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
