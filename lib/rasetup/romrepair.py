"""Complete MAME sets with ROM files found in other zips of the same collection.

A collection assembled from several MAME versions has sets whose files sit in a different zip
than the one the core looks in: the parent/clone tree changed between versions (ffightu needs
files from ffight in MAME 2003-Plus, but this collection keeps them in ffightua.zip). For every
set no core can start, the script looks for each missing file by CRC in all the zips of the
folder; when every missing file is found, it can add copies to the set's own zip, under the
name the core expects, so the set starts.

Only files are added: nothing in a zip is changed or removed. Each zip is backed up before it is
touched (retroarch-cache/romrepair-backup/<date>/) and written through a temporary copy, and the
change is logged in rename-log.tsv, so ./undo-rename.sh puts the original zips back.
"""
import collections
import datetime
import os
import shutil
import zipfile

from . import mame
from .common import CACHE, confirm, ensure_dir, info, ok, step, table, warn
from .folders import _log

BACKUP_DIR = CACHE + "/romrepair-backup"


def _chain_contents(name, dat, have):
    """CRCs and file names MAME sees for a set: its zip plus the parent/BIOS chain."""
    crcs, names = set(have[name][0]), set(have[name][1])
    cur, seen = dat[name][2], set()
    while cur and cur not in seen:
        seen.add(cur)
        if have.get(cur):
            crcs |= have[cur][0]
            names |= have[cur][1]
        cur = dat.get(cur, [None, None, None])[2]
    return crcs, names


def plan(folder, cores, manifest, incompatible):
    """[(set, label, core, [(target name, crc, source set)])] for sets that can be completed."""
    dats = {c: mame._index(c) for c in cores if c in mame.CORE_DATS}
    dats = {c: d for c, d in dats.items() if d}
    have = {}
    for f in os.listdir(folder):
        if f.lower().endswith(".zip") and not f.startswith("."):
            have[f[:-4].lower()] = mame._zip_contents(manifest, os.path.join(folder, f), f[:-4].lower())
    where = collections.defaultdict(list)
    for z, contents in sorted(have.items()):
        if contents:
            for c in contents[0]:
                where[c].append(z)
    out = []
    for g in incompatible:
        s = g["set"]
        if not have.get(s):
            continue
        best = None
        for core in cores:                       # systems.json order breaks ties
            d = dats.get(core)
            if not d or s not in d:
                continue
            crcs, names = _chain_contents(s, d, have)
            needed = [e.rsplit(":", 1) for e in d[s][4] + d[s][5]]
            missing = [(n, c) for n, c in needed if c not in crcs and n not in names]
            if not missing or not all(where.get(c) for _, c in missing):
                continue
            adds = [(n, c, next(z for z in where[c] if z != s)) for n, c in missing
                    if any(z != s for z in where[c])]
            if len(adds) == len(missing) and (best is None or len(adds) < len(best[1])):
                best = (core, adds)
        if best:
            out.append((s, g["label"], best[0], best[1]))
    return out


def _member_by_crc(zf, crc):
    for i in zf.infolist():
        if not i.is_dir() and format(i.CRC, "08x") == crc:
            return i
    return None


def _repair_zip(folder, s, adds, backup_root):
    path = os.path.join(folder, s + ".zip")
    backup = os.path.join(ensure_dir(backup_root), s + ".zip")
    shutil.copy2(path, backup)
    tmp = os.path.join(folder, f".{s}.zip.repair-tmp")
    shutil.copy2(path, tmp)
    try:
        with zipfile.ZipFile(tmp, "a", compression=zipfile.ZIP_DEFLATED) as zt:
            for name, crc, src in adds:
                with zipfile.ZipFile(os.path.join(folder, src + ".zip")) as zs:
                    info_src = _member_by_crc(zs, crc)
                    if info_src is None:
                        raise OSError(f"{crc} no longer in {src}.zip")
                    data = zs.read(info_src)
                zi = zipfile.ZipInfo(name, date_time=info_src.date_time)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zt.writestr(zi, data)
        with zipfile.ZipFile(tmp) as zt:                 # verify before replacing the original
            if zt.testzip() is not None:
                raise OSError("verification failed")
            got = {format(i.CRC, "08x") for i in zt.infolist()}
            if any(c not in got for _, c, _ in adds):
                raise OSError("a copied file is missing after writing")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        os.remove(backup)
        raise
    _log("zip-repair", backup, path)


def repair(folder, cores, manifest, incompatible):
    """Offer to complete the sets that can be completed. Returns how many zips were changed."""
    todo = plan(folder, cores, manifest, incompatible)
    if not todo:
        return 0
    step("MAME sets that can be completed with files from other zips")
    files = sum(len(a) for _, _, _, a in todo)
    info(f"{len(todo)} set(s) no core can start have every missing file in another zip of this "
         f"folder ({files} file(s) in total). Copies would be added to each set's own zip,")
    info("under the name the core expects; nothing in any zip is changed or removed.")
    rows = [(s + ".zip", label[:44], core, ", ".join(f"{n} ← {src}.zip" for n, _, src in a[:2])
             + (f" (+{len(a) - 2})" if len(a) > 2 else "")) for s, label, core, a in todo]
    table(rows, ["zip", "game", "core", "files to add"])
    backup_root = os.path.join(BACKUP_DIR, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    info(f"Each zip is backed up first to {backup_root}; ./undo-rename.sh restores them.")
    if not confirm(f"Add these files to {len(todo)} zip(s)?"):
        info("skipped; the sets stay in the MAME report")
        return 0
    done = 0
    for s, _, _, adds in todo:
        try:
            _repair_zip(folder, s, adds, backup_root)
            done += 1
        except (OSError, zipfile.BadZipFile) as e:
            warn(f"{s}.zip not changed: {e}")
    ok(f"{done} zip(s) completed (backups in {backup_root})")
    return done
