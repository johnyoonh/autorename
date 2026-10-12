#!/bin/bash

# Finder Quick Action and droplet entrypoint for AutoRename.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${AUTORENAME_PYTHON:-$SCRIPT_DIR/.venv/bin/python}"
PYTHON_SCRIPT="${AUTORENAME_SCRIPT:-$SCRIPT_DIR/autorename-pdf.py}"
CONFIG_FILE="${AUTORENAME_CONFIG:-$SCRIPT_DIR/config.yaml}"
LOG_FILE="${AUTORENAME_FINDER_LOG:-$HOME/Library/Logs/AutoRename-Finder.log}"

mkdir -p "$(dirname "$LOG_FILE")"

notify() {
    if [ "${AUTORENAME_NOTIFY:-1}" = "0" ]; then
        return
    fi
    /usr/bin/osascript - "$1" "$2" <<'APPLESCRIPT' >/dev/null 2>&1
on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
APPLESCRIPT
}

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

if [ ! -x "$PYTHON_BIN" ]; then
    log "ERROR Python executable not found: $PYTHON_BIN"
    notify "AutoRename Error" "Python environment not found. Run the project setup first."
    exit 1
fi

if [ ! -f "$PYTHON_SCRIPT" ]; then
    log "ERROR AutoRename script not found: $PYTHON_SCRIPT"
    notify "AutoRename Error" "AutoRename script not found."
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
    log "ERROR Config not found: $CONFIG_FILE"
    notify "AutoRename Error" "config.yaml was not found."
    exit 1
fi

preview_args=()
if [ "${1:-}" = "--preview" ]; then
    preview_args=(--dry-run)
    shift
fi

supported=()
unsupported=0
for filepath in "$@"; do
    if [ ! -f "$filepath" ]; then
        log "SKIP Missing file: $filepath"
        unsupported=$((unsupported + 1))
        continue
    fi

    extension="${filepath##*.}"
    extension="$(printf '%s' "$extension" | tr '[:upper:]' '[:lower:]')"
    case "$extension" in
        pdf|png|jpg|jpeg|tif|tiff|bmp|gif|webp|heic|heif)
            supported+=("$filepath")
            ;;
        *)
            log "SKIP Unsupported extension: $filepath"
            unsupported=$((unsupported + 1))
            ;;
    esac
done

if [ "${#supported[@]}" -eq 0 ]; then
    notify "AutoRename" "No supported PDF or image files were selected."
    exit 2
fi

log "START Files: ${#supported[@]}"
notify "AutoRename" "Processing ${#supported[@]} selected file(s)..."

output_file="$(mktemp -t autorename-finder.XXXXXX)"
trap 'rm -f "$output_file"' EXIT

"$PYTHON_BIN" "$PYTHON_SCRIPT" rename \
    --config "$CONFIG_FILE" --output json --quiet ${preview_args[@]+"${preview_args[@]}"} -- "${supported[@]}" \
    > "$output_file" 2>> "$LOG_FILE"
status=$?
cat "$output_file" >> "$LOG_FILE"
printf "\n" >> "$LOG_FILE"

summary="$("$PYTHON_BIN" - "$output_file" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    print("AutoRename did not return a valid result.")
    raise SystemExit

renamed = data.get("renamed", 0)
skipped = data.get("skipped", 0)
failed = data.get("failed", 0)
parts = []
if renamed:
    parts.append(f"{renamed} proposed" if data.get("dry_run") else f"{renamed} renamed")
if skipped:
    parts.append(f"{skipped} unchanged")
if failed:
    parts.append(f"{failed} failed")
print(", ".join(parts) if parts else "No files changed.")
PY
)"

log "END Exit: $status; $summary; unsupported: $unsupported"
if [ "$unsupported" -gt 0 ]; then
    summary="$summary; $unsupported unsupported"
fi
notify "AutoRename" "$summary"
exit "$status"
