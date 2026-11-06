"""Detect retroarch.cfg directories that no longer point to the library, and rescue files.

When RetroArch starts and a configured directory does not exist (the library's disk is not
mounted yet, or macOS has not granted access to removable volumes), it falls back to its default
(~/Documents/RetroArch/saves, .../states, screenshots "default") and, with
config_save_on_exit, writes that fallback into retroarch.cfg when it quits. From then on saves
and states go to the Mac, and the MAME cores read their button layouts (cfg/default.cfg,
cfg/<set>.cfg under the save directory) from an empty folder, so MAME games get MAME's
factory buttons.
"""
import filecmp
import os
import shutil

from .common import LIBRARY, RA_CFG, ensure_dir, info, ok, warn

# keys whose files are copied back to the library when the key pointed somewhere else
RESCUE = ("savefile_directory", "savestate_directory", "screenshot_directory")


def _current():
    from .configure import read_template   # configure imports this module
    return read_template(RA_CFG, {}) if os.path.exists(RA_CFG) else {}


def _resolve(v):
    if not v or v == "default":
        return None
    return os.path.realpath(os.path.expanduser(v))


def wrong_paths(wanted):
    """[(key, current, wanted)] for managed keys that should be in the library but are not."""
    cur = _current()
    out = []
    for k, w in wanted.items():
        if not w.startswith(LIBRARY + "/") or k not in cur:
            continue
        if _resolve(cur[k]) != os.path.realpath(w):
            out.append((k, cur[k], w))
    return out


def _rescue(src, dst):
    """Copy files from src that are not in dst. Never overwrites: differing files are listed."""
    copied, conflicts = [], []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):          # .DS_Store, AppleDouble "._x"
                continue
            s = os.path.join(root, name)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst, rel)
            if not os.path.exists(d):
                ensure_dir(os.path.dirname(d))
                shutil.copy2(s, d)
                copied.append(rel)
            elif not filecmp.cmp(s, d, shallow=False):
                conflicts.append(rel)
    return copied, conflicts


def check(wanted):
    """Report directories outside the library and copy their new files into it.
    The caller then rewrites the keys (merge_cfg). Returns the number of wrong keys."""
    bad = wrong_paths(wanted)
    if not bad:
        ok(f"retroarch.cfg directories point to the library ({LIBRARY})")
        return 0
    warn("retroarch.cfg has directories outside the library. RetroArch was probably opened while "
         f"{LIBRARY} was not available (disk not mounted yet, or no access to removable volumes) "
         "and saved its defaults:")
    for k, cur, w in bad:
        info(f"{k} = \"{cur}\"  (should be {w})")
    for k, cur, w in bad:
        src = _resolve(cur)
        if k not in RESCUE or not src or not os.path.isdir(src):
            continue
        copied, conflicts = _rescue(src, w)
        for rel in copied:
            info(f"copied to the library: {rel}")
        for rel in conflicts:
            warn(f"differs from the copy in the library, left in {src}: {rel}")
        if copied or conflicts:
            info(f"{src} is no longer used; delete it once you have checked the files above")
    return len(bad)
