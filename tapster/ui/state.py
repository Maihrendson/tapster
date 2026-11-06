from enum import Enum, auto


class AppState(Enum):
    IDLE = auto()
    CONNECTING = auto()
    COUNTDOWN = auto()
    RUNNING = auto()
