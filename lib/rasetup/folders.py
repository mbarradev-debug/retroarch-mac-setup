"""Detect system folders in ROMS, propose ES-DE -> libretro renames, merge safely, log for undo."""
import os

from .common import (CACHE, NO_SNAPSHOT_DIRNAME, ROMS, confirm, ensure_dir, info, is_hidden,
                     now_iso, ok, step, table, warn)

RENAME_LOG = CACHE + "/rename-log.tsv"


def _log(action, src, dst):
    ensure_dir(CACHE)
    with open(RENAME_LOG, "a", encoding="utf-8") as f:
        f.write(f"{now_iso()}\t{action}\t{src}\t{dst}\n")


def detect(systems):
    """Return (present, proposals, unknown).
    present: {libretro_name: folder_path}; proposals: [(current, new)]; unknown: [names]."""
    by_esde = {}
    for name, s in systems.items():
        for short in s.get("esde_names", []):
            by_esde[short.lower()] = name
    by_lower = {n.lower(): n for n in systems}

    present, proposals, unknown = {}, [], []
    for entry in sorted(os.listdir(ROMS)):
        path = os.path.join(ROMS, entry)
        if not os.path.isdir(path) or is_hidden(entry) or entry.startswith("_") or entry == NO_SNAPSHOT_DIRNAME:
            continue
        if entry in systems:
            present[entry] = path
        elif entry.lower() in by_lower:          # same name, different case
            proposals.append((entry, by_lower[entry.lower()]))
        elif entry.lower() in by_esde:
            proposals.append((entry, by_esde[entry.lower()]))
        else:
            unknown.append(entry)
    return present, proposals, unknown


def _relfiles(root):
    out = []
    for d, dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), root))
    return out


def _merge(src, dst):
    """Move every file of src into dst. Refuses if any (non-AppleDouble) file already exists."""
    files = _relfiles(src)
    clashes = [r for r in files if not os.path.basename(r).startswith("._")
               and os.path.exists(os.path.join(dst, r))]
    if clashes:
        return clashes
    for rel in files:
        s, d = os.path.join(src, rel), os.path.join(dst, rel)
        if os.path.exists(d):            # only AppleDouble metadata can reach this; keep src copy
            continue
        ensure_dir(os.path.dirname(d))
        os.rename(s, d)
        _log("merge-file", s, d)
    # remove now-empty directories (bottom-up); never removes anything with content
    for d, dirs, files in os.walk(src, topdown=False):
        try:
            os.rmdir(d)
        except OSError:
            pass
    return []


def rename_folders(systems):
    step("Detecting system folders in " + ROMS)
    present, proposals, unknown = detect(systems)
    for name in present:
        ok(f"{name}")
    for u in unknown:
        warn(f"'{u}' is not in systems.json — skipped (add it to the map to integrate it)")

    if proposals:
        info("These folders use ES-DE names and will be renamed to the libretro-database name.")
        info("Only the folders are renamed; ROM files inside are never touched.")
        rows = []
        for cur, new in proposals:
            target = os.path.join(ROMS, new)
            same = os.path.exists(target) and os.path.samefile(os.path.join(ROMS, cur), target)
            note = "merge into existing folder" if os.path.exists(target) and not same else ""
            rows.append((cur, new, note))
        table(rows, ["Current", "New", ""])
        if confirm("Rename these folders?"):
            for cur, new in proposals:
                src, dst = os.path.join(ROMS, cur), os.path.join(ROMS, new)
                if os.path.exists(dst) and not os.path.samefile(src, dst):
                    clashes = _merge(src, dst)
                    if clashes:
                        warn(f"'{cur}' NOT merged into '{new}': {len(clashes)} file(s) exist in both. Examples:")
                        for c in clashes[:10]:
                            info("      " + c)
                        warn("Resolve the duplicates by hand and run the script again.")
                        continue
                    ok(f"merged {cur} → {new}")
                else:
                    if os.path.exists(dst):  # case-only rename on a case-insensitive volume
                        tmp = src + ".renaming"
                        os.rename(src, tmp)
                        os.rename(tmp, dst)
                    else:
                        os.rename(src, dst)
                    _log("rename", src, dst)
                    ok(f"renamed {cur} → {new}")
                present[new] = dst
            info(f"Change log: {RENAME_LOG}  (revert with ./undo-rename.sh)")
        else:
            warn("Rename declined. Folders with ES-DE names are skipped this run.")
    return dict(sorted(present.items())), unknown
