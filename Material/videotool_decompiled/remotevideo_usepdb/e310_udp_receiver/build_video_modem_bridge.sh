#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SYSROOT=/mnt/d/hp-laptop/E-band/target-sysroot
TOOLCHAIN=/opt/gcc-linaro-7.5.0-2019.12-x86_64_arm-linux-gnueabihf
CC="$TOOLCHAIN/bin/arm-linux-gnueabihf-gcc"

if [ ! -x "$CC" ]; then
    echo "Compiler not found: $CC" >&2
    exit 1
fi

if [ ! -f "$SYSROOT/usr/include/iio.h" ]; then
    echo "E310 sysroot header not found: $SYSROOT/usr/include/iio.h" >&2
    exit 1
fi

"$CC" "$SCRIPT_DIR/video_modem_bridge.c" \
    -O2 \
    -o "$SCRIPT_DIR/video_modem_bridge_4096" \
    -I"$SYSROOT/usr/include" \
    -L"$SYSROOT/usr/lib" \
    -Wl,-rpath-link,"$SYSROOT/usr/lib" \
    -Wl,--allow-shlib-undefined \
    -liio -lpthread -lm

echo
file "$SCRIPT_DIR/video_modem_bridge_4096"
echo
strings "$SCRIPT_DIR/video_modem_bridge_4096" |
    grep 'GLIBC_' |
    sort -V |
    tail
