"""Reusable Pacman-style console progress bars with smooth mouth animation.

Supports multiple simultaneous bars rendered in-place via ANSI cursor movement.
Matches the visual style of Arch Linux / EndeavourOS pacman output:

    installing foo   00:05 [----c o  o  o  o  o  o  o  o  o  o  ]  12%
    downloading bar  00:12 [------------------------------------------] 100%

All frequency parameters use Hertz (Hz), i.e. cycles per second.
"""

import os
import sys
import threading
import time
from typing import List, Optional, Tuple

_ORANGE = "\033[38;2;255;165;0m"
_RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Low-level rendering helpers
# ---------------------------------------------------------------------------

def _elapsed_str(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _mouth_char(elapsed_s: float, mouth_hz: float, colorize: bool) -> str:
    """Return 'C' (open) or 'c' (closed), orange-coloured when *colorize* is True."""
    phase = (elapsed_s * mouth_hz) % 1.0
    ch = "C" if phase < 0.5 else "c"
    return (_ORANGE + ch + _RESET) if colorize else ch


def _render_bar_content(
    current: int,
    total: int,
    slots: int,
    elapsed_s: float,
    mouth_hz: float,
    colorize: bool,
) -> str:
    """Return bar content with *slots × 2* visible character columns.

    Each slot occupies two visible columns:

    - eaten    → ``--``
    - pac-man  → ``<mouth> ``  (mouth char + trailing space; orange if *colorize*)
    - remaining → ``o ``       (dot + trailing space)

    At 100 % all slots show ``--``.
    """
    if total <= 0:
        total = 1
    ratio = max(0.0, min(1.0, float(current) / float(total)))
    if ratio >= 1.0:
        return "-" * (slots * 2)

    pac_slot = min(int(ratio * slots), slots - 1)
    eaten = "--" * pac_slot
    mouth = _mouth_char(elapsed_s, mouth_hz, colorize) + " "
    remaining = "o " * (slots - pac_slot - 1)
    return eaten + mouth + remaining


def _compute_slots(terminal_cols: int, label_width: int) -> int:
    """Return how many 2-column slots fit between the bar brackets.

    Line layout (visible columns)::

        <label_width> <SP> MM:SS <SP> [ <slots×2> ] <SP> XXX%
        = label_width + 1 + 5 + 1 + 1 + slots*2 + 1 + 1 + 4
        = label_width + 14 + slots*2
    """
    overhead = label_width + 14
    slots = (terminal_cols - overhead) // 2
    return max(5, min(20, slots))


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

class _BarState:
    __slots__ = ("label", "total", "current", "start_time")

    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = total
        self.current = 0
        self.start_time = time.perf_counter()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class MultiPacmanProgress:
    """Render multiple Pac-Man-style progress bars simultaneously, in-place.

    Parameters
    ----------
    refresh_hz : float
        Repaint frequency in Hz (frames per second). Default 15.
    mouth_hz : float
        Full open/close mouth cycle frequency in Hz. Default 3.
    stream :
        Output stream. Defaults to ``sys.stdout``.

    Usage::

        mp = MultiPacmanProgress()
        bar_a = mp.add_bar("Installing foo", total=100)
        bar_b = mp.add_bar("Downloading bar", total=1000)
        mp.start()
        # call update() from any thread:
        mp.update(bar_a, 42)
        mp.update(bar_b, 750)
        mp.stop()

    Or as a context manager::

        with MultiPacmanProgress() as mp:
            bar = mp.add_bar("Downloading", total=1000)
            mp.update(bar, 500)

    Set the environment variable ``NO_COLOR`` to disable ANSI colours.
    """

    def __init__(
        self,
        *,
        refresh_hz: float = 15.0,
        mouth_hz: float = 3.0,
        stream=None,
    ) -> None:
        self.refresh_hz = max(1.0, float(refresh_hz))
        self.mouth_hz = max(0.1, float(mouth_hz))
        self.stream = stream or sys.stdout

        self._bars: List[_BarState] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._n_painted = 0  # number of lines written in the last paint pass

        self._enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self._colorize = self._enabled and ("NO_COLOR" not in os.environ)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_bar(self, label: str, total: int) -> int:
        """Register a new progress bar. Returns an integer ID for :meth:`update`."""
        bar = _BarState(label=str(label), total=max(1, int(total)))
        with self._lock:
            bar_id = len(self._bars)
            self._bars.append(bar)
        return bar_id

    def update(self, bar_id: int, current: int) -> None:
        """Set the current progress value for bar *bar_id*."""
        value = max(0, int(current))
        with self._lock:
            if 0 <= bar_id < len(self._bars):
                bar = self._bars[bar_id]
                bar.current = min(value, bar.total)

    def start(self) -> "MultiPacmanProgress":
        """Spawn the background render thread. Returns *self* for chaining."""
        if self._enabled:
            self._thread = threading.Thread(
                target=self._run, name="pacman-progress", daemon=True
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        """Stop rendering and paint the final state."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._enabled:
            self._paint()

    def __enter__(self) -> "MultiPacmanProgress":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Rendering  (called from render thread, or from stop() on main thread)
    # ------------------------------------------------------------------

    def _snapshot(self) -> List[Tuple[str, int, int, float]]:
        """Return ``(label, total, current, start_time)`` tuples, thread-safely."""
        with self._lock:
            return [(b.label, b.total, b.current, b.start_time) for b in self._bars]

    def _paint(self) -> None:
        snapshot = self._snapshot()
        if not snapshot:
            return

        now = time.perf_counter()
        label_width = max(len(label) for label, *_ in snapshot)

        try:
            cols = os.get_terminal_size(self.stream.fileno()).columns
        except (AttributeError, OSError, ValueError):
            cols = 80

        slots = _compute_slots(cols, label_width)
        w = self.stream.write
        n = len(snapshot)

        # Move cursor back up to overwrite the previous paint pass.
        if self._n_painted > 0:
            w(f"\033[{self._n_painted}A")
        self._n_painted = n

        for label, total, current, start_time in snapshot:
            elapsed_s = now - start_time
            content = _render_bar_content(
                current, total, slots, elapsed_s, self.mouth_hz, self._colorize
            )
            pct = int(100 * current / max(1, total))
            line = (
                f"{label[:label_width].ljust(label_width)} "
                f"{_elapsed_str(elapsed_s)} "
                f"[{content}] {pct:3d}%"
            )
            w(f"\r{line}\033[K\n")

        self.stream.flush()

    def _run(self) -> None:
        delay = 1.0 / self.refresh_hz
        while not self._stop_event.is_set():
            self._paint()
            self._stop_event.wait(delay)
