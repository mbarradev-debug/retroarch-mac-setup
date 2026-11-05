"""libretro-thumbnails Named_Snaps: index, matching, download to originals/, normalization."""
import html
import json
import os
import re
import struct
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .common import (CACHE, THUMBS_SERVER, Net, Progress, download, ensure_dir, http_get, info,
                     now_iso, pending, url_quote_path, warn)

THUMBS = CACHE + "/thumbnails"
ORIGINALS = THUMBS + "/originals"
NORMALIZED = THUMBS + "/normalized"
INDEXES = THUMBS + "/index"
INDEX_MAX_AGE = 30 * 86400
KIND = "Named_Snaps"

# RetroArch replaces these characters with '_' when it builds a thumbnail file name
# (gfx/gfx_thumbnail_path.c: strpbrk(s, "&*/:`\"<>?\\|"))
_RA_BAD = re.compile(r'[&*/:`"<>?\\|]')


def ra_sanitize(label):
    return _RA_BAD.sub("_", label)


def thumb_system(playlist_name):
    # RetroArch maps every "MAME..." playlist to the single MAME thumbnail repo
    return "MAME" if playlist_name.startswith("MAME") else playlist_name


# ---------------------------------------------------------------- index
def load_index(system, manifest, refresh=False):
    path = os.path.join(INDEXES, system + ".json")
    data = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    stale = data is None or refresh or time.time() - data.get("ts", 0) > INDEX_MAX_AGE
    if stale and Net.online:
        try:
            page = http_get(f"{THUMBS_SERVER}/{url_quote_path(system)}/{KIND}/", timeout=120).decode("utf-8", "replace")
            names = sorted({urllib.parse.unquote(html.unescape(h))[:-4]
                            for h in re.findall(r'href="([^"?/][^"]*?\.png)"', page)})
            data = {"ts": time.time(), "date": now_iso(), "names": names}
            ensure_dir(INDEXES)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            manifest["indexes"][system] = {"date": data["date"], "count": len(names)}
            info(f"{system}: thumbnail index refreshed ({len(names)} gameplay snaps)")
        except Exception as e:  # noqa: BLE001
            warn(f"{system}: could not fetch the thumbnail index ({e})")
    if data is None:
        pending("thumbnails", system, "thumbnail index not cached (offline)")
        return None
    return data["names"]


# ---------------------------------------------------------------- matching
_TAGS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_ARTICLE = re.compile(r"^(.*?),\s*(the|a|an)\b(.*)$", re.I)
_BAD_TAGS = ("beta", "proto", "demo", "sample", "hack", "pirate", "unl", "aftermarket", "program",
             "kiosk", "virtual console", "competition")
_REGION = [("usa", 0), ("world", 1), ("europe", 2), ("en", 2), ("japan", 4)]


def _fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _tokens(s, keep_tags=False):
    """'Legend of Zelda, The - A Link (USA)' -> ['the','legend','of','zelda','a','link']."""
    if not keep_tags:
        s = _TAGS.sub(" ", s)
    s = _fold(s).strip()
    head, sep, tail = s.partition(" - ")
    m = _ARTICLE.match(head.strip())
    if m and not m.group(3).strip():
        head = f"{m.group(2)} {m.group(1)}"
    s = head + sep + tail
    return [_ROMAN.get(t, t) for t in re.findall(r"[a-z0-9]+", s) if t != "and"]


# "Gauntlet 4" == "Gauntlet IV": numbers 2-10 are compared as roman numerals on both sides
_ROMAN = {"2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi", "7": "vii", "8": "viii", "9": "ix", "10": "x"}
_SPLIT = re.compile(r"\s+[~-]\s+")


def _parts(s):
    return [p for p in _SPLIT.split(_TAGS.sub(" ", s).strip()) if p.strip()]


def key(s, keep_tags=False):
    return " ".join(_tokens(s, keep_tags))


def loose_key(s):
    t = _tokens(s)
    if t and t[0] == "the":
        t = t[1:]
    return "".join(t)


def _tags(s):
    return {t.strip().lower() for g in re.findall(r"\(([^)]*)\)", s) for t in g.split(",")}


def _score(name, prefer_tags):
    tags = _tags(name)
    low = name.lower()
    s = 3
    for r, v in _REGION:
        if r in tags:
            s = min(s, v)
    s += sum(10 for b in _BAD_TAGS if b in low)
    s += 0.5 if re.search(r"\(rev|\(v\d", low) else 0
    s -= 5 * len(tags & prefer_tags)
    return (s, len(name), name)


class Matcher:
    def __init__(self, names):
        self.names = set(names)
        self.by_sanitized = {}
        self.by_full = {}
        self.by_key = {}
        self.by_loose = {}
        self.by_part = {}
        for n in names:
            for part in _parts(n)[1:]:          # alternate titles: "Senjou no Ookami II ~ Mercs"
                pk = key(part)
                if len(pk.split()) >= 1 and len(pk) >= 4:
                    self.by_part.setdefault(pk, []).append(n)
            self.by_sanitized.setdefault(ra_sanitize(n).lower(), n)
            self.by_full.setdefault(key(n, keep_tags=True), []).append(n)
            self.by_key.setdefault(key(n), []).append(n)
            self.by_loose.setdefault(loose_key(n), []).append(n)

    def _best(self, cands, prefer):
        return min(cands, key=lambda n: _score(n, prefer)) if cands else None

    def match(self, primary, extra=()):
        """primary: the ROM stem / MAME description. extra: DAT names, alternate descriptions.
        Returns (snap name, method, tried keys)."""
        tried = []
        candidates = [primary] + [e for e in extra if e and e != primary]
        prefer = _tags(primary)
        for c in candidates:                                     # 1. exact name
            tried.append(c)
            if c in self.names:
                return c, "exact", tried
            hit = self.by_sanitized.get(ra_sanitize(c).lower())
            if hit:
                return hit, "exact", tried
        for c in candidates:                                     # 2. drop trailing tags one by one
            cur = c
            while True:
                nxt = re.sub(r"\s*(\([^)]*\)|\[[^\]]*\])\s*$", "", cur)
                if nxt == cur or not nxt:
                    break
                cur = nxt
                if cur in self.names:
                    return cur, "without trailing tags", tried + [cur]
                for n in self.by_full.get(key(cur, True), []):
                    return n, "without trailing tags", tried + [cur]
        for c in candidates:                                     # 3. same title, any region/revision
            k = key(c)
            tried.append(f"title:{k}")
            hit = self._best(self.by_key.get(k, []), prefer)
            if hit:
                return hit, "title (region/revision ignored)", tried
        for c in candidates:                                     # 4. punctuation/spacing-insensitive
            k = loose_key(c)
            if len(k) < 4:
                continue
            hit = self._best(self.by_loose.get(k, []), prefer)
            if hit:
                return hit, "loose title", tried
        for c in candidates:                                     # 5. alternate title after " ~ " / " - "
            k = key(c)
            hit = self._best(self.by_part.get(k, []), prefer) if len(k) >= 4 else None
            if hit:
                return hit, "alternate title", tried + [f"part:{k}"]
        for c in candidates:                                     # 6. main title before the subtitle
            parts = _parts(c)
            if len(parts) > 1:
                k = key(parts[0])
                if len(k.split()) >= 2:
                    hit = self._best(self.by_key.get(k, []), prefer)
                    if hit:
                        return hit, "main title (subtitle ignored)", tried + [f"head:{k}"]
        return None, None, tried


# ---------------------------------------------------------------- images
def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    return None


def normalize(tool, src, dst, canvas):
    W, H = canvas
    ensure_dir(os.path.dirname(dst))
    size = png_size(src)
    kind, exe = tool
    if size is None and kind == "magick":
        out = subprocess.run([exe, "identify", "-format", "%w %h", src + "[0]"], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.split()
        size = (int(out[0]), int(out[1])) if len(out) == 2 else None
    if not size:
        raise RuntimeError("unreadable image")
    w, h = size
    if w <= W and h <= H:      # pixel-perfect integer upscale
        k = max(1, min(W // w, H // h))
        nw, nh, filt = w * k, h * k, "point"
    else:                      # larger than the canvas: fit, keeping the aspect ratio
        r = min(W / w, H / h)
        nw, nh, filt = max(1, round(w * r)), max(1, round(h * r)), "lanczos"
    tmp = dst + ".tmp.png"
    if kind == "magick":
        subprocess.run([exe, src + "[0]", "-filter", filt, "-resize", f"{nw}x{nh}!",
                        "-background", "black", "-gravity", "center", "-extent", f"{W}x{H}",
                        "-strip", "PNG24:" + tmp], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        subprocess.run([exe, "-s", "format", "png", "-z", str(nh), str(nw), src, "--out", tmp],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([exe, "--padToHeightWidth", str(H), str(W), "--padColor", "000000", tmp, "--out", tmp],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.replace(tmp, dst)


# ---------------------------------------------------------------- per-system pipeline
def process(playlist_name, items, manifest, tool, canvas, refresh_missing=False, renormalize=False):
    """items: dicts with rel, label, extra (alternate names). Adds item['snap'] when available.
    Returns (missing, deferred): missing = [(item, tried)] confirmed without snap;
    deferred = items whose status could not be decided (offline / download error)."""
    tsys = thumb_system(playlist_name)
    rec_sys = manifest["thumbnails"].setdefault(playlist_name, {})
    nos_sys = manifest["no_snapshot"].setdefault(playlist_name, {})
    canvas_s = f"{canvas[0]}x{canvas[1]}"
    norm_dir = os.path.join(NORMALIZED, playlist_name, KIND)
    orig_dir = os.path.join(ORIGINALS, tsys, KIND)

    todo, missing, deferred = [], [], []
    index = None
    matcher = None
    for it in items:
        rec = rec_sys.get(it["rel"])
        norm_path = os.path.join(norm_dir, ra_sanitize(it["label"]) + ".png")
        it["norm_path"] = norm_path
        if rec and rec.get("game") == it["label"] and os.path.exists(norm_path) \
                and rec.get("canvas") == canvas_s and not renormalize:
            it["snap"] = rec["snap"]
            continue
        if rec and rec.get("game") == it["label"] and os.path.exists(os.path.join(orig_dir, rec["snap"] + ".png")):
            it["snap"] = rec["snap"]          # original cached → only (re)normalize, no download
            todo.append(it)
            continue
        if it["rel"] in nos_sys and not refresh_missing:
            missing.append((it, nos_sys[it["rel"]].get("tried", [])))
            continue
        if matcher is None:
            index = load_index(tsys, manifest, refresh=refresh_missing)
            if index is None:
                deferred.extend(i for i in items if "snap" not in i and i not in todo
                                and i["rel"] not in nos_sys)
                break
            matcher = Matcher(index)
        snap, method, tried = matcher.match(it["label"], it.get("extra", ()))
        if snap:
            it["snap"], it["method"] = snap, method
            todo.append(it)
        else:
            missing.append((it, tried))

    if todo:
        prog = Progress(f"{playlist_name}: snaps", len(todo))

        def work(it):
            orig = os.path.join(orig_dir, it["snap"] + ".png")
            try:
                if not os.path.exists(orig):
                    if not Net.online:
                        raise ConnectionError("offline")
                    download(f"{THUMBS_SERVER}/{url_quote_path(tsys)}/{KIND}/{url_quote_path(it['snap'])}.png", orig)
                normalize(tool, orig, it["norm_path"], canvas)
                rec_sys[it["rel"]] = {"game": it["label"], "snap": it["snap"], "canvas": canvas_s,
                                      "original": os.path.relpath(orig, THUMBS),
                                      "normalized": os.path.relpath(it["norm_path"], THUMBS),
                                      "method": it.get("method", "cached"), "date": now_iso()}
                nos_sys.pop(it["rel"], None)
                return None
            except Exception as e:  # noqa: BLE001
                it.pop("snap", None)
                return (it, str(e))
            finally:
                prog.tick()

        with ThreadPoolExecutor(max_workers=8) as ex:
            failures = [r for r in ex.map(work, todo) if r]
        prog.done()
        for it, why in failures:
            deferred.append(it)
            pending("thumbnails", f"{playlist_name}/{it['rel']}", why)
        manifest.save()
    return missing, deferred
