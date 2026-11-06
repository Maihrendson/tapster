import os
import tempfile
import threading
import unittest
from pathlib import Path

from tapster.ipc import CommandServer, acquire_instance_lock, send_command


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
        self.addCleanup(os.close, first)
        self.assertIsNone(acquire_instance_lock(lock_path))
