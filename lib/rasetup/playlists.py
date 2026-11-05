"""Write RetroArch JSON playlists (format 1.5, as written by RetroArch v1.22 playlist.c)."""
import json
import os

from .common import RA_CORES, RA_PLAYLISTS, ensure_dir


def write(name, entries, cores, default_core):
    """entries: dicts with abs path, label, core. Every entry carries its own core_path so a single
    playlist can mix cores (MAME: mame2010 + mame2003-plus)."""
    ensure_dir(RA_PLAYLISTS)
    items = []
    for e in sorted(entries, key=lambda x: x["label"].lower()):
        core = e["core"]
        items.append({
            "path": e["path"],
            "label": e["label"],
            "core_path": os.path.join(RA_CORES, f"{core}_libretro.dylib"),
            "core_name": cores.get(core, {}).get("display_name", core),
            "crc32": "DETECT",
            "db_name": f"{name}.lpl",
        })
    pl = {
        "version": "1.5",
        "default_core_path": os.path.join(RA_CORES, f"{default_core}_libretro.dylib"),
        "default_core_name": cores.get(default_core, {}).get("display_name", default_core),
        "label_display_mode": 0,
        "right_thumbnail_mode": 0,
        "left_thumbnail_mode": 0,
        "thumbnail_match_mode": 0,
        "sort_mode": 0,
        "items": items,
    }
    path = os.path.join(RA_PLAYLISTS, f"{name}.lpl")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pl, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path, len(items)


def remove_stale(managed_before, managed_now):
    for name in set(managed_before) - set(managed_now):
        p = os.path.join(RA_PLAYLISTS, f"{name}.lpl")
        if os.path.exists(p):
            os.remove(p)
