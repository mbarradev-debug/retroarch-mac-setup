"""Write retroarch.cfg keys, per-core overrides/options, per-folder shader presets, autoconfig, font."""
import glob
import os
import re
import shutil

from . import controller, diskpaths
from .common import (BIOS_DIR, CACHE, PROJECT, RA_AUTOCONFIG, RA_CFG, RA_CONFIG_DIR, RA_HOME,
                     RA_PLAYLISTS, RA_PROJECT_ASSETS, RA_SHADERS, SAVES_DIR, ensure_dir, info, ok,
                     run, step, warn)

MARK = "# managed by retroarch-mac-setup"
_LINE = re.compile(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*(.*)$')   # core options use '-' (mame2003-plus_…)


def _q(v):
    v = str(v)
    return v if v.startswith('"') else f'"{v}"'


def merge_cfg(path, values, header=None):
    """Set key = "value" in a RetroArch-style cfg, keeping every other line as it is."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    elif header:
        lines = [header]
    seen, out = set(), []
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) in values:
            k = m.group(1)
            if k in seen:
                continue                      # drop duplicates of a managed key
            line = f"{k} = {_q(values[k])}"
            seen.add(k)
        out.append(line)
    lines = out
    for k, v in values.items():
        if k not in seen:
            lines.append(f"{k} = {_q(v)}")
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def remove_cfg_keys(path, keys):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    kept = [l for l in lines if not (_LINE.match(l) and _LINE.match(l).group(1) in keys)]
    if kept != lines:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")


def read_template(path, subs):
    vals = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _LINE.match(line)
            if not m or line.lstrip().startswith("#"):
                continue
            v = m.group(2).strip().strip('"')
            for k, s in subs.items():
                v = v.replace("{{" + k + "}}", s)
            vals[m.group(1)] = v
    return vals


def retroarch_running():
    return run(["pgrep", "-x", "RetroArch"], check=False).returncode == 0


def _require_closed():
    if retroarch_running():
        warn("RetroArch is running. It rewrites retroarch.cfg when it quits, which would undo these changes.")
        raise RuntimeError("Quit RetroArch (Hotkey+X twice, or Cmd+Q) and run the script again")


def _font_path():
    return os.path.join(RA_PROJECT_ASSETS, "PixelifySans-Regular.ttf")


def base_values():
    subs = {"RA_HOME": RA_HOME, "CACHE": CACHE, "BIOS": BIOS_DIR, "SAVES": SAVES_DIR, "FONT": _font_path()}
    return read_template(os.path.join(PROJECT, "config/retroarch.base.cfg"), subs)


def check_paths():
    """--check-paths: only put the disk directories back in retroarch.cfg (rescuing files)."""
    step("Checking retroarch.cfg directories")
    _require_closed()
    values = base_values()
    if diskpaths.check(values):
        merge_cfg(RA_CFG, {k: values[k] for k, _, _ in diskpaths.wrong_paths(values)})
        ok("directories restored in retroarch.cfg")


def configure(systems, present, cores, manifest, controller_option=None):
    step("Configuring RetroArch")
    _require_closed()

    for d in (BIOS_DIR, SAVES_DIR + "/saves", SAVES_DIR + "/states", SAVES_DIR + "/screenshots",
              RA_PLAYLISTS, RA_AUTOCONFIG + "/mfi", RA_PROJECT_ASSETS):
        ensure_dir(d)

    # font
    font_src = os.path.join(PROJECT, "fonts/PixelifySans-Regular.ttf")
    shutil.copy2(font_src, _font_path())

    values = base_values()
    diskpaths.check(values)          # before merge_cfg: rescues files saved off the disk
    merge_cfg(RA_CFG, values)
    ok(f"retroarch.cfg: {len(values)} managed keys → {RA_CFG}")

    # controller profile: one for every pad (mfi driver), chosen from the pads macOS knows
    controller.install(controller_option, manifest)

    # per-core overrides, options and per-folder shader presets
    for sysname in present:
        s = systems[sysname]
        for core in s["cores"]:
            if core not in cores:
                continue
            lib = cores[core]["library_name"]
            cdir = ensure_dir(os.path.join(RA_CONFIG_DIR, lib))
            ov_path = os.path.join(cdir, f"{lib}.cfg")
            if s.get("overrides"):
                merge_cfg(ov_path, s["overrides"], header=MARK)
                info(f"override {lib}: " + ", ".join(f"{k}={v}" for k, v in s["overrides"].items()))
            opts = s.get("core_options") or {}
            if any(isinstance(v, dict) for v in opts.values()):   # per-core form: {"<core>": {...}}
                opts = opts.get(core, {})
            if opts:
                merge_cfg(os.path.join(cdir, f"{lib}.opt"), opts, header=MARK)
                info(f"core options {lib}: " + ", ".join(f"{k}={v}" for k, v in opts.items()))
            # folder override: <core>/<folder name>.cfg, loaded after the core override for content
            # in that folder (configuration.c config_load_override). Used for the 4:3 TV aspect,
            # since one core can serve a TV console and a handheld (Genesis Plus GX: MD + Game Gear).
            dir_ov = os.path.join(cdir, f"{sysname}.cfg")
            if s.get("aspect") == "4:3":
                merge_cfg(dir_ov, {"aspect_ratio_index": "0"}, header=MARK)   # 0 = ASPECT_RATIO_4_3
            elif os.path.exists(dir_ov) and MARK in open(dir_ov, encoding="utf-8").read():
                remove_cfg_keys(dir_ov, {"aspect_ratio_index"})
            preset_path = os.path.join(cdir, f"{sysname}.slangp")   # folder preset: <core>/<folder name>.slangp
            shader = s.get("shader", "none")
            if shader and shader != "none":
                target = os.path.join(RA_SHADERS, "retroarch-mac-setup", f"{shader}.slangp")
                with open(preset_path, "w", encoding="utf-8") as f:
                    f.write(f'{MARK}\n#reference "{target}"\n')
            elif os.path.exists(preset_path) and MARK in open(preset_path, encoding="utf-8").read():
                os.remove(preset_path)
    ok("per-core overrides, core options, 4:3 folder overrides and per-system shader presets written")

