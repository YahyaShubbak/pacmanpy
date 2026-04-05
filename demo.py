"""Demo: three parallel Pac-Man progress bars (EndeavourOS / Arch pacman style).

Run:
    python demo.py

Disable colours:
    NO_COLOR=1 python demo.py
"""

import threading
import time

from pacman_prog_bar import MultiPacmanProgress


def _worker(mp: MultiPacmanProgress, bar_id: int, total: int, duration: float) -> None:
    """Simulate work by linearly advancing a bar over *duration* seconds."""
    interval = duration / total
    for step in range(1, total + 1):
        time.sleep(interval)
        mp.update(bar_id, step)


def main() -> None:
    # (label, total work units, simulated duration in seconds)
    tasks = [
        ("installing archlinux-keyring",  80,  4.0),
        ("installing base-devel",         200,  7.0),
        ("downloading linux-firmware",    500, 11.0),
    ]

    mp = MultiPacmanProgress(refresh_hz=20, mouth_hz=4)

    # Register all bars before starting so labels are aligned from the start.
    bar_ids = [mp.add_bar(label, total) for label, total, _ in tasks]

    mp.start()

    threads = [
        threading.Thread(
            target=_worker,
            args=(mp, bar_id, total, duration),
            daemon=True,
        )
        for bar_id, (_, total, duration) in zip(bar_ids, tasks)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    mp.stop()


if __name__ == "__main__":
    main()
