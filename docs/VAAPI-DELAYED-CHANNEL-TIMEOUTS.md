# VA-API surface trace and delayed channel timeouts

## Scope and safety state

This follow-up is diagnostic. It does not change the working GK104 video
context or legacy FIFO fixes, and it does not create a PGRAPH workaround. Mesa
builds are private and selected with `LIBVA_DRIVERS_PATH`; they are not part of
DKMS or `install.sh`.

No DKMS installation, module reload, system Mesa replacement, or reboot was
performed for this work. No hardware A/B decode was run while preparing the
instrumentation. The current kernel/module and GPU state therefore remain the
baseline from the prior run until the user chooses to reboot and test.

## Mesa trace: make the clear path positively testable

The prior private Mesa run loaded its custom VA driver successfully, decoded
3000 frames, and emitted no `NOUVEAU_DIAG_RT_CLEAR` record. A later source and
build audit found that it used `buildtype=release`, where Mesa defines
`MESA_DEBUG=0` and `debug_printf()` compiles to a no-op. The old empty trace
therefore says nothing about whether the VA allocation or clear path ran.

The expanded standalone patch
[`nvc0-rt-clear-diagnostic.patch`](../patches/mesa/nvc0-rt-clear-diagnostic.patch)
adds opt-in records at both the VA allocation decision and NVC0 callback:

- `NOUVEAU_DIAG_VA_SURFACE hook=active` reports NVC0 pipe initialization and
  the installed `clear_render_target` callback address.
- `phase=allocate-entry` reports the VA surface template dimensions, pipe
  format, interlace flag, bind flags, modifiers, and callback pointer.
- `phase=video-buffer` reports the created video buffer's actual format,
  dimensions, and interlace state.
- `phase=skip-clear` reports the runtime value returned by
  `PIPE_VIDEO_CAP_SKIP_CLEAR_SURFACE`; `phase=clear-decision action=skip`
  records the skip branch.
- `phase=planes` and `phase=plane` report present video planes, pipe-surface
  and resource formats, target, dimensions, depth/array size, bind flags,
  mip level, and layers.
- `phase=clear-before` and `phase=clear-after` bracket every VA frontend
  `pipe->clear_render_target()` call and print the callback address.
- `NOUVEAU_DIAG_RT_CLEAR` reports the NVC0 context, pipe format, NVC0 RT
  format field, RT dimensions, clear origin/size, and layer range immediately
  before NVC0 emits the clear state.

Set only `NOUVEAU_DIAG_VA_SURFACE=1` to enable the complete trace. The patch
does not issue an artificial clear or otherwise alter GPU state. The capability
query is still made once and its value controls the same early return as before.

Interpret a future instrumented log using this decision table:

| Evidence | Runtime conclusion |
| --- | --- |
| NVC0 `hook=active`, but no `phase=allocate-entry` | The exercised VA run did not reach this surface allocation function. |
| Allocation entry followed by `skip-clear ... value=1` and `action=skip` | The clear was intentionally skipped by the runtime capability result. |
| Allocation entry, `value=0`, but `planes count=0` or every reported plane has no texture | No clear callback was attempted because the video buffer exposed no clearable plane resource. |
| `clear-before` callback differs from NVC0 hook callback | The frontend invoked a different callback; the NVC0 clear hook is not the selected implementation for that call. |
| `clear-before` callback matches NVC0 hook callback and `NOUVEAU_DIAG_RT_CLEAR` follows | The VA allocation path entered `nvc0_clear_render_target()`. |
| `clear-before` matches but the NVC0 entry trace is absent | The run is internally inconsistent or the callbacks were from different pipe contexts; retain the full log and treat the trace as inconclusive. |

The relevant Mesa 26.0.8 source is
[`vlVaHandleSurfaceAllocate()`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.0.8/src/gallium/frontends/va/surface.c)
and
[`nvc0_clear_render_target()`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.0.8/src/gallium/drivers/nouveau/nvc0/nvc0_surface.c).
The VA frontend creates a video buffer, asks the pipe screen whether surface
clears may be skipped, obtains the video planes, and otherwise invokes the
pipe clear callback for non-null plane textures. VA surface creation can defer
allocation until later use, so allocation-entry records may occur during
decoder setup rather than at `vaCreateSurfaces()` itself.

## Private Mesa builds and capture harness

The 2026-09-28 work used the exact upstream Mesa 26.0.8 archive with SHA-256
`caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc` and the
prepared user-owned sysroot under
`/home/keivan/.cache/nouveau-mesa-diag-20260928/sysroot`. The uninstrumented
driver was compiled from a fresh source tree first at `-j1` (934/934 steps,
peak RSS 704276 KiB); it was staged under the new private follow-up cache. The
instrumented source is a separate fresh extraction with the patch applied at
zero fuzz. Its 935-step VA plugin build completed at `-j1` (peak RSS 704260
KiB). A source-level check then caught that `debug_printf()` would be disabled
in the release build, so the opt-in trace calls were changed to direct
`fprintf(stderr, ...)` and the two modified source files were recompiled and
relinked incrementally (6 Ninja steps, 5.17 seconds, peak RSS 101532 KiB). The
final private plugin contains every expected trace marker; both private
plugins resolve all dependencies. Neither prefix is under `/usr`.

The final patch passed strict apply and reverse checks against pristine source
with `patch --fuzz=0`; the applied source postimage matched the prepared
instrumented source. The archive checksum was rechecked before the diagnostic
build. The private uninstrumented plugin has none of the new trace strings;
the instrumented plugin includes allocation, capability, plane, clear,
hook-active, and RT-clear formats.

The capture helper is
[`tools/vaapi-capture.sh`](../tools/vaapi-capture.sh). It supports:

```bash
tools/vaapi-capture.sh system
tools/vaapi-capture.sh private-uninstrumented \
  /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-uninstrumented/prefix/lib/x86_64-linux-gnu/dri
tools/vaapi-capture.sh private-instrumented \
  /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented/prefix/lib/x86_64-linux-gnu/dri
```

Every mode runs the same named-device 3000-frame H.264 command on
`/home/keivan/test_1080p.mkv`. The helper records kernel release, module path,
loaded and on-disk Nouveau `srcversion`, input checksum, selected
`LIBVA_DRIVERS_PATH`, FFmpeg command/PID, wall time, FFmpeg and `tee`
`PIPESTATUS`, realtime and monotonic timestamps, and hashes of kernel,
FFmpeg, and transcript logs. It starts `journalctl -kf -n 0 -o short-monotonic`
before FFmpeg, refuses to proceed if the follower reports an error, verifies
the expected private or system plugin path and `va_openDriver() returns 0`,
then leaves the journal follower active for 15 seconds after FFmpeg exits.
Each capture is written to a unique user-owned directory under
`$NOUVEAU_CAPTURE_ROOT` or `~/nouveau-vaapi-captures`.

Before a later A/B sequence, reboot manually into the existing
`7.0.0-34-generic` kernel and installed `nouveau-hpd-ddc/0.1.13` build solely
to restore clean GPU/channel state. Do not reinstall DKMS just to populate the
new persistent build-state marker. At the start of that fresh session check:

```bash
uname -r
modinfo -n nouveau
cat /sys/module/nouveau/srcversion
modinfo -F srcversion nouveau
```

The helper also checks that loaded and on-disk `srcversion` match before each
run. If a run reports `STOP_A_B=1` or exits 5 due to a timeout, channel kill,
BAR2/PTE fault, PRIV_VIOLATION, SIGBUS, or GPU reset, do not run another
condition until after another clean reboot.

The planned first matrix is one run per condition:

1. system Mesa;
2. private uninstrumented Mesa 26.0.8;
3. private instrumented Mesa 26.0.8.

No A/B run has yet been performed with this new harness. The earlier basic
VA-init control is valid for its narrow scope, but it does not allocate H.264
surfaces or exercise video engines. Do not interpret that control as a clean
decode-surface result.

## Existing delayed-failure capture: exact and missing timing

The existing capture is
`/home/keivan/nouveau-pgraph-3000-mesa-diag-20260928-kernel.log`, with the
paired FFmpeg log and transcript adjacent to it. The kernel logger used
`journalctl -kf -n 0 -o short-monotonic`. FFmpeg exited 0 after outputting
3000 frames, decoding 3003 frames, and reporting zero decode errors; the last
progress line reports `elapsed=0:00:38.71`.

The kernel capture provides precise monotonic times for the events below. The
old transcript records FFmpeg exit status but not the process-exit timestamp,
and its wall-clock start has only whole-second precision. Thus the delay from
actual FFmpeg exit cannot be computed exactly from this run. The last PROP
record is completion-adjacent: it is 4.332184 seconds before the first timeout
and 8.635197 seconds before the second. Those are measured event-to-event intervals,
not exact exit-to-event delays.

| Monotonic time | Observed event | Relation |
| ---: | --- | --- |
| `19460.725159` | Last GR `TRAP ch 2`, FFmpeg PID 28135 | Completion-adjacent; final output reports 38.71 s elapsed. |
| `19460.725861`–`19460.726888` | Four GPC PROP `RT_WIDTH_OVERRUN` records, format `0x37` | Last PROP burst ends at `.726888`. |
| `19465.059072` | First FIFO `SCHED_ERROR 0a [CTXSW_TIMEOUT]` | 4.332184 s after the last PROP record. |
| `19465.059872` | Runlist 1, channel 4: `rc scheduled`, client label `av:h264:df0[28138]` | 0.800 ms after timeout. |
| `19465.063543` | Channel 4 errored and disabled | 4.471 ms after timeout. |
| `19465.064324` | FFmpeg PID 28135 reports `channel 4 killed!` | 5.252 ms after timeout. |
| `19469.362085` | Second FIFO `CTXSW_TIMEOUT` | 4.303013 s after first timeout; 8.635197 s after last PROP. |
| `19469.363074` | Runlist 2, channel 5: `rc scheduled`, same client label/PID | 0.989 ms after timeout. |
| `19469.363974` | Channel 5 errored and disabled | 1.889 ms after timeout. |
| `19469.366163` | FFmpeg PID 28135 reports `channel 5 killed!` | 4.078 ms after timeout. |
| `19469.383435` | BAR2/HOST_CPU READ PTE fault at `0x49d000`, channel `-1` / unknown | 17.272 ms after channel 5 kill; 8.656547 s after last PROP. |

**Observed:** the scheduler errors and channel kills are distinct from the
successful FFmpeg exit. The BAR2 fault follows the second kill in the log by
17.272 ms and is reported on channel `-1` with an unknown instance.

**Source-proven:** Linux v7.0
[`gk104.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/gk104.c)
maps scheduler code `0x0a` to `CTXSW_TIMEOUT`. The handler acknowledges the
scheduler error, looks for engines busy in a context switch, and schedules
engine/runlist recovery. GK104 recovery can mark the active channel dead,
disable it, queue runlist recovery, and reinitialize a stuck engine. The
`rc scheduled` message is emitted by
[`runl.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/runl.c)
when recovery is queued; channel-kill messages come from the GK104 FIFO
recovery path and it notifies the channel client. The userspace-facing
`ffmpeg[28135]: channel N killed!` line is printed by
[`nouveau_chan.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nouveau_chan.c)
when that kill event arrives; it also marks the channel killed and fails its
fence context. The GR PROP logger is in
[`gf100.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/gr/gf100.c)
and reports trap bits/coordinates/format, not the preceding GR method or
complete render-target state.

**Not established:** Mesa's Kepler decoder creates separate BSP, VP, and PPP
channels, but the captured FIFO records do not map channel IDs 4 and 5 to
those engines. They show runlists 1 and 2 and the same VA decoder client
label; they do not include per-channel engine names. Do not assign either ID
to BSP/MSVLD, VP/MSPDEC, or PPP/MSPPP from this log alone.

**Inferred, not proven:** the BAR2 PTE fault is temporally downstream of FIFO
channel recovery and may be a secondary teardown/recovery effect. Because it
is on channel `-1` / unknown and no memory-object ownership is logged, the
capture does not prove that the timeouts caused the fault or that the fault
caused the timeouts.

The legacy nonstall fix adjusts the event index used for nonstall/fence
notification on FIFOs without a per-runlist constructor. The observed
`CTXSW_TIMEOUT` path is a separate FIFO scheduler interrupt/recovery path.
The event fix could affect outstanding waits or when teardown occurs, but
this capture does not show it causing or fixing the context-switch timeout.

The upstream Linux v4.11 Nouveau recovery series already included GK104 FIFO
changes to ACK `SCHED_ERROR` before recovery, trigger MMU fault handling, and
improve engine/channel recovery. The v7.0 source still contains that recovery
path. The bounded review did not identify a newer fix specifically matching
this delayed GK104 VA decoder timeout/teardown pattern; this is not proof that
no such fix exists. The v4.11 series summary is recorded in the
[Linux DRM pull archive](https://lkml.rescloud.iu.edu/1702.2/05124.html), and
the current handler is in the linked v7.0 `gk104.c` source above.

## Current conclusion

- **Observed:** the video-engine PRIV_VIOLATION/SIGBUS chain did not recur in
  the successful 3000-frame run; the decode crossed the old 30-second
  failure interval. The later CTXSW_TIMEOUT/channel-kill/BAR2 sequence did
  occur.
- **Source-proven:** the process has both a 3D GR context and separate video
  engine channels. The PROP records identify the error bits but not the GR
  command or RT dimensions that caused them.
- **Still HYPOTHESIS:** the source of the PROP traps, delayed scheduler
  timeouts, and subsequent unknown-channel BAR2 fault is unresolved. No
  functional GR fix was created.

The next evidence step is to run the three capture modes with the new surface
trace and 15-second post-exit journal tail, stopping after any critical
failure signature. The current run is the known-good decode baseline, not a
reason to alter the working kernel fixes.
