# GK104 VP channel teardown trace

This is an offline source analysis and a prepared, opt-in trace procedure for
the remaining VP-channel teardown timeout. It does not change the working
video fixes, alter scheduling, or install anything. The private Mesa clear
candidate is frozen functionally; the extra Mesa change in this checkpoint
prints channel identities only when `NOUVEAU_DIAG_VA_SURFACE=1`.

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

This is a private review artifact only. It was not registered with DKMS,
installed, loaded, or hardware-tested. The running 0.1.13 module remains
unchanged and does not contain these parameters. Do not install this artifact
as a replacement for 0.1.13; any later deployment needs a separately versioned
diagnostic package and its own review. No scheduler backport, Mesa teardown
reorder, or functional kernel change is included.

If later approved for a controlled capture, enable the two parameters only on
the separately installed diagnostic module through
`/sys/module/nouveau/parameters/diag_fence_wait` and
`/sys/module/nouveau/parameters/diag_ctxsw`, then reset both to `0`. The next
hardware test should reuse the private native-surface Mesa candidate and keep
the existing functional kernel behavior.

## Current confidence

- **PROVEN:** Mesa channel identity is BSP chid 3, VP chid 4, PPP chid 5.
- **PROVEN:** the 15-second VP teardown stall is the Nouveau idle-fence wait.
- **PROVEN:** the timeout recovery targeted PPP chid 5 while VP chid 4 was
  waiting; this temporal overlap does not establish causation.
- **UNKNOWN:** which engine bit or bits formed `engm`, whether the VP semaphore
  release executed, and why the VP fence did not signal before its deadline.
- **UNRESOLVED:** whether the BAR2/PTE fault is primary or secondary.
- **UNSUPPORTED as a direct explanation:** patch 2's nonstall event index for
  this non-lazy busy-poll fence wait.
- **No functional Mesa teardown reorder or kernel recovery change was made.**
