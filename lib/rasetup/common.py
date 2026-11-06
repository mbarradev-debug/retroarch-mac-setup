"""Shared paths, console output, prompts, networking and the cache manifest."""
import datetime
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- fixed paths
# game library, chosen by setup.sh (lib/library.sh) and passed in the environment
LIBRARY = os.environ.get("RASETUP_LIBRARY") or "/Volumes/BACKUP"
ROMS = os.environ.get("RASETUP_ROMS") or LIBRARY + "/roms"
NO_SNAPSHOT_DIRNAME = "_no_snapshot"
NO_SNAPSHOT = ROMS + "/" + NO_SNAPSHOT_DIRNAME
CACHE = LIBRARY + "/retroarch-cache"
BIOS_DIR = LIBRARY + "/bios"
SAVES_DIR = LIBRARY + "/retroarch-saves"

RA_HOME = os.path.expanduser("~/Library/Application Support/RetroArch")
# RetroArch's configuration lives in the library (survives a reformat, and RetroArch cannot
# overwrite it while the library is missing). RetroArch always loads RA_CFG_LINK, a symlink to
# RA_CFG; it saves through the link in place (config_file_write: fopen "wb").
RA_CFG_LINK = RA_HOME + "/config/retroarch.cfg"
RA_STORE = LIBRARY + "/retroarch-config"
RA_CFG = RA_STORE + "/retroarch.cfg"
RA_CONFIG_DIR = RA_STORE + "/config"          # rgui_config_directory: overrides, core options, remaps
RA_CORES = RA_HOME + "/cores"
RA_INFO = RA_HOME + "/info"
RA_SHADERS = RA_HOME + "/shaders"
RA_PLAYLISTS = RA_HOME + "/playlists"
RA_AUTOCONFIG = RA_STORE + "/autoconfig"     # joypad_autoconfig_dir
RA_PROJECT_ASSETS = RA_HOME + "/retroarch-mac-setup"
APP = "/Applications/RetroArch.app"

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILDBOT_CORES = "https://buildbot.libretro.com/nightly/apple/osx/arm64/latest"
BUILDBOT_ASSETS = "https://buildbot.libretro.com/assets/frontend"
THUMBS_SERVER = "https://thumbnails.libretro.com"
DB_RAW = "https://raw.githubusercontent.com/libretro/libretro-database/master"
USER_AGENT = "retroarch-mac-setup/1.0 (+https://github.com/)"

# ---------------------------------------------------------------- output
_TTY = sys.stdout.isatty()
_lock = threading.Lock()


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def step(msg):
    print("\n" + _c("1;36", "==> " + msg), flush=True)


def info(msg):
    print("    " + msg, flush=True)


def ok(msg):
    print("    " + _c("32", "✔ ") + msg, flush=True)


def warn(msg):
    print("    " + _c("33", "! ") + msg, flush=True)


def err(msg):
    print("    " + _c("31", "✖ ") + msg, file=sys.stderr, flush=True)


class Progress:
    """Single-line progress counter (thread safe)."""

    def __init__(self, label, total):
        self.label, self.total, self.n, self.t = label, total, 0, 0.0

    def tick(self, k=1):
        with _lock:
            self.n += k
            now = time.time()
            if _TTY and (now - self.t > 0.2 or self.n >= self.total):
                self.t = now
                sys.stdout.write(f"\r    {self.label}: {self.n}/{self.total}")
                sys.stdout.flush()

    def done(self):
        if _TTY:
            sys.stdout.write("\r" + " " * 70 + "\r")
        info(f"{self.label}: {self.n}/{self.total}")


def table(rows, headers):
    widths = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    line = "    " + "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(_c("1", line))
    print("    " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("    " + "  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def confirm(question, default=False):
    """Ask yes/no. Reads the terminal; when there is no answer (EOF) the default is used."""
    suffix = " [S/n] " if default else " [s/N] "
    sys.stdout.write(_c("1;35", "    ? " + question) + suffix)
    sys.stdout.flush()
    try:
        ans = sys.stdin.readline()
    except KeyboardInterrupt:
        print()
        raise
    if not ans:
        print()
        return default
    ans = ans.strip().lower()
    if not ans:
        return default
    return ans in ("s", "si", "sí", "y", "yes")


# ---------------------------------------------------------------- filesystem helpers
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def is_hidden(name):
    return name.startswith(".")  # includes AppleDouble "._x" files created on exFAT


def write_atomic(path, data, mode="w"):
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, mode, **({"encoding": "utf-8"} if "b" not in mode else {})) as f:
        f.write(data)
    os.replace(tmp, path)


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def run(cmd, check=True, capture=True):
    return subprocess.run(cmd, check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def which(name):
    p = shutil.which(name)
    if p:
        return p
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if os.access(os.path.join(d, name), os.X_OK):
            return os.path.join(d, name)
    return None


# ---------------------------------------------------------------- network
class Net:
    online = False
    forced_offline = False

    @classmethod
    def detect(cls, forced_offline=False):
        cls.forced_offline = forced_offline
        if forced_offline:
            cls.online = False
            return False
        for host in ("buildbot.libretro.com", "thumbnails.libretro.com"):
            try:
                socket.create_connection((host, 443), timeout=5).close()
                cls.online = True
                return True
            except OSError:
                continue
        cls.online = False
        return False


def url_quote_path(path):
    return urllib.parse.quote(path, safe="/")


def http_get(url, timeout=60, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except (urllib.error.URLError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def download(url, dest, timeout=120, retries=3):
    """Download to dest atomically (dest.part -> dest)."""
    ensure_dir(os.path.dirname(dest))
    part = dest + ".part"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            os.replace(part, dest)
            return dest
        except urllib.error.HTTPError as e:
            if os.path.exists(part):
                os.remove(part)
            if e.code == 404:
                raise
            last = e
        except (urllib.error.URLError, OSError) as e:
            last = e
            if os.path.exists(part):
                os.remove(part)
        time.sleep(1.5 * (attempt + 1))
    raise last


# ---------------------------------------------------------------- pending work (reported at the end)
PENDING = []


def pending(category, item, reason):
    with _lock:
        PENDING.append((category, item, reason))


# ---------------------------------------------------------------- manifest
class Manifest:
    """JSON record of everything in the cache. Saved atomically."""

    PATH = CACHE + "/manifest.json"

    def __init__(self):
        self.data = {"version": 1}
        if os.path.exists(self.PATH):
            with open(self.PATH, encoding="utf-8") as f:
                self.data = json.load(f)
        for k in ("thumbnails", "no_snapshot", "indexes", "downloads", "cores", "rom_crc", "mame_sets"):
            self.data.setdefault(k, {})

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        with _lock:
            write_atomic(self.PATH, json.dumps(self.data, indent=1, ensure_ascii=False, sort_keys=True))


def load_systems():
    with open(os.path.join(PROJECT, "systems.json"), encoding="utf-8") as f:
        return json.load(f)["systems"]
