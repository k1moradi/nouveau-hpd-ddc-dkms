# FFplay progressive-export candidate: synchronization and lifetime audit

## Scope

This is a read-only source audit of the pinned Mesa 26.0.8 candidate and the
Nouveau implementation in that same source snapshot. It is CPU-only evidence.
The candidate was not installed, loaded, or executed, and no GPU workload was
started. No functional fix is claimed.

## Timeout correction

The pre-patch-0002 candidate from patch 0001 called:

```c
screen->fence_finish(screen, NULL, fence, 5ULL * ONE_SECOND_IN_NS)
```

The Gallium `fence_finish` contract expresses its timeout in nanoseconds. In
this Mesa snapshot, however, `nouveau_screen_fence_finish()` only treats a zero
timeout specially (poll); for every nonzero timeout it calls
`nouveau_fence_wait()` without passing the requested duration. That path reaches
`nouveau_bo_wait()`, which submits `DRM_NOUVEAU_GEM_CPU_PREP` with flags `0`;
`NOUVEAU_BO_NOBLOCK` is the only path that adds `NOUVEAU_GEM_CPU_PREP_NOWAIT`.
There is no five-second deadline in this Nouveau path. Therefore the candidate's
five-second argument does not guarantee a five-second return bound.

The export caller holds `drv->mutex` from surface lookup through staging and
descriptor construction. Thus a delayed fence can serialize other operations
using this VA driver mutex for longer than the candidate comment previously
implied. The lock also protects the looked-up surface and shared compositor
state; simply dropping it around the wait would need a separate lifetime and
state audit. This note does not recommend unlocking it.

Source references from the pinned tree:

| File | SHA-256 | Relevant code |
|---|---|---|
| `src/gallium/frontends/va/surface.c` (after patch 0001, before patch 0002) | `ca7e3b5fbd1f0c915d0efb6143ee620a883a17507d2a90442ef2b512b516c3da` | helper requests 5s at lines 1468–1475; export holds mutex from 1525 to 1711 |
| `src/gallium/drivers/nouveau/nouveau_screen.c` | `a8ddee6c408ac58253a217e7cfa46a71796e514e9f89505d24872efa55ee8922` | lines 90–99; nonzero timeout goes to `nouveau_fence_wait()` |
| `src/gallium/drivers/nouveau/nouveau_fence.c` | `0e9c792177caba5e04f7d778948587d91d482467690335da7144c8ec8203c406` | `nouveau_fence_wait()` invokes `_nouveau_fence_wait()` |
| `src/gallium/winsys/nouveau/drm/nouveau.c` | `2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f` | lines 908–937; CPU_PREP uses no wait deadline and only adds NOWAIT when requested |
| `src/gallium/include/pipe/p_screen.h` | `0fdc1d82c68ab560f1c5c4ac0a901c98f358985d230f61d8e530adcca9c147d4` | lines 390–405; timeout is specified in nanoseconds |

## Decoder-to-compositor ordering

The candidate initializes `struct pipe_vpp_desc param = {0}` and does not set
`param.base.in_fence`. It passes the decoded `pipe_video_buffer` to
`vlVaPostProcCompositor()`, not the enclosing `vlVaSurface` that contains the VA
surface fence. The YUV compositor path in `vlVaPostProcCompositor()` submits
`vl_compositor_yuv_deint_full()` and does not consume `param->base.in_fence`.
The later `pipe_context::flush()` fence alone does not establish that the
decoder's writes completed before the compositor read. A separate native
Nouveau implicit-fence path does establish that dependency for the pinned
implementation.

The normal VA video-processing path assigns `vpp.base.in_fence` from the source
surface, but a Gallium compositor fallback still calls
`vlVaPostProcCompositor()`; the compositor implementation does not wait on that
field. The Nouveau VP3 decoder also does not install a codec `fence_wait`
callback in its common initializer. However, the decoder and compositor access
the same plane GEM objects through Nouveau pushbuffers:

1. `nvc0_create_decoder()` sets `dec->client = nvc0->base.client`. The VP3
   buffer stores two plane resources. `nvc0_decoder_setup_ppp()` adds both
   plane BOs to the PPP pushbuffer with `NOUVEAU_BO_WR | NOUVEAU_BO_VRAM`, and
   `nvc0_decoder_ppp()` kicks that pushbuffer.
2. The kernel's Nouveau GEM pushbuffer path creates a fence for that submission
   and `validate_fini()` adds it to each referenced BO's `dma_resv` as a WRITE
   fence when `write_domains` is set.
3. The weave compositor obtains sampler views from those same plane resources.
   NVC0 texture validation registers each underlying BO in the 3D pushbuffer as
   `NOUVEAU_BO_RD`.
4. Linux Nouveau `validate_list()` calls `nouveau_fence_sync()` before
   submitting that read. For a read, `dma_resv_usage_rw(false)` selects
   `DMA_RESV_USAGE_WRITE`, which includes the pending decoder WRITE fence.
   Nouveau inserts a channel dependency for a fence on the same DRM file when
   available; if it cannot use that path, it waits on the fence. This orders
   the compositor source read after the earlier PPP write to that BO.
5. NVC0 also marks the output planes `GPU_WRITING`. Texture validation emits a
   texture-cache flush before sampling and changes the resource status to
   `GPU_READING`.

This source chain supports decode-output-to-compositor-read ordering for the
native path where both accesses name the same GEM BO and retain the recorded
read/write domains. It does not depend on `param.base.in_fence`. It does not
test hardware execution or prove the internal VP3 BSP-to-VP-to-PPP pipeline,
field parity, or pixel correctness.

Relevant pinned hashes:

| File | SHA-256 | Relevant code |
|---|---|---|
| `src/gallium/frontends/va/postproc.c` | `6ca76b96dbaabb457d91b4ed1f5faabc0d260adf1bde202373c6f6d8374e4787` | compositor dispatch at lines 381–388; normal processing sets `in_fence` at 756 |
| `src/gallium/drivers/nouveau/nouveau_vp3_video.c` | `c9b2158ac53faaa408d1a67dc3c61945d4e4da1a8f851b324eccc503180dd5fa` | common initializer at lines 241–248 |
| `src/gallium/drivers/nouveau/nouveau_vp3_video.h` | `86d97313e8ae883186f1c389a67ebcb69ebe4e5089d55d094fac5ec21acb80bc` | codec base and decoder state |
| `src/gallium/drivers/nouveau/nvc0/nvc0_video.c` | `2c1662f23fb1afe0fd76c758e4779eee0e0ede8562938f7479e45dd3bec118a6` | NVC0 copies the codec template and installs common callbacks |
| `src/gallium/include/pipe/p_video_state.h` | `3076164814084c2a1b39f47a1160ba731df89876b3922eeb73a39508deb0fddb` | picture descriptor defines `in_fence` as a fence for decoder begin-frame to wait on |

The exact Ubuntu kernel source package is `7.0.0-34.34`, archive SHA-256
`a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`.
`nouveau_gem.c` is SHA-256
`c8e2d5e10c31eb869bc9e0496488df7904273c15232763ce5498a9d758ca616e` and
`nouveau_fence.c` is SHA-256
`37f989f57e26083d45d1ef688d42d23c104306bc242d7d1afbd461423947e72b` in both
the retained clean and diagnostic-enabled source trees. Installed matching
kernel headers package `7.0.0-34.34` provides `dma-resv.h` SHA-256
`f629666c24ad18fedc5c85c7c65169805b80fc83886b462fb58846ffbb2d27ee`.

## Bounded staging-output wait candidate

Patch `0002-use-bounded-nouveau-fence-poll.patch` now replaces the candidate's
nonzero Nouveau `fence_finish()` wait with repeated zero-timeout polls against a
monotonic deadline and a maximum 1 ms sleep interval. This avoids entering the
blocking Nouveau `nouveau_fence_wait()` path whose API has no timeout argument.
Source-contract tests require the zero-timeout call, deadline check, and
monotonic sleep helper, and reject the old `timeout_ns` call site. The helper
was subsequently compiled and linked in the Mesa plugin; it has not been
executed. Scheduler delays or contention on Nouveau's userspace fence lock can
still delay when the helper returns; it is a practical deadline loop, not a
real-time guarantee. Build commands and hashes are recorded in
[`BOUNDED-WAIT-PATCH-BUILD-20261003.md`](BOUNDED-WAIT-PATCH-BUILD-20261003.md).

The export call still holds `drv->mutex` during this loop and may serialize VA
operations for approximately the configured five-second interval. Do not treat
this as production-quality latency or concurrency behavior.

## Descriptor FD and staging-resource lifetime

On success, the candidate obtains the PRIME FD(s) through
`screen->resource_get_handle()` before destroying the staging video buffer.
Gallium documents that the caller takes ownership of FD handles returned by
`resource_get_handle()`. The Nouveau winsys implements the FD path through
`drmPrimeHandleToFD()`. This ordering is consistent with returning caller-owned
handles while releasing the temporary Gallium references. No specific FD
use-after-free is evident in the inspected source path. Repeated import/use,
surface reuse, and close ordering remain runtime-unvalidated.

The staging video buffer destructor releases its resource references. The VA
descriptor's PRIME FDs are separate handles; the Linux dma-buf documentation
describes the FD as the userspace reference to the shared buffer. See
[Linux dma-buf documentation](https://docs.kernel.org/driver-api/dma-buf.html)
and [libva DRM PRIME descriptor documentation](https://github.com/intel/libva/blob/master/va/va_drmcommon.h).

## Result and next discriminator

The source audit corrects the prior “waits up to five seconds” claim to
“requests five seconds, but the pinned Nouveau implementation does not enforce
that timeout." A source patch and regression checks now use deadline-based
zero-timeout polling. A later checkpoint compiled and linked it, but it has
not been executed. The native Nouveau implicit GEM-fence chain source-supports
the input dependency; no explicit VA `in_fence` is required for the same-BO
path audited here. Do not promote the candidate based on source analysis,
successful compilation, or successful export alone.

Before any functional claim, review the compiled patch 0002, then obtain
exact-frame NV12 comparisons, parity/chroma checks, repeated resource/FD reuse,
and visible playback on an approved clean desktop boot. This audit does not
authorize such a run.
