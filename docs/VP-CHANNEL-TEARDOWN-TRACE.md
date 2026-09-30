# GK104 VP channel teardown trace

This investigation tracks the remaining Nouveau BAR2/HOST_CPU PTE faults and
the legacy VP3 teardown timeout. The private Mesa native-surface clear fix and
the separate-channel teardown ordering have each been tested in controlled
hardware runs. The latest run completed the VP and PPP idle fences promptly
with no context-switch timeout or channel kill, but still logged two BAR2 PTE
faults. Neither Mesa candidate is installed system-wide.

## Candidate-run observations

The source-correlated private native-surface capture is:

`/home/keivan/nouveau-vaapi-captures/20260929T164635Z-private-instrumented-23623`

The hash-verified capture completed 3000 output frames, decoded 3002 with zero
decode errors, and exited 0. The VA clear fix produced expected RT dimensions
and no PROP or BAR2/PTE records. It still had a VP channel teardown timeout:

| Monotonic time | Event | From VP channel-free begin | From FFmpeg start | From FFmpeg exit |
| ---: | --- | ---: | ---: | ---: |
| `8422.106624806` | Mesa begins deleting decoder channel index 1 (`vp`) | `0` | `+41.002055 s` | `-15.134125 s` |
| `8426.401149` | FIFO `CTXSW_TIMEOUT` on runlist 2 | `+4.294524 s` | `+45.296579 s` | `-10.839601 s` |
| `8426.458191` | Runlist 2/channel 5 recovery scheduled | `+4.351566 s` | `+45.353621 s` | `-10.782559 s` |
| `8426.458643` | Runlist 2 recovery scheduled | `+4.352018 s` | `+45.354073 s` | `-10.782107 s` |
| `8426.458968` | Channel 5 disabled | `+4.352343 s` | `+45.354398 s` | `-10.781782 s` |
| `8426.459304` | FFmpeg is told channel 5 was killed | `+4.352679 s` | `+45.354734 s` | `-10.781446 s` |
| `8437.107052` | `failed to idle channel 4` | `+15.000427 s` | `+56.002482 s` | `-0.133698 s` |
| `8437.108320209` | Mesa's VP channel-free ioctl returns | `+15.001695 s` | `+56.003750 s` | `-0.132430 s` |
| `8437.240749893` | FFmpeg process exits successfully | `+15.134125 s` | `+56.136180 s` | `0` |
| `8452.428219108` | Journal logger stops after its tail | `+30.321594 s` | `+71.323649 s` | `+15.187469 s` |

The kernel idle error is printed 1.268 ms before Mesa's synchronous VP channel
free returns. FFmpeg starts at monotonic `8381.104569726`, exits at
`8437.240749893` with status 0, and the journal logger stops at
`8452.428219108`, 15.187 seconds after process exit. The capture-file hashes
in `SHA256SUMS` verify, and Mesa, journal, and harness monotonic values align
in this capture. The later Mesa channel-handle records map channel 4 to VP and
channel 5 to PPP. The source call chain below ties the channel-4 idle error to
the VP free operation; channel 5 is the separate recovery target, while the
engine that raised the timeout remains unknown.

## Source-proven call chain

The exact local sources inspected were Mesa `26.0.8-1ubuntu0.3`, libdrm
`2.4.131-1` (upstream tag `libdrm-2.4.131`), and Ubuntu Linux source package
`7.0.0-34.34`.

1. Mesa's `nouveau_vp3_decoder_destroy()` deletes decoder channels in BSP, VP,
   PPP order. For the VP slot it calls `nouveau_object_del(&dec->channel[1])`
   synchronously. The decoder setup in `nvc0_video.c` supplies the matching
   BSP, VP, and PPP FIFO engine selectors.
2. The Mesa winsys assigns the kernel's allocated `req.channel` to
   `nouveau_object.handle`. On delete, a `NOUVEAU_FIFO_CHANNEL_CLASS` object
   calls `drmCommandWrite(..., DRM_NOUVEAU_CHANNEL_FREE, ...)` with that handle.
   In libdrm, `drmCommandWrite()` makes the ioctl through `drmIoctl()`.
3. Linux maps `DRM_NOUVEAU_CHANNEL_FREE` to
   `nouveau_abi16_ioctl_channel_free()`. The ioctl finds the kernel channel by
   `req->channel`/`chid` and calls `nouveau_abi16_chan_fini()` synchronously.
4. `nouveau_abi16_chan_fini()` first calls
   `drm_sched_entity_fini()` when the channel has a scheduler entity, then calls
   `nouveau_channel_idle()`.
5. `nouveau_channel_idle()` creates a fence and calls
   `nouveau_fence_wait(fence, false, false)`. With `lazy=false`, this selects
   `nouveau_fence_wait_busy()`, which polls `nouveau_fence_done()` until the
   fence signals or its deadline expires. `nouveau_fence_emit()` sets that
   fence deadline to `jiffies + 15 * HZ`. On failure, `nouveau_channel_idle()`
   prints `failed to idle channel %d` using the same kernel channel's `chid`.

The scheduler step is a separate possible blocking point. Upstream v7.0's DRM
scheduler source has `drm_sched_entity_fini()` call
[`drm_sched_entity_kill()`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/scheduler/sched_entity.c#L329-L347),
which removes the entity from its run queue and then calls
[`wait_for_completion(&entity->entity_idle)`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/scheduler/sched_entity.c#L231-L250)
without an explicit timeout. That happens before Nouveau's channel-idle fence
wait. The earlier userspace-only capture bracketed both stages together; a
later function-graph capture measured the 15-second wait inside
`nouveau_channel_idle()` itself (see the follow-up below).

The earlier Mesa `object-destroy engine=vp` records bracket deletion of the
VP engine context object; `nouveau_object_subchan_del()` sends that through
NVIF and it returned quickly. The 15-second span begins later at
`channel-destroy stage=object-begin index=1 engine=vp`. That object is the
FIFO channel, whose delete path is the synchronous `DRM_NOUVEAU_CHANNEL_FREE`
ioctl above. The context-object delete and channel-free delay are separate
operations.

The ioctl lookup and teardown are synchronous. Thus the `failed to idle
channel 4` printed inside the VP-slot channel-free call identifies **channel 4
as the Mesa VP channel for this run**. This is a direct source/call-bracket
mapping, not an assignment based only on channel numbering. The ioctl teardown
continues after `nouveau_channel_idle()` returns an error, which is consistent
with FFmpeg still exiting successfully.

The whole Mesa VP channel-free interval is `15.001695 s`; the error appears at
`15.000427 s`. This closely matches the kernel's explicit 15-second fence
deadline. The later function-graph capture measured the individual stages:
`nouveau_fence_wait()` occupied `15.000303 s`, `nouveau_channel_idle()`
occupied `15.000630 s`, and the full ABI16 channel-free ioctl occupied
`15.003396 s`. Only about 2.8 ms of the ioctl was outside the idle call, so
the scheduler-entity completion wait does not explain the measured stall.

`CTXSW_TIMEOUT` is handled separately by the FIFO scheduler interrupt path
(`gf100_fifo_intr_sched()` -> `gf100_fifo_intr_sched_ctxsw()` -> the FIFO's
`intr_ctxsw_timeout` callback). Its appearance 4.295 seconds into the VP
channel-free call does not by itself prove it caused the idle fence to time
out. The 15-second fence deadline explains the later idle error without
requiring a second post-kill timeout: the approximately 10.648 seconds from
channel-5 kill to channel-4 idle error are the remaining portion of the same
VP channel-free interval.

There is a further distinction between the channel being recovered and the
engine that first reported the context-switch timeout. In Linux v7.0,
`gf100_fifo_intr_sched_ctxsw()` scans engine context-switch status and builds
an engine mask, then passes that mask to the runlist recovery callback. The
recovery code looks up active channel/group IDs on affected runlists and logs
the channel it recovers. That log identifies the recovered context; it does
not print which engine set the original timeout bit. Thus, even if the new
Mesa identity record maps chid 5 to the VP slot, that would identify the VP
channel whose context was recovered, but would not alone prove that VP was the
engine that initiated `CTXSW_TIMEOUT` if multiple engine contexts share that
channel context. See the upstream v7.0 [GK104 FIFO interrupt handler](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/gk104.c#L651-L690)
and [GF100 FIFO timeout/recovery path](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/engine/fifo/gf100.c#L618-L691).

The function-graph trace measures the timeout-handler and channel-free paths,
but does not capture the engine mask's contents. The later Mesa identity trace
maps chid 5 to PPP, so the recovery target is known; the engine that raised the
timeout remains unknown. The next diagnostic is a narrowly scoped, log-only
record of the computed `engm` mask and each selected engine's ID/type and
active context before recovery. It must not change recovery behavior.

## Channel 5 and patch 2

In the first userspace-only capture, the Mesa binary predated the channel
identity print, so the channel-5 kill could not be assigned to BSP, VP, or PPP
from that capture. The later function-graph capture includes the identity
records and supersedes that limitation: it maps channel 5 to PPP and channel 4
to VP, as detailed below. This maps the recovery target, not the engine bit
that first raised `CTXSW_TIMEOUT`.

The new Mesa-only patch
[`nouveau-vp3-channel-id-diagnostic.patch`](../patches/mesa/nouveau-vp3-channel-id-diagnostic.patch)
logs each decoder slot's engine name, `channel->handle` (the kernel chid),
channel class, and engine-object class at decoder-destroy entry, before any
channel object is released. The next run can therefore match runlist 2/chid 5
to BSP, VP, or PPP without a kernel source change. Engine class IDs also
distinguish the attached legacy video object.

The legacy nonstall patch changes only the event index used when registering a
nonstall uevent. Linux v7.0's `nouveau_channel_idle()` path does not wait on
that uevent: it calls the non-lazy `nouveau_fence_wait()` polling path and
checks fence progress directly with `nouveau_fence_done()`. Therefore patch 2
is **not a direct explanation for the observed 15-second idle-fence deadline**.
Scheduler entity teardown runs earlier and could be indirectly affected by
event-driven job progress, but this capture does not show that. Do not remove
or change patch 2 from this evidence.

## Bounded current-upstream source comparison

On 2026-09-29, the inspected `torvalds/linux` master head was
`6f8319e3e9a44dd537d17f41565a8453c560a581`. Its
[`nouveau_channel_idle()`](https://github.com/torvalds/linux/blob/6f8319e3e9a44dd537d17f41565a8453c560a581/drivers/gpu/drm/nouveau/nouveau_chan.c#L65-L86)
still emits a fence, calls `nouveau_fence_wait(fence, false, false)`, and logs
`failed to idle channel` if the wait fails. The corresponding
[`nouveau_fence_emit()` and busy-wait code](https://github.com/torvalds/linux/blob/6f8319e3e9a44dd537d17f41565a8453c560a581/drivers/gpu/drm/nouveau/nouveau_fence.c#L208-L331)
set the per-fence deadline to 15 seconds and poll completion until that
deadline. This current-source comparison is consistent with the Linux 7.0
source analysis above; it does not identify why the VP fence fails to
complete on this K4200.

Master also moves destruction of the channel kill-event subscription ahead of
fence-context teardown in
[`nouveau_channel_del()`](https://github.com/torvalds/linux/blob/6f8319e3e9a44dd537d17f41565a8453c560a581/drivers/gpu/drm/nouveau/nouveau_chan.c#L88-L120).
The code comment explains that the event handler dereferences `chan->fence`,
which context teardown frees. Linux v7.0 destroys that event later in channel
deletion. This is a separate lifetime/UAF ordering change; it does not change
the idle-fence wait, its deadline, or the FIFO context-switch recovery path,
so it is not evidence of a fix for the observed timeout.

This was a bounded source comparison, not an exhaustive search of all kernel
history. It found no source change in the inspected master snapshot that
explains or fixes this GK104 VP-channel timeout. The timeout's cause remains
unresolved.

## 2026-09-29 function-graph and channel-identity capture

The follow-up capture is
`/home/keivan/nouveau-vaapi-captures/20260929T201613Z-private-instrumented-22129`.
All five files listed in `SHA256SUMS` verify, including the kernel log, FFmpeg
log, transcript, function graph, and trace metadata. The run used the private
native-surface Mesa plugin and the existing `7.0.0-34-generic` / DKMS 0.1.13
kernel baseline; it did not install or replace a driver.

The decode completed successfully: 3000 output frames, 3002 decoded frames,
zero decode errors, FFmpeg exit 0. The clear diagnostic reported the expected
1920x544 and 960x272 render-target dimensions, and this capture had no
GR/PROP-overrun records. Unlike the earlier native-surface capture, this run
did contain one BAR2/HOST_CPU PTE fault after the channel-idle failure. Thus
BAR2 was absent in one candidate run and present in this one; the clear fix
does not establish that BAR2 failures are eliminated.

Mesa's decoder-destroy identity records establish the kernel channel mapping
for this process:

| Mesa decoder slot | Kernel chid | Engine class |
| --- | ---: | ---: |
| BSP | 3 | `0x95b1` |
| VP | 4 | `0x95b2` |
| PPP | 5 | `0x90b3` |

The kernel's runlist-2 recovery record names chid 5, so the recovered and
killed channel is the PPP channel. The `failed to idle channel 4` line maps to
the VP channel. This mapping comes from the Mesa channel handles captured
before destruction, not an inference from numeric channel order. The log still
does not report which engine bit first raised the context-switch timeout;
recovering chid 5 identifies the context selected for recovery, not
necessarily the initiating engine.

The monotonic timeline is:

| Event | Monotonic seconds | From FFmpeg start | Relative to VP channel-free begin |
| --- | ---: | ---: | ---: |
| FFmpeg starts | 2502.333211 | 0 | - |
| Mesa begins freeing VP channel / kernel enters channel-free path | 2543.908084 | +41.574874 s | 0 |
| `CTXSW_TIMEOUT` | 2548.208884 | +45.875673 s | +4.300800 s |
| runlist 2, chid 5 recovery scheduled | 2548.210177 | +45.876966 s | +4.302093 s |
| runlist 2 recovery scheduled | 2548.210940 | +45.877729 s | +4.302856 s |
| chid 5 disabled | 2548.213810 | +45.880599 s | +4.305726 s |
| chid 5 killed notification | 2548.214549 | +45.881338 s | +4.306465 s |
| `failed to idle channel 4` | 2558.909080 | +56.575869 s | +15.000996 s |
| Mesa VP channel-free call returns | 2558.911095 | +56.577884 s | +15.003011 s |
| BAR2/HOST_CPU PTE fault | 2558.928106 | +56.594895 s | +15.020022 s |
| FFmpeg exits, status 0 | 2559.066387 | +56.733177 s | +15.158303 s |
| journal tail stops | 2574.298847 | +71.965636 s | +30.390763 s |

The last FFmpeg progress line reports 3000 frames at 38.74 seconds of process
elapsed time; actual process exit is 56.733 seconds after start because
decoder/channel destruction continues after frame production. The journal
logger remained active 15.232 seconds after the exact FFmpeg exit timestamp.

The function graph resolves where the long teardown interval is spent:

| Kernel function | Inclusive duration |
| --- | ---: |
| `nouveau_fence_wait()` | 15.000303 s |
| `nouveau_channel_idle()` | 15.000630 s |
| `nouveau_abi16_ioctl_channel_free()` | 15.003396 s |

The graph therefore puts essentially the entire 15-second delay in
`nouveau_channel_idle()`'s fence wait. The rest of the channel-free ioctl
accounts for about 2.766 ms. This is much stronger than timing the userspace
call alone: it does not support `drm_sched_entity_fini()` as the source of the
15-second delay in this run. The fence still fails to complete on VP chid 4;
the PPP chid 5 context-switch recovery occurs about 4.301 seconds into that
wait. Temporal overlap does not show whether the PPP recovery causes the VP
fence failure.

The later BAR2/PTE fault is observed 17.011 ms after the VP idle error and
15.232 seconds before process exit. Its ordering is consistent with it being
downstream of channel recovery, but neither this trace nor the source proves
causation. The fact that it recurred in a run with valid surface metadata and
no PROP overrun makes it a separate unresolved symptom rather than evidence
that the clear fix failed.

## Assessment of the upstream scheduler-entity teardown backport

The proposed upstream pair is real and narrowly fixes a scheduler-entity
double-finalization bug:

- [`2f5f05633e2229c8ec379e1d65151165c905671e`](https://github.com/torvalds/linux/commit/2f5f05633e2229c8ec379e1d65151165c905671e)
  makes `drm_sched_entity_kill()` public and exported for Nouveau to call.
- [`cac96c8d93faa073456fbb3a504b2a3d15d51841`](https://github.com/torvalds/linux/commit/cac96c8d93faa073456fbb3a504b2a3d15d51841)
  replaces an early `drm_sched_entity_fini()` with `drm_sched_entity_kill()`;
  the scheduler is finalized later by `nouveau_sched_destroy()`.

However, this is not currently a good causal candidate for these Mesa VP3
channels. In Linux v7.0, `nouveau_abi16_chan_alloc()` creates `chan->sched`
only when `nouveau_cli_uvmm(cli)` is active, and `nouveau_abi16_chan_fini()`
calls either entity function only under `if (chan->sched)`. The code comments
identify this scheduler as belonging to the VM_BIND UAPI. The exact Mesa 26.0.8
VP3 winsys allocates its channels with the legacy
`DRM_NOUVEAU_CHANNEL_ALLOC` ioctl; its Nouveau Gallium paths contain no
`DRM_NOUVEAU_VM_INIT` / VM_BIND setup. Mesa's separate `DRM_NOUVEAU_SVM_INIT`
use is not UVMM initialization. Consequently, source evidence strongly
indicates `chan->sched` is NULL for these decode channels, so the proposed
Nouveau replacement is skipped for them.

That conclusion is source-based rather than a direct runtime dump of
`chan->sched`. Even setting that aside, the function graph attributes about
15.0006 seconds to `nouveau_channel_idle()` and only 2.766 ms to the rest of
the ioctl, while the scheduler fix changes entity cleanup semantics. It could
correct a separate bug for UVMM-backed channels, but there is no evidence it
will fix this legacy VP fence timeout. Do not build a full kernel solely to
test this pair against the current VP3 symptom without new evidence that the
decode channels have an active scheduler entity.

The installed kernel's `Module.symvers` does not export
`drm_sched_entity_kill()`, and its headers do not declare it. The first patch
is required for an out-of-tree Nouveau module to link against a kernel carrying
the second change; both patches require a full kernel source/build change in
this environment. They cannot be added only to the existing DKMS `nouveau.ko`
package. No kernel source or installed driver was changed for this assessment.

**Current conclusion:** the function-graph capture proves the VP idle-fence
wait is the 15-second delay and identifies channel 5 as PPP. The scheduler
double-fini fix is a valid upstream correction but is strongly unsupported as
the cause of this particular legacy Mesa VP3 timeout. The cause of the PPP
context-switch timeout and the unsignaled VP fence remains unresolved.

## Function-graph result and next diagnostic

The function-graph capture above completed the channel mapping and localized
the 15-second interval to `nouveau_fence_wait()` inside
`nouveau_channel_idle()`. The existing capture helper remains available for
later use, but another function-graph-only run would not answer the remaining
questions.

The capture used a private Mesa build at:

```text
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-channel-id-trace/prefix/lib/x86_64-linux-gnu/dri
```

Its `libgallium_drv_video.so` SHA-256 was
`defee11fcedabc2b81730bfcc0d8d425133cccc76751e5c17652a9420b56268f`. It
contained the native-surface fix and the opt-in channel identity trace. The
hash-verified capture mapped BSP/VP/PPP to chids 3/4/5 and showed the VP idle
fence timing above.

### Standalone kernel diagnostic patch

[`patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch`](../patches/diagnostic/gk104-vp-idle-fence-ctxsw-trace.patch)
is a separate, opt-in Linux v7.0 diagnostic. It is not listed in the DKMS
patch series and does not change fence packets, timeouts, scheduler state, or
recovery behavior.

The patch adds two root-writable module parameters, both defaulting to off:

- `diag_fence_wait=1` marks only fences created by `nouveau_channel_idle()`.
  For those fences it records the sequence, GPU virtual semaphore address,
  memory value immediately before emit, `emit32` return value, and kernel
  push-buffer PUT/current/free cursors. While the idle wait is pending, it
  samples the semaphore value once per second and records the final value and
  result at return. The common Nouveau channel wrapper does not expose a
  trustworthy live GPFIFO GET value here, so the diagnostic deliberately does
  not read an undocumented USERD offset.
- `diag_ctxsw=1` records the computed `engm` mask, then logs each selected
  engine's runlist, engine ID/name/type/instance, active context ID and whether
  that ID is a channel or channel group. It also records each matching engine
  context immediately before the existing MMU-fault recovery trigger. These
  messages are limited to the rare context-switch timeout path.

#### Exact Ubuntu-source build and artifact validation

The standalone patch was built against the installed Ubuntu source package
`linux-source-7.0.0` version `7.0.0-34.34`, using
`/lib/modules/7.0.0-34-generic/build`. The source archive SHA-256 is
`a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`. The
diagnostic patch passed `patch --dry-run --fuzz=0 --forward --batch -p1`
against all five exact Ubuntu source files it changes. The isolated DKMS-style
build completed with `build_script_exit=0`; Kbuild linked `nouveau.ko` and
`MODPOST` completed without unresolved-symbol errors.

The built artifact is preserved at:

```text
/home/keivan/.cache/nouveau-vp-idle-fence-diag-build-20260929/artifacts/nouveau-diag-final.ko
```

Its SHA-256 is
`8d36b99b595f650a2f8b622879358b32d110bb158f1d0069ec9bd7095bcbc9e6`,
`modinfo -F vermagic` reports `7.0.0-34-generic SMP preempt mod_unload
modversions`, and its Nouveau source version is
`B19B8AAE48467545E652509`. `modinfo -p` exposes both
`diag_fence_wait` and `diag_ctxsw`. The linked module contains every expected
`NOUVEAU_DIAG_IDLE_FENCE` and `NOUVEAU_DIAG_CTXSW` marker, including the
once-per-second `wait-progress` record. Disassembly confirms the fence
parameter branch is present in `nouveau_fence_wait()` and `nv84_fence_emit()`,
and the context-switch parameter branch is present in
`gf100_fifo_intr_ctxsw_timeout()`.

The successful build log is preserved at
`/home/keivan/.cache/nouveau-vp-idle-fence-diag-build-20260929/artifacts/build-final.log`
with SHA-256
`2e38d54489f3494804528c54808eae00a9cb865d2f15822cd8cb4fbccbe382fc`.
`SHA256SUMS-final` in the same artifact directory records the module, log,
patch, and Ubuntu source-archive hashes. The successful log shows the existing
HPD/DDC patch, GK104 non-privileged video-context patch, and legacy nonstall
patch were included. The existing HPD hunk applied with fuzz 1 due to its
pre-existing build-script behavior; the new diagnostic itself was applied
strictly with zero fuzz.

The toolchain emitted two non-fatal environment warnings: the `gcc` and
`x86_64-linux-gnu-gcc` names refer to the same Ubuntu GCC 15.2.0 version, and
the installed `pahole` is version 0 while the kernel build used version 131.
Kbuild therefore skipped BTF generation because the matching `vmlinux` is not
available. Compilation, `MODPOST`, and module linking all succeeded.

The preserved `.ko` above is a private review artifact, not the file registered
directly with DKMS. A separately versioned `nouveau-hpd-ddc/0.1.13-diag1`
package was subsequently installed and loaded after reboot; the capture below
used that module. The original `.13` build remains preserved for rollback. No
scheduler backport or functional kernel change is included.

## Current confidence

- **PROVEN:** Mesa channel identity is BSP chid 3, VP chid 4, PPP chid 5.
- **PROVEN:** the 15-second VP teardown stall is the Nouveau idle-fence wait.
- **PROVEN:** the new diagnostic identifies `engm=0x4` as MSPPP, runlist 2,
  active chid 5, and the recovery targets that PPP channel while VP chid 4 is
  waiting.
- **PROVEN:** the VP idle-fence submission returned 0 and kicked the push
  buffer. The semaphore at GPU address `0x14040` moved from `0xbbc` to `0xbbd`
  but never reached target sequence 3007 (`0xbbf`) before the 15-second
  deadline. This shows incomplete fence progress; it does not prove why the
  release stopped.
- **UNKNOWN:** whether PPP recovery caused VP command progress to stop, whether
  VP itself stopped executing, or whether another shared FIFO condition is
  responsible.
- **UNRESOLVED:** whether the BAR2/PTE fault is primary or secondary.
- **UNSUPPORTED as a direct explanation:** patch 2's nonstall event index for
  this non-lazy busy-poll fence wait.
- **No Mesa teardown reorder has been hardware-tested, and no kernel recovery
  change was made.** A standalone Mesa-only ordering candidate is described
  below.

## Fresh diagnostic capture and serialized teardown candidate

After installing the separately versioned diagnostic module and rebooting,
one private native-surface Mesa run was captured at:

`/home/keivan/nouveau-vaapi-captures/20260930T023643Z-private-instrumented-5108`

All entries in that capture's `SHA256SUMS` verify. The hash values are:

| File | SHA-256 |
| --- | --- |
| `kernel.log` | `0822ea431fffd99b647eee031a3245e330f3657e90a2d394ed457bd65f22b96c` |
| `ffmpeg.log` | `8b1b6ee0da8e660d05f3944fc78bcf40b0c111ce4d9c7ce7a01924224266eac1` |
| `transcript.txt` | `e15f3cc2c19170a5415eca1bd5c1584a674e5759a470c2783595b7d534f798ee` |

The loaded and on-disk diagnostic kernel module both had source version
`B19B8AAE48467545E652509`. Libva opened the exact private native-surface
plugin, and `va_openDriver()` returned 0. The input hash was
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`.
FFmpeg output 3000 frames, decoded 3002 with zero decode errors, and exited 0.
The run took 56.34 seconds wall time; its final media progress was 38.71
seconds. The VA trace reports `SKIP_CLEAR_SURFACE=0`; all clears entered
`nvc0_clear_render_target()` with correct `1920x544` or `960x272` RT extents.
This kernel capture has no PROP/RT-overrun or BAR2/PTE records. The earlier
candidate capture did have a BAR2 fault, so BAR2 is improved in this sample,
not established as fixed.

The combined monotonic timeline is:

| Monotonic time | Event | Since FFmpeg start | Relative to FFmpeg exit |
| ---: | --- | ---: | ---: |
| `373.458864409` | FFmpeg starts | `0` | `-56.212822 s` |
| `414.524727454` | VP3 decoder destruction begins | `+41.065863 s` | `-15.146959 s` |
| `414.525120492` | PPP engine-object deletion begins | `+41.066256 s` | `-15.146566 s` |
| `414.526152001` | PPP engine-object deletion ends | `+41.067288 s` | `-15.145535 s` |
| `414.529605402` | VP chid 4 channel-free call begins | `+41.070741 s` | `-15.142081 s` |
| `414.530106` | VP idle fence emitted: seq 3007, address `0x14040`, old value `0xbbc`, kick called | `+41.071242 s` | `-15.141581 s` |
| `414.530560` | VP fence busy-wait begins; value remains `0xbbc` | `+41.071696 s` | `-15.141127 s` |
| `415.530079` | First sample: semaphore advances to `0xbbd` | `+42.071215 s` | `-14.141608 s` |
| `418.828701` | `CTXSW_TIMEOUT` | `+45.369837 s` | `-10.842986 s` |
| `418.829592` | Diagnostic records `engine_mask=0x4` | `+45.370728 s` | `-10.842095 s` |
| `418.831719` | Selected engine: MSPPP, runlist 2, active chid 5 | `+45.372855 s` | `-10.839968 s` |
| `418.833974` | PPP chid 5 killed | `+45.375110 s` | `-10.837713 s` |
| `429.530655` | VP idle-fence wait ends `-EBUSY`; semaphore still `0xbbd` | `+56.071791 s` | `-0.141032 s` |
| `429.530981` | Kernel reports `failed to idle channel 4` | `+56.072117 s` | `-0.140706 s` |
| `429.531822677` | PPP chid 5 channel-free call begins, after VP wait returns | `+56.072958 s` | `-0.139864 s` |
| `429.537165245` | PPP channel-free call returns in about 5.34 ms | `+56.078301 s` | `-0.134521 s` |
| `429.671686632` | FFmpeg exits 0 | `+56.212822 s` | `0` |
| `444.875895124` | Journal logger stops after post-FFmpeg tail | `+71.417031 s` | `+15.204208 s` |

The diagnostic samples once per second. After the one-step advance to `0xbbd`,
the semaphore remained at that value in every later sample. The channel-free
ordering is also explicit in Mesa's timestamped log: PPP's context object is
deleted before the VP channel wait starts, but PPP's channel itself is not
freed until VP returns. Thus the capture confirms PPP context-switch recovery
overlaps the VP idle-fence wait, but does not show that the PPP channel-free
call caused the VP wait to stall.

## Mesa-only serialized teardown candidate

The exact Mesa `26.0.8-1ubuntu0.3` source uses separate BSP, VP, and PPP
channels on GK104. Before this candidate its destructor deletes all three
engine objects first, then frees each push buffer and channel. The trace shows
that PPP engine-object deletion therefore happens before the VP idle-fence
wait. This makes serialization a controlled hypothesis worth testing; it is
not causal proof.

[`patches/mesa/nouveau-vp3-serialize-channel-teardown.patch`](../patches/mesa/nouveau-vp3-serialize-channel-teardown.patch)
changes only the distinct-channel branch to perform, in order:

```text
BSP object -> BSP push buffer -> BSP channel
VP object  -> VP push buffer  -> VP channel
PPP object -> PPP push buffer -> PPP channel
```

The shared-channel branch retains the original all-engine-objects-first
ordering. The candidate does not change decode submissions, kernel behavior,
idle-fence semantics, or error handling. It keeps the native-surface fix and
timestamped diagnostics in place.

The diagnostic, native-surface, channel-ID, and teardown patches were applied
in that order with `patch --fuzz=0` to a staging copy of the private
uninstrumented Mesa 26.0.8 source. The native-surface helper verified its
exact five-file preimage hashes, and the resulting VP3 source matched the
private candidate build source byte-for-byte. The static regression check
verifies both the per-engine ordering and unchanged shared-channel ordering.
A new private build was started from a clean Meson
setup but stopped after 208 of 934 targets to avoid wasting RAM/time. The
changed VP3 source was then built and linked incrementally in the already
successful private Mesa build cache: 4 Ninja steps, `build_script_exit=0`,
with `-j1`. Failed/interrupted and successful logs are kept separately under:

`/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-candidate/`

The new private Gallium plugin SHA-256 is
`d841244e592f171adebdbeaa556c97cb2e1bf639fceba81b0a829c9720b1119f`.
Its `nouveau_drv_video.so` symlink resolves to that plugin, all timestamped
surface/clear/channel markers are present, and `ldd` reports no missing
dependencies. The previous working native-surface candidate prefix was not
modified. This new plugin is **not installed system-wide**. Its first
controlled hardware run is recorded below.

The pre-run checklist below was used for the controlled run recorded below.
At that time a fresh reboot was required because the preceding capture set
`STOP_A_B=1`. Keep the installed diagnostic DKMS module and Mesa
native-surface fix unchanged; change only to the new private prefix. After
reboot, verify the module, enable both diagnostics, and verify their readback:

```bash
cd ~/nouveau-hpd-ddc-dkms
sudo tools/verify-vp-fence-diagnostic.sh post-reboot
sudo sh -c 'printf 1 > /sys/module/nouveau/parameters/diag_fence_wait; printf 1 > /sys/module/nouveau/parameters/diag_ctxsw'
sudo sh -c 'printf "diag_fence_wait="; cat /sys/module/nouveau/parameters/diag_fence_wait; printf "diag_ctxsw="; cat /sys/module/nouveau/parameters/diag_ctxsw'
```

Both readbacks should be `Y`. Then verify and run only the serialized candidate:

```bash
DRI="$HOME/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-candidate/prefix/lib/x86_64-linux-gnu/dri"
printf '%s  %s\n' \
  'd841244e592f171adebdbeaa556c97cb2e1bf639fceba81b0a829c9720b1119f' \
  "$DRI/libgallium_drv_video.so" | sha256sum --check -
readlink -f "$DRI/nouveau_drv_video.so"
tools/vaapi-capture.sh private-instrumented "$DRI"
```

Keep SDDM off during the capture and do not run
`/home/keivan/cli-low-memory.sh` until it completes. The capture helper now
holds an exclusive lock for the complete run and journal tail; the low-memory
script takes the same exclusive lock and refuses to stop SDDM or other
services while that lock is held. A second simultaneous capture is also
refused before its logger or FFmpeg starts. The helper records SDDM state
before and after and rejects a capture if it changes. The explicit VA-API
render-node test does not require SDDM. If this capture reports `STOP_A_B=1`,
do not run another GPU condition in the same boot.

The paired local low-memory helper is outside this repository at
`/home/keivan/cli-low-memory.sh`. The tested copy had SHA-256
`64e53cfaf476718c48e76c634c57b5314cee8a77ae41b364b49952b67e5a45eb`; it
takes the same nonblocking exclusive lock and exits with status 3 before
stopping services if a capture owns it. Recompute this hash if the local
helper changes. The capture regression test exercises this external script
with an isolated temporary `HOME`.

## Serialized teardown candidate hardware capture — 2026-09-29

The planned test above was performed after a fresh boot. The capture is:

`/home/keivan/nouveau-vaapi-captures/20260930T033219Z-private-instrumented-4381`

All three recorded SHA-256 values verify:

| File | SHA-256 |
| --- | --- |
| `kernel.log` | `f5162a447bc10467fd7c05ea8fe406b42503f6744a987a7a94f03e98c89fcfed` |
| `ffmpeg.log` | `608fca8270d3f578d4f5f32f040d7cdcfb73de286a931fad663a42b6a287f014` |
| `transcript.txt` | `f874d2f0ae0a0cae2489ab60a3ac0731b0a5c7507c246304b6a8b8d5ab973e05` |

The baseline was kernel `7.0.0-34-generic` with the already-installed
`nouveau-hpd-ddc/0.1.13-diag1`; loaded and on-disk Nouveau `srcversion` both
matched `B19B8AAE48467545E652509`. Both kernel diagnostic parameters were
enabled for this one run. SDDM and `display-manager` were inactive at both
capture start and end. The private `nouveau_drv_video.so` path was selected
and `va_openDriver()` returned 0. The input SHA-256 remained
`d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`.

FFmpeg completed 3000 output frames, decoded 3002 frames with zero decode
errors, and exited 0. Media progress elapsed was 38.72 s and wall time was
41.33 s. The harness returned 5 / `STOP_A_B=1` because it observed BAR2/PTE
faults; this is a successful decode with a kernel fault, not a clean capture.
No second GPU workload was run in this boot. The diagnostic parameters were
turned off after the capture.

The VA instrumentation reported `SKIP_CLEAR_SURFACE=0` and entered
`nvc0_clear_render_target()` for each plane. The emitted RT dimensions were
only `1920x544` and `960x272`; the kernel log contained no PROP,
`RT_WIDTH_OVERRUN`, or `RT_HEIGHT_OVERRUN` record.

The Mesa channel identity records map BSP to chid 3, VP to chid 4, and PPP to
chid 5. The monotonic timestamps from Mesa, the kernel journal, and the
harness align. The important teardown events were:

| Monotonic time | Event | Relative to FFmpeg start |
| ---: | --- | ---: |
| `432.058294584` | FFmpeg starts | `0` |
| `434.389081` | BSP/chid 3 idle fence completes | `+2.330786 s` |
| `434.398166` | BAR2/HOST_CPU read PTE fault at `0x377000`, channel `-1` / unknown | `+2.339871 s` |
| `473.120117073` | Decoder destruction begins | `+41.061822 s` |
| `473.120942082`–`473.128230169` | BSP/chid 3 channel free | `+41.062648`–`+41.069936 s` |
| `473.128627715` | VP/chid 4 channel-free call begins | `+41.070333 s` |
| `473.129053` | VP idle fence seq 3007 emitted at `0x14040`; prior value `0xbbd`; kick called | `+41.070758 s` |
| `473.130166` | VP fence wait begins; observed value `0xbbd` | `+41.071871 s` |
| `473.143037` | VP fence reaches target `0xbbf`; wait result 0, 14 jiffies | `+41.084742 s` |
| `473.144392714` | VP/chid 4 channel-free call ends; duration 15.765 ms | `+41.086098 s` |
| `473.144413548` | PPP engine-object destruction begins | `+41.086119 s` |
| `473.145027` | BAR2/HOST_CPU read PTE fault at `0x388000`, channel `-1` / unknown | `+41.086732 s` |
| `473.146173485` | PPP engine-object destruction ends | `+41.087879 s` |
| `473.147030`–`473.147888` | PPP/chid 5 idle fence seq 3007 reaches `0xbbf`; wait result 0 | `+41.088735`–`+41.089593 s` |
| `473.149213786` | Decoder destruction ends; total 29.097 ms | `+41.090919 s` |
| `473.267709911` | FFmpeg exits 0 | `+41.209415 s` |
| `488.425711544` | Journal follower stops after 15 s post-exit tail | `+56.367417 s` |

There was no `CTXSW_TIMEOUT`, `SCHED_ERROR`, channel kill, or channel-idle
failure in this kernel capture. In particular, VP's idle semaphore advanced
from `0xbbd` to its target `0xbbf`; the previous non-serialized capture had
stopped at `0xbbd` and waited for 15 seconds while the kernel recovered PPP
chid 5. In this candidate, VP's complete channel-free call took about 15.8 ms,
and PPP destruction began only after VP channel-free returned.

This is a strong one-run A/B result for the teardown-order hypothesis: with
the same diagnostic kernel, native-surface correction, and input, changing
the Mesa distinct-channel destructor order coincided with the VP fence
completing promptly and the prior MSPPP timeout/recovery disappearing. The
runs were on separate boots, and this candidate has only one hardware sample,
so serialization is strongly supported as the cause of the long VP wait, not
yet proven across repetitions. Keep the Mesa change as a private candidate;
the incremental build used for this A/B is not a clean-build provenance
artifact for an upstream submission.

### Remaining BAR2/PTE fault

The serialized capture still has two BAR2 faults, and they are not simply
downstream of a context-switch timeout: this kernel log contains no such
timeout or channel recovery. The first fault occurred about 2.34 seconds into
active decoding, before decoder teardown and 9.085 ms after the recorded
chid 3 idle-fence completion. The second occurred about 0.613 ms after PPP
engine-object destruction began, before PPP channel-free began. Their ordering
does not establish which operation caused either fault, or whether the two
faults share a cause.

The exact Ubuntu `linux-source-7.0.0` package for `7.0.0-34.34` explains what
the record does and does not identify:

- `nvkm/engine/fifo/gk104.c` maps fault engine `0x05` to BAR2 / instance
  memory.
- `nvkm/engine/fifo/gf100.c`, in `gf100_fifo_mmu_fault_recover()`, handles a
  BAR2 fault by calling `nvkm_bar_bar2_reset()` before logging the fault.
  It records the hardware-reported address, HOST_CPU client, access and PTE
  reason, plus the instance address.
- `nvkm/engine/fifo/chan.c` and `runl.c` look up the instance address against
  live channel instance objects. A missing match produces channel `-1` and
  name `unknown`, as seen for both addresses (`0x00ffbb7000`).

Thus **observed**: both are BAR2 HOST_CPU read PTE faults which the driver could
not associate with a live Nouveau channel instance; the existing recovery
path resets BAR2. **Not established**: which BAR2 mapping or caller accessed
`0x377000` / `0x388000`, whether the first reset contributes to the second
fault, and whether either fault affects decode output. Their channel `-1`
status does not prove a kernel CPU caller or identify an owning allocation.
The BAR2 issue remains unresolved and must be kept separate from the now
strongly supported VP/PPP teardown-order result.

### Exact GK104 BAR2 mapping path

Further inspection of that same Ubuntu source package establishes the BAR2
address-space path used by GK104:

- `nvkm/engine/device/base.c`, `nve4_chipset`, selects
  `.imem = nv50_instmem_new` and `.bar = gf100_bar_new` for GK104.
- `nvkm/engine/device/pci.c:nvkm_device_pci_resource_idx()` maps
  `NVKM_BAR2_INST` to the PCI resource after BAR0 and BAR1. On this K4200,
  `/sys/bus/pci/devices/0000:01:00.0/resource` shows that resource as
  `0xde000000..0xdfffffff`, a 32 MiB BAR. The fault offsets `0x377000` and
  `0x388000` are inside that aperture, not beyond its bounds.
- `nvkm/subdev/bar/gf100.c:gf100_bar_oneinit_bar()` creates a BAR2 VMM from
  address zero with the PCI BAR2 size and joins it to instance memory.
- `nvkm/subdev/instmem/nv50.c:nv50_instobj_kmap()` allocates a 4 KiB-granular
  VMA in that BAR2 VMM, maps an instance-memory object into it, and creates a
  CPU `ioremap_wc()` at `BAR2 physical base + VMA address`. The fast instance
  memory read/write accessors use `ioread32_native()` and
  `iowrite32_native()` through that mapping. Unused mappings enter an LRU and
  can later be evicted; object destruction unmaps the CPU mapping and VMA.
- `nvkm_bar_bar2_reset()` in `nvkm/subdev/bar/base.c` calls the BAR2 init and
  wait hooks. GK104's GF100 BAR2 init rewrites the BAR2 base register from the
  BAR2 instance-memory object. That reset path does not itself rebuild the
  individual instance-object VMA mappings.

This makes the NV50 instance-memory CPU mapping a **strong candidate path** for
the HOST_CPU BAR2 faults: it is the GK104 implementation that directly maps
instance objects through BAR2 for CPU reads and writes, and the reported fault
offsets lie within its aperture. It is not yet proof that either captured
fault came from `nv50_instobj_rd32()` or `nv50_instobj_wr32()`, nor that a
mapping was stale or absent. The fault record's `inst` value and channel `-1`
do not identify the CPU-side object.

The next diagnostic should remain observational and separate from the
production DKMS path. An opt-in trace in `nv50_instobj_kmap()`, eviction, and
destruction should report VMA start/size, instance object pointer, backing
memory address/size/target, and map lifetime. Add narrowly filtered
before/after records in the fast read/write accessors when the computed BAR2
offset falls on the two observed pages (`0x377000` or `0x388000`). That would
show whether a live NV50 instance object owned either offset at access time and
which object operation coincided with the fault, without changing mapping,
fence, or recovery behavior. Because mappings may predate a userspace test,
the trace must be enabled before relevant instance-object mappings are
created, or it must use a separately validated safe snapshot mechanism; it
must not traverse the VMM tree without its required synchronization.

No Mesa package, DKMS module, or kernel image was installed or replaced for
this capture. The existing `0.1.13-diag1` DKMS module was the test baseline;
the Mesa candidate remained in its private prefix.

### BAR2 mapping/access diagnostic candidate

The standalone patch `patches/diagnostic/gk104-bar2-instmem-map-trace.patch`
adds the opt-in `diag_bar2_map` module parameter, default false. It traces
NV50 instance-memory mappings overlapping either fault page at map creation,
LRU eviction, and object destruction. It also records before/after 32-bit
reads and writes through the fast BAR2 accessors when their computed address
falls in either page. Records include the VMA/backing-object identity and are
rate-limited to eight total records per second. No mapping, read/write, fence,
or recovery behavior is changed.

Validation so far:

- The patch applies with `--fuzz=0` to the exact Ubuntu `7.0.0-34.34`
  `nv50.c` source preimage and produces the same file as the staged candidate.
- That changed `nv50.o` compiles against the installed
  `7.0.0-34-generic` kernel headers with `-j1`; the object contains the
  `diag_bar2_map`, `NOUVEAU_DIAG_BAR2_MAP`, and
  `NOUVEAU_DIAG_BAR2_ACCESS` markers and the diagnostic helper symbols.
- Patch SHA-256:
  `cb981c696c315cab8fd365f32b2e76b17f9d31c06c3248b604785649627cfa77`.
  The compiled `nv50.o` SHA-256 is
  `7ed1b029e19cd79e85fc07b7d1f605cd1ec0097fcff60c488f902a5d37d6743a`; its
  build log SHA-256 is
  `7eaacbcf3a44e25958a98804e2a3495e4a910eff9ccc2c68696e37e4174f536a`.
- A private full-module build then completed with one job using the same
  Ubuntu source package and the HPD/DDC, GK104 nonprivileged-context, legacy
  nonstall, and VP fence/CTXSW diagnostic patches from `0.1.13-diag1`, plus
  this BAR2 patch applied with zero fuzz. The conditional BAR2 marker block
  was added only to the private build-script copy; it is not yet part of the
  repository DKMS build script or normal installer.
- The resulting private module reports vermagic
  `7.0.0-34-generic SMP preempt mod_unload modversions`, srcversion
  `72DEE2B4ECFF77AD3764039`, and contains `diag_fence_wait`, `diag_ctxsw`,
  and `diag_bar2_map`. Its strings include the IDLE_FENCE, CTXSW, BAR2_MAP,
  and BAR2_ACCESS records, and `nm` shows the two NV50 diagnostic helpers.
  `nouveau.ko` SHA-256 is
  `8bce6d049a76676c1d93e7eae027c69517b2604bd21bac1fd68870708aa3e992`; the
  full build log SHA-256 is
  `cd2a8daa9791fdc1de14b2997658be768524dbd0dfb5401a307a60b246a55f14`.
- The full module build exited 0 with no undefined-reference, modpost, or
  compiler errors. The only notices were the kernel build's compiler-name
  alias and missing matching `pahole` version; the installed GCC version
  matched the kernel compiler version. That initial private artifact was not
  registered with DKMS or installed. It was subsequently reproduced through
  the committed `0.1.13-diag2` preparation/install flow; DKMS now reports
  `0.1.13-diag2` installed on disk at srcversion
  `72DEE2B4ECFF77AD3764039`. The live module remains `0.1.13-diag1` at
  srcversion `B19B8AAE48467545E652509` until the staged dracut boot is used.

At this checkpoint the packaging gate was complete: the separately versioned
`0.1.13-diag2` module is installed on disk with the expected srcversion. The
remaining runtime gate was a reboot using the current dracut image, which embeds
`options nouveau diag_bar2_map=1`; the host-side option file has been removed.
After reboot, require `tools/verify-bar2-map-diagnostic.sh post-reboot` to
pass before enabling `diag_fence_wait` and `diag_ctxsw` and running the single
serialized-teardown capture. Enabling `diag_bar2_map` only after Nouveau has
initialized is insufficient to capture the early mapping lifetime. The
completed runtime results are recorded below.

### Full-file diag2 result — 2026-09-30

The subsequent dracut boot loaded `diag2` (`72DEE2B4ECFF77AD3764039`) and
passed the post-reboot verifier. The private Mesa native-surface plus
serialized-teardown candidate was selected by its exact private path. One
3,000-frame capture and two full-file captures completed with zero decode
errors and no stop signature. Each full run decoded all 40,561 frames from
`/home/keivan/test_1080p.mkv` and exited 0; wall times were 534.72 and 535.15
seconds. The first full-run capture is
`/home/keivan/nouveau-vaapi-captures/20260930T083455Z-private-instrumented-13895`;
all four SHA-256 entries in `SHA256SUMS` verified.

The repeat full-run capture is
`/home/keivan/nouveau-vaapi-captures/20260930T085548Z-private-instrumented-16322`;
its four SHA-256 entries also verified. It used the same kernel srcversion and
the same pinned private Mesa plugin hash.

The Mesa trace continued to report correct native render-target dimensions
(`1920x544` and `960x272`) and emitted no PGRAPH PROP overrun. The kernel log
showed successful BSP, VP, and PPP idle-fence completion. VP chid 4's fence
wait advanced from `0xaa2e` to target `0xaa31` in 21 jiffies (18.502 ms from
the journal timestamps). Mesa's VP channel-destroy interval was 22.547 ms;
PPP's subsequent channel-destroy interval was 6.079 ms. The repeated run also
completed VP chid 4's fence successfully in 20 jiffies and completed the PPP
fence with result 0. There was no `CTXSW_TIMEOUT`, channel kill, failed idle,
or BAR2/PTE record in any of the three captures. The BAR2 mapping trace did record
the one-page `0x377000` mapping and successful writes during decoding, followed
by its destruction near process exit. It also recorded destruction of the
`0x379000`/`0x2a000` mapping at teardown, which spans `0x388000`; the
corresponding map creation was not present, and the diagnostic reported
suppressed callbacks.

This is strong hardware evidence that the native-surface and serialized VP3
teardown candidates support this complete H.264 file on the tested K4200
configuration over two consecutive full-file decodes in one boot. This is
stronger repeatability evidence, but it is not proof of reliability across
boots, other media, or other clients. The earlier BAR2 PTE faults remain
unexplained and were absent, rather than reproduced and mapped, in these
`diag2` samples. Preserve that issue as unresolved and separate from the
clear-state and VP/PPP teardown fixes.
