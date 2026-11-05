"""Print a libretro core's retro_system_info as JSON.

RetroArch names override/preset/options folders after `library_name`, which can differ from the
`corename` in the .info file (e.g. mame2010). Run in a child process so a misbehaving core cannot
crash the installer.
"""
import ctypes
import json
import sys


class RetroSystemInfo(ctypes.Structure):
    _fields_ = [("library_name", ctypes.c_char_p),
                ("library_version", ctypes.c_char_p),
                ("valid_extensions", ctypes.c_char_p),
                ("need_fullpath", ctypes.c_bool),
                ("block_extract", ctypes.c_bool)]


def main(path):
    lib = ctypes.CDLL(path)
    si = RetroSystemInfo()
    lib.retro_get_system_info(ctypes.byref(si))
    dec = lambda b: b.decode("utf-8", "replace") if b else ""  # noqa: E731
    print(json.dumps({"library_name": dec(si.library_name),
                      "library_version": dec(si.library_version),
                      "valid_extensions": dec(si.valid_extensions),
                      "need_fullpath": bool(si.need_fullpath),
                      "block_extract": bool(si.block_extract)}))


if __name__ == "__main__":
    main(sys.argv[1])
