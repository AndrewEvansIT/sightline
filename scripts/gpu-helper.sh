#!/bin/sh
# com.sightline.gpu-limit: sets how much memory the GPU may use for models (sysctl iogpu.wired_limit_mb)
# to one of this Mac's allowed steps. It does nothing else.
#
#   com.sightline.gpu-limit <MB>     set it now and save it for the next restart (Sightline runs this through sudo -n)
#   com.sightline.gpu-limit --boot   re-apply the saved value (the LaunchDaemon runs this at startup, as root)
#
# This file is a template: install-gpu-helper.sh fills in ALLOWED with the steps computed from this Mac's memory
# (Normal = 0, the macOS default of about 75%; 80%; 85%; 90%; Max = memory minus headroom, never 100%) and installs
# it root:wheel 755 as /Library/PrivilegedHelperTools/com.sightline.gpu-limit. Editing the source changes nothing
# until it is installed again.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 022

ALLOWED="@ALLOWED@"
KEY=iogpu.wired_limit_mb
STATE_DIR="/Library/Application Support/Sightline"
STATE="$STATE_DIR/gpu-limit-mb"

allowed() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
  for v in $ALLOWED; do
    [ "$v" = "$1" ] && return 0
  done
  return 1
}

apply() {
  sysctl -w "$KEY=$1" >/dev/null
  now=$(sysctl -n "$KEY")
  if [ "$now" != "$1" ]; then
    echo "gpu-limit: asked for $1 MB but $KEY reads $now" >&2
    exit 1
  fi
}

if [ "$#" -ne 1 ]; then
  echo "usage: com.sightline.gpu-limit <one of: $ALLOWED>  or  --boot" >&2
  exit 2
fi

if [ "$1" = "--boot" ]; then
  [ -f "$STATE" ] || exit 0  # nothing chosen yet: leave the macOS default alone
  mb=$(head -c 16 "$STATE" | tr -d '[:space:]')
  if ! allowed "$mb"; then
    echo "gpu-limit: ignoring saved value '$mb'" >&2
    exit 2
  fi
  # The GPU driver may register its sysctl after early daemons start: wait up to 2 minutes.
  i=0
  until sysctl -n "$KEY" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
      echo "gpu-limit: $KEY never appeared" >&2
      exit 1
    fi
    sleep 2
  done
  apply "$mb"
  echo "$(date '+%Y-%m-%d %H:%M:%S') boot: $KEY=$mb"
  exit 0
fi

if ! allowed "$1"; then
  echo "gpu-limit: $1 is not an allowed value" >&2
  exit 2
fi
apply "$1"
mkdir -p "$STATE_DIR"
printf '%s\n' "$1" > "$STATE.tmp"
mv -f "$STATE.tmp" "$STATE"
