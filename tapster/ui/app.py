"""Comportamento do aplicativo: estados, contagem regressiva e eventos."""

import queue
import threading
import time

from tapster.engine import ClickEngine
from tapster.errors import ClickerError
from tapster.settings import parse_settings
from tapster.ui.state import AppState
from tapster.ui.window import TapsterWindow

# Um clique em andamento pode cair no botão logo depois de parar. Sem esta
# pausa, ele acionaria "Iniciar" e o autoclick recomeçaria sozinho.
RESTART_COOLDOWN_S = 0.6
# Segurar a tecla não pode iniciar e parar várias vezes seguidas.
HOTKEY_DEBOUNCE_S = 0.3
# Ao autorizar, o cursor está sobre o diálogo do sistema. Este tempo mínimo
# permite levá-lo ao lugar certo antes do primeiro clique.
DELAY_AFTER_PERMISSION_S = 3
EVENT_POLL_MS = 30


class TapsterApp:
    def __init__(self, root, clicker):
        self.root = root
        self.clicker = clicker
        # Outras threads só falam com a interface por esta fila, lida na thread do Tk.
        self.events = queue.Queue()
        self.engine = ClickEngine(
            self._click,
            lambda count: self.events.put(("progress", count)),
            lambda count, error: self.events.put(("finished", count, error)),
        )
        self.state = AppState.IDLE
        self.pointer_inside = False
        self.start_allowed_at = 0.0
        self.hotkey_allowed_at = 0.0
        self.countdown_job = None
        self.button_code = None
        self.settings = None
        self.connect_attempt = 0

        self.window = TapsterWindow(root, on_action=self.toggle, on_close=self.quit)
        self.window.set_status("Aperte F8 para clicar onde o cursor estiver.")
        self.window.bind_pointer(self._on_pointer_enter, self._on_pointer_leave, self._on_any_press)
        self._poll_events()

    def handle_command(self, command):
        """Recebe comandos externos. Pode ser chamado de outra thread."""
        if command in ("toggle", "show"):
            self.events.put((command,))

    def handle_session_closed(self):
        """Avisa que o sistema encerrou a permissão. Pode ser chamado de outra thread."""
        self.events.put(("closed",))

    def show_error(self, message):
        self.window.set_status(message, error=True)

    def toggle(self):
        if self.state != AppState.IDLE:
            self.stop()
        elif time.monotonic() >= self.start_allowed_at:
            self.start()

    def start(self, skip_delay=False):
        try:
            settings = parse_settings(*self.window.form_values())
        except ValueError as error:
            self.show_error(str(error))
            return
        self.button_code = self.window.selected_button_code()

        if self.clicker.connected:
            self.settings = settings._replace(delay_s=0) if skip_delay else settings
            self._begin_countdown()
            return

        self.settings = settings._replace(delay_s=max(settings.delay_s, DELAY_AFTER_PERMISSION_S))
        self._set_state(AppState.CONNECTING)
        self.window.set_status("Autorize o controle do mouse no diálogo do sistema.")
        self.connect_attempt += 1
        threading.Thread(target=self._connect, args=(self.connect_attempt,), daemon=True).start()

    def stop(self):
        if self.state == AppState.RUNNING:
            # A mensagem final chega no evento "finished" do motor.
            self.engine.stop()
            self._to_idle()
        elif self.state != AppState.IDLE:
            self.connect_attempt += 1  # Ignora a resposta de uma conexão cancelada.
            self.window.set_counter(0)
            self._to_idle()
            self.window.set_status("Cancelado.")

    def quit(self):
        # Espera o motor para não encerrar entre apertar e soltar o botão.
        self.engine.stop(wait=True)
        self.root.after_cancel(self.poll_job)
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
        self.root.destroy()

    def _click(self):
        self.clicker.click(self.button_code)

    def _toggle_from_hotkey(self):
        now = time.monotonic()
        if now < self.hotkey_allowed_at:
            return
        self.hotkey_allowed_at = now + HOTKEY_DEBOUNCE_S
        if self.state != AppState.IDLE:
            self.stop()
        elif now >= self.start_allowed_at:
            # Quem aperta F8 já está com o cursor no lugar, então não há o que esperar.
            self.start(skip_delay=True)

    def _connect(self, attempt):
        try:
            self.clicker.connect()
        except ClickerError as error:
            self.events.put(("connect_failed", attempt, str(error)))
        else:
            self.events.put(("connected", attempt))

    def _begin_countdown(self):
        self._set_state(AppState.COUNTDOWN)
        self._tick_countdown(self.settings.delay_s)

    def _tick_countdown(self, remaining):
        if remaining > 0:
            self.window.set_counter(remaining)
            self.window.set_status("Posicione o cursor fora desta janela.")
            self.countdown_job = self.root.after(1000, self._tick_countdown, remaining - 1)
            return
        self.countdown_job = None
        self.window.set_counter(0)
        if self.pointer_inside:
            self._to_idle()
            self.show_error("Tire o mouse da janela do Tapster antes de começar.")
            return
        self._set_state(AppState.RUNNING)
        self.window.set_status("Para parar: F8 ou mouse sobre a janela.")
        self.engine.start(self.settings.interval_s, self.settings.limit)

    def _to_idle(self):
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None
        self.start_allowed_at = time.monotonic() + RESTART_COOLDOWN_S
        self._set_state(AppState.IDLE)

    def _set_state(self, state):
        self.state = state
        self.window.show_state(state)

    def _on_pointer_enter(self, _event):
        self.pointer_inside = True
        if self.state == AppState.RUNNING:
            self.stop()

    def _on_pointer_leave(self, _event):
        self.pointer_inside = False

    def _on_any_press(self, _event):
        self.pointer_inside = True
        if self.state == AppState.RUNNING:
            self.stop()

    def _poll_events(self):
        try:
            while True:
                self._handle_event(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.poll_job = self.root.after(EVENT_POLL_MS, self._poll_events)

    def _handle_event(self, kind, *data):
        if kind == "progress":
            if self.state == AppState.RUNNING:
                self.window.set_counter(data[0])
        elif kind == "finished":
            self._on_finished(*data)
        elif kind == "connected":
            if data[0] == self.connect_attempt and self.state == AppState.CONNECTING:
                self._begin_countdown()
        elif kind == "connect_failed":
            attempt, message = data
            if attempt == self.connect_attempt and self.state == AppState.CONNECTING:
                self._to_idle()
                self.show_error(message)
        elif kind == "closed":
            self.engine.stop()
            if self.state != AppState.IDLE:
                self._to_idle()
            self.show_error("O sistema encerrou a permissão do mouse.")
        elif kind == "toggle":
            self._toggle_from_hotkey()
        elif kind == "show":
            self.window.bring_to_front()

    def _on_finished(self, count, error):
        self.window.set_counter(count)
        reached_the_end = self.state == AppState.RUNNING
        if reached_the_end:
            self._to_idle()
        if error:
            self.show_error(f"Parado por erro: {error}")
        elif reached_the_end:
            self.window.set_status(f"Concluído: {_format_clicks(count)}.")
        else:
            self.window.set_status(f"Parado: {_format_clicks(count)}.")


def _format_clicks(count):
    return "1 clique" if count == 1 else f"{count} cliques"
