import time
import tkinter as tk
import unittest

from tapster.settings import BUTTONS
from tapster.ui.app import DELAY_AFTER_PERMISSION_S, TapsterApp
from tapster.ui.state import AppState


class FakeClicker:
    def __init__(self):
        self.connected = True
        self.clicks = []

    def connect(self):
        self.connected = True

    def click(self, button_code):
        self.clicks.append(button_code)


class AppTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        # Oculta, a janela não recebe eventos do cursor real da máquina.
        self.root.withdraw()
        self.clicker = FakeClicker()
        self.app = TapsterApp(self.root, self.clicker)
        self.window = self.app.window
        self.window.delay_var.set("0")
        self.window.interval_var.set("10")

    def tearDown(self):
        self.app.quit()

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)

    def start_running(self):
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, AppState.RUNNING)

    def test_runs_until_limit_and_reports_it(self):
        self.window.limit_enabled_var.set(True)
        self.window.limit_var.set("3")
        self.app.toggle()
        self.pump(0.3)
        self.assertEqual(len(self.clicker.clicks), 3)
        self.assertEqual(self.app.state, AppState.IDLE)
        self.assertEqual(self.window.status_label.cget("text"), "Concluído: 3 cliques.")

    def test_selected_button_is_used(self):
        self.window.button_var.set("Direito")
        self.window.limit_enabled_var.set(True)
        self.window.limit_var.set("1")
        self.app.toggle()
        self.pump(0.1)
        self.assertEqual(self.clicker.clicks, [BUTTONS["Direito"]])

    def test_pointer_entering_window_stops_clicking(self):
        self.start_running()
        self.app._on_pointer_enter(None)
        self.pump(0.05)
        stopped_at = len(self.clicker.clicks)
        self.pump(0.1)
        self.assertEqual(self.app.state, AppState.IDLE)
        self.assertEqual(len(self.clicker.clicks), stopped_at)
        self.assertTrue(self.window.status_label.cget("text").startswith("Parado:"))

    def test_late_click_after_stopping_does_not_restart(self):
        self.start_running()
        self.app._on_pointer_enter(None)
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, AppState.IDLE)

    def test_does_not_start_with_pointer_over_window(self):
        self.app._on_pointer_enter(None)
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, AppState.IDLE)
        self.assertEqual(self.clicker.clicks, [])

    def test_invalid_input_shows_error_without_starting(self):
        self.window.interval_var.set("abc")
        self.app.toggle()
        self.pump(0.02)
        self.assertEqual(self.app.state, AppState.IDLE)
        self.assertIn("intervalo", self.window.status_label.cget("text"))

    def test_hotkey_starts_without_the_delay_and_stops(self):
        self.window.delay_var.set("30")
        self.app.handle_command("toggle")
        self.pump(0.06)
        self.assertEqual(self.app.state, AppState.RUNNING)
        self.app.hotkey_allowed_at = 0.0
        self.app.handle_command("toggle")
        self.pump(0.06)
        self.assertEqual(self.app.state, AppState.IDLE)

    def test_button_still_respects_the_delay(self):
        self.window.delay_var.set("30")
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, AppState.COUNTDOWN)
        self.assertEqual(self.window.counter_label.cget("text"), "30")

    def test_held_hotkey_does_not_flip_repeatedly(self):
        self.app.handle_command("toggle")
        self.app.handle_command("toggle")
        self.app.handle_command("toggle")
        self.pump(0.06)
        self.assertEqual(self.app.state, AppState.RUNNING)

    def test_hotkey_after_permission_keeps_time_to_move_the_cursor(self):
        self.clicker.connected = False
        self.window.delay_var.set("0")
        self.app.handle_command("toggle")
        self.pump(0.1)
        self.assertEqual(self.app.state, AppState.COUNTDOWN)
        self.assertEqual(self.app.settings.delay_s, DELAY_AFTER_PERMISSION_S)

    def test_preset_selects_the_interval(self):
        self.window.speed_chips[20].invoke()
        self.assertEqual(self.window.interval_var.get(), "20")

    def test_counter_shows_clicks_after_finishing(self):
        self.window.limit_enabled_var.set(True)
        self.window.limit_var.set("4")
        self.app.toggle()
        self.pump(0.3)
        self.assertEqual(self.window.counter_label.cget("text"), "4")

    def test_session_closed_stops_clicking(self):
        self.start_running()
        self.app.handle_session_closed()
        self.pump(0.1)
        self.assertEqual(self.app.state, AppState.IDLE)

    def test_cancelled_connection_is_ignored(self):
        self.clicker.connected = False
        self.app.connect_attempt = 7
        self.app._set_state(AppState.CONNECTING)
        self.app.stop()
        self.app.events.put(("connected", 7))
        self.pump(0.1)
        self.assertEqual(self.app.state, AppState.IDLE)
