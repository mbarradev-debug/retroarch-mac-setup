"""MAME romset analysis: per-zip compatibility with each MAME core, BIOS/device exclusion, full names."""
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from .common import CACHE, DB_RAW, Net, Progress, download, info, is_hidden, ok, pending, step, warn

MAME_DATS = CACHE + "/dats/mame"
# core -> DAT file in libretro-database/metadat/mame (full XML with per-ROM CRCs)
CORE_DATS = {
    "mame2010": "MAME 2010 XML.xml",
    "mame2003_plus": "MAME 2003-Plus XML.xml",
}


def _index(core, update=False):
    """Compact JSON index built once from the big XML:
    set -> [desc, cloneof, romof, is_bios, own, merged]; own/merged = ["romname:crc", ...]."""
    xml_path = os.path.join(MAME_DATS, CORE_DATS[core])
    idx_path = os.path.join(MAME_DATS, core + ".index.v3.json")
    if Net.online and (update or not os.path.exists(xml_path)):
        try:
            info(f"downloading {CORE_DATS[core]} …")
            download(f"{DB_RAW}/metadat/mame/{urllib.parse.quote(CORE_DATS[core])}", xml_path, timeout=600)
            if os.path.exists(idx_path):
                os.remove(idx_path)
        except Exception as e:  # noqa: BLE001
            warn(f"{core} DAT download failed: {e}")
    if os.path.exists(idx_path) and (not os.path.exists(xml_path) or
                                     os.path.getmtime(idx_path) >= os.path.getmtime(xml_path)):
        with open(idx_path, encoding="utf-8") as f:
            return json.load(f)
    if not os.path.exists(xml_path):
        pending("dats", CORE_DATS[core], "MAME DAT not cached")
        return None
    sets, raw = {}, {}
    for _, el in ET.iterparse(xml_path):
        if el.tag in ("game", "machine"):
            roms = []
            for r in el.findall("rom"):
                crc = r.get("crc")
                if not crc or r.get("status") == "nodump":
                    continue
                roms.append(((r.get("name") or "").lower(), crc.lower(), r.get("merge") is not None, r.get("bios")))
            default_bios = next((b.get("name") for b in el.findall("biosset") if b.get("default") == "yes"), None)
            is_bios = el.get("isbios") == "yes" or el.get("isdevice") == "yes" or el.get("runnable") == "no"
            raw[el.get("name")] = (el.findtext("description") or el.get("name"), el.get("cloneof"),
                                   el.get("romof"), is_bios, roms, default_bios)
            el.clear()

    def default_bios(name, depth=0):
        cur = raw.get(name)
        if not cur or depth > 6:
            return None
        return cur[5] or (default_bios(cur[2], depth + 1) if cur[2] else None)

    for name, (desc, cloneof, romof, is_bios, roms, _) in raw.items():
        dflt = default_bios(name)
        own, merged = set(), set()
        for rn, crc, is_merged, bios in roms:
            if bios and dflt and bios != dflt:
                continue        # alternative BIOS (another region): only loaded if the user selects it
            (merged if is_merged else own).add(f"{rn}:{crc}")
        sets[name] = [desc, cloneof, romof, is_bios, sorted(own), sorted(merged)]
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(sets, f)
    return sets


def _zip_contents(manifest, path, name):
    """(crcs, file names) of a zip, cached in the manifest by size+mtime."""
    st = os.stat(path)
    rec = manifest["mame_sets"].get(name)
    if rec and rec["s"] == st.st_size and rec["m"] == int(st.st_mtime) and "n" in rec:
        return (set(rec["c"]), set(rec["n"])) if not rec.get("bad") else None
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            crcs = sorted({format(i.CRC, "08x") for i in infos})
            names = sorted({os.path.basename(i.filename).lower() for i in infos})
        manifest["mame_sets"][name] = {"s": st.st_size, "m": int(st.st_mtime), "c": crcs, "n": names}
        return set(crcs), set(names)
    except (zipfile.BadZipFile, OSError):
        manifest["mame_sets"][name] = {"s": st.st_size, "m": int(st.st_mtime), "c": [], "n": [], "bad": True}
        return None


def _check(name, dat, have):
    """Return (level, reason). level: 2 = every ROM CRC-exact, 1 = every ROM present by name but some
    CRCs differ (MAME prints WRONG CHECKSUMS and still starts the game), 0 = will not start."""
    s = dat.get(name)
    if not s:
        return 0, "set name not in this core's DAT (romset newer than the core)"
    z = have.get(name)
    if z is None:
        return 0, "unreadable zip"
    crcs, names = set(z[0]), set(z[1])
    needed = [e.rsplit(":", 1) for e in s[4] + s[5]]
    if any(c not in crcs for _, c in needed):
        cur, seen = s[2], set()                       # inherited ROMs: parent / BIOS chain
        while cur and cur not in seen:
            seen.add(cur)
            if have.get(cur) is None:
                if s[5]:
                    return 0, f"needs parent/BIOS '{cur}.zip' (missing or unreadable)"
                break
            crcs |= have[cur][0]
            names |= have[cur][1]
            cur = dat.get(cur, [None, None, None])[2]
    missing = [(n, c) for n, c in needed if c not in crcs and n not in names]
    if missing:
        return 0, f"{len(missing)} ROM file(s) missing (e.g. {missing[0][0]})"
    wrong = [n for n, c in needed if c not in crcs]
    if wrong:
        return 1, f"{len(wrong)} ROM(s) with a different CRC (runs with WRONG CHECKSUMS warning, may glitch)"
    return 2, ""


def analyze(folder, cores, manifest, update=False):
    """Return (games, bios_sets, incompatible). games: dicts with rel, set, label, core, alt_names."""
    step("MAME romset analysis")
    dats = {}
    for core in cores:
        if core in CORE_DATS:
            d = _index(core, update)
            if d:
                dats[core] = d
    zips = sorted(f for f in os.listdir(folder) if f.lower().endswith(".zip") and not is_hidden(f))
    have = {}
    prog = Progress("reading zip CRCs", len(zips))
    for f in zips:
        have[f[:-4].lower()] = _zip_contents(manifest, os.path.join(folder, f), f[:-4].lower())
        prog.tick()
    prog.done()
    manifest.save()

    games, bios_sets, incompatible = [], [], []
    for f in zips:
        name = f[:-4].lower()
        if any(name in d and d[name][3] for d in dats.values()):
            bios_sets.append(f)
            continue
        # best level wins; on a tie the systems.json order (mame2010 first) decides
        results = {c: _check(name, dats[c], have) for c in cores if c in dats}
        reasons = {c: r for c, (lvl, r) in results.items() if r}
        best = max((lvl for lvl, _ in results.values()), default=0)
        chosen = next((c for c in cores if c in results and results[c][0] == best), None) if best else None
        descs = [dats[c][name][0] for c in dats if name in dats[c]]
        if not chosen:
            incompatible.append({"rel": f, "set": name, "label": descs[0] if descs else name, "reasons": reasons})
            continue
        d = dats[chosen][name]
        alt = list(dict.fromkeys(descs))
        parent = d[1]
        if parent:
            for c in dats:
                if parent in dats[c]:
                    alt.append(dats[c][parent][0])
        games.append({"rel": f, "set": name, "label": d[0], "core": chosen, "alt_names": alt,
                      "parent": parent, "exact": best == 2,
                      "warning": "" if best == 2 else results[chosen][1]})
    by_core = {}
    for g in games:
        by_core[g["core"]] = by_core.get(g["core"], 0) + 1
    ok("playable: " + ", ".join(f"{c} {n}" for c, n in by_core.items()) + f"  (total {len(games)})")
    warned = sum(1 for g in games if not g["exact"])
    if warned:
        info(f"of which {warned} have ROMs with a different CRC (start with a warning, may glitch) — see report")
    info(f"BIOS/device sets kept in place but excluded from the playlist: {len(bios_sets)}")
    if incompatible:
        warn(f"{len(incompatible)} sets do not match any installed MAME core → listed in the MAME report")
    return games, bios_sets, incompatible
