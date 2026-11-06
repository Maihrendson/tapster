import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path

import tapster
from tapster import (
    ClickEngine,
    CommandServer,
    PortalError,
    TapsterApp,
    acquire_instance_lock,
    parse_settings,
    send_command,
)


class ParseSettingsTest(unittest.TestCase):
    def test_valid_values(self):
        self.assertEqual(parse_settings("250", True, "5", "2"), (0.25, 5, 2))

    def test_limit_is_ignored_when_disabled(self):
        self.assertEqual(parse_settings("100", False, "abc", "0"), (0.1, None, 0))

    def test_invalid_values(self):
        for args in (
            ("abc", False, "1", "0"),
            ("1", False, "1", "0"),
            ("100", True, "0", "0"),
            ("100", False, "1", "-1"),
            ("100", False, "1", "61"),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                parse_settings(*args)


class ClickEngineTest(unittest.TestCase):
    def run_engine(self, click, interval_s=0.001, limit=None):
        finished = threading.Event()
        outcome = {}

        def on_finished(count, error):
            outcome.update(count=count, error=error)
            finished.set()

        engine = ClickEngine(click, lambda _count: None, on_finished)
        engine.start(interval_s, limit)
        return engine, finished, outcome

    def test_stops_at_limit(self):
        clicks = []
        _, finished, outcome = self.run_engine(lambda: clicks.append(1), limit=5)
        self.assertTrue(finished.wait(2))
        self.assertEqual(len(clicks), 5)
        self.assertEqual(outcome, {"count": 5, "error": None})

    def test_stop_interrupts_a_long_interval(self):
        engine, finished, _ = self.run_engine(lambda: None, interval_s=60)
        time.sleep(0.05)
        engine.stop()
        self.assertTrue(finished.wait(1))

    def test_click_error_ends_the_run(self):
        def fail():
            raise PortalError("sem permissão")

        _, finished, outcome = self.run_engine(fail)
        self.assertTrue(finished.wait(2))
        self.assertEqual(outcome, {"count": 0, "error": "sem permissão"})


class CommandChannelTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.path = self.dir / "test.sock"

    def test_command_reaches_the_server(self):
        received = threading.Event()
        commands = []

        def on_command(command):
            commands.append(command)
            received.set()

        server = CommandServer(self.path, on_command)
        server.start()
        self.addCleanup(server.close)
        self.assertTrue(send_command("toggle", self.path))
        self.assertTrue(received.wait(1))
        self.assertEqual(commands, ["toggle"])

    def test_send_reports_when_no_window_is_open(self):
        self.assertFalse(send_command("toggle", self.path))

    def test_server_replaces_a_stale_socket(self):
        self.path.touch()
        server = CommandServer(self.path, lambda _command: None)
        server.start()
        self.addCleanup(server.close)
        self.assertTrue(send_command("show", self.path))

    def test_socket_is_removed_on_close(self):
        server = CommandServer(self.path, lambda _command: None)
        server.start()
        server.close()
        self.assertFalse(self.path.exists())
        self.assertFalse(send_command("show", self.path))

    def test_only_one_instance_gets_the_lock(self):
        lock_path = self.dir / "test.lock"
        first = acquire_instance_lock(lock_path)
        self.assertIsNotNone(first)
        self.addCleanup(tapster.os.close, first)
        self.assertIsNone(acquire_instance_lock(lock_path))


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
        self.app = TapsterApp(self.root, clicker=self.clicker)
        self.app.delay_var.set("0")
        self.app.interval_var.set("10")

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
        self.assertEqual(self.app.state, TapsterApp.RUNNING)

    def test_runs_until_limit_and_reports_it(self):
        self.app.limit_enabled_var.set(True)
        self.app.limit_var.set("3")
        self.app.toggle()
        self.pump(0.3)
        self.assertEqual(len(self.clicker.clicks), 3)
        self.assertEqual(self.app.state, TapsterApp.IDLE)
        self.assertEqual(self.app.status_label.cget("text"), "Concluído: 3 cliques.")

    def test_selected_button_is_used(self):
        self.app.button_var.set("Direito")
        self.app.limit_enabled_var.set(True)
        self.app.limit_var.set("1")
        self.app.toggle()
        self.pump(0.1)
        self.assertEqual(self.clicker.clicks, [tapster.BUTTONS["Direito"]])

    def test_pointer_entering_window_stops_clicking(self):
        self.start_running()
        self.app._on_pointer_enter(None)
        self.pump(0.05)
        stopped_at = len(self.clicker.clicks)
        self.pump(0.1)
        self.assertEqual(self.app.state, TapsterApp.IDLE)
        self.assertEqual(len(self.clicker.clicks), stopped_at)
        self.assertTrue(self.app.status_label.cget("text").startswith("Parado:"))

    def test_late_click_after_stopping_does_not_restart(self):
        self.start_running()
        self.app._on_pointer_enter(None)
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, TapsterApp.IDLE)

    def test_does_not_start_with_pointer_over_window(self):
        self.app._on_pointer_enter(None)
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, TapsterApp.IDLE)
        self.assertEqual(self.clicker.clicks, [])

    def test_invalid_input_shows_error_without_starting(self):
        self.app.interval_var.set("abc")
        self.app.toggle()
        self.pump(0.02)
        self.assertEqual(self.app.state, TapsterApp.IDLE)
        self.assertIn("intervalo", self.app.status_label.cget("text"))

    def test_hotkey_starts_without_the_delay_and_stops(self):
        self.app.delay_var.set("30")
        self.app.handle_command("toggle")
        self.pump(0.06)
        self.assertEqual(self.app.state, TapsterApp.RUNNING)
        self.app.hotkey_allowed_at = 0.0
        self.app.handle_command("toggle")
        self.pump(0.06)
        self.assertEqual(self.app.state, TapsterApp.IDLE)

    def test_button_still_respects_the_delay(self):
        self.app.delay_var.set("30")
        self.app.toggle()
        self.pump(0.05)
        self.assertEqual(self.app.state, TapsterApp.COUNTDOWN)
        self.assertEqual(self.app.counter_label.cget("text"), "30")

    def test_held_hotkey_does_not_flip_repeatedly(self):
        self.app.handle_command("toggle")
        self.app.handle_command("toggle")
        self.app.handle_command("toggle")
        self.pump(0.06)
        self.assertEqual(self.app.state, TapsterApp.RUNNING)

    def test_hotkey_after_permission_keeps_time_to_move_the_cursor(self):
        self.clicker.connected = False
        self.app.delay_var.set("0")
        self.app.handle_command("toggle")
        self.pump(0.1)
        self.assertEqual(self.app.state, TapsterApp.COUNTDOWN)
        self.assertEqual(self.app.settings[2], tapster.DELAY_AFTER_PERMISSION_S)

    def test_preset_selects_the_interval(self):
        self.app.speed_chips[20].invoke()
        self.assertEqual(self.app.interval_var.get(), "20")

    def test_counter_shows_clicks_after_finishing(self):
        self.app.limit_enabled_var.set(True)
        self.app.limit_var.set("4")
        self.app.toggle()
        self.pump(0.3)
        self.assertEqual(self.app.counter_label.cget("text"), "4")

    def test_cancelled_connection_is_ignored(self):
        self.clicker.connected = False
        self.app.connect_attempt = 7
        self.app._set_state(TapsterApp.CONNECTING)
        self.app.stop()
        self.app.events.put(("connected", 7))
        self.pump(0.1)
        self.assertEqual(self.app.state, TapsterApp.IDLE)


if __name__ == "__main__":
    unittest.main()
