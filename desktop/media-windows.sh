#!/usr/bin/env bash
# Reproducible inputs; native Windows PE executables with static x264, no MSYS DLLs.
# Run in the MSYS2 UCRT64 shell after build-windows.py --prepare.
set -euo pipefail
cd "$(dirname "$0")/.."
project="$PWD"
cache="$project/desktop/.cache/windows"
work="$cache/media-work"
prefix="$work/prefix"
mkdir -p "$work" "$cache/media/bin"
tar -xf "$cache/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.gz" -C "$work"
tar -xf "$cache/ffmpeg-8.1.tar.xz" -C "$work"
tar -xf "$cache/zlib-1.3.2.tar.gz" -C "$work"
jobs="${NUMBER_OF_PROCESSORS:-2}"
cd "$work/zlib-1.3.2"
make -f win32/Makefile.gcc -j"$jobs" libz.a
mkdir -p "$prefix/lib" "$prefix/include"
cp libz.a "$prefix/lib/"
cp zlib.h zconf.h "$prefix/include/"
cd "$work/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55"
./configure --host=x86_64-w64-mingw32 --prefix="$prefix" --enable-static --disable-cli --disable-opencl --extra-ldflags=-static
make -j"$jobs"
make install
cd "$work/ffmpeg-8.1"
PKG_CONFIG_PATH="$prefix/lib/pkgconfig" ./configure --prefix="$work/staging" --target-os=mingw32 --arch=x86_64 \
  --disable-shared --enable-static --disable-autodetect --enable-gpl --enable-libx264 --enable-zlib \
  --disable-debug --disable-doc --disable-ffplay \
  --pkg-config=pkgconf --extra-cflags="-I$prefix/include" --extra-ldflags="-static -L$prefix/lib"
make -j"$jobs"
cp ffmpeg.exe ffprobe.exe "$cache/media/bin/"
cp COPYING.GPLv2 COPYING.GPLv3 LICENSE.md "$cache/media/"
cp "$work/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55/COPYING" "$cache/media/COPYING.x264"
cp "$work/zlib-1.3.2/LICENSE" "$cache/media/LICENSE.zlib"
# objdump verifies there is no dependency on an installed MinGW/MSYS environment.
for program in "$cache/media/bin/"*.exe; do
  if objdump -p "$program" | grep -Ei 'DLL Name:.*(msys|libgcc|libstdc|libwinpthread|zlib|libz\.)'; then
    echo "Nonportable media binary: $program" >&2
    exit 1
  fi
done
rm -rf "$work"
