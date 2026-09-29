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
in this capture. This timing alone would only be correlation; the source call
chain below makes channel 4's identity stronger. Channel 5 remains a different
question.

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
`15.000427 s`. This is a very close match to the kernel's explicit 15-second
fence deadline, making the idle-fence wait a strong candidate for the long
interval. However, `drm_sched_entity_fini()` has a distinct completion wait
before the idle-fence call. This capture did not time those stages separately,
so it does not prove how much time was spent in either one. The prepared
function-graph trace should show the duration of `drm_sched_entity_fini()` and
the following `nouveau_channel_idle()` path separately.

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

The prepared function-graph trace measures how long the timeout handler and
channel-free path run; it does not capture the engine mask's contents. If
mapping chid 5 to a Mesa slot still leaves the initiating engine ambiguous,
the next diagnostic should be a narrowly scoped, log-only record at the
`engm` collection point containing runlist ID and each engine's ID/type and
context ID before recovery. It must not change recovery behavior.

## Channel 5 and patch 2

In the first userspace-only capture, the Mesa binary predated the channel
identity print, so the channel-5 kill could not be assigned to BSP, VP, or PPP
from that capture. The later function-graph capture includes the identity
records and supersedes that limitation: it maps channel 5 to PPP and channel 4
to VP, as detailed below.

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

## Existing tracing and prepared capture

Kernel `7.0.0-34-generic` has `CONFIG_FTRACE`,
`CONFIG_FUNCTION_GRAPH_TRACER`, and `CONFIG_DYNAMIC_FTRACE` enabled. Its loaded
Nouveau symbols include `nouveau_abi16_ioctl_channel_free` and
`nouveau_channel_idle`. Tracefs at `/sys/kernel/tracing` is root-only, so this
unprivileged inspection did not read `available_filter_functions` or change
tracefs state. The helper performs that availability check with privilege at
run time and refuses to start if an existing trace/tracer/filter is active.

[`tools/vaapi-channel-free-ftrace.sh`](../tools/vaapi-channel-free-ftrace.sh)
uses the existing function-graph tracer; no kernel module rebuild or
diagnostic kernel patch is needed when the runtime filter check succeeds. It
traces the synchronous Nouveau channel-free ioctl and, when traceable, the
CTXSW handler. It excludes the frequently polled `nouveau_fence_done()` leaf
from graph records to keep the trace bounded while retaining the enclosing
fence-wait duration. It selects the `mono` trace clock when available, runs
the existing VAAPI capture as the invoking desktop user, attaches
`function-graph.log` and its metadata to that capture, updates `SHA256SUMS`,
and restores tracefs settings. Before FFmpeg, it verifies the private plugin's
symlink and SHA-256 (`defee11f...b56268f`); afterward it records whether the BSP,
VP, and PPP identity lines all appeared. It aborts rather than replacing a
running tracer or clearing a non-empty trace buffer. Use `pkexec` for the same
helper if `sudo` is unavailable in the current privilege context.

The Mesa channel-ID diagnostic was built into a new private prefix only:

```text
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-channel-id-trace/prefix/lib/x86_64-linux-gnu/dri
```

Its `libgallium_drv_video.so` SHA-256 is
`defee11fcedabc2b81730bfcc0d8d425133cccc76751e5c17652a9420b56268f`; the
pre-instrumented native-surface candidate remains unchanged at its original
prefix. The new build contains the existing native-surface fix plus
opt-in-only identity prints. It has passed a private compile, marker check,
`ldd` check, and source regression check; it has **not** been run on the GPU.

Do not run another same-boot GPU test after the prior harness set
`STOP_A_B=1`. After rebooting into the existing kernel and DKMS module, verify
that baseline and run exactly one capture:

```bash
sudo reboot
```

After reconnecting, verify `uname -r`, `modinfo -n nouveau`, and matching
`/sys/module/nouveau/srcversion` / `modinfo -F srcversion` as before. Then run:

```bash
cd ~/nouveau-hpd-ddc-dkms
pkexec "$PWD/tools/vaapi-channel-free-ftrace.sh" \
  /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-channel-id-trace/prefix/lib/x86_64-linux-gnu/dri
```

Use `pkexec` in this environment; the user's shell has previously run with
`NoNewPrivs`, which prevents `sudo` from starting the helper.

Use only the existing `7.0.0-34-generic` plus installed DKMS `0.1.13`
baseline. Do not install this Mesa prefix system-wide, rebuild/reinstall
DKMS, or perform another same-boot comparison if the capture reports
`STOP_A_B=1`.

## Confidence

- **PROVEN from source and synchronous trace brackets:** channel 4's failed
  idle is emitted while freeing Mesa's VP channel in this capture.
- **STRONGLY SUPPORTED by source and timing:** the 15.001695-second VP
  channel-free interval is dominated by the explicit 15-second Nouveau idle
  fence wait.
- **UNRESOLVED:** which decoder channel is chid 5, which engine's context
  switch timed out, and whether the timeout causes the idle fence not to
  complete.
- **UNSUPPORTED as a direct cause:** patch 2/nonstall event indexing causing
  this non-lazy 15-second busy-poll timeout.
- **No functional kernel fix or kernel diagnostic patch was created.**
