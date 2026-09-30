# Mesa VA-API functional candidate

This records the diagnostic-free Mesa 26.0.8 candidate for the GK104 Quadro
K4200 investigation. It is a private build candidate. It has not been
installed into `/usr` and is not a packaged Mesa replacement.

## Functional changes

The native-surface patch makes the VA frontend create a driver-owned
`pipe_surface` from each generic video-buffer plane before calling
`clear_render_target()`. The native surface retains the driver-private
metadata and is released through `pipe_surface_reference()` after the clear.
NVC0 and NV50 register their existing native constructors and matching
destructors; surfaces created by those constructors retain their owning pipe
context. Drivers without a `create_surface` callback keep the generic-surface
fallback.

The VP3 teardown patch changes only the distinct-channel path. For each BSP,
VP, and PPP engine it deletes the engine object, push buffer, then channel
before proceeding to the next engine. The shared-channel path retains the
original all-engine-objects-first ordering.

These two changes are kept separate from the Mesa allocation, clear, and
channel-identity diagnostics. Behavior-only source checks are
[`test-mesa-native-surface-functional.py`](../tests/test-mesa-native-surface-functional.py)
and
[`test-mesa-vp3-serialized-teardown-functional.py`](../tests/test-mesa-vp3-serialized-teardown-functional.py).

## Reproduction inputs

The input is the original Ubuntu Mesa 26.0.8 source archive:

```text
archive: mesa_26.0.8.orig.tar.xz
SHA-256: caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc
```

Apply the patches in this order to a pristine extraction with strict matching:

```bash
REPO="$HOME/nouveau-hpd-ddc-dkms"
ARCHIVE="$HOME/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/mesa_26.0.8.orig.tar.xz"
MESA_SOURCE="$HOME/.cache/mesa-26.0.8-functional"
sha256sum "$ARCHIVE"
mkdir -p "$MESA_SOURCE"
tar -xf "$ARCHIVE" -C "$MESA_SOURCE" --strip-components=1
cd "$MESA_SOURCE"
patch --dry-run --fuzz=0 --batch --forward -p1 \
  < "$REPO/patches/mesa/nvc0-create-surface-for-va-clear-functional.patch"
patch --fuzz=0 --batch --forward -p1 \
  < "$REPO/patches/mesa/nvc0-create-surface-for-va-clear-functional.patch"
patch --dry-run --fuzz=0 --batch --forward -p1 \
  < "$REPO/patches/mesa/nouveau-vp3-serialize-channel-teardown-functional.patch"
patch --fuzz=0 --batch --forward -p1 \
  < "$REPO/patches/mesa/nouveau-vp3-serialize-channel-teardown-functional.patch"
python3 "$REPO/tests/test-mesa-native-surface-functional.py" "$MESA_SOURCE"
python3 "$REPO/tests/test-mesa-vp3-serialized-teardown-functional.py" "$MESA_SOURCE"
```

The patch hashes are:

| Patch | SHA-256 |
| --- | --- |
| `nvc0-create-surface-for-va-clear-functional.patch` | `c26a6d5c5b964a8651293db9d467efdeb769239979682f2e4f3de8d5ada04d90` |
| `nouveau-vp3-serialize-channel-teardown-functional.patch` | `5c3c6320ebfe967794ce13a60dc782752c565e164d93fdd687c75fdcf726ae77` |

The patches use zero context to avoid nested-diff whitespace ambiguity. Both
applied with `--fuzz=0` to a fresh archive replay. The complete
source trees byte-matched outside Meson/Ninja-generated Python `__pycache__`
directories, including all six modified source files.

## Private build artifact

The clean source, build, and prefix are under the user-owned cache directory:

```text
source:  ~/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/source/mesa-26.0.8
build:   ~/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/build-clean
prefix:  ~/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/prefix-clean
```

Configuration enables only Gallium Nouveau, VA-API, and H.264 decode; OpenGL,
Vulkan, LLVM, and tests are disabled. The private dependency sysroot is
`~/.cache/nouveau-mesa-diag-20260928/sysroot` (174 files, 3.2 MiB). Its file
manifest SHA-256 is
`da722ac07a46239b0ceca319c30ba13f5f86319d83430b40ac4c66167c09ec27`. Mako
1.3.10 was loaded from that private sysroot; no Python or Mesa package was
installed system-wide.

The setup, build, and private-prefix install commands were:

```bash
ROOT="$HOME/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930"
SRC="$ROOT/source/mesa-26.0.8"
BUILD="$ROOT/build-clean"
PREFIX="$ROOT/prefix-clean"
SYSROOT="$HOME/.cache/nouveau-mesa-diag-20260928/sysroot"
env PKG_CONFIG_SYSROOT_DIR="$SYSROOT" \
    PKG_CONFIG_PATH="$SYSROOT/usr/lib/x86_64-linux-gnu/pkgconfig:$SYSROOT/usr/share/pkgconfig" \
    PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages" \
    meson setup "$BUILD" "$SRC" --prefix="$PREFIX" --wrap-mode=nodownload \
      -Dplatforms=[] -Dgallium-drivers=nouveau -Dvulkan-drivers=[] \
      -Dgallium-va=enabled -Dvideo-codecs=h264dec -Dllvm=disabled \
      -Dbuild-tests=false -Dopengl=false -Dglx=disabled -Degl=disabled \
      -Dgbm=disabled -Dgles1=disabled -Dgles2=disabled
env PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages" \
    ninja -C "$BUILD" -j1
ninja -C "$BUILD" install
```

The source, build, and prefix paths are the three paths listed above. The
environment is intentionally private: `SYSROOT` resolves to the cache path
listed above, and `PREFIX` is the user-owned `prefix-clean` directory.

The successful clean build completed 1008/1008 Ninja steps with exit 0 using
one job. Compiler/linker versions were GCC 15.2.0 and GNU ld 2.46; Meson was
1.10.1 and Ninja 1.13.2. The build, setup, and private-prefix install logs
have SHA-256 values:

| Evidence | SHA-256 |
| --- | --- |
| Meson setup log | `1ed46f307778978b2aa7c54a8af63a609e3d72ef8b183c5244934461ef778908` |
| Ninja build log | `65c1ab6b8c25c1979d20eff450ccc4b9f3b3cb04dfc582bd2e6971b699313b7d` |
| Private install log | `103bb7ae0bb52bad6e69423c318b929699014f8a236550c7222b9f2b2a8f9aa6` |

The resulting private plugin is:

```text
~/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/prefix-clean/lib/x86_64-linux-gnu/dri/libgallium_drv_video.so
SHA-256: 7115d52de2cc245076f18a38a28e4f844ca1affffbe946b6d8ba127afb87d753
```

`nouveau_drv_video.so` in that directory resolves to the plugin above. `ldd`
reported no missing dependencies. The binary contains none of the unique
`NOUVEAU_DIAG_*`, `phase=channel-identity`, `phase=channel-destroy`, or
`phase=object-destroy` trace strings. Generic Mesa utility symbols such as
`os_time_get_nano` are not used as diagnostic-marker checks.

## Hardware validation status

Before the diagnostic-free build, the same two functional changes were
repeatedly exercised with the private Mesa diagnostic build on the K4200,
including complete 40,561-frame H.264 decodes with clean fences and teardown.
The historical BAR2/PTE fault did not reproduce in the runs with `diag3`
mapping traces; its original cause remains unknown.

The diagnostic-free plugin above has now been hardware-tested after a fresh
boot into the preserved production Nouveau `0.1.13` module. Capture directory:

`/home/keivan/nouveau-vaapi-captures/20260930T134903Z-private-uninstrumented-4544`

The run used input SHA-256
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`, loaded
and on-disk Nouveau srcversion `57AE1B168D50DB546CD87A1`, and the private
plugin SHA-256
`7115d52de2cc245076f18a38a28e4f844ca1affffbe946b6d8ba127afb87d753`.
libva opened the pinned private `nouveau_drv_video.so`; SDDM/display-manager
state was inactive at both capture boundaries. The result was:

- 40,561 output frames and 40,561 decoded frames, with zero decode errors.
- FFmpeg exited 0 after 537.10 seconds.
- There was no PROP/RT overrun, `CTXSW_TIMEOUT`, channel kill, failed idle,
  `PRIV_VIOLATION`, SIGBUS, or GPU reset in the captured boot/runtime evidence.
- One Nouveau BAR2/HOST_CPU READ PTE fault remained. The harness therefore
  returned 5 and set `STOP_A_B=1`, as designed.

This proves full-file decode completion with the functional-only Mesa build
and production Nouveau `.13`; it does not establish that every Nouveau fault
is fixed. The BAR2 fault is not evidence of a decoded-frame error, but it is a
real kernel event and its source remains unresolved. No further GPU work was
run in that boot after `STOP_A_B=1`.

The guarded rollback helper is
[`rollback-bar2-diagnostic-v3-to-production.sh`](../tools/rollback-bar2-diagnostic-v3-to-production.sh).
Its `--check-only` mode validates both pinned artifacts without changing the
system. The normal mode selects the existing production `.13` DKMS artifact,
regenerates the dracut initramfs, verifies the exact module bytes in the image,
and leaves the currently loaded `diag3` untouched until a manual reboot. The
post-reboot production verifier
[`verify-production-nouveau-baseline.sh`](../tools/verify-production-nouveau-baseline.sh)
passed before the capture above was started and confirmed the production
module and absence of diagnostic parameters.
