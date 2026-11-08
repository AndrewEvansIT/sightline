#!/bin/zsh
# Builds the small native helpers Sightline's widgets call. Read-only tools; none needs admin rights.
set -e
cd "$(dirname "$0")"
mkdir -p build
for src in *.swift; do
  name="${src%.swift}"
  extra=()
  [[ "$name" == "power" ]] && extra=(-lIOReport)
  swiftc -O "$src" -o "build/$name" -framework IOKit -framework Foundation "${extra[@]}"
  echo "built helpers/build/$name"
done
