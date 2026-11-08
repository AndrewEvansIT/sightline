#!/bin/sh
# Installs Sightline's optional GPU memory helper, so the GPU memory card can change how much memory the GPU may use.
# Sightline runs it through macOS's password prompt ("Install helper" on the GPU memory card); you can also run
#   sudo scripts/install-gpu-helper.sh <your user name>
#
# It computes this Mac's steps from its memory and installs root-owned copies, so later edits to these files change nothing:
#   gpu-helper.sh (steps filled in)  -> /Library/PrivilegedHelperTools/com.sightline.gpu-limit   (root:wheel 755)
#   a sudoers rule for <user>        -> /etc/sudoers.d/sightline-gpu-limit                      (root:wheel 440, checked by visudo first)
#                                       allowing only "com.sightline.gpu-limit <step>" for those steps, without a password
#   com.sightline.gpu-limit.plist    -> /Library/LaunchDaemons/com.sightline.gpu-limit.plist     (root:wheel 644, loaded now)
#                                       re-applies the chosen step at startup
# Today's limit is saved for the next restart if it is one of the steps. Nothing is changed until you pick a step.
#
#   install-gpu-helper.sh --print-steps [total_mb]   print the steps for this Mac (or for total_mb) and exit; no root needed
#   install-gpu-helper.sh --build DIR <user>          write the three files into DIR and check them; installs nothing, no root needed
#   install-gpu-helper.sh --uninstall                remove all three (the limit goes back to normal at the next restart)
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

SRC=$(cd "$(dirname "$0")" && pwd)
HELPER=/Library/PrivilegedHelperTools/com.sightline.gpu-limit
SUDOERS=/private/etc/sudoers.d/sightline-gpu-limit
DAEMON=/Library/LaunchDaemons/com.sightline.gpu-limit.plist
STATE_DIR="/Library/Application Support/Sightline"
MIN_LEFT=4096    # MB: never a step that leaves less than this
MAX_LEFT=40960   # MB: Max leaves this much on big Macs; 8% of memory on smaller ones (at least MIN_LEFT)

# The steps, in MB. Same rule as sightline/widgets/gpu_limit.py step_values(): Normal (0), 80%, 85%, 90%, Max,
# leaving out any step that leaves less than 4 GB or isn't above the one before.
steps() {
  total=$1
  out=0
  floor=$((total * 75 / 100))
  for pct in 80 85 90; do
    mb=$((total * pct / 100))
    if [ $((total - mb)) -ge $MIN_LEFT ] && [ "$mb" -gt "$floor" ]; then
      out="$out $mb"
      floor=$mb
    fi
  done
  head=$((total * 8 / 100))
  [ "$head" -lt $MIN_LEFT ] && head=$MIN_LEFT
  [ "$head" -gt $MAX_LEFT ] && head=$MAX_LEFT
  mx=$((total - head))
  if [ "$mx" -gt "$floor" ] && [ $((total - mx)) -ge $MIN_LEFT ]; then
    out="$out $mx"
  fi
  echo "$out"
}

total_mb() {
  echo $(( $(sysctl -n hw.memsize) / 1048576 ))
}

# build DIR USER ALLOWED: write the helper, the sudoers rule and the LaunchDaemon into DIR, then check all three.
build() {
  dir=$1 user=$2 allowed=$3
  sed "s/@ALLOWED@/$allowed/" "$SRC/gpu-helper.sh" > "$dir/helper"
  {
    echo "# Sightline: $user may set the GPU memory limit without a password, through the root helper only,"
    echo "# and only to this Mac's steps. Installed by Sightline's install-gpu-helper.sh."
    printf 'Cmnd_Alias SIGHTLINE_GPU_LIMIT = '
    first=1
    for v in $allowed; do
      [ $first -eq 1 ] || printf ', '
      printf '%s %s' "$HELPER" "$v"
      first=0
    done
    printf '\n%s ALL = (root) NOPASSWD: SIGHTLINE_GPU_LIMIT\n' "$user"
  } > "$dir/sudoers"
  cp "$SRC/com.sightline.gpu-limit.plist" "$dir/plist"
  visudo -cf "$dir/sudoers" >/dev/null
  plutil -lint "$dir/plist" >/dev/null
  sh -n "$dir/helper"
  if grep -q '@ALLOWED@' "$dir/helper"; then echo "Steps weren't filled in; stopping." >&2; exit 1; fi
}

check_user() {
  case "$1" in
    ''|*[!A-Za-z0-9._-]*|-*) echo "usage: install-gpu-helper.sh <user name>" >&2; exit 2 ;;
  esac
}

if [ "${1:-}" = "--print-steps" ]; then
  steps "${2:-$(total_mb)}"
  exit 0
fi

if [ "${1:-}" = "--build" ]; then
  [ -d "${2:-}" ] || { echo "usage: install-gpu-helper.sh --build DIR <user>" >&2; exit 2; }
  check_user "${3:-}"
  build "$2" "$3" "$(steps "$(total_mb)")"
  echo "Built and checked in $2."
  exit 0
fi

[ "$(id -u)" -eq 0 ] || { echo "Run it as root (Sightline asks for your password; or use sudo)." >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout system/com.sightline.gpu-limit 2>/dev/null || true
  rm -f "$DAEMON" "$SUDOERS" "$HELPER"
  echo "GPU memory helper removed. The limit goes back to the macOS default at the next restart."
  exit 0
fi

USER_NAME=${1:-}
check_user "$USER_NAME"
id "$USER_NAME" >/dev/null 2>&1 || { echo "No user named $USER_NAME." >&2; exit 2; }
[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] || { echo "This needs an Apple Silicon Mac." >&2; exit 1; }
sysctl -n iogpu.wired_limit_mb >/dev/null 2>&1 || { echo "This Mac has no iogpu.wired_limit_mb setting." >&2; exit 1; }

ALLOWED=$(steps "$(total_mb)")
TMP=$(mktemp -d /tmp/sightline-gpu.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# Check everything before touching anything.
grep -Eq '^[#@]includedir /private/etc/sudoers.d' /etc/sudoers || { echo "/etc/sudoers doesn't read /etc/sudoers.d; stopping." >&2; exit 1; }
build "$TMP" "$USER_NAME" "$ALLOWED"

install -d -o root -g wheel -m 755 "$STATE_DIR"
install -d -o root -g wheel -m 755 /Library/PrivilegedHelperTools
install -o root -g wheel -m 755 "$TMP/helper" "$HELPER"
now=$(sysctl -n iogpu.wired_limit_mb)
for v in $ALLOWED; do
  if [ "$v" = "$now" ] && [ ! -f "$STATE_DIR/gpu-limit-mb" ]; then
    printf '%s\n' "$now" > "$STATE_DIR/gpu-limit-mb"
  fi
done
install -o root -g wheel -m 440 "$TMP/sudoers" "$SUDOERS"
install -o root -g wheel -m 644 "$TMP/plist" "$DAEMON"
launchctl bootout system/com.sightline.gpu-limit 2>/dev/null || true
launchctl bootstrap system "$DAEMON"

if sudo -u "$USER_NAME" sudo -n -l "$HELPER" 0 >/dev/null 2>&1; then
  echo "GPU memory helper installed. Steps: $ALLOWED MB. Now: $(sysctl -n iogpu.wired_limit_mb) MB."
else
  echo "Installed, but the sudoers rule didn't take effect for $USER_NAME." >&2
  exit 1
fi
