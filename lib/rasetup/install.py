"""Install RetroArch (cached DMG), cores, core info files and the slang shader pack."""
import glob
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from . import corebuild
from .common import (APP, BUILDBOT_ASSETS, BUILDBOT_CORES, CACHE, Net, PROJECT, RA_CORES, RA_HOME,
                     RA_INFO, RA_SHADERS, confirm, download, ensure_dir, err, file_sha256, http_get, info,
                     now_iso, ok, pending, run, step, warn, which)

INSTALLER_DIR = CACHE + "/installer"
CORES_CACHE = CACHE + "/cores"
SHADERS_CACHE = CACHE + "/shaders"
CASK_API = "https://formulae.brew.sh/api/cask/retroarch-metal.json"


# ---------------------------------------------------------------- RetroArch.app
def inspect_app(app=APP):
    """Return dict(version, archs, vulkan, moltenvk) or None when not installed."""
    plist = os.path.join(app, "Contents/Info.plist")
    if not os.path.exists(plist):
        return None
    with open(plist, "rb") as f:
        p = plistlib.load(f)
    exe = os.path.join(app, "Contents/MacOS", p.get("CFBundleExecutable", "RetroArch"))
    archs = run(["lipo", "-archs", exe], check=False).stdout.split()
    fw = os.path.join(app, "Contents/Frameworks")
    moltenvk = bool(glob.glob(fw + "/MoltenVK*.framework")) or bool(glob.glob(fw + "/libMoltenVK*.dylib"))
    with open(exe, "rb") as f:
        blob = f.read()
    vulkan = b"\x00vulkan\x00" in blob
    glcore = b"\x00glcore\x00" in blob
    return {"version": p.get("CFBundleShortVersionString", "?"), "archs": archs,
            "vulkan": vulkan and moltenvk, "moltenvk": moltenvk, "glcore": glcore}


def _cached_dmgs():
    return sorted(glob.glob(INSTALLER_DIR + "/RetroArch_Metal-*.dmg"),
                  key=lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(p))])


def _fetch_latest_dmg(manifest):
    """Download the DMG used by the Homebrew cask 'retroarch-metal' (universal / Apple Silicon)."""
    meta = json.loads(http_get(CASK_API))
    ver, url, sha = meta["version"], meta["url"], meta.get("sha256")
    dest = f"{INSTALLER_DIR}/RetroArch_Metal-{ver}.dmg"
    if not os.path.exists(dest):
        info(f"Downloading RetroArch {ver} (Metal/universal build, includes MoltenVK) …")
        download(url, dest, timeout=600)
        if sha and sha != "no_check" and file_sha256(dest) != sha:
            os.remove(dest)
            raise RuntimeError("RetroArch DMG checksum mismatch — download discarded")
        manifest["downloads"]["retroarch"] = {"version": ver, "file": os.path.basename(dest),
                                              "url": url, "sha256": sha, "date": now_iso()}
        ok(f"cached installer: {dest}")
    return dest, ver


def _install_from_dmg(dmg):
    if run(["pgrep", "-x", "RetroArch"], check=False).returncode == 0:
        raise RuntimeError("RetroArch is running — quit it and run the script again")
    mnt = tempfile.mkdtemp(prefix="ra-dmg-")
    run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-mountpoint", mnt, dmg])
    try:
        src = os.path.join(mnt, "RetroArch.app")
        if os.path.exists(APP):
            shutil.rmtree(APP)
        run(["ditto", src, APP])
    finally:
        run(["hdiutil", "detach", mnt, "-quiet"], check=False)
    run(["xattr", "-dr", "com.apple.quarantine", APP], check=False)


def ensure_retroarch(manifest, update=False):
    step("RetroArch")
    cur = inspect_app()
    need = cur is None
    if cur and "arm64" not in cur["archs"]:
        warn(f"Installed RetroArch {cur['version']} is not native for Apple Silicon (archs: {cur['archs']}).")
        warn("That is the OpenGL/x86_64 build (cask 'retroarch'). The native one is cask 'retroarch-metal'.")
        need = confirm("Replace it with the native universal build?", default=True)
    elif cur and not cur["vulkan"]:
        warn("Installed RetroArch has no Vulkan/MoltenVK support.")
        need = confirm("Replace it with the Metal build (which bundles MoltenVK)?", default=True)

    dmg = None
    if Net.online:
        try:
            dmg, latest = _fetch_latest_dmg(manifest)
            if cur and update and cur["version"] != latest:
                need = confirm(f"Update RetroArch {cur['version']} → {latest}?", default=True)
        except Exception as e:  # noqa: BLE001
            warn(f"Could not check the latest RetroArch release: {e}")
    if not dmg:
        cached = _cached_dmgs()
        dmg = cached[-1] if cached else None

    if need:
        if not dmg:
            err("RetroArch is not installed and no installer is cached (offline).")
            pending("retroarch", "RetroArch_Metal.dmg", "needs internet once")
            return None
        info(f"Installing from {os.path.basename(dmg)} …")
        _install_from_dmg(dmg)
        cur = inspect_app()
        ok(f"installed RetroArch {cur['version']} → {APP}")
    else:
        ok(f"RetroArch {cur['version']} already installed ({'/'.join(cur['archs'])})")

    if not cur["vulkan"]:
        err("This RetroArch build does not include the Vulkan driver (MoltenVK). Vulkan cannot be used.")
        return None
    ok("Vulkan driver present (MoltenVK bundled)" + (", glcore available as fallback" if cur["glcore"] else ""))
    return cur


def extract_bundle_assets(manifest, version):
    """Extract RetroArch.app's assets.zip (menu icons, sounds, overlays, databases, cheats) into
    RetroArch's folder, as RetroArch does on its first launch. The script does it so it happens
    *before* the newer core info files and shaders are installed: RetroArch's own extraction
    (disabled in retroarch.base.cfg) ran on the first launch after the script and replaced them
    with the older bundled ones. Repeated only when the app changes."""
    zpath = os.path.join(APP, "Contents/Resources/assets.zip")
    if not os.path.exists(zpath):
        return
    st = os.stat(zpath)
    key = f"{version}:{st.st_size}:{int(st.st_mtime)}"
    if manifest["downloads"].get("bundle_assets") == key and os.path.isdir(os.path.join(RA_HOME, "assets")):
        ok("bundled assets already extracted")
        return
    info("extracting the app's bundled assets (menu icons, overlays, databases) …")
    ensure_dir(RA_HOME)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(RA_HOME)
    # the bundle also carries shaders_slang: make ensure_shaders put the cached pack back
    marker = os.path.join(RA_SHADERS, "shaders_slang", ".retroarch-mac-setup-sha256")
    if os.path.exists(marker):
        os.remove(marker)
    manifest["downloads"]["bundle_assets"] = key
    ok("bundled assets extracted")


# ---------------------------------------------------------------- cores + info
def _core_library_name(dylib):
    """Ask the core itself (retro_get_system_info) in a child process; overrides use this name."""
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corename.py")
    r = subprocess.run([sys.executable, "-I", "-B", helper, dylib], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    if r.returncode == 0 and r.stdout.strip():
        return json.loads(r.stdout)
    return None


def ensure_info(manifest, update=False):
    zpath = CORES_CACHE + "/info.zip"
    if Net.online and (update or not os.path.exists(zpath)):
        try:
            download(BUILDBOT_ASSETS + "/info.zip", zpath)
            manifest["downloads"]["info.zip"] = {"date": now_iso()}
        except Exception as e:  # noqa: BLE001
            warn(f"core info download failed: {e}")
    if not os.path.exists(zpath):
        pending("cores", "info.zip", "core info files need internet once")
        return
    ensure_dir(RA_INFO)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(RA_INFO)


def read_info(core):
    p = os.path.join(RA_INFO, f"{core}_libretro.info")
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="replace"):
            m = re.match(r'\s*([A-Za-z0-9_]+)\s*=\s*"?(.*?)"?\s*$', line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def ensure_cores(cores, manifest, update=False):
    step("Cores (" + ", ".join(sorted(cores)) + ")")
    ensure_info(manifest, update)
    ensure_dir(RA_CORES)
    installed = {}
    for core in sorted(cores):
        zname = f"{core}_libretro.dylib.zip"
        zpath = os.path.join(CORES_CACHE, zname)
        if Net.online and (update or not os.path.exists(zpath)):
            try:
                download(f"{BUILDBOT_CORES}/{zname}", zpath, timeout=600)
                manifest["downloads"].setdefault("cores", {})[core] = {"file": zname, "date": now_iso()}
                info(f"downloaded {zname}")
            except Exception as e:  # noqa: BLE001
                warn(f"{core}: download failed ({e})")
        dylib = os.path.join(RA_CORES, f"{core}_libretro.dylib")
        rec = manifest["cores"].get(core, {})
        fixed = corebuild.ensure(core, manifest)      # built from source with a fix, if any
        if fixed:
            source = "patched:" + os.path.basename(fixed)
            if not os.path.exists(dylib) or rec.get("source") != source:
                shutil.copy2(fixed, dylib)
                rec = {"source": source}
        else:
            if not os.path.exists(zpath):
                pending("cores", core, "not cached; needs internet")
                continue
            sha = file_sha256(zpath)
            if not os.path.exists(dylib) or rec.get("zip_sha256") != sha or rec.get("source"):
                with zipfile.ZipFile(zpath) as z:
                    z.extract(f"{core}_libretro.dylib", RA_CORES)
                rec = {"zip_sha256": sha}
        if not rec.get("library_name"):
            sysinfo = _core_library_name(dylib)
            if sysinfo:
                rec.update(sysinfo)
            else:
                rec["library_name"] = read_info(core).get("corename", core)
                warn(f"{core}: could not query the core; using corename from .info")
        rec["display_name"] = read_info(core).get("display_name", core)
        manifest["cores"][core] = rec
        installed[core] = rec
        ok(f"{core:18} → {rec['library_name']}" + ("  (fixed build)" if rec.get("source") else ""))
    manifest.save()
    return installed


# ---------------------------------------------------------------- shaders
def ensure_shaders(manifest, update=False):
    step("Shaders (slang)")
    zpath = SHADERS_CACHE + "/shaders_slang.zip"
    if Net.online and (update or not os.path.exists(zpath)):
        try:
            info("downloading the official slang-shaders pack …")
            download(BUILDBOT_ASSETS + "/shaders_slang.zip", zpath, timeout=600)
            manifest["downloads"]["shaders_slang.zip"] = {"date": now_iso()}
        except Exception as e:  # noqa: BLE001
            warn(f"shader download failed: {e}")
    if not os.path.exists(zpath):
        pending("shaders", "shaders_slang.zip", "needs internet once")
        return False
    dest = os.path.join(RA_SHADERS, "shaders_slang")
    marker = os.path.join(dest, ".retroarch-mac-setup-sha256")
    sha = file_sha256(zpath)
    if not (os.path.exists(marker) and open(marker).read().strip() == sha):
        if os.path.exists(dest):
            shutil.rmtree(dest)
        ensure_dir(dest)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(dest)
        with open(marker, "w") as f:
            f.write(sha)
        ok("slang-shaders extracted")
    else:
        ok("slang-shaders up to date")
    # project presets → RetroArch and a copy in the cache
    for target in (os.path.join(RA_SHADERS, "retroarch-mac-setup"), SHADERS_CACHE + "/presets"):
        ensure_dir(target)
        for f in glob.glob(os.path.join(PROJECT, "config/shaders/*.slangp")):
            shutil.copy2(f, target)
    ok("project presets installed")
    return True


# ---------------------------------------------------------------- image tool
def image_tool():
    """ImageMagick if available (installs it with Homebrew when online), else macOS 'sips'."""
    magick = which("magick")
    if magick:
        return ("magick", magick)
    brew = which("brew")
    if brew and Net.online:
        info("Installing ImageMagick with Homebrew …")
        if run([brew, "install", "imagemagick"], check=False, capture=False).returncode == 0:
            magick = which("magick")
            if magick:
                return ("magick", magick)
    warn("ImageMagick not available — falling back to macOS 'sips' (bilinear scaling).")
    return ("sips", "/usr/bin/sips")
