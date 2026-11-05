"""List ROMs of a system folder and identify them by CRC against libretro-database DATs."""
import os
import re
import urllib.parse
import zipfile
import zlib

from .common import CACHE, DB_RAW, Net, download, info, is_hidden, pending, warn

DATS = CACHE + "/dats"
SKIP_DIRS = {"images", "media", "downloaded_images", "videos", "manuals"}
MAX_HASH = 64 * 1024 * 1024


def list_roms(folder, extensions):
    """Relative paths of ROMs in folder (top level + one sub-folder level, for multi-file games)."""
    exts = {e.lower() for e in extensions}
    found = []
    for entry in sorted(os.listdir(folder)):
        p = os.path.join(folder, entry)
        if is_hidden(entry) or entry.startswith("_"):
            continue
        if os.path.isdir(p):
            if entry.lower() in SKIP_DIRS:
                continue
            for sub in sorted(os.listdir(p)):
                if not is_hidden(sub) and os.path.isfile(os.path.join(p, sub)) and _ext(sub) in exts:
                    found.append(os.path.join(entry, sub))
        elif _ext(entry) in exts:
            found.append(entry)
    # drop files referenced by .m3u playlists (multi-disc) and .bin tracks referenced by .cue
    referenced = set()
    for rel in found:
        if _ext(rel) in ("m3u", "cue"):
            base = os.path.dirname(rel)
            try:
                text = open(os.path.join(folder, rel), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if _ext(rel) == "m3u":
                refs = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
            else:
                refs = re.findall(r'FILE\s+"([^"]+)"', text, re.I)
            for r in refs:
                referenced.add(os.path.normpath(os.path.join(base, r)))
    return [r for r in found if os.path.normpath(r) not in referenced]


def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _variants(data, ext):
    crcs = [zlib.crc32(data)]
    if data[:4] == b"NES\x1a" and len(data) > 16:              # iNES header (No-Intro DATs list both)
        crcs.append(zlib.crc32(data[16:]))
    if ext in ("smc", "swc", "fig", "sfc") and len(data) % 1024 == 512:  # copier header
        crcs.append(zlib.crc32(data[512:]))
    if data[1:10] == b"ATARI7800" and len(data) > 128:        # a78 header
        crcs.append(zlib.crc32(data[128:]))
    if data[:4] == b"LYNX" and len(data) > 64:                 # lnx header
        crcs.append(zlib.crc32(data[64:]))
    return [format(c & 0xFFFFFFFF, "08x") for c in crcs]


def rom_crcs(path):
    ext = _ext(path)
    if ext == "zip":
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if not i.is_dir() and not os.path.basename(i.filename).startswith(".")]
            if not members:
                return []
            m = max(members, key=lambda i: i.file_size)
            iext = _ext(m.filename)
            if iext in ("nes", "smc", "swc", "fig", "sfc", "a78", "lnx") and m.file_size <= MAX_HASH:
                return _variants(z.read(m), iext)
            return [format(m.CRC, "08x")]
    if ext in ("7z", "cue", "chd", "m3u", "pbp", "iso", "img", "ccd", "toc"):
        return []
    size = os.path.getsize(path)
    if size > MAX_HASH:
        return []
    with open(path, "rb") as f:
        return _variants(f.read(), ext)


def cached_crcs(manifest, system, folder, rel):
    p = os.path.join(folder, rel)
    st = os.stat(p)
    rec = manifest["rom_crc"].setdefault(system, {}).get(rel)
    if rec and rec["s"] == st.st_size and rec["m"] == int(st.st_mtime):
        return rec["c"]
    try:
        c = rom_crcs(p)
    except (zipfile.BadZipFile, OSError) as e:
        warn(f"cannot read {rel}: {e}")
        c = []
    manifest["rom_crc"][system][rel] = {"s": st.st_size, "m": int(st.st_mtime), "c": c}
    return c


def load_dat(system, kind, update=False):
    """crc -> [game names] from libretro-database metadat/<kind>/<system>.dat (cached)."""
    if not kind:
        return {}
    path = f"{DATS}/{kind}/{system}.dat"
    if Net.online and (update or not os.path.exists(path)):
        try:
            download(f"{DB_RAW}/metadat/{kind}/{urllib.parse.quote(system)}.dat", path)
        except Exception as e:  # noqa: BLE001
            warn(f"DAT for {system} not downloaded: {e}")
    if not os.path.exists(path):
        pending("dats", system, "CRC identification DAT not cached")
        return {}
    text = open(path, encoding="utf-8", errors="replace").read()
    out = {}
    for g in re.finditer(r'game \(\s*name "((?:[^"\\]|\\.)*)"(.*?)\n\)', text, re.S):
        name = g.group(1)
        for crc in re.findall(r'\bcrc ([0-9A-Fa-f]{8})\b', g.group(2)):
            lst = out.setdefault(crc.lower(), [])
            if name not in lst:
                lst.append(name)
    info(f"DAT {kind}/{system}: {len(out)} CRCs")
    return out
