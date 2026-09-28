"""The only place OmaSkins runs commands that change the system.

Every real action goes through `run()`, which takes an argument list (never a shell string, so
nothing in a theme name, path or URL can be read as extra shell syntax) and refuses any program
that isn't in ALLOWED. The prototype allows nothing; each go-live batch adds exactly the
programs it needs, so this list is always the complete answer to "what can OmaSkins do?".

(data.py also calls a few programs, all read-only: pacman -Q, fc-list, hyprctl getoption, magick
for thumbnails in the app's own cache.)
"""

import subprocess

ALLOWED = frozenset()  # grows batch by batch; empty = prototype, nothing runs


class NotAllowed(Exception):
    pass


def check(argv):
    """Raise NotAllowed unless argv is a non-empty list whose program is allow-listed."""
    if isinstance(argv, str) or not argv or not all(isinstance(a, str) for a in argv):
        raise NotAllowed(f"not an argument list: {argv!r}")
    if argv[0] not in ALLOWED:
        raise NotAllowed(f"{argv[0]} is not on OmaSkins' allow-list")


def run(argv, timeout=300):
    """Run an allow-listed command; returns the CompletedProcess (never raises on a non-zero exit)."""
    check(argv)
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
