"""Button layouts for MAME games, assigned inside MAME (not with RetroArch button remaps).

Physical button = RetroPad button with the same letter (see the SN30 Pro autoconfig profile).

Why not RetroArch button remaps: with RetroArch 1.22.2 and the MAME cores, every layout that
redirected face buttons through RetroArch remap files (ours, or ones set by hand in Quick Menu
-> Controls) left one or two buttons dead, while games that needed no redirection worked
perfectly. So RetroArch passes the pad through untouched and MAME's own input configuration
assigns the buttons, the same mechanism as MAME's Tab menu.

Layouts (physical button -> MAME button):
  standard  every MAME game:            Y=1  B=2  X=3  A=4  R=5  R2=6  L=7
            (Neo Geo: A/B/C/D = 1-4 = low punch, low kick, strong punch, strong kick)
  cps       CPS1/CPS2/CPS3 6-button:    Y=1 (LP) X=2 (MP) R=3 (HP) B=4 (LK) A=5 (MK) R2=6 (HK)

MAME 2003-Plus (MAME 0.78, binary configs, src/config.c):
  * RetroArch remap only sets the controller type "RetroPad": B A Y X L R L2 R2 = JOYCODE 1-8
  * cfg/default.cfg ("MAMEDEF" v7, applies by button type to every game): standard layout
  * cfg/<set>.cfg ("MAMECFG" v9, per game): needs the game's exact input port list. Neo Geo
    games share one list (config/mame/mame2003plus-neogeo-ports.cfg); each CPS game's list was
    written by MAME itself and is kept in config/mame/mame2003plus-ports/ with an index.
    CPS games get the cps layout there, Neo Geo games the standard one.
MAME 2010 (MAME 0.139, XML configs; A B X Y L R L2 L3 = JOYCODE 1-8, R2 is never read):
  * RetroArch folder remap: only R2 -> L3 (one way), so R2 reaches MAME as JOYCODE 8
  * cfg/default.cfg: standard layout
  * cfg/<set>.cfg for CPS games: cps layout. Without a port tag MAME 0.139 matches fields by
    type, player, mask and default value (inptport.c load_game_config), so one list of every
    CPS button field (config/mame/mame2010-cps-buttons.json, from cps1.c/cps2.c/cps3.c) works
    for all CPS games; fields a game does not have are ignored.
Files changed in MAME's own menu are kept: only untouched files or files we wrote are replaced.
"""
import hashlib
import json
import os
import re
import struct
import xml.etree.ElementTree as ET

from .common import PROJECT, RA_CONFIG_DIR, SAVES_DIR, ensure_dir, info, ok, warn
from .mame import MAME_DATS

MARK = "# managed by retroarch-mac-setup"
REMAP_DIR = os.path.join(RA_CONFIG_DIR, "remaps")
MAME_CFG = os.path.join(PROJECT, "config/mame")
NEOGEO_TEMPLATE = os.path.join(MAME_CFG, "mame2003plus-neogeo-ports.cfg")
M2003_DEFAULT_TEMPLATE = os.path.join(MAME_CFG, "mame2003plus-default.cfg")
M2003_PORTS_DIR = os.path.join(MAME_CFG, "mame2003plus-ports")
M2010_CPS_FIELDS = os.path.join(MAME_CFG, "mame2010-cps-buttons.json")

RP = {"b": 0, "y": 1, "select": 2, "start": 3, "a": 8, "x": 9, "l": 10, "r": 11,
      "l2": 12, "r2": 13, "l3": 14, "r3": 15}
DEV_RETROPAD = 1

CPS_SOURCES = {"cps1.c", "cps2.c", "cps3.c"}
NEOGEO_SOURCES = {"neodrvr.c", "neogeo.c"}
SOURCE_XML = "MAME 2010 XML.xml"   # the 2003-Plus XML has no sourcefile attribute

# MAME button -> JOYCODE number, per core and layout
M2003 = {  # RetroPad type: B A Y X L R L2 R2 = 1..8
    "standard": {1: 3, 2: 1, 3: 4, 4: 2, 5: 6, 6: 8, 7: 5},
    "cps": {1: 3, 2: 4, 3: 6, 4: 1, 5: 2, 6: 8},
}
M2010 = {  # A B X Y L R L2 L3(<-R2) = 1..8
    "standard": {1: 4, 2: 2, 3: 3, 4: 1, 5: 6, 6: 8, 7: 5},
    "cps": {1: 4, 2: 3, 3: 6, 4: 2, 5: 1, 6: 8},
}
# Neo Geo-only layout written by the previous version (buttons 1-4), recognised as ours
_M2003_PREVIOUS = [{1: 3, 2: 1, 3: 4, 4: 2}]

# mame2003-plus code numbers (src/input.h, src/inptport.h)
STANDARD_FLAG = 0x10000000
JOYCODE_BUTTON1 = {1: 109, 2: 125, 3: 141, 4: 157}
IPT_BUTTON1 = 15


def _sources():
    """set -> [sourcefile, cloneof] from the MAME 2010 XML, cached as JSON."""
    xml_path = os.path.join(MAME_DATS, SOURCE_XML)
    idx = os.path.join(MAME_DATS, "mame2010.sources.json")
    if os.path.exists(idx) and (not os.path.exists(xml_path)
                                or os.path.getmtime(idx) >= os.path.getmtime(xml_path)):
        with open(idx, encoding="utf-8") as f:
            return json.load(f)
    if not os.path.exists(xml_path):
        return {}
    out = {}
    for _, el in ET.iterparse(xml_path):
        if el.tag in ("game", "machine"):
            out[el.get("name")] = [el.get("sourcefile"), el.get("cloneof")]
            el.clear()
    with open(idx, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


# ------------------------------------------------------------------ RetroArch remap files ---

def _remap_text(title, device, buttons=None):
    lines = [MARK, f"# {title}"]
    for port in range(1, 5):
        lines.append(f'input_libretro_device_p{port} = "{device}"')
        lines.append(f'input_remap_port_p{port} = "{port - 1}"')
        for phys, target in (buttons or {}).items():
            lines.append(f'input_player{port}_btn_{phys} = "{RP[target]}"')
    return "\n".join(lines) + "\n"


def _player1_buttons(text):
    vals = dict(re.findall(r'^input_player1_btn_(\w+)\s*=\s*"(-?\d+)"', text, flags=re.M))
    return {k: int(vals.get(k, RP[k])) for k in RP}


# remaps written by earlier versions (RetroArch may have re-saved them without our marker)
_OLD_TABLES = {
    "mame2010": [{1: "a", 2: "b", 3: "x", 4: "y", 5: "l", 6: "r", 7: "l2"}],
    "mame2003_plus": [{1: "b", 2: "a", 3: "y", 4: "x", 5: "l", 6: "r", 7: "l2", 8: "r2"},
                      {1: "y", 2: "x", 3: "r", 4: "b", 5: "a", 6: "r2", 7: "l", 8: "l2"}],
}
_OLD_LAYOUTS = [{"y": 1, "b": 2, "x": 3, "a": 4, "r": 5, "r2": 6, "l": 7},
                {"x": 1, "a": 2, "y": 3, "b": 4, "r": 5, "r2": 6, "l": 7},
                {"y": 1, "x": 2, "r": 3, "b": 4, "a": 5, "r2": 6, "l": 7}]


def _ours(text, core):
    have = _player1_buttons(text)
    for layout in _OLD_LAYOUTS:
        for table in _OLD_TABLES.get(core, []):
            want = {k: RP[k] for k in RP}
            for phys, mame_btn in layout.items():
                if mame_btn in table:
                    want[phys] = RP[table[mame_btn]]
            if have == want:
                return True
    return False


def _write_remap(path, text, core):
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            cur = f.read()
        if MARK not in cur and not _ours(cur, core):
            return False          # the user saved their own remap here: leave it alone
        if cur == text:
            return True
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# --------------------------------------------------------------- mame2003-plus binary cfg ---

class _Reader:
    def __init__(self, data, p):
        self.d, self.p = data, p

    def u32(self):
        v, = struct.unpack(">I", self.d[self.p:self.p + 4]); self.p += 4
        return v

    def u16(self):
        v, = struct.unpack(">H", self.d[self.p:self.p + 2]); self.p += 2
        return v

    def seq(self):
        return [self.u32() for _ in range(self.u16())]


def _seq_bytes(s):
    return struct.pack(">H", len(s)) + b"".join(struct.pack(">I", c) for c in s)


def _parse_game(data):
    """MAMECFG v9 -> [total, defaults, current, tail]; port = [type, mask, default, codes]."""
    if data[:8] != b"MAMECFG\x09":
        return None
    r = _Reader(data, 8)
    try:
        total = r.u32()
        ports = [[r.u32(), r.u16(), r.u16(), r.seq()] for _ in range(2 * total)]
    except struct.error:
        return None
    return [total, ports[:total], ports[total:], data[r.p:]]


def _build_game(total, defaults, current, tail):
    out = bytearray(b"MAMECFG\x09") + struct.pack(">I", total)
    for t, m, dv, s in defaults + current:
        out += struct.pack(">IHH", t, m, dv) + _seq_bytes(s)
    return bytes(out + tail)


def _parse_default(data):
    """MAMEDEF v7 -> [[type, default_seq, seq], ...]."""
    if data[:8] != b"MAMEDEF\x07":
        return None
    r = _Reader(data, 8)
    out = []
    try:
        while r.p + 4 <= len(data):
            out.append([r.u32(), r.seq(), r.seq()])
    except struct.error:
        return None
    return out


def _build_default(entries):
    out = bytearray(b"MAMEDEF\x07")
    for t, a, b in entries:
        out += struct.pack(">I", t) + _seq_bytes(a) + _seq_bytes(b)
    return bytes(out)


def _button(t):
    """(MAME button number, player) of an IPT_BUTTONn type with player flags, else None."""
    btn, player = (t & 0xFF) - IPT_BUTTON1 + 1, ((t >> 16) & 7) + 1
    return (btn, player) if 1 <= btn <= 10 and player in JOYCODE_BUTTON1 else None


def _code(mapping, btn, player):
    return STANDARD_FLAG | (JOYCODE_BUTTON1[player] + mapping[btn] - 1)


def _with_layout(current, mapping):
    cur = [list(p) for p in current]
    for p in cur:
        bp = _button(p[0])
        if bp and bp[0] in mapping:
            p[3] = [_code(mapping, *bp)]
    return cur


def _write_game_cfg(path, template, mapping):
    """Write/refresh cfg/<set>.cfg from 'template' ([total, defaults, defaults-as-current, tail])."""
    total, defaults, untouched, tail = template
    wanted = _with_layout(untouched, mapping)
    if os.path.exists(path):
        with open(path, "rb") as f:
            cur = _parse_game(f.read())
        if cur is None or cur[1] != defaults:
            return "skip"                         # another port layout: not ours to touch
        if cur[2] == wanted:
            return "same"
        ours = cur[2] == untouched or any(cur[2] == _with_layout(untouched, m)
                                          for m in list(M2003.values()) + _M2003_PREVIOUS)
        if not ours:
            return "kept"                         # changed in MAME's own menu
        tail = cur[3]                             # keep coin counters / mixer levels
    with open(path, "wb") as f:
        f.write(_build_game(total, defaults, wanted, tail))
    return "written"


def _write_m2003_default(cfg_dir):
    path = os.path.join(cfg_dir, "default.cfg")
    src = path if os.path.exists(path) else M2003_DEFAULT_TEMPLATE
    if not os.path.exists(src):
        return "missing"
    with open(src, "rb") as f:
        entries = _parse_default(f.read())
    if entries is None:
        return "skip"
    mapping = M2003["standard"]
    for t, default, current in entries:          # refuse if anything was changed in MAME's menu
        bp = _button(t)
        mine = bp and bp[0] in mapping and current == [_code(mapping, *bp)]
        if current != default and not mine:
            return "kept"
    new = [[t, d, [_code(mapping, *_button(t))] if _button(t) and _button(t)[0] in mapping else d]
           for t, d, _ in entries]
    data = _build_default(new)
    if src == path:
        with open(path, "rb") as f:
            if f.read() == data:
                return "same"
    with open(path, "wb") as f:
        f.write(data)
    return "written"


def _port_index():
    idx = os.path.join(M2003_PORTS_DIR, "index.json")
    if not os.path.exists(idx):
        return {}
    with open(idx, encoding="utf-8") as f:
        return json.load(f)


def _as_template(g):
    return [g[0], g[1], [list(x) for x in g[1]], g[3]]


def _template_for(cfg_dir, s, index):
    """Port list for set s: the stored template, else the game's own cfg written by MAME."""
    h = index.get(s)
    if h and os.path.exists(os.path.join(M2003_PORTS_DIR, f"{h}.cfg")):
        with open(os.path.join(M2003_PORTS_DIR, f"{h}.cfg"), "rb") as f:
            g = _parse_game(f.read())
        if g:
            return _as_template(g)
    p = os.path.join(cfg_dir, f"{s}.cfg")
    if os.path.exists(p):
        with open(p, "rb") as f:
            g = _parse_game(f.read())
        if g:
            return _as_template(g)
    return None


def store_port_templates(cfg_dir, sets):
    """Keep the port lists MAME wrote for these sets in the project (deduplicated), so the
    per-game configs can be rebuilt after a reformat without starting every game again."""
    ensure_dir(M2003_PORTS_DIR)
    index = _port_index()
    for s in sets:
        p = os.path.join(cfg_dir, f"{s}.cfg")
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            g = _parse_game(f.read())
        if not g:
            continue
        h = hashlib.sha1(_build_game(g[0], g[1], g[1], b"")).hexdigest()[:12]
        tp = os.path.join(M2003_PORTS_DIR, f"{h}.cfg")
        if not os.path.exists(tp):
            with open(tp, "wb") as f:
                f.write(_build_game(g[0], g[1], g[1], g[3]))
        index[s] = h
    with open(os.path.join(M2003_PORTS_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(index.items())), f, indent=1)
    return index


# ------------------------------------------------------------------ mame2010 XML configs ---

def _xml(system, ports):
    body = "\n".join(
        f'            <port type="{t}"' + "".join(f' {k}="{v}"' for k, v in attrs.items()) + '>\n'
        f'                <newseq type="standard">{seq}</newseq>\n'
        f'            </port>' for t, attrs, seq in ports)
    return ('<?xml version="1.0"?>\n'
            '<!-- This file is autogenerated; comments and unknown tags will be stripped -->\n'
            f'<mameconfig version="10">\n    <system name="{system}">\n        <input>\n'
            + body + '\n        </input>\n    </system>\n</mameconfig>\n')


def _xml_ports(text):
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError:
        return None
    # MAME normalises codes when it rewrites the file (e.g. JOYCODE_2_BUTTON8_SWITCH)
    return {(p.get("type"), p.get("mask")): re.sub(r"_SWITCH\b", "", " ".join((p.findtext("newseq") or "").split()))
            for p in root.iter("port")}


def _m2010_default(layout=None):
    layout = layout or M2010["standard"]
    return _xml("default", [(f"P{pl}_BUTTON{b}", {}, f"JOYCODE_{pl}_BUTTON{j}")
                            for pl in range(1, 5) for b, j in layout.items()])


# default.cfg written by the previous version (CPS order for every game), recognised as ours
_M2010_PREVIOUS_DEFAULT = {1: 4, 2: 3, 3: 6, 4: 2, 5: 1, 6: 8, 7: 5}


def _m2010_cps(system, fields):
    return _xml(system, [(f"P{pl}_BUTTON{b}", {"mask": mask, "defvalue": dv},
                          f"JOYCODE_{pl}_BUTTON{M2010['cps'][b]}")
                         for pl, b, mask, dv in fields if b in M2010["cps"]])


def _write_xml(path, text, accept):
    """Write unless the file holds input settings that are not ours ('accept': our variants).
    MAME rewrites these files keeping only the entries that matched a field, so a subset of
    one of our variants still counts as ours."""
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            have = _xml_ports(f.read())
        if have == _xml_ports(text):
            return "same"
        if have and not any(have.items() <= _xml_ports(a).items() for a in accept):
            return "kept"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return "written"


# -------------------------------------------------------------------------------- driver ---

def write(sysname, games, cores):
    src = _sources()
    if not src:
        warn("MAME 2010 XML not cached: CPS and Neo Geo games are not told apart")

    def driver(g):
        for name in (g["set"], g.get("parent"), (src.get(g["set"]) or [None, None])[1]):
            drv = (src.get(name) or [None])[0] if name else None
            if drv:
                return drv
        return None

    kinds = {}
    for g in games:
        drv = driver(g)
        kinds[g["set"]] = "cps" if drv in CPS_SOURCES else "neogeo" if drv in NEOGEO_SOURCES else "other"

    # RetroArch remaps: controller type only (+ R2 -> L3 for mame2010), no other redirection
    for core in ("mame2003_plus", "mame2010"):
        if core not in cores:
            continue
        d = ensure_dir(os.path.join(REMAP_DIR, cores[core]["library_name"]))
        if core == "mame2003_plus":
            text = _remap_text("MAME 2003-Plus: RetroPad controller type; buttons are assigned "
                               "in MAME's own config (cfg/default.cfg, cfg/<set>.cfg)", DEV_RETROPAD)
        else:
            text = _remap_text("MAME 2010: R2 -> L3 only (mame2010 does not read R2; MAME sees "
                               "L3 as button 8); buttons are assigned in MAME's config", DEV_RETROPAD,
                               {"r2": "l3"})
        name = f"{sysname}.rmp"
        if not _write_remap(os.path.join(d, name), text, core):
            info(f"remap {name} kept (saved by you)")
        for other in os.listdir(d):                       # stale managed remaps (old layouts)
            p = os.path.join(d, other)
            if other.endswith(".rmp") and other != name:
                with open(p, encoding="utf-8", errors="replace") as f:
                    cur = f.read()
                if MARK in cur or _ours(cur, core):
                    os.remove(p)

    stats = {}

    def count(k):
        stats[k] = stats.get(k, 0) + 1

    if "mame2003_plus" in cores:
        cfg_dir = ensure_dir(os.path.join(SAVES_DIR, "saves", cores["mame2003_plus"]["library_name"],
                                          "mame2003-plus", "cfg"))
        if _write_m2003_default(cfg_dir) == "kept":
            warn("MAME 2003-Plus default.cfg kept: it holds settings changed in MAME's own menu")
        index = _port_index()
        neo = None
        if os.path.exists(NEOGEO_TEMPLATE):
            with open(NEOGEO_TEMPLATE, "rb") as f:
                neo = _as_template(_parse_game(f.read()))
        for g in games:
            kind = kinds[g["set"]]
            if g["core"] != "mame2003_plus" or kind == "other":
                continue
            tpl = neo if kind == "neogeo" else _template_for(cfg_dir, g["set"], index)
            if tpl is None and kind == "cps":
                # clones normally share the parent's input ports; MAME ignores the file if not
                for rel in (g.get("parent"), (src.get(g["set"]) or [None, None])[1]):
                    if rel and tpl is None:
                        tpl = _template_for(cfg_dir, rel, index)
            if tpl is None:
                count(f"{kind} 2003-Plus: no port list")
                continue
            res = _write_game_cfg(os.path.join(cfg_dir, f"{g['set']}.cfg"), tpl,
                                  M2003["cps"] if kind == "cps" else M2003["standard"])
            count(f"{kind} 2003-Plus: {res}")

    if "mame2010" in cores:
        cfg_dir = ensure_dir(os.path.join(SAVES_DIR, "saves", cores["mame2010"]["library_name"],
                                          "mame2010", "cfg"))
        if _write_xml(os.path.join(cfg_dir, "default.cfg"), _m2010_default(),
                      [_m2010_default(), _m2010_default(_M2010_PREVIOUS_DEFAULT)]) == "kept":
            warn("MAME 2010 default.cfg kept: it holds settings changed in MAME's own menu")
        fields = []
        if os.path.exists(M2010_CPS_FIELDS):
            with open(M2010_CPS_FIELDS, encoding="utf-8") as f:
                fields = json.load(f)
        for g in games:
            if g["core"] == "mame2010" and kinds[g["set"]] == "cps" and fields:
                text = _m2010_cps(g["set"], fields)
                count("cps 2010: " + _write_xml(os.path.join(cfg_dir, f"{g['set']}.cfg"), text, [text]))

    n = {k: sum(1 for v in kinds.values() if v == k) for k in ("cps", "neogeo", "other")}
    ok(f"button layouts: standard for {n['other']} games and {n['neogeo']} Neo Geo, "
       f"CPS 6-button for {n['cps']}")
    for k, v in sorted(stats.items()):
        if not k.endswith(("same", "written")):
            info(f"{k}: {v}")
    missing = stats.get("cps 2003-Plus: no port list", 0)
    if missing:
        warn(f"{missing} CPS game(s) on MAME 2003-Plus have no port list yet: start each once, "
             "close it and run the script again (until then they use the standard layout)")
