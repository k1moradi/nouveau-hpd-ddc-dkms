# NVE4 VA-API decode and remaining PGRAPH traps

## Scope and checkpoint

This note records the K4200 result after the GK104 legacy-video context fix
and the opt-in legacy FIFO nonstall-event fix. It treats the successful video
decode as the working baseline and investigates the separate PGRAPH/PROP
messages without changing the video path.

Hardware and software in the capture:

- NVIDIA Quadro K4200, GK104GL, PCI ID `10de:11b4`
- Ubuntu kernel `7.0.0-34-generic`
- Mesa VA-API driver `26.0.8-1ubuntu0.3`, Gallium NVE4
- FFmpeg `8.0.1-3ubuntu2`
- VA-API `1.23`
- render node `/dev/dri/renderD128`

The repository starting point for this follow-up was
`b9744ba922a407aa2992efdef4bb54ed2962e303` on
`review/gk104-vaapi-video-decode-20260927`. The diagnostic/build-state change
is separately recorded in commit `0c8d803f70d262cf86b7b57b0c1dd2e6fd441656`.

## Hardware observations

The two video changes were enabled together for the successful hardware run:

- The GK104 legacy-video context mapping fix applies non-privileged mappings
  only to MSPDEC, MSPPP, and MSVLD contexts.
- The experimental legacy FIFO fix uses global nonstall event index 0 when
  the FIFO has no per-runlist nonstall constructor.

The 3000-frame H.264 High 1920x1080 run used VA-API surfaces (`pix_fmt=vaapi`)
and completed with **3002 frames decoded, zero decode errors, 39.01 seconds
elapsed, and FFmpeg exit 0**. It passed the earlier approximately 30-second
failure interval. The associated kernel capture did not contain the prior
MSVLD/MSPDEC/MSPPP `PRIV_VIOLATION`, killed-channel/pushbuf `ENODEV`, Mesa
SIGBUS, or BAR2/HOST_CPU PTE failure chain.

The same kernel capture did contain **86 PGRAPH GPC/PROP trap records**:

- 83 records include `RT_WIDTH_OVERRUN`.
- 49 records include `RT_HEIGHT_OVERRUN`.
- Some records include both bits, so the category counts overlap.
- The first cluster starts at monotonic `2825.762544`; the last record in
  that cluster is at `2825.847864`.
- A second cluster starts at `2864.417077` and ends at `2864.423974`, about
  38.66 seconds after the first cluster began and close to process teardown.
- No continuous per-frame stream appears between those clusters in this
  capture.

These traps did not prevent the 3000-frame decode from completing. They are
separate from the earlier video-engine channel failure and are not evidence
that either video fix failed.

## VA-API initialization control

The earlier empty `~/nouveau-vaapi-init-kernel.log` is **invalid**. Its
journal logger was stopped before FFmpeg started, so it says nothing about
VA-API initialization.

The valid control was rerun on 2026-09-28 and is recorded in
[`VAAPI-INIT-CONTROL-20260928.md`](VAAPI-INIT-CONTROL-20260928.md). The journal
logger was started first, confirmed alive, kept active through this command,
and stopped only after FFmpeg exited:

```bash
ffmpeg -hide_banner -loglevel verbose \
    -init_hw_device vaapi=va:/dev/dri/renderD128 \
    -f lavfi -i 'nullsrc=s=16x16:d=0.1' \
    -frames:v 1 -f null -
```

FFmpeg exited 0 and opened Mesa `26.0.8-1ubuntu0.3` for NVE4. The overlapping
kernel log was zero bytes, and the trap/fault keyword scan found no matches.

The valid control shows that opening the VA-API driver and running this small
CPU `lavfi` frame did not produce these traps in that sample. It does **not**
create an H.264 decoder, VA-API decode surfaces, or the NVE4 video-engine
channels; it cannot establish that decoder/surface setup is trap-free.

## Mesa-instrumented follow-up capture

On 2026-09-28, the standalone `nvc0_clear_render_target()` trace was built in
a private prefix and selected with `LIBVA_DRIVERS_PATH`; no Mesa packages,
DKMS modules, or kernel state were changed. The diagnostic build used the
upstream Mesa 26.0.8 source archive with SHA-256
`caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc`. The
cached Ubuntu `26.0.8-1ubuntu0.3` source patch series was inspected and does
not patch Nouveau Gallium sources. The VA `surface.c` and Nouveau
`nouveau_vp3_video.c` files have identical SHA-256 values in the original and
Ubuntu source trees; `nvc0_surface.c` differs only by this local diagnostic.
The test therefore exercises the same relevant Nouveau Gallium source, but
the private plugin is an upstream-source build and reports
`Mesa Gallium driver 26.0.8 for NVE4`, without Ubuntu's package revision
suffix.

### Rebuild the instrumented driver in a private prefix

The following recipe starts from a fresh extraction and keeps the build,
installed driver, and sysroot under the user's cache. It does not install or
replace system Mesa packages. The sysroot is the prepared, user-owned build
sysroot used for the captured Mesa build; `-j1` keeps peak build memory low.

```bash
set -euo pipefail
REPO=/home/keivan/nouveau-hpd-ddc-dkms
CACHE=/home/keivan/.cache/nouveau-mesa-diag-20260928
ARCHIVE=/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/mesa_26.0.8.orig.tar.xz
WORK="$CACHE/rebuild-rt-clear"
SYSROOT="$CACHE/sysroot"
SRCROOT="$WORK/source"
SRC="$SRCROOT/mesa-26.0.8"
BUILD="$WORK/build"
PREFIX="$WORK/prefix"
DRI="$PREFIX/lib/x86_64-linux-gnu/dri"

if [ -e "$WORK" ]; then
    echo "choose a fresh WORK directory; refusing to reuse $WORK" >&2
    exit 1
fi
printf '%s  %s\n' \
    'caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc' \
    "$ARCHIVE" | sha256sum --check -
mkdir -p "$SRCROOT"
tar -xf "$ARCHIVE" -C "$SRCROOT"
cd "$SRC"
patch --dry-run --batch --fuzz=0 -p1 \
    < "$REPO/patches/mesa/nvc0-rt-clear-diagnostic.patch"
patch --batch --fuzz=0 -p1 \
    < "$REPO/patches/mesa/nvc0-rt-clear-diagnostic.patch"

PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages" \
PKG_CONFIG_PATH="$SYSROOT/usr/lib/x86_64-linux-gnu/pkgconfig:$SYSROOT/usr/share/pkgconfig" \
PKG_CONFIG_SYSROOT_DIR="$SYSROOT" \
meson setup "$BUILD" "$SRC" \
    --prefix="$PREFIX" \
    --buildtype=release \
    --wrap-mode=nodownload \
    -Dplatforms=[] \
    -Dgallium-drivers=nouveau \
    -Dvulkan-drivers=[] \
    -Dgallium-va=enabled \
    -Dvideo-codecs=h264dec \
    -Dllvm=disabled \
    -Dbuild-tests=false \
    -Dopengl=false \
    -Dglx=disabled \
    -Degl=disabled \
    -Dgbm=disabled \
    -Dgles1=disabled \
    -Dgles2=disabled
ninja -C "$BUILD" -j1 src/gallium/targets/va/libgallium_drv_video.so
meson install -C "$BUILD" --no-rebuild
```

Check that the private DRI entry is the just-built Gallium plugin, contains
the diagnostic string, and has no unresolved shared-library dependencies:

```bash
set -euo pipefail
CACHE=/home/keivan/.cache/nouveau-mesa-diag-20260928
WORK="$CACHE/rebuild-rt-clear"
PREFIX="$WORK/prefix"
DRI="$PREFIX/lib/x86_64-linux-gnu/dri"
PLUGIN="$DRI/nouveau_drv_video.so"
test -L "$PLUGIN"
test "$(readlink -f "$PLUGIN")" = "$DRI/libgallium_drv_video.so"
strings "$PLUGIN" | grep -F 'NOUVEAU_DIAG_RT_CLEAR'
ldd "$PLUGIN" | tee "$WORK/plugin-ldd.txt"
if grep -Fq 'not found' "$WORK/plugin-ldd.txt"; then
    echo 'private Mesa plugin has unresolved dependencies' >&2
    exit 1
fi
```

For this build, the private plugin's dependencies resolve against the host
libraries, so `LD_LIBRARY_PATH` is not needed. `LIBVA_DRIVERS_PATH` below is
the selection mechanism that points libva at the private DRI directory.

### Run and verify the selected driver

The logger overlapped both tests. The init-only command loaded the private
driver, exited 0, and produced a zero-byte kernel log. The 3000-frame run
kept the known-good FFmpeg options and named VA-API device unchanged. For a
fresh build from the recipe above, this command selects that build's private
plugin and enables the trace; `/usr/bin/time` and `tee` capture duration,
output, and exit status. This is a repeatable command template; the recorded
2026-09-28 capture used the earlier private prefix shown in its loader proof
below, and the results that follow are from that recorded run:

```bash
CACHE=/home/keivan/.cache/nouveau-mesa-diag-20260928
WORK="$CACHE/rebuild-rt-clear"
PREFIX="$WORK/prefix"
DRI="$PREFIX/lib/x86_64-linux-gnu/dri"
PLUGIN="$DRI/nouveau_drv_video.so"
RUN_LOG="$WORK/ffmpeg-3000.log"

set -o pipefail
env LIBVA_DRIVERS_PATH="$DRI" NOUVEAU_DIAG_VA_SURFACE=1 \
    /usr/bin/time -f 'wall_seconds=%e' \
    ffmpeg -hide_banner -loglevel verbose \
    -init_hw_device vaapi=va:/dev/dri/renderD128 \
    -hwaccel vaapi \
    -hwaccel_device va \
    -hwaccel_output_format vaapi \
    -i /home/keivan/test_1080p.mkv \
    -an -frames:v 3000 -f null - 2>&1 | tee "$RUN_LOG"
ffmpeg_status=${PIPESTATUS[0]}
printf 'FFmpeg exit=%s\n' "$ffmpeg_status"

grep -F "Trying to open $PLUGIN" "$RUN_LOG"
grep -F 'va_openDriver() returns 0' "$RUN_LOG"
grep -F 'VAAPI driver: Mesa Gallium driver 26.0.8 for NVE4.' "$RUN_LOG"
test "$ffmpeg_status" -eq 0
```

The first grep is the loader-path check: the verbose libva log must name the
private `$PLUGIN` path, and `va_openDriver() returns 0` must confirm the open
succeeded. If those checks fail, do not interpret the diagnostic environment
variable or absence of trace records; FFmpeg may have used another driver or
failed to load the private one. Keep the kernel logger running concurrently
with FFmpeg, as in the recorded capture procedure.

For the recorded 2026-09-28 capture, the verbose FFmpeg log contains the
following loader evidence for the then-current private prefix:

```text
libva: Trying to open /home/keivan/.cache/nouveau-mesa-diag-20260928/prefix/lib/x86_64-linux-gnu/dri/nouveau_drv_video.so
libva: va_openDriver() returns 0
VAAPI driver: Mesa Gallium driver 26.0.8 for NVE4.
```

FFmpeg selected the private plugin, used `pixfmt:vaapi` surfaces, output 3000
frames, decoded 3003 frames with zero decode errors, reported 38.71 seconds
elapsed, and exited 0. The capture still had clustered startup PROP traps:
43 records included `RT_WIDTH_OVERRUN` and 7 included
`RT_HEIGHT_OVERRUN` (the counts overlap). Those records ran from monotonic
`19422.024041` through `19422.179315`; one further PROP trap was logged at
`19460.725159`, near the 3000-frame completion. No continuous per-frame trap
stream appeared.

The opt-in Mesa trace emitted **zero** `NOUVEAU_DIAG_RT_CLEAR` records even
though FFmpeg loaded the private driver and was launched with the environment
flag. A later build audit found that this private Mesa build used
`buildtype=release`, which defines `MESA_DEBUG=0`; Mesa's
[`debug_printf()`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.0.8/src/util/u_debug.h)
then compiles to a no-op. Therefore the old zero-record result is **not
evidence** that allocation or clear was absent, and it does not demote the
surface-clear candidate. The expanded trace described below uses opt-in
`fprintf(stderr, ...)` records so it remains active in a release build.

The same kernel capture had two later FIFO `CTXSW_TIMEOUT` scheduler errors,
video channels 4 and 5 killed, and a BAR2/HOST_CPU PTE fault. The precise
monotonic event sequence and the missing exact FFmpeg process-exit timestamp
are documented in
[`VAAPI-DELAYED-CHANNEL-TIMEOUTS.md`](VAAPI-DELAYED-CHANNEL-TIMEOUTS.md).
These appeared after the successful FFmpeg output and are separate from the earlier
MSVLD `PRIV_VIOLATION`/Mesa SIGBUS failure chain; they do mean that this
follow-up must not be summarized as free of all channel or BAR2 faults. This
capture still had no MSVLD `PRIV_VIOLATION` and FFmpeg itself returned 0. The
old transcript records FFmpeg exit status but not its exact timestamp, so the
delays from process exit cannot be stated exactly from this capture.

The exact local records are outside Git at:

- `/home/keivan/nouveau-vaapi-init-mesa-diag-20260928-{kernel.log,ffmpeg.log,transcript.txt}`
- `/home/keivan/nouveau-pgraph-3000-mesa-diag-20260928-{kernel.log,ffmpeg.log,transcript.txt}`

The kernel-log SHA-256 values are respectively
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` and
`1df7bbd0a6e652402ab0b08ca2ce5259bd0884ac03d01fc1d1457b2d98d89a77`.
The corresponding FFmpeg logs have SHA-256 values
`df33a26c34a6aab135419be27a873ff1cc1f6e24de42542b5b6fac212ea2ff4d` and
`ee534f0b00b2f503bf71682449e9c6d3e8f22c6fbe5f55498a23847ed8be2780`; the
transcripts have SHA-256 values
`f460925df7d923ae73aea229e1387154e5e246e45e36cdf1c7fc72c30e0f3012` and
`46f72bc892c365d356a4edaf7e409c851906f18de990b1d96a078e5548ffe5fd`.
The init-only control details are also recorded in
[`VAAPI-INIT-CONTROL-20260928.md`](VAAPI-INIT-CONTROL-20260928.md).

## Source audit: why a video process also uses GR/PGRAPH

The exact Ubuntu Mesa source package was `mesa 26.0.8-1ubuntu0.3`. Its
`mesa_26.0.8.orig.tar.xz` SHA-256 was
`caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc`; the
Ubuntu delta did not patch the Nouveau Gallium video files inspected here.
The matching kernel source was Ubuntu `linux-source-7.0.0`.

Source references: [Ubuntu Mesa source package](https://packages.ubuntu.com/source/resolute-updates/mesa), [Mesa 26.0.8 Nouveau 3D clear path](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.0.8/src/gallium/drivers/nouveau/nvc0/nvc0_surface.c), [Mesa 26.0.8 VA surface allocation](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.0.8/src/gallium/frontends/va/surface.c), and [Linux v7.0 Nouveau GR trap decoder](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/gr/gf100.c).

The source shows two engine paths in the same Gallium VA process:

1. `nvc0_create()` calls `nouveau_context_init()` and initializes the NVC0
   graphics pipe context; it also installs `nvc0_create_decoder()` and
   `nvc0_video_buffer_create()` as the video callbacks. The screen selects an
   NVE4 3D class on Kepler and creates the 3D object on its channel. Thus a
   VA-API process can submit GR/PGRAPH work even though its bitstream decode
   is performed by video engines.
2. On Kepler, `nvc0_create_decoder()` creates separate BSP, VP, and PPP FIFO
   channels/objects. `nvc0_decoder_begin_frame()`, bitstream submission, and
   `nvc0_decoder_end_frame()` submit work to those decoder engines. These
   are not the GR context reported in the PROP trap.

Relevant exact-source locations:

- Mesa `src/gallium/drivers/nouveau/nouveau_screen.c:390-405` creates the
  screen channel; `:502-518` creates a context pushbuf on that channel.
- Mesa `src/gallium/drivers/nouveau/nvc0/nvc0_screen.c:938-965` selects and
  instantiates the 3D object, including NVE4 on Kepler.
- Mesa `src/gallium/drivers/nouveau/nvc0/nvc0_context.c:404-474` initializes
  the NVC0 pipe context and assigns the video callbacks.
- Mesa `src/gallium/drivers/nouveau/nvc0/nvc0_video.c:28-88` sends frame
  work to BSP/VP/PPP; `:91-110` starts decoder creation and selects the
  Kepler path.

## Static Mesa candidate: VA surface clears

The most concrete source-level candidate for the startup burst is the default
VA surface initialization clear:

1. VA surfaces are lazily allocated when `vlVaGetSurfaceBuffer()` first needs
   one (`src/gallium/frontends/va/surface.c:995-1003`). Decode setup and
   `vlVaBeginPicture()` reach this accessor.
2. `vlVaHandleSurfaceAllocate()` creates the video buffer and, unless the
   screen advertises `PIPE_VIDEO_CAP_SKIP_CLEAR_SURFACE`, calls
   `pipe->clear_render_target()` once for each video plane
   (`surface.c:952-990`). NVC0's VP3 video-cap callback returns 0 for this
   unknown capability, so the clear is not skipped.
3. On GK104, Nouveau reports no progressive video support. VA therefore marks
   the default NV12 decode template interlaced. Nouveau's specialized VP3
   buffer path allocates two 2D-array resources, one `R8_UNORM` luma plane and
   one `R8G8_UNORM` chroma plane, each with two layers and
   `PIPE_BIND_RENDER_TARGET` (`nouveau_vp3_video.c:91-145,167-175`); this is
   the actual NV12 path for the tested device. The generic progressive video
   buffer path also requests render-target binding, but is not the path
   selected by this GK104 capability report.
4. Mesa's `nvc0_clear_render_target()` emits 3D state for render-target
   address, width, height, format, layout/layer state, scissor, and
   `CLEAR_BUFFERS` (`nvc0/nvc0_surface.c:289-365`). That is a direct candidate
   for a GR/PROP render-target overrun during initial video-surface clears.

This source path makes surface initialization a plausible static candidate
for the startup burst. The earlier private-Mesa capture emitted no
`nvc0_clear_render_target()` records, but that build compiled its
`debug_printf()` hook away. That absence is inconclusive and does not correlate
or rule out the clear path. The kernel log also does not identify the GR
method, active render-target dimensions, or userspace call site.

The teardown burst remains less explained. Video-buffer destruction releases
resource references, and decoder destruction releases video-engine objects
and buffers; the inspected destructors do not issue a corresponding explicit
3D clear. The burst may be delayed reporting from earlier queued GR work or a
different teardown operation. The current capture cannot decide that.

## Timestamped VA and decoder-teardown trace

The standalone diagnostic patch now timestamps every
`NOUVEAU_DIAG_VA_SURFACE` and `NOUVEAU_DIAG_RT_CLEAR` record with Mesa's
`os_time_get_nano()` monotonic clock. It remains opt-in through
`NOUVEAU_DIAG_VA_SURFACE=1`, writes only to stderr, and is not part of the
DKMS patch series. No GPU command, fence result, or destruction order is
changed.

The trace covers the VA allocation decision and callback path described
above, then follows the process teardown path:

- `va-context-destroy` marks entry, the decoder destroy call, and exit from
  `vlVaDestroyContext()` in `src/gallium/frontends/va/context.c`.
- `decoder-destroy`, `object-destroy`, and `channel-destroy` mark the shared
  VP3 decoder destructor in `src/gallium/drivers/nouveau/nouveau_vp3_video.c`.
  Its array indices are logged as BSP, VP, and PPP, matching the channels
  created in that order by `nvc0_create_decoder()` in `nvc0/nvc0_video.c`.
  Logs bracket each engine object deletion, pushbuffer destruction, and FIFO
  channel object deletion.
- `video-buffer-destroy` brackets release of the video buffer's resource and
  sampler-view references.
- `sync-wait` records a Gallium pipe-fence or decoder-fence wait only when it
  fails or takes at least 100 ms. The record includes its monotonic completion
  time and duration, avoiding a line for every fast per-frame synchronization.

The VP3 decoder destructor itself contains no explicit fence wait or idle
operation. `nouveau_pushbuf_destroy()` in `nouveau_screen.c` frees the
pushbuffer's userspace bookkeeping and references; these new records show
whether time is spent inside the existing pushbuffer/object destruction calls
without attributing an implicit GPU wait to them. The trace does not identify
the kernel channel ID; that still requires kernel-side correlation.

### Rebuild the existing private instrumented prefix

The source and build below are the already prepared user-owned private Mesa
tree. This updates only that private VA driver and prefix. The source archive
hash is rechecked, and the full diagnostic patch has been tested with strict
zero-fuzz application against the matching source files from that exact
archive. `-j1` keeps peak build memory low.

```bash
set -euo pipefail
ARCHIVE=/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/mesa_26.0.8.orig.tar.xz
WORK=/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented
BUILD="$WORK/build"
PREFIX="$WORK/prefix"
DRI="$PREFIX/lib/x86_64-linux-gnu/dri"
PLUGIN="$DRI/nouveau_drv_video.so"

printf '%s  %s\n' \
    'caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc' \
    "$ARCHIVE" | sha256sum --check -
ninja -C "$BUILD" -j1 src/gallium/targets/va/libgallium_drv_video.so
meson install -C "$BUILD" --no-rebuild

test -L "$PLUGIN"
test "$(readlink -f "$PLUGIN")" = "$DRI/libgallium_drv_video.so"
for marker in \
    'NOUVEAU_DIAG_VA_SURFACE mono_ns=' \
    'NOUVEAU_DIAG_RT_CLEAR mono_ns=' \
    'hook=active' \
    'phase=allocate-entry' \
    'phase=sync-wait' \
    'phase=va-context-destroy' \
    'phase=decoder-destroy' \
    'phase=video-buffer-destroy' \
    'phase=object-destroy' \
    'phase=channel-destroy'; do
    strings "$PLUGIN" | grep -F "$marker" >/dev/null || {
        echo "missing private Mesa diagnostic marker: $marker" >&2
        exit 1
    }
done
ldd "$PLUGIN" | tee "$WORK/plugin-ldd.txt"
if grep -Fq 'not found' "$WORK/plugin-ldd.txt"; then
    echo 'private Mesa plugin has unresolved dependencies' >&2
    exit 1
fi
```

The next hardware run should be made only after a fresh reboot into the
existing kernel/DKMS baseline. Do not install this prefix system-wide. The
capture helper verifies the selected plugin and requires both the timestamped
hook marker and timestamped allocation-entry marker before it considers an
instrumented run valid:

```bash
cd /home/keivan/nouveau-hpd-ddc-dkms
tools/vaapi-capture.sh private-instrumented \
  /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented/prefix/lib/x86_64-linux-gnu/dri
```

No runtime result is claimed for these new timestamped teardown records until
that capture is actually performed.

## Trap fields and format limits

Linux v7.0 `drivers/gpu/drm/nouveau/nvkm/engine/gr/gf100.c:1224-1253` names
ROP/PROP error bits `0x10` as `RT_WIDTH_OVERRUN` and `0x20` as
`RT_HEIGHT_OVERRUN`. Its handler reads the trap status, X/Y, format, and
storage-type fields and logs them; it does not capture the preceding GR
method or active RT extent. The names establish the hardware-reported error
class, but the source does not define the precise dimension comparison or
which programmed state caused it.

The log's format values are `0x37` and `0x2e`. In Mesa's generated GK104
definitions, those numbers are also `GK104_IMAGE_FORMAT_R8_UNORM` and
`GK104_IMAGE_FORMAT_RG8_UNORM`, which are suggestive of the Y and UV planes
of NV12. That is only a numeric match: the 3D render-target table uses the
separate `G80_SURFACE_FORMAT` namespace, and the kernel's six-bit PROP trap
field has not been proven to use the GK104 image-format enum. Therefore the
trap formats are **not decoded here** as R8/RG8 render-target formats.

The trap coordinates are likewise insufficient to recover the active RT
width/height because those dimensions are absent from the kernel record.
There is no justified functional change to make from these fields alone.

## Upstream history search

The Mesa source package and current Nouveau source paths were reviewed,
including the recent GitLab file history for
`nvc0_surface.c` and `nouveau_vp3_video.c`. Searches for
`RT_WIDTH_OVERRUN`, `RT_HEIGHT_OVERRUN`, VP5/PGRAPH, and Nouveau video-surface
clear fixes did not locate an upstream fix that matches this trap pattern.
This is a bounded search, not proof that no related report or patch exists.

## Diagnosis and next step

**Observed:** hardware H.264 decode completed 3000 output frames with both
video fixes and the instrumented private Mesa driver; FFmpeg reported zero
decode errors and exit 0. Startup and completion-adjacent PROP overrun traps
remain. The follow-up capture also logged later FIFO context-switch timeouts,
video-channel kills, and a BAR2 PTE fault, but no MSVLD `PRIV_VIOLATION` or
FFmpeg failure. The overlapping init-only control was clean.

**Source-proven:** the VA frontend's default surface-allocation path requests
render-target-capable NV12 surfaces and calls the pipe clear callback unless
the screen advertises `PIPE_VIDEO_CAP_SKIP_CLEAR_SURFACE`. Nouveau returns
false for that capability. This run did not log the callback. The PROP
records contain only the error class and partial trap state.

**Hypothesis:** the precise GR operation behind the PROP traps remains
unknown. VA surface clears are a plausible source-level path, but the
instrumented run produced no corresponding clear records. The later video
channel timeouts and BAR2 PTE fault also need separate correlation.

At this earlier checkpoint, the PGRAPH root cause was still a hypothesis and
no functional fix had been tested.

The small opt-in trace in
[`patches/mesa/nvc0-rt-clear-diagnostic.patch`](../patches/mesa/nvc0-rt-clear-diagnostic.patch)
prints the NVC0 context, pipe format, RT format value, RT dimensions, clear
rectangle, and layer range immediately before `nvc0_clear_render_target()`
emits the state. It is standalone Mesa instrumentation: it is not included in
`install.sh`, DKMS, or the kernel package. The initial capture yielded no
records; the later candidate capture below positively exercised the hook.

The expanded opt-in surface-allocation and callback trace, plus a reproducible
three-mode capture helper, is documented in
[`VAAPI-DELAYED-CHANNEL-TIMEOUTS.md`](VAAPI-DELAYED-CHANNEL-TIMEOUTS.md). At
that point the runtime trace had not yet been positively exercised. A
functional change was deferred until the expected surface dimensions could be
verified.

The later timestamped private-instrumented capture did positively exercise the
clear callback and exposed a generic `pipe_surface` / Nouveau-private
`nv50_surface` layout mismatch. The exact source/runtime correlation, full
monotonic event timeline, and a standalone Mesa candidate are documented in
[`VA-SURFACE-CLEAR-CANDIDATE.md`](VA-SURFACE-CLEAR-CANDIDATE.md). The PGRAPH
clear defect is source-proven. The later private Mesa candidate A/B strongly
supports that it caused the earlier PROP overrun pattern.

The legacy-video fix details remain in
[`VP5-VIDEO-DECODE.md`](VP5-VIDEO-DECODE.md); the nonstall build experiment is
in [`LEGACY-FIFO-NONSTALL-EXPERIMENT.md`](LEGACY-FIFO-NONSTALL-EXPERIMENT.md).

## Fresh system-Mesa capture — 2026-09-29

A fresh-boot condition-A run selected the distro plugin at
`/usr/lib/x86_64-linux-gnu/dri/nouveau_drv_video.so` and verified
`va_openDriver() returns 0`. With kernel `7.0.0-34-generic` and the existing
`nouveau-hpd-ddc/0.1.13` module, FFmpeg completed the same 3000-frame H.264
decode: 3000 output frames, 3003 decoded, zero decode errors, exit 0.

This system-Mesa run also recorded startup and completion-adjacent GR/PROP
traps, then `CTXSW_TIMEOUT`, recovery/killing of channel 5 on runlist 2, a
channel-4 idle failure, and a BAR2/HOST_CPU PTE fault. All were timestamped
before FFmpeg process exit; the harness therefore set `STOP_A_B=1`. This
confirms the delayed failure is not exclusive to the private diagnostic Mesa
plugin, while the different number of timeouts and BAR2 address mean the two
captures are similar rather than identical. It does not prove that PROP traps
caused the scheduler timeout or that channel recovery caused the BAR2 fault.

Conditions B and C were not run in that boot. A later fresh-boot run tested
the private-instrumented native-surface candidate; its results are recorded
in [`VA-SURFACE-CLEAR-CANDIDATE.md`](VA-SURFACE-CLEAR-CANDIDATE.md). The full
hash-verified system-Mesa timeline, source boundary, and runlist/channel
mapping limits are in
[`VAAPI-DELAYED-CHANNEL-TIMEOUTS.md`](VAAPI-DELAYED-CHANNEL-TIMEOUTS.md).
