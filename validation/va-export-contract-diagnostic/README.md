# VA-API PRIME export contract probe for H.264 on the Nouveau VP3 engine

This is a standalone diagnostic client built with unmodified FFmpeg and libva
interfaces. It is **compiled but not run**. It does not modify mpv, FFmpeg,
ffplay, Mesa, the kernel module, or the installed system.

## What it measures

The pinned input is H.264 High, decoded through FFmpeg's VAAPI hardware
decoder. The probe:

1. obtains FFmpeg's VA display;
2. verifies `VAProfileH264High` and `VAEntrypointVLD`;
3. creates the FFmpeg-equivalent config with the exact arguments used by the
   pinned decoder (`vaCreateConfig(..., NULL, 0, ...)`) and records the surface
   memory-type mask, pixel formats, and usage hints;
4. decodes one actual VAAPI surface;
5. calls `vaSyncSurface()` on that surface;
6. calls `vaExportSurfaceHandle()` with the same DRM PRIME 2,
   read-only/separate-layers flags used by FFmpeg's mapping path; and
7. records the status and, on success, descriptor dimensions, object count,
   modifier, layer formats, plane indices, offsets, and pitches.

The output can establish whether this FFmpeg-equivalent configuration advertises DRM PRIME 2
and what happens when the actual decoded surface is synchronized and exported.
The current source query is configuration-level; the advertised memory-type
mask alone does **not** prove every driver-private surface layout must be
exportable. A failing runtime export still needs to be interpreted against the
applicable VA contract and the exact queried attributes.

## Build-only artifact

The current source is pinned by SHA-256
19c397eabc9719e66952ef7980129eaefad5f10cc7434a1bebdfed52acaea054.
It requires an explicit --execute argument and verifies the pinned input
SHA-256 before creating a VA device. The expected input hash is
d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2.

It was compiled with GCC 15.2.0-16ubuntu1 at
/usr/bin/x86_64-linux-gnu-gcc-15, compiler SHA-256
b5f1b773a7c733738352000c92a077dc5852a1a2fc6d836b1e411be1e9ec5f88,
and locally available FFmpeg 8.0.1 runtime sonames. No development packages
were installed. The exact successful build command was:

```sh
cc -std=c11 -Wall -Wextra -Werror \
  -I/usr/include/x86_64-linux-gnu \
  -I/home/keivan/.cache/nouveau-mesa-diag-20260928/sysroot/usr/include \
  -I/home/keivan/.cache/vaapi-app-source-review/ffmpeg-n8.0.1 \
  validation/va-export-contract-diagnostic/va_vp3_export_probe.c \
  -L/usr/lib/x86_64-linux-gnu \
  -Wl,-rpath,/usr/lib/x86_64-linux-gnu \
  -l:libavformat.so.62 -l:libavcodec.so.62 -l:libavutil.so.60 \
  -l:libva.so.2 \
  -o /home/keivan/nouveau-vaapi-app-validation/va-vp3-export-probe-20261004-explicit
~~~

The retained binary SHA-256 is
f109aa7ed076a1c4082e15573dee877edfc43b463403202c77e60cf672ecfba7;
its ELF build ID is 5e461d5b18f72a1bf9afa8857ba8693d1bdb881e. Direct dynamic
dependency paths and hashes, compiler identity, exact command, FFmpeg source
contract hashes, and the superseded older no-gate executable are recorded in
[`build-manifest.json`](build-manifest.json). The old binary
`/home/keivan/nouveau-vaapi-app-validation/va-vp3-export-probe-20261004`
(SHA-256 `69f5ccc437b76f492261f7ec72a29ba7109a7d596088b083a185e817eb04a8bf`)
is superseded and must not be run. Use only the `-explicit` binary named below.
The FFmpeg and VA headers used for the build are locally retained and
hash-pinned in the source audit. The CPU regression hashes and checks the
pinned FFmpeg decoder config creation and DRM PRIME export call and flags.

The compile command is separate from execution. A later user-controlled
diagnostic run must use the actual reviewed Nouveau deployment from a logged-in
desktop and preserve the complete output. The exact invocation must include:

~~~sh
/home/keivan/nouveau-vaapi-app-validation/va-vp3-export-probe-20261004-explicit --execute /home/keivan/test_1080p.mkv /dev/dri/renderD128
~~~

Without --execute, the probe exits before opening the input or creating a VA
device. It has not been run.

## CPU-only regression

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/va-export-contract-diagnostic/tests/test_va_vp3_export_probe.py
```

The tests check the source contract and do not execute the probe.
