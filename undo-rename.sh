#!/bin/bash
# Reverts the folder renames/merges done by setup.sh, newest first, using the log in the library.
# ROM files are only moved back to where they were, and MAME zips completed by setup.sh are
# replaced by their backups; nothing else is deleted.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/library.sh
source "$HERE/lib/library.sh"
resolve_library "$@"
LOG="$CACHE/rename-log.tsv"
[[ -s "$LOG" ]] || { echo "Nothing to undo ($LOG is empty or missing)."; exit 0; }

echo "Changes recorded in $LOG:"
awk -F'\t' '$2=="rename"{print "  folder: " $4 "  →  " $3} $2=="merge-file"{m++} $2=="zip-repair"{z++} END{if(m) print "  plus " m " file(s) merged into existing folders (moved back too)"; if(z) print "  plus " z " MAME zip(s) completed with files from other zips (original zips restored)"}' "$LOG"
read -r -p "Revert them? [s/N] " ans
[[ "$ans" =~ ^[sSyY] ]] || { echo "Cancelled."; exit 0; }

errors=0
# newest first
tail -r "$LOG" | while IFS=$'\t' read -r ts action src dst; do
  case "$action" in
    rename|merge-file)
      if [[ ! -e "$dst" ]]; then echo "  skip (missing): $dst"; continue; fi
      if [[ -e "$src" ]]; then echo "  skip (already exists): $src"; errors=$((errors+1)); continue; fi
      mkdir -p "$(dirname "$src")"
      mv "$dst" "$src" && echo "  ✔ $dst → $src"
      ;;
    zip-repair)   # src = backup of the original zip, dst = the completed zip
      if [[ ! -e "$src" ]]; then echo "  skip (backup missing): $src"; continue; fi
      mv -f "$src" "$dst" && echo "  ✔ restored $dst"
      ;;
  esac
done
# remove folders emptied by un-merging (only if empty)
awk -F'\t' '$2=="merge-file"{print $4}' "$LOG" | while read -r f; do rmdir "$(dirname "$f")" 2>/dev/null || true; done
mv "$LOG" "$LOG.undone-$(date +%Y%m%d-%H%M%S)"
echo "Done. Run ./setup.sh again to regenerate playlists (it will propose the renames again)."
