"""Repetição dos cliques, independente de como cada clique é enviado."""

import threading
import time

from tapster.errors import ClickerError


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
            # O limite evita travar a interface se o envio do clique não responder.
            self._thread.join(timeout=1)

    def _run(self, interval_s, limit, stop):
        count = 0
        error = None
        next_click = time.monotonic()
        while not stop.is_set() and (limit is None or count < limit):
            try:
                self._click()
            except ClickerError as exc:
                error = str(exc)
                break
            count += 1
            self._on_progress(count)
            if limit is not None and count >= limit:
                break
            # Se um clique atrasar, segue em frente em vez de compensar com uma rajada.
            next_click = max(next_click + interval_s, time.monotonic())
            stop.wait(next_click - time.monotonic())
        self._on_finished(count, error)
