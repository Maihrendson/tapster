import threading
import time
import unittest

from tapster.engine import ClickEngine
from tapster.errors import ClickerError


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
            raise ClickerError("sem permissão")

        _, finished, outcome = self.run_engine(fail)
        self.assertTrue(finished.wait(2))
        self.assertEqual(outcome, {"count": 0, "error": "sem permissão"})
