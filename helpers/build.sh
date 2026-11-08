#!/bin/zsh
# Builds the small native helpers Sightline's widgets call. Read-only tools; none needs admin rights.
set -e
cd "$(dirname "$0")"
mkdir -p build
for src in *.swift; do
  name="${src%.swift}"
  extra=()
  [[ "$name" == "power" ]] && extra=(-lIOReport)
  # Universal (Apple Silicon + Intel), runs on macOS 13 and later. The power helper is Apple Silicon only.
  if [[ "$name" == "power" ]]; then
    swiftc -O -target arm64-apple-macos13.0 "$src" -o "build/$name" -framework IOKit -framework Foundation "${extra[@]}"
  else
    swiftc -O -target arm64-apple-macos13.0 "$src" -o "build/$name-arm64" -framework IOKit -framework Foundation "${extra[@]}"
    swiftc -O -target x86_64-apple-macos13.0 "$src" -o "build/$name-x86_64" -framework IOKit -framework Foundation "${extra[@]}"
    lipo -create "build/$name-arm64" "build/$name-x86_64" -output "build/$name"
    rm "build/$name-arm64" "build/$name-x86_64"
  fi
  echo "built helpers/build/$name"
done
