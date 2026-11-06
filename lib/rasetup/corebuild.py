"""Cores built here from source with a fix, used instead of the libretro buildbot's.

mame2010: the core's global memory pool (src/emu/emualloc.c, global_resource_pool) is a static
object, and its destructor runs in exit() and in dlclose(). It frees machine_config objects that
are no longer valid, so RetroArch crashed ("quit unexpectedly", EXC_BAD_ACCESS in
machine_config::~machine_config) on Cmd+Q after any MAME 2010 game, and once when the core was
unloaded. patches/mame2010-libretro/ marks the pool [[clang::no_destroy]]: it is never
destroyed, and the OS reclaims its memory when the process ends.

The source is pinned to one commit, so every build is the same. The build needs the Command
Line Tools (clang, make, git) and internet once (about 2 minutes on an M4); the result is kept
in the cache, so later runs and restores after a reformat install it offline. Without a build,
the buildbot core is used and the crash stays.
"""
import glob
import os
import shutil
import subprocess
import tempfile

from .common import CACHE, PROJECT, Net, file_sha256, info, now_iso, ok, run, warn

PATCHED = {
    "mame2010": {
        "repo": "https://github.com/libretro/mame2010-libretro.git",
        "commit": "dff8aadd1c3f38215af3955746d6e19abe0ddcea",
        "patches": "patches/mame2010-libretro",
        "make": ["platform=osx"],
    },
}
CORES_CACHE = CACHE + "/cores"


def _key(core):
    spec = PATCHED[core]
    patches = sorted(glob.glob(os.path.join(PROJECT, spec["patches"], "*.patch")))
    digest = "".join(file_sha256(p)[:8] for p in patches)
    return spec["commit"][:10] + "-" + digest, patches


def cached_path(core):
    key, _ = _key(core)
    return os.path.join(CORES_CACHE, f"{core}_libretro.patched-{key}.dylib")


def _tools():
    if run(["xcode-select", "-p"], check=False).returncode != 0:
        return False            # /usr/bin/make etc. are stubs that open an install dialog
    return all(shutil.which(t) for t in ("git", "make", "clang"))


def _build(core, dest, patches):
    spec = PATCHED[core]
    work = tempfile.mkdtemp(prefix=f"{core}-build-")
    log = os.path.join(CORES_CACHE, f"{core}-build.log")
    src = os.path.join(work, "src")
    try:
        os.makedirs(src)
        for cmd in (["git", "init", "-q"],
                    ["git", "fetch", "-q", "--depth", "1", spec["repo"], spec["commit"]],
                    ["git", "checkout", "-q", "FETCH_HEAD"]):
            r = run(["git", "-C", src] + cmd[1:], check=False)
            if r.returncode != 0:
                warn(f"{core}: could not get the source ({(r.stderr or '').strip()[:200]})")
                return False
        for p in patches:
            r = run(["git", "-C", src, "apply", "--whitespace=nowarn", p], check=False)
            if r.returncode != 0:
                warn(f"{core}: patch {os.path.basename(p)} does not apply: {(r.stderr or '').strip()[:200]}")
                return False
        info(f"{core}: compiling the fixed core (a few minutes; log: {log}) …")
        with open(log, "w") as f:
            r = subprocess.run(["make", f"-j{os.cpu_count() or 4}", "-C", src] + spec["make"],
                               stdout=f, stderr=subprocess.STDOUT)
        built = os.path.join(src, f"{core}_libretro.dylib")
        if r.returncode != 0 or not os.path.exists(built):
            warn(f"{core}: build failed (see {log})")
            return False
        tmp = dest + ".tmp"
        shutil.copy2(built, tmp)
        os.replace(tmp, dest)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ensure(core, manifest):
    """Path of the fixed dylib for core (built now if needed and possible), or None."""
    if core not in PATCHED:
        return None
    key, patches = _key(core)
    dest = cached_path(core)
    if os.path.exists(dest):
        return dest
    reason = None
    if not Net.online:
        reason = "offline"
    elif not _tools():
        reason = "needs the Command Line Tools: run xcode-select --install, then ./setup.sh again"
    if reason is None and _build(core, dest, patches):
        for old in glob.glob(os.path.join(CORES_CACHE, f"{core}_libretro.patched-*.dylib")):
            if old != dest:
                os.remove(old)
        manifest["downloads"].setdefault("patched_cores", {})[core] = {
            "file": os.path.basename(dest), "commit": PATCHED[core]["commit"], "date": now_iso()}
        ok(f"{core}: fixed core built and cached ({os.path.basename(dest)})")
        return dest
    warn(f"{core}: using the buildbot core, which crashes RetroArch on Cmd+Q"
         + (f" (fixed build {reason})" if reason else ""))
    return None
