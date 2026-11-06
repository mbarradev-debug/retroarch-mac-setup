# Sourced by setup.sh and undo-rename.sh: find the game library and export its paths.
#
# The library is one folder that holds everything the project keeps across reformats:
#   <library>/roms/  (or another folder given with --roms)   bios/   retroarch-saves/   retroarch-cache/
# It is chosen, in this order, by: --library PATH, the path saved by an earlier run, the only
# folder under /Volumes that was already set up (has roms/ and retroarch-cache/), or a question.
# The choice is saved in $LIBRARY_SETTINGS; --library and --roms change it.
#
# After resolve_library: LIBRARY, ROMS, CACHE exported (RASETUP_LIBRARY, RASETUP_ROMS for Python).

LIBRARY_SETTINGS="$HOME/Library/Application Support/RetroArch/retroarch-mac-setup/settings.cfg"

_lib_fail() { printf '\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# a path typed or dragged into Terminal: quotes, "\ " escapes, trailing spaces, ~
_lib_clean() {
  local p="$1"
  p="${p#"${p%%[![:space:]]*}"}"; p="${p%"${p##*[![:space:]]}"}"
  p="${p#\"}"; p="${p%\"}"; p="${p#\'}"; p="${p%\'}"
  p="${p//\\ / }"
  if [[ "$p" == "~"* ]]; then p="$HOME${p#\~}"; fi
  if [[ "$p" != "/" ]]; then p="${p%/}"; fi
  printf '%s' "$p"
}

_lib_setting() {   # key -> value from the settings file
  [[ -f "$LIBRARY_SETTINGS" ]] || return 0
  sed -n "s/^$1=\"\(.*\)\"\$/\1/p" "$LIBRARY_SETTINGS" | tail -1
}

_lib_save() {
  mkdir -p "$(dirname "$LIBRARY_SETTINGS")"
  {
    echo "# retroarch-mac-setup: game library chosen with --library / --roms (or asked once)"
    echo "library=\"$LIBRARY\""
    if [[ "$ROMS" != "$LIBRARY/roms" ]]; then echo "roms=\"$ROMS\""; fi
  } > "$LIBRARY_SETTINGS"
}

# volume root of a /Volumes/... path, empty for anything else
_lib_volume() {
  if [[ "$1" == /Volumes/* ]]; then printf '/Volumes/%s' "$(cut -d/ -f3 <<<"$1")"; fi
}

_lib_mounted() { mount | grep -qF " on $1 ("; }

# "" when usable, else the reason
_lib_problem() {
  local p="$1" vol
  vol="$(_lib_volume "$p")"
  if [[ -n "$vol" ]] && ! _lib_mounted "$vol"; then
    echo "the disk $vol is not connected (or it was renamed)"; return
  fi
  [[ -d "$p" ]] || { echo "$p does not exist"; return; }
  [[ -w "$p" ]] || { echo "$p is not writable"; return; }
}

_lib_ask() {   # $1 = why we ask
  [[ -t 0 ]] || _lib_fail "$1. Run ./setup.sh --library /path/to/your/library"
  echo "$1." >&2
  echo "The library is the folder that holds roms/ (one folder per system inside). bios/, saves" >&2
  echo "and the cache are created next to it. Type its path, or drag the folder here, and press Enter:" >&2
  local ans
  read -r -p "> " ans || true
  ans="$(_lib_clean "$ans")"
  [[ -n "$ans" ]] || _lib_fail "No library given"
  printf '%s' "$ans"
}

resolve_library() {   # args: the script's own arguments
  local opt_lib="" opt_roms="" changed=0 a why ans
  while [[ $# -gt 0 ]]; do
    a="$1"; shift
    case "$a" in
      --library) opt_lib="${1:-}"; shift || true ;;
      --library=*) opt_lib="${a#*=}" ;;
      --roms) opt_roms="${1:-}"; shift || true ;;
      --roms=*) opt_roms="${a#*=}" ;;
    esac
  done

  LIBRARY="$(_lib_clean "$opt_lib")"
  [[ -n "$LIBRARY" ]] && changed=1
  if [[ -z "$LIBRARY" ]]; then
    LIBRARY="$(_lib_setting library)"
    if [[ -n "$LIBRARY" ]]; then
      why="$(_lib_problem "$LIBRARY")"
      if [[ -n "$why" ]]; then
        [[ -t 0 ]] || _lib_fail "Library $LIBRARY: $why. Connect it, or choose another with --library PATH"
        echo "Library $LIBRARY: $why." >&2
        read -r -p "Connect it and press Enter, or type the new path: " ans || true
        ans="$(_lib_clean "$ans")"
        [[ -n "$ans" ]] && { LIBRARY="$ans"; changed=1; }
      fi
    fi
  fi
  if [[ -z "$LIBRARY" ]]; then      # first run on this Mac: a library set up before?
    local found=() d
    for d in /Volumes/*/; do
      d="${d%/}"
      [[ -d "$d/roms" && -d "$d/retroarch-cache" ]] && found+=("$d")
    done
    if [[ ${#found[@]} -eq 1 ]]; then
      LIBRARY="${found[0]}"; changed=1
      echo "Library found: $LIBRARY (choose another with --library PATH)" >&2
    elif [[ ${#found[@]} -gt 1 ]]; then
      LIBRARY="$(_lib_ask "Several libraries found: ${found[*]}")"; changed=1
    else
      LIBRARY="$(_lib_ask "No game library chosen yet")"; changed=1
    fi
  fi
  # the roms folder itself was given
  if [[ "$(basename "$LIBRARY")" == "roms" && ! -d "$LIBRARY/roms" && -d "$LIBRARY" ]]; then
    LIBRARY="$(dirname "$LIBRARY")"
  fi

  why="$(_lib_problem "$LIBRARY")"
  [[ -z "$why" ]] || _lib_fail "Library: $why"

  if [[ -n "$opt_roms" ]]; then
    ROMS="$(_lib_clean "$opt_roms")"; changed=1
  else
    ROMS="$(_lib_setting roms)"
    [[ $changed -eq 1 || -z "$ROMS" ]] && ROMS="$LIBRARY/roms"
  fi
  why="$(_lib_problem "$ROMS")"
  if [[ -n "$why" ]]; then
    if [[ "$ROMS" == "$LIBRARY/roms" && ! -e "$ROMS" ]]; then
      _lib_fail "$ROMS does not exist. Create it and put each system's games in its own folder, e.g. roms/snes, roms/megadrive, roms/mame (see README, Library layout)"
    fi
    _lib_fail "ROM folder: $why"
  fi

  CACHE="$LIBRARY/retroarch-cache"
  if [[ $changed -eq 1 ]]; then _lib_save; fi
  export LIBRARY ROMS CACHE
  export RASETUP_LIBRARY="$LIBRARY" RASETUP_ROMS="$ROMS"
}
