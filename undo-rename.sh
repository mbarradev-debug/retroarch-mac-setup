#!/bin/bash
# Reverts the folder renames/merges done by setup.sh, newest first, using the log on the disk.
# ROM files are only moved back to where they were; nothing is deleted.
set -euo pipefail
LOG="/Volumes/BACKUP/retroarch-cache/rename-log.tsv"

mount | grep -q " on /Volumes/BACKUP " || { echo "✖ /Volumes/BACKUP is not mounted." >&2; exit 1; }
[[ -s "$LOG" ]] || { echo "Nothing to undo ($LOG is empty or missing)."; exit 0; }

echo "Changes recorded in $LOG:"
awk -F'\t' '$2=="rename"{print "  folder: " $4 "  →  " $3} $2=="merge-file"{m++} END{if(m) print "  plus " m " file(s) merged into existing folders (moved back too)"}' "$LOG"
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
  esac
done
# remove folders emptied by un-merging (only if empty)
awk -F'\t' '$2=="merge-file"{print $4}' "$LOG" | while read -r f; do rmdir "$(dirname "$f")" 2>/dev/null || true; done
mv "$LOG" "$LOG.undone-$(date +%Y%m%d-%H%M%S)"
echo "Done. Run ./setup.sh again to regenerate playlists (it will propose the renames again)."
