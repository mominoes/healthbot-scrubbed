"""Native macOS folder dialog via osascript. Returns a real path; never reads files."""
from __future__ import annotations

import subprocess

PROMPT = "Choose medical records folder"
SCRIPT = f'POSIX path of (choose folder with prompt "{PROMPT}")'


class PickerCancelled(Exception):
    pass


class PickerError(Exception):
    pass


def choose_folder(timeout: float = 600) -> str:
    """Open the folder dialog and return the chosen POSIX path.

    Raises PickerCancelled if the user cancels (osascript error -128), PickerError on
    any other failure. The error never includes osascript's stderr.
    """
    try:
        proc = subprocess.run(["osascript", "-e", SCRIPT], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        raise PickerError("osascript not available (the folder picker needs macOS)")
    except subprocess.TimeoutExpired:
        raise PickerError("folder dialog timed out")
    if proc.returncode != 0:
        if "-128" in (proc.stderr or ""):
            raise PickerCancelled()
        raise PickerError("folder dialog failed")
    path = proc.stdout.rstrip("\r\n")
    if not path:
        raise PickerError("folder dialog returned nothing")
    return path
