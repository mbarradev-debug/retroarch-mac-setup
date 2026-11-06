"""retroarch-mac-setup — entry point (called by setup.sh)."""
import argparse
import datetime
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rasetup import bios, configure, content, folders, install, mame, playlists, remaps, thumbs  # noqa: E402
from rasetup.common import (CACHE, NO_SNAPSHOT, PENDING, RA_PLAYLISTS, ROMS, Manifest, Net,  # noqa: E402
                            confirm, ensure_dir, err, info, load_systems, now_iso, ok, step,
                            table, warn)


def parse_args():
    p = argparse.ArgumentParser(description="Install and configure RetroArch on Apple Silicon.")
    p.add_argument("--refresh-missing", action="store_true",
                   help="search again online for ROMs marked as having no gameplay snapshot")
    p.add_argument("--update", action="store_true",
                   help="re-download RetroArch, cores, shaders and DATs even if cached")
    p.add_argument("--offline", action="store_true", help="never use the network")
    p.add_argument("--canvas", default=None, help="thumbnail canvas WxH (default 960x720, 4:3)")
    p.add_argument("--controller", choices=["auto", "nintendo", "standard"], default=None,
                   help="controller profile: nintendo (Switch Pro / 8BitDo in Switch mode), standard "
                        "(Xbox, PlayStation, others); auto = detect (default, remembered)")
    p.add_argument("--check-paths", action="store_true",
                   help="only check that retroarch.cfg saves/states/BIOS point to the library, and fix them")
    # read by setup.sh (lib/library.sh) before Python starts; listed here for --help
    p.add_argument("--library", metavar="PATH",
                   help="game library: the folder holding roms/ (bios/, saves and cache go next to "
                        "it); remembered")
    p.add_argument("--roms", metavar="PATH", help="ROM folder, if it is not <library>/roms; remembered")
    p.add_argument("--renormalize", action="store_true", help="rebuild normalized thumbnails from originals")
    return p.parse_args()


# ---------------------------------------------------------------- ROM moves (_no_snapshot)
def _companions(folder, rel):
    """Files that belong to a ROM entry (cue+bins, m3u+discs, or a whole game sub-folder)."""
    if os.path.dirname(rel):
        return [os.path.dirname(rel).split(os.sep)[0]]          # move the game folder as a unit
    out = [rel]
    ext = rel.rsplit(".", 1)[-1].lower()
    if ext in ("cue", "m3u"):
        text = open(os.path.join(folder, rel), encoding="utf-8", errors="replace").read()
        refs = re.findall(r'FILE\s+"([^"]+)"', text, re.I) if ext == "cue" else \
            [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        for r in refs:
            if os.path.exists(os.path.join(folder, r)):
                out += _companions(folder, r) if r.lower().endswith(".cue") else [r]
    return list(dict.fromkeys(out))


def move_entry(src_root, dst_root, rel):
    moved = []
    for part in _companions(src_root, rel):
        s, d = os.path.join(src_root, part), os.path.join(dst_root, part)
        if os.path.exists(d):
            warn(f"not moved, already exists at destination: {d}")
            continue
        ensure_dir(os.path.dirname(d))
        os.rename(s, d)
        moved.append(part)
    return moved


# ---------------------------------------------------------------- helpers
def unique_labels(items):
    seen = {}
    for it in items:
        k = it["label"].lower()
        seen[k] = seen.get(k, 0) + 1
    for it in items:
        if seen[it["label"].lower()] > 1:
            it["label"] = f"{it['label']} [{os.path.splitext(os.path.basename(it['rel']))[0]}]"


def console_items(sysname, s, folder, manifest, update):
    rels = content.list_roms(folder, s["extensions"])
    dat = content.load_dat(sysname, s.get("dat"), update)
    items = []
    for rel in rels:
        stem = os.path.splitext(os.path.basename(rel))[0]
        extra = []
        if dat:
            for crc in content.cached_crcs(manifest, sysname, folder, rel):
                extra += dat.get(crc, [])
        items.append({"rel": rel, "label": stem, "extra": list(dict.fromkeys(extra)), "core": s["cores"][0]})
    manifest.save()
    unique_labels(items)
    return items


def restore_found(systems, present, manifest, tool, canvas):
    """--refresh-missing: ROMs in _no_snapshot that now have a snap can be moved back."""
    back = []
    for sysname in present:
        src = os.path.join(NO_SNAPSHOT, sysname)
        if not os.path.isdir(src):
            continue
        s = systems[sysname]
        if s["type"] == "arcade":
            rels = sorted(f for f in os.listdir(src) if f.lower().endswith(".zip") and not f.startswith("."))
            labels = {r: manifest["no_snapshot"].get(sysname, {}).get(r, {}).get("game", r[:-4]) for r in rels}
        else:
            rels = content.list_roms(src, s["extensions"])
            labels = {r: os.path.splitext(os.path.basename(r))[0] for r in rels}
        if not rels:
            continue
        idx = thumbs.load_index(thumbs.thumb_system(sysname), manifest, refresh=True)
        if not idx:
            continue
        m = thumbs.Matcher(idx)
        for r in rels:
            extra = manifest["no_snapshot"].get(sysname, {}).get(r, {}).get("alt", [])
            snap, _, _ = m.match(labels[r], extra)
            if snap:
                back.append((sysname, r, snap))
    if not back:
        return
    step("ROMs in _no_snapshot that now have a gameplay snapshot")
    counts = {}
    for sysname, _, _ in back:
        counts[sysname] = counts.get(sysname, 0) + 1
    table([(k, v) for k, v in counts.items()], ["System", "ROMs to move back"])
    if confirm("Move them back to their system folder?"):
        for sysname, r, _ in back:
            move_entry(os.path.join(NO_SNAPSHOT, sysname), os.path.join(ROMS, sysname), r)
            manifest["no_snapshot"].get(sysname, {}).pop(r, None)
        manifest.save()
        ok(f"{len(back)} ROM(s) moved back")


# ---------------------------------------------------------------- main
def main():
    args = parse_args()
    if args.check_paths:
        configure.check_paths()
        return 0
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = ensure_dir(os.path.join(CACHE, "reports", ts))
    for d in ("thumbnails/originals", "thumbnails/normalized", "cores", "shaders", "installer", "dats"):
        ensure_dir(os.path.join(CACHE, d))
    manifest = Manifest()
    systems = load_systems()

    step("Network")
    if Net.detect(args.offline):
        ok("online")
    else:
        warn("offline — using only what is in " + CACHE)

    canvas_s = args.canvas or manifest.get("canvas") or "960x720"
    m = re.fullmatch(r"(\d+)x(\d+)", canvas_s)
    if not m:
        err("--canvas must look like 960x720")
        return 2
    canvas = (int(m.group(1)), int(m.group(2)))
    manifest["canvas"] = canvas_s

    # 1. folders
    present, unknown = folders.rename_folders(systems)
    if not present:
        warn("No known system folders found.")

    # 2. RetroArch, cores, shaders
    ra = install.ensure_retroarch(manifest, args.update)
    manifest.save()
    needed = sorted({c for n in present for c in systems[n]["cores"]})
    cores = install.ensure_cores(needed, manifest, args.update)
    install.ensure_shaders(manifest, args.update)
    tool = install.image_tool()

    # 3. configuration
    if ra:
        configure.configure(systems, present, cores, manifest, args.controller)
    else:
        warn("RetroArch not available — configuration skipped this run")

    # 4. BIOS
    bios_lines, bios_problems = bios.check(systems, present)

    # 5. optional restore of ROMs that now have a snap
    if args.refresh_missing and Net.online:
        restore_found(systems, present, manifest, tool, canvas)

    # 6. content + thumbnails
    per_system = {}
    mame_report = []
    for sysname, folder in present.items():
        s = systems[sysname]
        step(f"{sysname}")
        if s["type"] == "arcade" and any(c in mame.CORE_DATS for c in s["cores"]):
            games, bios_sets, incompatible = mame.analyze(folder, [c for c in s["cores"] if c in cores],
                                                          manifest, args.update)
            items = [{"rel": g["rel"], "label": g["label"], "extra": g["alt_names"], "core": g["core"],
                      "set": g["set"], "parent": g["parent"]} for g in games]
            unique_labels(items)
            mame_report.append((sysname, games, bios_sets, incompatible))
        else:
            items = console_items(sysname, s, folder, manifest, args.update)
        info(f"{len(items)} ROM(s)")
        missing, deferred = thumbs.process(sysname, items, manifest, tool, canvas,
                                           args.refresh_missing, args.renormalize)
        with_snap = sum(1 for it in items if "snap" in it)
        ok(f"gameplay snaps: {with_snap}/{len(items)}")
        if deferred:
            warn(f"{len(deferred)} could not be checked now (offline or download error) — kept in the playlist")
        per_system[sysname] = {"folder": folder, "items": items, "missing": missing, "deferred": deferred}

    # 7. ROMs without gameplay snap → _no_snapshot (with confirmation)
    nos = manifest["no_snapshot"]
    # MAME parents are needed by their clones (split sets): never move them, just hide them
    for n, d in per_system.items():
        parents = {it.get("parent") for it in d["items"] if it.get("parent")}
        keep = []
        for it, tried in d["missing"]:
            if it.get("set") in parents:
                it["hidden"] = True
                rec = nos.setdefault(n, {}).setdefault(it["rel"], {})
                rec.update({"game": it["label"], "tried": tried[:12], "kept_as_parent": True, "date": now_iso()})
            else:
                keep.append((it, tried))
        d["missing"] = keep
    candidates = {n: list(d["missing"]) for n, d in per_system.items()}
    candidates = {n: c for n, c in candidates.items() if c}
    moved_now = {}
    if candidates:
        step("ROMs with no gameplay snapshot (after exact, region/revision-less and title matching)")
        table([(n, len(c)) for n, c in candidates.items()], ["System", "ROMs to move"])
        info(f"They would be moved (never deleted) to {NO_SNAPSHOT}/<system>/ and left out of the playlist.")
        detail = os.path.join(report_dir, "no_snapshot_candidates.txt")
        with open(detail, "w", encoding="utf-8") as f:
            for n, c in candidates.items():
                f.write(f"## {n} ({len(c)})\n")
                for it, tried in c:
                    f.write(f"{it['rel']}\t{it['label']}\n")
        info(f"Full list: {detail}")
        go = confirm("Move these ROMs now?")
        for n, c in candidates.items():
            for it, tried in c:
                # recorded as "no snapshot" so it is not searched online again (unless --refresh-missing)
                rec = nos.setdefault(n, {}).setdefault(it["rel"], {})
                rec.update({"game": it["label"], "tried": tried[:12], "alt": it.get("extra", [])[:6],
                            "date": now_iso()})
                if go:
                    moved = move_entry(per_system[n]["folder"], os.path.join(NO_SNAPSHOT, n), it["rel"])
                    if moved:
                        rec["moved_to"] = os.path.join(NO_SNAPSHOT, n, it["rel"])
                        moved_now.setdefault(n, []).append(it["rel"])
                        it["moved"] = True
        manifest.save()
        if go:
            ok(f"moved {sum(len(v) for v in moved_now.values())} ROM(s)")
        else:
            warn("Nothing moved. They stay in their playlists (without image); you will be asked again next run.")

    # 8. playlists
    step("Playlists → " + RA_PLAYLISTS)
    for n, d in per_system.items():
        s = systems[n]
        entries = []
        for it in d["items"]:
            if it.get("moved") or it.get("hidden"):
                continue
            entries.append({"path": os.path.join(d["folder"], it["rel"]), "label": it["label"], "core": it["core"]})
        path, count = playlists.write(n, entries, cores, s["cores"][0])
        ok(f"{n}.lpl: {count} games")
    playlists.remove_stale(manifest.get("playlists", []), list(per_system))
    manifest["playlists"] = list(per_system)
    manifest.save()

    # 8b. MAME button layouts (fighting-game order, CPS 6-button)
    if mame_report:
        step("MAME button layouts → " + remaps.REMAP_DIR)
        for sysname, games, _, _ in mame_report:
            remaps.write(sysname, games, cores)

    # 9. reports
    step("Reports → " + report_dir)
    write_reports(report_dir, per_system, mame_report, bios_lines, unknown, moved_now, manifest)

    if PENDING:
        warn(f"{len(PENDING)} item(s) pending download — see pending.txt (run again with internet)")
    if bios_problems:
        warn(f"{bios_problems} system(s) are missing a required BIOS — see bios.txt")
    print()
    ok("Done. Open RetroArch:  open -a RetroArch")
    return 0


def write_reports(report_dir, per_system, mame_report, bios_lines, unknown, moved_now, manifest):
    def w(name, lines):
        p = os.path.join(report_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        info(p)

    # cumulative list of everything currently in _no_snapshot
    nos_lines = [f"ROMs without a gameplay snapshot — generated {now_iso()}",
                 f"Location: {NO_SNAPSHOT}/<system>/  (moved, never deleted)", ""]
    for n, recs in sorted(manifest["no_snapshot"].items()):
        moved = sorted(r for r, v in recs.items() if v.get("moved_to"))
        kept = sorted(r for r, v in recs.items() if not v.get("moved_to"))
        if not moved and not kept:
            continue
        nos_lines.append(f"## {n}: {len(moved)} moved" + (f", {len(kept)} still in place (move not confirmed, or MAME parent needed by clones)" if kept else ""))
        for r in moved:
            nos_lines.append(f"  {r}" + ("   [moved this run]" if r in moved_now.get(n, []) else ""))
        for r in kept:
            nos_lines.append(f"  (kept) {r}")
        nos_lines.append("")
    w("no_snapshot.txt", nos_lines)
    if os.path.isdir(NO_SNAPSHOT):
        shutil.copy2(os.path.join(report_dir, "no_snapshot.txt"), os.path.join(NO_SNAPSHOT, "_report.txt"))

    for sysname, games, bios_sets, incompatible in mame_report:
        lines = [f"{sysname} romset report — {now_iso()}", ""]
        by_core = {}
        for g in games:
            by_core.setdefault(g["core"], []).append(g)
        for core, gs in by_core.items():
            lines.append(f"## Runs with {core}: {len(gs)}")
        warned = [g for g in games if g.get("warning")]
        lines += ["", f"## In the playlist but with ROMs whose CRC differs (MAME shows WRONG CHECKSUMS, may glitch): {len(warned)}"]
        lines += [f"  {g['rel']:16} {g['label']}  [{g['core']}]  —  {g['warning']}" for g in warned]
        blockers = {}
        for x in incompatible:
            for r in x["reasons"].values():
                m = re.search(r"\(e\.g\. ([^)]+)\)|needs parent/BIOS '([^']+)'", r)
                if m:
                    k = m.group(1) or m.group(2)
                    blockers[k] = blockers.get(k, 0) + 1
                    break
        if blockers:
            lines += ["", "## Most common blockers (fixing one file can recover many games)"]
            for k, v in sorted(blockers.items(), key=lambda kv: -kv[1])[:15]:
                lines.append(f"  {v:4} games blocked by {k}")
        lines += ["", f"## BIOS / device sets (kept, excluded from playlist): {len(bios_sets)}"]
        lines += ["  " + b for b in bios_sets]
        lines += ["", f"## Incompatible with every installed MAME core (kept in place, not in playlist): {len(incompatible)}"]
        for x in incompatible:
            why = "; ".join(f"{c}: {r}" for c, r in x["reasons"].items())
            lines.append(f"  {x['rel']:16} {x['label']}  —  {why}")
        w(f"mame_{sysname}.txt".replace(" ", "_"), lines)

    w("bios.txt", bios_lines or ["No detected system needs a BIOS."])
    w("unknown_folders.txt", unknown or ["(none)"])
    summary = []
    for n, d in per_system.items():
        snaps = sum(1 for it in d["items"] if "snap" in it)
        summary.append(f"{n}: {len(d['items'])} ROMs, {snaps} with gameplay snap, "
                       f"{len(d['missing'])} without, {len(d['deferred'])} pending")
    w("summary.txt", summary)
    w("pending.txt", [f"{c}\t{i}\t{r}" for c, i, r in PENDING] or ["(nothing pending)"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        err("interrupted")
        sys.exit(130)
    except RuntimeError as e:
        err(str(e))
        sys.exit(1)
