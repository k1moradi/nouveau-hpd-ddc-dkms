# VA-API surface trace and delayed channel timeouts

## Scope and safety state

This follow-up is diagnostic. It does not change the working GK104 video
context or legacy FIFO fixes, and it does not create a PGRAPH workaround. Mesa
builds are private and selected with `LIBVA_DRIVERS_PATH`; they are not part of
DKMS or `install.sh`.

No DKMS installation, module reload, or system Mesa replacement was performed
for this follow-up. The user rebooted into the existing kernel/module before
condition A; this document records that system-Mesa capture below. It triggered
`STOP_A_B=1`, so do not use the current boot for another Mesa condition. No
additional GPU workload was run while preparing this analysis.

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

The first matrix condition, system Mesa, ran after a fresh reboot on
2026-09-29 and is recorded below. It reproduced critical kernel signatures,
so `STOP_A_B=1`; conditions B and C were not run in that boot. The earlier
basic VA-init control remains valid only for driver initialization: it did not
allocate H.264 surfaces or exercise video engines, and is not a clean
decode-surface control.

## Previous private-Mesa capture: exact and missing timing

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

**Observed:** FFmpeg returned 0, and the scheduler errors/channel kills follow
the final progress record in the logs. The old transcript does not timestamp
the FFmpeg process exit, so their ordering relative to process exit is unknown.
The BAR2 fault follows the second kill in the kernel log by 17.272 ms and is
reported on channel `-1` with an unknown instance.

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
these captures do not show it causing or fixing the context-switch timeout.

The upstream Linux v4.11 Nouveau recovery series already included GK104 FIFO
changes to ACK `SCHED_ERROR` before recovery, trigger MMU fault handling, and
improve engine/channel recovery. The v7.0 source still contains that recovery
path. The bounded review did not identify a newer fix specifically matching
this delayed GK104 VA decoder timeout/teardown pattern; this is not proof that
no such fix exists. The v4.11 series summary is recorded in the
[Linux DRM pull archive](https://lkml.rescloud.iu.edu/1702.2/05124.html), and
the current handler is in the linked v7.0 `gk104.c` source above.

## Fresh system-Mesa condition A — 2026-09-29

### Capture integrity and result

The user ran condition A after a clean reboot. The capture directory is
`/home/keivan/nouveau-vaapi-captures/20260929T103651Z-system-4106`. Its
`SHA256SUMS` verification passed for `kernel.log`, `ffmpeg.log`, and
`transcript.txt`. The recorded hashes are:

| File | SHA-256 |
| --- | --- |
| `kernel.log` | `3024ebb5efa6a4f2d592628566e7ce828f441bc3910da68e63ee00a1e66f34b3` |
| `ffmpeg.log` | `5498bc50e07d8fcd9a08dff4dc0fee916e605458a295b7f72e3de8c75f5e6256` |
| `transcript.txt` | `4c9a311acddad0e43ef6a50ac16be3245061041d638f097bd3a0dbe6b94eb81b` |

The capture verified kernel `7.0.0-34-generic`, DKMS
`nouveau-hpd-ddc/0.1.13`, and matching loaded/on-disk Nouveau `srcversion`
`57AE1B168D50DB546CD87A1`. Libva opened the exact system plugin
`/usr/lib/x86_64-linux-gnu/dri/nouveau_drv_video.so`; it reported Mesa
`26.0.8-1ubuntu0.3` for NVE4 and `va_openDriver() returns 0`.

FFmpeg PID 4230 used the same named-device 3000-frame H.264 invocation and
input as the previous run. It output 3000 frames, decoded 3003 frames with
zero decode errors, and exited 0. Its final progress record says
`elapsed=0:00:38.73`; `/usr/bin/time` recorded 56.36 seconds wall time. The
exact process-exit timestamp is monotonic `1029.808282701`, status 0. The
capture harness set `STOP_A_B=1` because of the Nouveau failure records. No
private-uninstrumented or private-instrumented condition was run afterward.

### Exact monotonic timeline

All offsets below use the harness's monotonic FFmpeg start (`973.572587112`)
and exact process exit (`1029.808282701`). A negative exit offset means the
event occurred before FFmpeg exited. The final progress line itself has no
wall-clock/monotonic stamp, so only its FFmpeg-reported elapsed time is known.

| Monotonic time | Event | Since FFmpeg start | Relative to FFmpeg exit |
| ---: | --- | ---: | ---: |
| `973.572587112` | FFmpeg start, PID 4230 | `+0.000000 s` | `-56.235696 s` |
| `975.946112` | First GR `TRAP`, GR channel 2, FFmpeg PID 4230 | `+2.373525 s` | `-53.862171 s` |
| `975.946746` | First PROP trap, `RT_WIDTH_OVERRUN`, format `0x37` | `+2.374159 s` | `-53.861537 s` |
| `976.052410` | Last PROP in startup burst; 53 startup PROP records total | `+2.479823 s` | `-53.755873 s` |
| `976.052410`–`1014.639808` | Quiet interval between startup burst and next GR trap | `38.587398 s` | — |
| `1014.639808` | Completion-adjacent GR `TRAP`, channel 2 | `+41.067221 s` | `-15.168475 s` |
| `1014.640447`–`1014.641472` | Four completion-adjacent PROP traps, all `RT_WIDTH_OVERRUN`, format `0x37` | `+41.067860`–`+41.068885 s` | `-15.167836`–`-15.166811 s` |
| `1018.985696` | FIFO `SCHED_ERROR 0a [CTXSW_TIMEOUT]` | `+45.413109 s` | `-10.822587 s` |
| `1018.986604` | Runlist 2, channel 5 (`av:h264:df0[4234]`): `rc scheduled` | `+45.414017 s` | `-10.821679 s` |
| `1018.989141` | Runlist 2: generic `rc scheduled` record | `+45.416554 s` | `-10.819142 s` |
| `1018.989973` | Runlist 2/channel 5: `errored - disabling channel` | `+45.417386 s` | `-10.818310 s` |
| `1018.991223` | FFmpeg PID 4230 reports `channel 5 killed!` | `+45.418636 s` | `-10.817060 s` |
| `1029.686075` | FFmpeg PID 4230 reports `failed to idle channel 4` | `+56.113488 s` | `-0.122208 s` |
| `1029.706186` | BAR2/HOST_CPU READ PTE fault at `0x377000`, channel `-1` / unknown | `+56.133599 s` | `-0.102097 s` |
| `1029.808283` | FFmpeg process exit, status 0 | `+56.235696 s` | `0.000000 s` |
| `1029.940455` | Post-FFmpeg journal tail begins | `+56.367867 s` | `+0.132172 s` |
| `1044.995956` | Journal follower stopped | `+71.423369 s` | `+15.187673 s` |

The kernel log contains 15 GR `TRAP` records and 57 PROP records: 49 use
format `0x37`, 8 use `0x2e`; 56 report width overrun and 21 height overrun
(the error-bit counts overlap). Four PROP records are in the final burst; the
other 53 are in the startup burst. The final FFmpeg progress says all 3000
frames were output at elapsed 38.73 s, while the exact process exit is at
56.24 s after start. The kernel records establish that the timeout, channel
recovery, idle failure, and BAR2 fault all happened before that process exit.

### Comparison with the previous private-Mesa capture

The previous private diagnostic capture is
`/home/keivan/nouveau-pgraph-3000-mesa-diag-20260928-{kernel.log,ffmpeg.log,transcript.txt}`.
Its kernel log SHA-256 is
`1df7bbd0a6e652402ab0b08ca2ce5259bd0884ac03d01fc1d1457b2d98d89a77`; the
FFmpeg log and transcript hashes are recorded below. Both runs used the same
decode workload; one selected the system Mesa plugin, the other a private
instrumented VA plugin. Condition A verifies the DKMS module and matching
`srcversion`; the earlier capture transcript does not record kernel/module
provenance, so its exact Nouveau `srcversion` cannot be verified from the
saved artifacts.

| Evidence | 2026-09-29 system Mesa | 2026-09-28 private diagnostic Mesa |
| --- | --- | --- |
| Kernel/module provenance | Kernel `7.0.0-34-generic`, DKMS `0.1.13`, loaded/disk `srcversion` match | Transcript does not record `uname`, `modinfo`, or `srcversion`; exact binary match unverified |
| GR trap channel | `ch 2`, FFmpeg PID 4230 | `ch 2`, FFmpeg PID 28135 |
| PROP records | 57 total: 49 format `0x37`, 8 format `0x2e`; width 56, height 21 | 43 total: 40 format `0x37`, 3 format `0x2e`; width 43, height 7 |
| PROP timing | startup `975.946746`–`976.052410`; final burst `1014.640447`–`1014.641472` | startup `19422.024614`–`19422.179315`; final burst `19460.725861`–`19460.726888` |
| Timeout/recovery | one timeout; runlist 2/channel 5 recovery and kill | two timeouts; first runlist 1/channel 4, then runlist 2/channel 5 |
| Idle failure | channel 4 at `1029.686075` | none in the capture |
| BAR2 fault | READ PTE `0x377000`, HOST_CPU, channel `-1` / unknown | READ PTE `0x49d000`, HOST_CPU, channel `-1` / unknown |
| Relative to FFmpeg exit | all listed failures before exact exit by 102 ms or more | exact process-exit time was not recorded; relation is unknown |

Both logs show startup PROP bursts, a long quiet interval during the 3000-frame
decode, a completion-adjacent PROP burst, and a FIFO timeout about 4.33–4.34 s
after the final PROP record. They are a similar recurring pattern, not identical
events: the new capture has one timeout/kill rather than two, and its BAR2
address and channel-4 idle failure differ. Two runs are insufficient to call
the pattern deterministic.

### Source interpretation and confidence

**SOURCE-PROVEN:** Linux v7.0 decodes scheduler code `0x0a` as
`CTXSW_TIMEOUT`. The scheduler handler scans engines reporting a context-switch
wait, obtains the active channel/group ID, and requests recovery; runlist
recovery disables/removes channels in the flagged group and may reset engines
still associated with it. The current FIFO log does not print the triggering
engine mask or its engine names. See the v7.0
[`gf100.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/gf100.c#L579-L650),
[`runl.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/runl.c#L58-L170),
and [`chan.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/chan.c#L227-L245).

GK104 does not assign a universal engine list to runlist numbers in a static
source table. `gk104_top_parse()` reads the hardware TOP table, and
`gk104_fifo_runl_ctor()` groups each engine using its runtime `tdev->runlist`
value. Thus `runlist 2` identifies the runtime queue, but this capture cannot
say which GK104 engine(s) belong to it. No TOP engine/runlist debug dump is
present in the capture; the read-only boot-journal inspection also surfaced no
topology records. See
[`top/gk104.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/top/gk104.c#L35-L100)
and [`fifo/gk104.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/gk104.c#L713-L751).

Mesa 26.0.8 creates three separate Kepler decoder FIFO channels in array order
for BSP, VP, and PPP, and constructs the corresponding engine objects on those
channels. The capture's `av:h264:df0` client label is consistent with the
decoder, but does not identify an array entry. Channel 2 in the earlier GR
trap is a GR channel and is distinct from FIFO channel 5 in the recovery
record. The native-surface capture's channel 4 is now mapped to the Mesa VP
channel by the synchronous free-call source path; channel 5 remains unmapped
pending the opt-in channel-handle trace. See Mesa
[`nvc0_video.c`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.0.8/src/gallium/drivers/nouveau/nvc0/nvc0_video.c#L91-183).

Assessment after the native-surface candidate run:

| Proposition | Assessment |
| --- | --- |
| A. PROP traps and FIFO scheduler failure are related | **PLAUSIBLE in the earlier captures, but not required for the timeout.** The native-surface candidate run had a timeout without PROP traps, so the scheduler failure can occur independently of the observed invalid-clear PROP pattern. |
| B. Timeout is a video-engine teardown/context-switch issue | **STRONGLY SUPPORTED as a VP teardown association, with channel 4 mapped to VP.** Runlist 2/channel 5 recovery began 4.295 s into that synchronous VP channel-free call, which lasted 15.002 s. Source and timestamp brackets map the subsequent `failed to idle channel 4` to the VP channel being freed. Channel 5 is not yet mapped, and neither temporal overlap nor the channel-4 mapping proves that VP deletion caused the separate channel-5 context-switch timeout. |
| C. BAR2 fault is downstream of channel recovery | **PLAUSIBLE from earlier captures only; causal status UNKNOWN.** The system-Mesa BAR2/PTE fault followed channel recovery and channel-4 idle failure, but the candidate run had no BAR2/PTE record. The timestamps do not prove that recovery caused the fault or that it was secondary. |
| D. Patch 2/nonstall event-index fix is involved | **UNSUPPORTED as a direct cause.** The nonstall event registration/notification path is distinct from `SCHED_ERROR` `CTXSW_TIMEOUT` recovery. A fence-notification mismatch could indirectly affect wait/teardown timing, so patch 2 could improve that separate issue while this scheduler failure remains. No removal or modification is justified from these captures. |

The older private run also had a BAR2 PTE fault 17.272 ms after its second
channel kill; the new system-Mesa fault is after recovery as well but has a
different address and follows a different number of timeouts. Both look
temporally secondary, while causality remains unproven.

### Prepared teardown trace

The next diagnostic uses the existing function-graph tracer in the installed
kernel plus opt-in Mesa channel identity records. This can map channel 5 to
BSP/VP/PPP and show whether the 15-second channel-free interval is spent in
`drm_sched_entity_fini()` or `nouveau_channel_idle()` without rebuilding the
kernel module. The exact source call chain, 15-second fence deadline, patch-2
assessment, and one-run procedure are in
[`VP-CHANNEL-TEARDOWN-TRACE.md`](VP-CHANNEL-TEARDOWN-TRACE.md). No kernel
diagnostic patch has been created. If runtime ftrace cannot trace the
channel-free entry point, the fallback is a standalone opt-in, log-only patch
at `gf100_fifo_intr_sched_ctxsw()` and the FIFO recovery callback. It would log
existing runlist, engine, channel, and context IDs only; it would not change
MMIO, timeouts, or recovery.

The capture harness was not changed. For condition A it recorded the exact
system plugin path, successful `va_openDriver()`, FFmpeg exit code/status,
`STOP_A_B=1`, journal follower start/stop times, empty journal-stderr file,
and a non-empty kernel log containing the full failure sequence. These records
are sufficient to distinguish successful decode plus kernel failure from an
FFmpeg crash, wrong VA driver, or startup logger error in this run. The harness
does not persist a separate `journalctl` follower exit status; that does not
block analysis of this capture because the decisive events precede FFmpeg exit
and are present in the hash-verified log.

## Updated conclusion after native-surface candidate — 2026-09-29

- **Observed, earlier runs:** the pre-candidate private-instrumented Mesa run
  and system-Mesa condition A both completed the 3000-frame decode but showed
  PROP traps and FIFO recovery; condition A also showed an idle failure and
  BAR2/PTE fault. The conditions were captured on separate boots. Their
  differing event counts and fault details do not establish identical failure
  sequences.
- **Observed, candidate run:** after a fresh boot, the private Mesa
  native-surface candidate completed 3000 output frames, decoded 3002 with zero
  decode errors, and exited 0. The exact private plugin path and successful
  `va_openDriver()` were verified. All 36 render-target clear calls used the
  expected plane dimensions, and this capture had no GR/PROP or BAR2/PTE
  records.
- **Observed, candidate run:** one `CTXSW_TIMEOUT` on runlist 2 led to recovery
  and channel 5 being killed. The Mesa VP channel-object deletion interval
  overlapped the timeout and remained blocked for 15.002 seconds. Channel 4
  failed to idle 1.268 ms before the VP deletion returned. These timestamps
  placed the timeout within VP-channel teardown. The source-correlated
  synchronous channel-free call maps channel 4's idle failure to the Mesa VP
  channel. Channel 5's decoder slot remains unknown. PPP channel deletion
  completed in 3.588 ms in this run.
- **Source-proven / strongly supported:** the earlier VA clear path passed a
  generic `pipe_surface` where NVC0 expected a Nouveau-private `nv50_surface`.
  The candidate creates a native Nouveau surface; its corrected runtime RT
  dimensions and absence of the prior PROP overrun signatures strongly
  support this mismatch as the cause of those traps. This is one hardware
  A/B, not broad reproducibility proof.
- **Unresolved:** the VP context-switch timeout and channel-4 idle failure
  remain despite the corrected clear surfaces. Their root cause is not
  established. The earlier condition-A BAR2 fault is not present in this
  candidate capture, and neither its relationship to channel recovery nor
  causation is established.

Condition C (private-instrumented native-surface candidate) has now been run;
the earlier statements that the clear trace was unexercised and that C was
the next condition are historical. The harness set `STOP_A_B=1` because the
kernel reported a timeout/channel-recovery signature. Do not run another GPU
condition in the same boot. No functional kernel/FIFO fix was created, and
the Mesa candidate remains private rather than installed system-wide. The
capture hashes, complete timestamped trace, and separate PGRAPH/scheduler
assessment are recorded in
[`VA-SURFACE-CLEAR-CANDIDATE.md`](VA-SURFACE-CLEAR-CANDIDATE.md).
