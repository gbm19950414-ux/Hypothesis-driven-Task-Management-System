#!/bin/bash
set -euo pipefail

# Backup Samsung SSD -> multiple destinations (MyPassport, 旅游) using rsync.
#
# Usage:
#   ./backup_samsung_to_mypassport.sh clone [--dry-run] [--dst MyPassport|旅游|all]
#   ./backup_samsung_to_mypassport.sh mirror [--dry-run] I_UNDERSTAND_DELETE_IN_DST [--dst MyPassport|旅游|all]
#
# Modes:
#   clone  : first safe copy (NO deletion on destination)
#   mirror : keep destination identical to source INSIDE each backup folder (will delete extra files under DST)
#
# Notes:
#   - Default is backing up to BOTH destination disks (all).
#   - Mirror mode is dangerous; prefer clone for the secondary/offline disk.

SRC="/Volumes/Samsung_SSD_990_PRO_2TB_Media"

# ---- Destinations (support multiple backup disks) ----
DST_DISK_MYPASSPORT="/Volumes/MyPassport"
DST_DISK_TRAVEL="/Volumes/旅游"

# Destination selector: MyPassport | 旅游 | all (default)
MODE="${1:-clone}"                 # clone | mirror
DRYRUN_FLAG="${2:-}"               # --dry-run (optional)
DST_SELECTOR="${3:-all}"           # --dst handled below; default all

# Parse optional --dst argument in any position after MODE
for arg in "$@"; do
  case "$arg" in
    --dst)
      # handled by looking ahead in the loop below
      ;;
  esac
done

# Simple parser for --dst <value>
DST_SELECTOR="all"
for ((i=1; i<=$#; i++)); do
  if [ "${!i}" = "--dst" ]; then
    j=$((i+1))
    if [ $j -le $# ]; then
      DST_SELECTOR="${!j}"
    fi
  fi
done

# Build destination list
DST_DISKS=()
case "$DST_SELECTOR" in
  all) DST_DISKS+=("$DST_DISK_MYPASSPORT" "$DST_DISK_TRAVEL") ;;
  MyPassport) DST_DISKS+=("$DST_DISK_MYPASSPORT") ;;
  旅游) DST_DISKS+=("$DST_DISK_TRAVEL") ;;
  *)
    echo "ERROR: Unknown --dst value '$DST_SELECTOR'. Use: MyPassport | 旅游 | all" >&2
    exit 1
    ;;
esac

# Backup folder layout on each destination disk
DST_SUBPATH="backup/Samsung_SSD_990_PRO_2TB_Media/"
LOG_SUBPATH="backup/_logs"

# ---- Hard safety rails (to prevent reversed direction / deleting source) ----
# These must match your real volume mount names.
EXPECTED_SRC_MOUNT="/Volumes/Samsung_SSD_990_PRO_2TB_Media"
EXPECTED_DST_DISK_MOUNTS=("/Volumes/MyPassport" "/Volumes/旅游")

# Resolve symlinks/.. to stable absolute paths
realpath_py() {
  /usr/bin/python3 - "$1" <<'PY'
import os,sys
print(os.path.realpath(sys.argv[1]))
PY
}

SRC_REAL="$(realpath_py "$SRC")"

# Require exact expected mount points (prevents accidentally pointing to the wrong source)
if [ "$SRC_REAL" != "${EXPECTED_SRC_MOUNT}/" ] && [ "$SRC_REAL" != "${EXPECTED_SRC_MOUNT}" ]; then
  echo "ERROR: SRC mount mismatch. Expected: ${EXPECTED_SRC_MOUNT}/  Got: $SRC_REAL" >&2
  exit 1
fi

# Safety token is required for mirror mode; allow it anywhere in args
SAFETY_TOKEN=""
for arg in "$@"; do
  if [ "$arg" = "I_UNDERSTAND_DELETE_IN_DST" ]; then
    SAFETY_TOKEN="$arg"
  fi
done

if [ "$MODE" = "mirror" ] && [ "$SAFETY_TOKEN" != "I_UNDERSTAND_DELETE_IN_DST" ]; then
  echo "ERROR: Mirror mode requires explicit token to enable deletions under DST." >&2
  echo "Run as: $0 mirror [--dry-run] I_UNDERSTAND_DELETE_IN_DST [--dst MyPassport|旅游|all]" >&2
  exit 1
fi

# ---- Safety: ensure source is mounted ----
if [ ! -d "$SRC" ]; then
  echo "ERROR: Source not mounted: $SRC" >&2
  exit 1
fi

# Resolve source device once
SRC_DEV="$(/bin/df -P "$SRC" | /usr/bin/tail -n 1 | /usr/bin/awk '{print $1}')"

# Validate each destination disk before copying
for DST_DISK in "${DST_DISKS[@]}"; do
  # Ensure destination disk is mounted
  if [ ! -d "$DST_DISK" ]; then
    echo "ERROR: Destination disk not mounted: $DST_DISK" >&2
    exit 1
  fi

  DST_DISK_REAL="$(realpath_py "$DST_DISK")"

  # Require destination to be one of the expected mounts
  ok_mount=0
  for m in "${EXPECTED_DST_DISK_MOUNTS[@]}"; do
    if [ "$DST_DISK_REAL" = "$m" ]; then
      ok_mount=1
    fi
  done
  if [ $ok_mount -ne 1 ]; then
    echo "ERROR: Destination disk mount mismatch. Got: $DST_DISK_REAL" >&2
    echo "Expected one of: ${EXPECTED_DST_DISK_MOUNTS[*]}" >&2
    exit 1
  fi

  # Ensure source is not on the destination disk mount
  case "$SRC_REAL" in
    "$DST_DISK_REAL"*)
      echo "ERROR: Source appears to be on destination disk mount. Refusing. SRC=$SRC_REAL DST_DISK=$DST_DISK_REAL" >&2
      exit 1
      ;;
  esac

  # Ensure different physical devices
  DST_DEV="$(/bin/df -P "$DST_DISK" | /usr/bin/tail -n 1 | /usr/bin/awk '{print $1}')"
  if [ "$SRC_DEV" = "$DST_DEV" ]; then
    echo "ERROR: SRC and destination disk appear to be on the same device ($SRC_DEV). Refusing." >&2
    exit 1
  fi

done

# Create destination folders and log dirs
for DST_DISK in "${DST_DISKS[@]}"; do
  DST="${DST_DISK}/${DST_SUBPATH}"
  LOG_DIR="${DST_DISK}/${LOG_SUBPATH}"
  mkdir -p "$DST" "$LOG_DIR"

done

# ---- rsync options ----
# Note: MyPassport is exFAT. exFAT does NOT preserve macOS permissions/xattrs/resource forks.
# We therefore avoid archive mode (-a) to reduce noisy metadata issues, while still copying content reliably.
RSYNC_COMMON=(
  -rltDv
  --human-readable
  --stats
  --progress
  --modify-window=2
  --exclude='.Trashes'
  --exclude='.Spotlight-V100'
  --exclude='.fseventsd'
  --exclude='.TemporaryItems'
  --exclude='.DocumentRevisions-V100'
  --exclude='.DS_Store'
)

# Optional: on macOS, this can reduce "file vanished" warnings if apps are actively writing.
# RSYNC_COMMON+=(--delete-delay)

# Dry-run
if [ "$DRYRUN_FLAG" = "--dry-run" ]; then
  RSYNC_COMMON=(-n "${RSYNC_COMMON[@]}")
fi

for DST_DISK in "${DST_DISKS[@]}"; do
  DST="${DST_DISK}/${DST_SUBPATH}"
  LOG_DIR="${DST_DISK}/${LOG_SUBPATH}"
  LOG_FILE="${LOG_DIR}/rsync_$(date +%Y-%m-%d_%H%M%S).log"

  {
    echo "=== Backup start: $(date) ==="
    echo "MODE=$MODE  DRYRUN=$DRYRUN_FLAG  DST_SELECTOR=$DST_SELECTOR"
    echo "SRC=$SRC"
    echo "DST_DISK=$DST_DISK"
    echo "DST=$DST"
  } | tee -a "$LOG_FILE"

  if [ "$MODE" = "clone" ]; then
    echo "[Clone] First-safe copy: NO delete on destination." | tee -a "$LOG_FILE"
    rsync "${RSYNC_COMMON[@]}" "$SRC" "$DST" | tee -a "$LOG_FILE"

  elif [ "$MODE" = "mirror" ]; then
    echo "[Mirror] Keep destination identical to source INSIDE backup folder (will delete extra files under DST)." | tee -a "$LOG_FILE"
    rsync "${RSYNC_COMMON[@]}" --delete-delay "$SRC" "$DST" | tee -a "$LOG_FILE"

  else
    echo "ERROR: Unknown mode '$MODE'. Use: clone | mirror" >&2
    exit 1
  fi

  echo "=== Backup done: $(date) ===" | tee -a "$LOG_FILE"

done