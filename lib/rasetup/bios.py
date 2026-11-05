"""Check BIOS files for detected systems. Never downloads anything."""
import os

from .common import BIOS_DIR, file_md5, info, ok, step, warn


def check(systems, present):
    step(f"BIOS check ({BIOS_DIR})")
    lines, problems = [], 0
    for name in present:
        b = systems[name].get("bios", {})
        files = b.get("files", [])
        if not files:
            continue
        mode = b.get("mode", "optional")
        status = []
        for f in files:
            p = os.path.join(BIOS_DIR, f["file"])
            if not os.path.exists(p):
                status.append((f, "missing"))
            elif f.get("md5") and file_md5(p) != f["md5"]:
                status.append((f, "wrong md5"))
            else:
                status.append((f, "ok"))
        good = [f for f, st in status if st == "ok"]
        if mode == "all":
            fine = len(good) == len(files)
        elif mode == "one_of":
            fine = bool(good)
        else:
            fine = True
        head = f"{name}: " + ("OK" if fine else ("MISSING (required)" if mode != "optional" else "optional"))
        (ok if fine and (good or mode == "optional") else warn)(head)
        lines.append(head)
        need = {"all": "all of these", "one_of": "at least one of these", "optional": "optional"}[mode]
        for f, st in status:
            line = f"      [{st:9}] {f['file']:22} {f.get('desc', '')}  md5={f.get('md5') or '-'}"
            if st != "ok":
                info(line)
            lines.append(line)
        if not fine:
            problems += 1
            msg = f"      → copy {need} into {BIOS_DIR}/ (exact file name, lower/upper case as shown)"
            info(msg)
            lines.append(msg)
    if not lines:
        ok("no detected system needs a BIOS")
    return lines, problems
