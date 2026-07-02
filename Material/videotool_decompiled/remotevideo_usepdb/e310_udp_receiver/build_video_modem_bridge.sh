#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SYSROOT=/mnt/d/hp-laptop/E-band/target-sysroot
TOOLCHAIN=/opt/gcc-linaro-7.5.0-2019.12-x86_64_arm-linux-gnueabihf
CC="$TOOLCHAIN/bin/arm-linux-gnueabihf-gcc"
# Allow overriding COMMIT_ID via environment variable, or auto-resolve Windows Git Worktrees under WSL
COMMIT_ID=${COMMIT_ID:-}
if [ -z "$COMMIT_ID" ] || [ "$COMMIT_ID" = "unknown" ]; then
    GIT_FILE="$SCRIPT_DIR/../../../../.git"
    if [ -f "$GIT_FILE" ]; then
        GITDIR_LINE=$(cat "$GIT_FILE" 2>/dev/null || echo "")
        if echo "$GITDIR_LINE" | grep -q "^gitdir:"; then
            WIN_PATH=$(echo "$GITDIR_LINE" | cut -d' ' -f2- | tr -d '\r')
            if echo "$WIN_PATH" | grep -q -E "^[A-Za-z]:/"; then
                DRIVE=$(echo "$WIN_PATH" | cut -c1 | tr '[:upper:]' '[:lower:]')
                REST_PATH=$(echo "$WIN_PATH" | cut -c4-)
                REAL_GIT_DIR="/mnt/$DRIVE/$REST_PATH"
                if [ -d "$REAL_GIT_DIR" ]; then
                    export GIT_DIR="$REAL_GIT_DIR"
                    export GIT_WORK_TREE="$SCRIPT_DIR/../../../../"
                fi
            fi
        fi
    fi
    COMMIT_ID=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
fi

if [ "$COMMIT_ID" != "unknown" ]; then
    if ! git diff --quiet -- "$SCRIPT_DIR/video_modem_bridge.c" 2>/dev/null; then
        COMMIT_ID="${COMMIT_ID}-dirty"
    fi
fi
COMMIT_ID_CLEAN=${COMMIT_ID%-dirty}
OUTPUT="$SCRIPT_DIR/video_modem_bridge_4096_${COMMIT_ID_CLEAN}"

if [ ! -x "$CC" ]; then
    echo "Compiler not found: $CC" >&2
    exit 1
fi

if [ ! -f "$SYSROOT/usr/include/iio.h" ]; then
    echo "E310 sysroot header not found: $SYSROOT/usr/include/iio.h" >&2
    exit 1
fi

"$CC" "$SCRIPT_DIR/video_modem_bridge.c" \
    -DCOMMIT_ID="\"$COMMIT_ID\"" \
    -O2 \
    -o "$OUTPUT" \
    -I"$SYSROOT/usr/include" \
    -L"$SYSROOT/usr/lib" \
    -Wl,-rpath-link,"$SYSROOT/usr/lib" \
    -Wl,--allow-shlib-undefined \
    -liio -lpthread -lm

cp "$OUTPUT" "$SCRIPT_DIR/video_modem_bridge"

echo
echo "Built: $OUTPUT"
echo "Commit: $COMMIT_ID"
echo "Copied to: $SCRIPT_DIR/video_modem_bridge"
file "$OUTPUT"
echo
strings "$OUTPUT" |
    grep 'GLIBC_' |
    sort -V |
    tail
