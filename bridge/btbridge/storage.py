"""The persistence rule at runtime (DECISIONS #121, docs/PERSISTENCE.md): "persist only what must survive a reboot".

``boot_cleanup`` runs once per boot in the boot step (``hifi-bridge-boot.service``, before the bridge and the network agent start, so
nothing else writes the data directory at that moment) and removes what a power cut can leave behind on the card:

* temporary files of atomic writes (``.<name>.tmp-<pid>[-<thread>]`` of the config store and the network file, ``<name>.tmp`` of the
  journals and certificates). Every interrupted write leaves one and the pid-named ones never get overwritten, so without this they
  would accumulate with every power cut. A temporary file is never read: the target file is either the old or the new version;
* files of earlier versions that the rule no longer allows on the card (``faults.jsonl``: the fault history lives in RAM now);
* update staging files no persistent state needs any more (``ota.manager.cleanup_staging``).

It never raises: a failing cleanup must not stop the boot step (the status file and the setup code come after it).
"""
from __future__ import annotations

import os
import re
from typing import Callable

TMP_NAME = re.compile(r"^\..+\.tmp-\d+(-\d+)?$|^[^.].*\.tmp$")
TMP_DIRS = ("", "certs")                         # relative to the data directory; ota/ is handled by cleanup_staging
LEGACY = ("faults.jsonl", "faults.jsonl.tmp")    # fault history on the card (before DECISIONS #121)


def boot_cleanup(data_dir: str, log: Callable[[str], None] = print) -> list[str]:
    """Remove leftovers that must not survive a reboot. Returns the removed paths relative to ``data_dir``."""
    from .ota import manager
    removed: list[str] = []
    try:
        for sub in TMP_DIRS:
            d = os.path.join(data_dir, sub)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if (TMP_NAME.match(name) or (sub == "" and name in LEGACY)) and os.path.isfile(p) and not os.path.islink(p):
                    os.unlink(p)
                    removed.append(os.path.join(sub, name) if sub else name)
        removed += manager.cleanup_staging(data_dir, at_boot=True)
    except OSError as e:
        log(f"boot cleanup: stopped ({type(e).__name__}: {e}); removed so far: {', '.join(removed) or 'nothing'}")
        return removed
    if removed:
        log(f"boot cleanup: removed {', '.join(removed)}")
    return removed
