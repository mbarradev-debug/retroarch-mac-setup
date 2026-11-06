"""Pick the mfi controller profile from the game controllers macOS knows about.

RetroArch 1.22.2's mfi driver connects every pad as "mFi Controller" with no vendor or product
ID (input/drivers_joypad/mfi_joypad.m, mfi_joypad_autodetect_add), so one autoconfig profile
serves every pad and RetroArch cannot choose per controller. The script chooses instead:

  nintendo  Apple labels the face buttons by the printed letter (A right, B bottom): Switch
            Pro Controller, Joy-Con, 8BitDo pads in Switch mode. Vendor 0x057E.
  standard  Apple labels them Xbox-style (A bottom): Xbox, PlayStation and everything else.
            RetroArch's stock profile.

Both give the same RetroPad button for the same position (bottom = B, left = Y), so game
layouts, MAME layouts and hotkeys work alike. Paired pads are read from Bluetooth (also when
switched off) and wired ones from USB; a pad that is connected wins over one only paired.
The choice is kept in the manifest, so a later run without the pad keeps it.
"""
import json
import os
import re
import shutil

from .common import PROJECT, RA_AUTOCONFIG, confirm, info, ok, run, warn

PROFILES = {"nintendo": "Nintendo layout (Switch Pro Controller, 8BitDo in Switch mode)",
            "standard": "Xbox / PlayStation layout"}
NINTENDO = 0x057E
# vendors that make game controllers, for USB devices (USB has no "gamepad" type to read)
PAD_VENDORS = {NINTENDO, 0x045E, 0x054C, 0x2DC8, 0x0E6F, 0x0F0D, 0x20D6, 0x0738, 0x1532,
               0x24C6, 0x0079, 0x2563, 0x146B}
_PADLIKE = re.compile(r"controller|gamepad|joy-?con|joystick|dualsense|dualshock|8bitdo", re.I)


def _profiler(datatype):
    r = run(["system_profiler", "-json", datatype], check=False)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {}


def _hex(v):
    m = re.match(r"\s*0x([0-9a-fA-F]+)", str(v or ""))
    return int(m.group(1), 16) if m else None


def _bluetooth():
    out = []
    for bt in _profiler("SPBluetoothDataType").get("SPBluetoothDataType", []):
        for section, connected in (("device_connected", True), ("device_not_connected", False)):
            for dev in bt.get(section) or []:
                for name, d in dev.items():
                    kind = d.get("device_minorType", "")
                    if kind in ("Gamepad", "Joystick") or (not kind and _PADLIKE.search(name)):
                        out.append({"name": name, "vendor": _hex(d.get("device_vendorID")),
                                    "connected": connected, "via": "Bluetooth"})
    return out


def _usb():
    out = []

    def walk(x):
        if isinstance(x, dict):
            name = x.get("_name", "")
            vendor = _hex(x.get("USBDeviceKeyVendorID") or x.get("vendor_id"))
            if vendor is not None and (vendor in PAD_VENDORS or _PADLIKE.search(name)):
                out.append({"name": name, "vendor": vendor, "connected": True, "via": "USB"})
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(_profiler("SPUSBHostDataType"))      # macOS 15+
    if not out:
        walk(_profiler("SPUSBDataType"))      # older macOS
    return out


def find_pads():
    pads, seen = [], set()
    for p in _usb() + _bluetooth():           # USB first: a pad on a cable is also paired
        key = (p["name"], p["vendor"])
        if key in seen:
            continue
        seen.add(key)
        p["profile"] = "nintendo" if p["vendor"] == NINTENDO else "standard"
        pads.append(p)
    return pads


def _describe(p):
    state = "connected" if p["connected"] else "paired"
    return f'{p["name"]} ({p["via"]}, {state})'


def choose(option, manifest):
    """Profile name for this run. option: None, "auto", "nintendo" or "standard"."""
    saved = manifest.get("controller") or {}
    if option in PROFILES:
        manifest["controller"] = {"profile": option, "source": "option"}
        info(f"controller profile: {PROFILES[option]} (--controller {option})")
        return option
    if option is None and saved.get("source") == "option" and saved.get("profile") in PROFILES:
        info(f"controller profile: {PROFILES[saved['profile']]} (chosen with --controller; "
             "--controller auto detects it again)")
        return saved["profile"]

    pads = find_pads()
    pool = [p for p in pads if p["connected"]] or pads
    for p in pads:
        info("controller found: " + _describe(p))
    kinds = []
    for p in pool:
        if p["profile"] not in kinds:
            kinds.append(p["profile"])

    if not kinds:
        profile = saved.get("profile") if saved.get("profile") in PROFILES else "standard"
        warn("no game controller connected or paired with this Mac: using the "
             f"{PROFILES[profile]}. Pair the controller and run ./setup.sh again, or choose with "
             "--controller nintendo|standard")
        manifest["controller"] = {"profile": profile, "source": saved.get("source", "default")}
        return profile

    profile = kinds[0]
    if len(kinds) > 1:
        names = {k: ", ".join(p["name"] for p in pool if p["profile"] == k) for k in kinds}
        warn("controllers of both layouts found; RetroArch's mfi driver can use only one profile "
             "for all of them:")
        for k in kinds:
            info(f"{PROFILES[k]}: {names[k]}")
        profile = "nintendo" if confirm(f"Use the {PROFILES['nintendo']} profile? (no = "
                                        f"{PROFILES['standard']})", default=kinds[0] == "nintendo") \
            else "standard"
    manifest["controller"] = {"profile": profile, "source": "detected",
                              "pads": [p["name"] for p in pool]}
    return profile


def install(option, manifest):
    profile = choose(option, manifest)
    src = os.path.join(PROJECT, "config/autoconfig/mfi", f"{profile}.cfg")
    shutil.copy2(src, os.path.join(RA_AUTOCONFIG, "mfi/MFi_Controller.cfg"))
    ok(f"controller profile installed: {PROFILES[profile]} (mfi/MFi_Controller.cfg)")
    return profile
