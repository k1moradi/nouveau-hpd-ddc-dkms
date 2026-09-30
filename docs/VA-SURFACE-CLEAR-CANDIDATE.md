# VA surface clear object mismatch and candidate Mesa correction

## Scope and safety

This checkpoint analyzes the private-instrumented capture from 2026-09-29 and
prepares a standalone Mesa userspace candidate. It does not change the working
GK104 video-context or legacy FIFO fixes. The candidate is not part of
`install.sh`, DKMS, or the kernel package. It has now been run once on the GPU
from its private prefix. No system Mesa package or Nouveau module was
installed or replaced for this work.

Repository base: `dc688643986bdb469000090d8fba54a6b26ef2b6` on
`review/gk104-vaapi-followup-20260928`.

## Capture identity and hashes

Capture directory:
`/home/keivan/nouveau-vaapi-captures/20260929T135604Z-private-instrumented-2063`.
The three files listed in `SHA256SUMS` verify successfully:

| File | SHA-256 |
| --- | --- |
| `kernel.log` | `f1b84dc09af4cf53d30173e4fdd6ac58389f616d601b14d04d0be7b48bc27162` |
| `ffmpeg.log` | `905513a2cf551ce89b8eda6dadca0c0dfa68a18f907cb9200d12ec451ed706af` |
| `transcript.txt` | `afa2c210e5ea72e175d8b3dc2bdf257fdab641f25b49314e0cbcc6993574c39c` |

The private driver path was selected and `va_openDriver()` returned 0. The
instrumentation emitted `hook=active`, nine `allocate-entry` records, nine
`SKIP_CLEAR_SURFACE=0` records, and 36 before/callback/after clear records.
FFmpeg exited 0 after 3000 output frames and 3002 decoded frames with zero
decode errors. Its final progress line reported 38.82 seconds elapsed; the
exact process lifetime from harness monotonic timestamps was 49.960562 seconds.
The kernel logger continued until 15.177 seconds after process exit. The
harness set `STOP_A_B=1` because the kernel log contains critical Nouveau
failures; no further GPU run was made in this boot.

Every timestamped VA trace record, render-target callback, GR/PROP trap,
FIFO timeout/recovery event, FFmpeg boundary, and logger boundary is listed
with offsets from FFmpeg start and exit in
[`evidence/20260929-private-instrumented-timeline.tsv`](evidence/20260929-private-instrumented-timeline.tsv).

## Clock alignment and combined timeline

Mesa uses `os_time_get_nano()`, implemented with `timespec_get(...,
TIME_MONOTONIC)`. The harness records the same host monotonic clock, and
`journalctl -o short-monotonic` records the kernel event on the matching
monotonic time base. No offset adjustment was applied. Their direct numerical
alignment is also consistent around the clear/trap sequence: the first
`RT_CLEAR` is at `389.417595153`, and the first kernel PROP record is at
`389.419832`, 2.237 ms later. During teardown, kernel timeout records fall
inside the corresponding Mesa channel-destruction intervals to within the
same millisecond clock scale.

FFmpeg started at monotonic `387.033178508` and exited at `436.993740717`.
The table gives every record's offset from both boundaries; the TSV contains
all individual clear and trap records.

| Monotonic time | Event | Since FFmpeg start | Relative to exit |
| ---: | --- | ---: | ---: |
| `389.417257228` | First VA video-surface allocation; 1920x1088, `Y8_U8V8_420_UNORM` | `+2.384079 s` | `-47.576483 s` |
| `389.417595153` | First `nvc0_clear_render_target()` trace, plane 0, invalid RT `81x1` for clear `1920x544` | `+2.384417 s` | `-47.576146 s` |
| `389.419057` | First kernel GR trap, channel 4 | `+2.385878 s` | `-47.574684 s` |
| `389.419832` | First PROP trap; startup cluster begins | `+2.386653 s` | `-47.573909 s` |
| `389.564019735` | Last startup allocation clear, plane 3 | `+2.530841 s` | `-47.429721 s` |
| `389.573072` | Last startup PROP trap; startup cluster ends | `+2.539893 s` | `-47.420669 s` |
| `428.209877654` | Last clear in completion-adjacent allocation, plane 3 | `+41.176699 s` | `-8.783863 s` |
| `428.211045` | Completion-adjacent GR trap begins | `+41.177866 s` | `-8.782696 s` |
| `428.212197` | Completion-adjacent PROP cluster begins | `+41.179018 s` | `-8.781544 s` |
| `428.218923` | Last completion-adjacent PROP trap | `+41.185744 s` | `-8.774818 s` |
| `428.264473491` | Mesa begins channel-object deletion for VP channel slot 1 | `+41.231295 s` | `-8.729267 s` |
| `432.552107` | FIFO `SCHED_ERROR 0a [CTXSW_TIMEOUT]`, runlist 1 | `+45.518928 s` | `-4.441634 s` |
| `432.552628` | Runlist 1, channel 6: `rc scheduled` | `+45.519449 s` | `-4.441113 s` |
| `432.552957` | Runlist 1 generic `rc scheduled` | `+45.519778 s` | `-4.440784 s` |
| `432.553318` | Runlist 1/channel 6: `errored - disabling channel` | `+45.520139 s` | `-4.440423 s` |
| `432.553666` | Channel 6 killed | `+45.520487 s` | `-4.440075 s` |
| `432.558199107` | VP channel-object deletion returns | `+45.525021 s` | `-4.435542 s` |
| `432.558471947` | Mesa begins channel-object deletion for PPP channel slot 2 | `+45.525293 s` | `-4.435269 s` |
| `436.848090` | FIFO `SCHED_ERROR 0a [CTXSW_TIMEOUT]`, runlist 2 | `+49.814911 s` | `-0.145651 s` |
| `436.848625` | Runlist 2, channel 7: `rc scheduled` | `+49.815446 s` | `-0.145116 s` |
| `436.848957` | Runlist 2 generic `rc scheduled` | `+49.815778 s` | `-0.144784 s` |
| `436.849317` | Runlist 2/channel 7: `errored - disabling channel` | `+49.816138 s` | `-0.144424 s` |
| `436.849636` | Channel 7 killed | `+49.816457 s` | `-0.144105 s` |
| `436.853225472` | PPP channel-object deletion returns | `+49.820047 s` | `-0.140515 s` |
| `436.853243297` | Mesa decoder destructor returns | `+49.820065 s` | `-0.140497 s` |
| `436.862118` | BAR2/HOST_CPU READ PTE fault at `0x46f000`, channel -1/unknown | `+49.828939 s` | `-0.131623 s` |
| `436.993740717` | FFmpeg process exits 0 | `+49.960562 s` | `0.000000 s` |
| `437.117622718` | Post-FFmpeg journal-tail interval begins | `+50.084444 s` | `+0.123882 s` |
| `452.170786119` | Journal follower stops | `+65.137608 s` | `+15.177045 s` |

The final FFmpeg progress line has FFmpeg's elapsed timer (`38.82 s`), not a
monotonic timestamp, so it is preserved separately in the TSV rather than
assigned a fabricated absolute time. The kernel timeout events occur after
the last PROP record, but the first timeout is inside VP channel deletion and
the second is inside PPP channel deletion. The second recovery and BAR2 fault
also occur before the exact FFmpeg exit.

## Exact Mesa 26.0.8 surface-layout finding

Source inspected: Mesa `26.0.8` from the Ubuntu source package
`26.0.8-1ubuntu0.3`. The upstream source archive SHA-256 is
`caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc`; the
Ubuntu delta does not modify these VA/VL/Nouveau source paths.

The A-G checks are established for the captured clear path:

1. **A — generic VL path: confirmed for this runtime surface.** The logged
   format is `PIPE_FORMAT_Y8_U8V8_420_UNORM`, not `PIPE_FORMAT_NV12`.
   `nouveau_vp3_video_buffer_create()` routes non-NV12 formats to
   `vl_video_buffer_create()`.
2. **B — plain descriptors: confirmed.** `struct vl_video_buffer` embeds
   `struct pipe_surface surfaces[VL_MAX_SURFACES]`. Its surface initializer
   assigns the resource, format, and first/last layer directly into those
   base objects.
3. **C — getter returns those objects: confirmed.**
   `vl_video_buffer_get_surfaces()` returns `&buf->surfaces[0]`.
4. **D — VA passes an array element directly: confirmed.**
   `vlVaHandleSurfaceAllocate()` calls
   `clear_render_target(pipe, &surfaces[i], ...)`.
5. **E — NVC0 expects its private derived type: confirmed.**
   `nvc0_clear_render_target()` casts `dst` using `nv50_surface(dst)`.
6. **F — private fields are read: confirmed.** It reads `offset`, `width`,
   `height`, and `depth` from the derived structure to program render-target
   address, extent, layer state, and push-space size.
7. **G — runtime values match the layout overlay: confirmed.** The generic
   `pipe_surface` fields occupy 32 bytes on this 64-bit build. The private
   `nv50_surface.width` begins 36 bytes from the object start, where a cast of
   an element in the plain array reads the next element's packed format
   field. Its private `height` at byte 40 reads that next element's
   `first_layer`; `depth` reads its `last_layer`. The luma/chroma sequence
   therefore yields `81x1`, `82x0`, `82x1`, then `0x0` from the zeroed
   following slot. Those exactly match the trace, while
   `pipe_surface_size()` reports the real `1920x544` and `960x272` extents.

This is a source-proven object-type/layout mismatch in the VA frontend clear
call. The emitted NVC0 clear uses those invalid private dimensions while its
clear rectangle is 1920x544 or 960x272. In the pre-fix capture, the first
kernel PROP traps followed the first clear by about 1.5–2.2 ms; the final
clear was followed by the last PROP cluster beginning 2.3 ms later. That made
the invalid clear state a strongly supported cause of that capture's
`RT_WIDTH_OVERRUN` / `RT_HEIGHT_OVERRUN` traps. The candidate A/B below now
supplies the predicted corrected-surface/no-PROP result, strongly supporting
that causal explanation in hardware.

## Scheduler timeout is a separate result

The destructor trace distinguishes short deletion of engine objects from the
long channel-object deletions:

| Mesa channel slot | Decoder engine label | Channel-object delete interval | Kernel recovery inside interval |
| ---: | --- | --- | --- |
| 0 | BSP | `428.256668233`–`428.264176163` (7.508 ms) | none |
| 1 | VP | `428.264473491`–`432.558199107` (4.294 s) | runlist 1/channel 6 timeout at `432.552107`; channel killed at `432.553666` |
| 2 | PPP | `432.558471947`–`436.853225472` (4.295 s) | runlist 2/channel 7 timeout at `436.848090`; channel killed at `436.849636` |

The two kernel timeouts and channel kills fall inside distinct, non-overlapping
Mesa VP and PPP channel-object deletion intervals. This makes channel 6 = VP
and channel 7 = PPP **strongly supported for this run by timestamp correlation**.
The kernel log does not print the Mesa channel pointer, so the mapping is not
directly source-proven. The FIFO records identify the runtime runlists as 1
and 2; GK104 source obtains runlist membership from runtime TOP data, so these
IDs are not universal static engine names.

The first timeout begins about 4.29 seconds into VP channel deletion; the
second begins about 4.29 seconds into PPP channel deletion. This strongly
localizes the stalls to channel teardown/context switching. It does not
establish why either engine fails to switch context. The PGRAPH clear problem
and the VP/PPP scheduler timeouts remain separate findings. The clear occurs
about 4.33 seconds before the first timeout, and timestamp order alone does
not prove it caused the timeout.

The BAR2/HOST_CPU PTE fault occurs 12.482 ms after channel 7 is killed and
131.623 ms before FFmpeg exits. Recovery-before-fault is observed; a secondary
BAR2 fault is plausible, but no causal relationship is proven. No channel-4
idle failure appears in this capture; that was present in the earlier
system-Mesa run, not this one.

The existing kernel nonstall patch changes fence/nonstall event indexing.
The capture does not implicate that event path in the scheduler's
`CTXSW_TIMEOUT` recovery. A fence-notification issue could still affect other
wait behavior, but no change to patch 2 is justified here. No kernel
diagnostic or functional kernel patch was created.

## Upstream history and candidate

The audited Mesa 26.0.8 VA path passes `&surfaces[i]` directly, and the NVC0
clear callback casts the pointer to `nv50_surface`. A bounded review of Mesa
GitLab history found no fix for this specific mismatch. Nearby commits
`b36fba19` (removing a surface object in `vlVaPutSurface()`), `2eb45daa`
(de-pointerizing `pipe_surface`), and `c18032ad` (changing the VA allocation
helper's parameters) do not change the allocation clear path. The current
`nvc0_resource.c` history also does not add a `create_surface` callback.
Related Mesa issues
[#13611](https://gitlab.freedesktop.org/mesa/mesa/-/work_items/13611) and
[#14058](https://gitlab.freedesktop.org/mesa/mesa/-/work_items/14058) contain
related Kepler/NVE4 VAAPI and `RT_*_OVERRUN` reports, but neither establishes
this object-layout cause or supplies a verified fix.

History references: [b36fba19](https://gitlab.freedesktop.org/mesa/mesa/-/commit/b36fba19),
[2eb45daa](https://gitlab.freedesktop.org/mesa/mesa/-/commit/2eb45daa),
and [c18032ad](https://gitlab.freedesktop.org/mesa/mesa/-/commit/c18032ad).

### Candidate implementation

The candidate is
[`patches/mesa/nvc0-create-surface-for-va-clear.patch`](../patches/mesa/nvc0-create-surface-for-va-clear.patch).
Source inspection found that the unmodified NVC0 context did **not** install
Gallium's optional `pipe_context.create_surface` callback. A VA-only change
calling that callback would therefore receive `NULL` on this GPU and fail
surface allocation. That first VA-only candidate was compiled in a separate
private directory during exploration, but it was not selected by the capture
harness or tested on hardware. It is not the candidate documented below.

The replacement registers the existing NVC0 and NV50 native surface
constructors directly as each context's `create_surface` callback. Both
constructors now set the returned surface's owning context; NVC0 also
registers the existing `nv50_surface_destroy()` destructor, while NV50
already had it. This covers both Nouveau clear callbacks, which cast
destinations to the same private `nv50_surface` type. Runtime evidence is
from NVC0 only; the NV50 addition is based on its matching source contract
and has not been hardware-tested.

In the VA frontend, each nonempty plane is converted from the generic
video-buffer descriptor through `pipe->create_surface()` when the callback is
available. NVC0 and NV50 now supply that callback, so the clear receives a
real driver-owned `nv50_surface` with valid offset, extent, depth, format,
mip level, and first/last layer. The VA frontend releases the owned surface
with `pipe_surface_reference()` after the clear. Drivers without a create
callback retain their prior direct-clear path.

If a driver callback exists but surface creation fails, the VA frontend
destroys and clears the newly created video buffer and returns allocation
failure without issuing that plane's clear. This is an allocation error, not
a silent fallback to the unsafe generic descriptor. The opt-in trace records
both the native surface and source descriptor pointers. The existing NVC0
trace then reports dimensions from the real derived surface.

The patch is based on the exact Mesa 26.0.8 source after applying the
timestamp diagnostic patch. Its five-file diff passes strict apply and reverse
checks with `patch --fuzz=0`. Because the candidate hunks intentionally use
minimal context, apply them with the fail-closed helper, which verifies the
five exact diagnostic-patched preimage hashes before applying:

```bash
tools/apply-mesa-va-clear-candidate.sh <mesa-source-root>
```

The helper uses `patch --fuzz=0` and then runs the source regression check.
That check verifies both callback registrations, native constructor use,
context ownership/destruction, the VA clear argument, reference release
ordering, and preservation of template format/level/layer metadata and
derived width/height/depth/offset. It can also be run separately after
application with `python3 tests/test-mesa-va-clear-surface.py <mesa-source-root>`.

Validation reconstructed a diagnostic-only preimage from a private build-tree
copy, reversed the candidate patch with zero fuzz, and then ran the helper.
All five preimage hashes matched, the strict dry run had no offsets, application
succeeded, and the source regression check passed. Running the helper against
an already candidate-patched tree was rejected at the hash gate, as intended.

The candidate and diagnostic plugin were compiled privately with Ninja `-j1`
and copied to a separate user-owned prefix:

```text
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-candidate/prefix/lib/x86_64-linux-gnu/dri/nouveau_drv_video.so
```

`nouveau_drv_video.so` resolves to that prefix's
`libgallium_drv_video.so`. Candidate plugin SHA-256:
`512a7b524fca0e403540c1223c0b6f106406fb3b418de6417972bc352f02ef61`.
The clear-surface creation/failure, timestamp, allocation, and NVC0 clear
diagnostic markers are present; `ldd` reports no missing dependencies. The
candidate remains private; it is not installed system-wide or included in
DKMS. The initial static checkpoint preceded its first hardware run, recorded
below.

The first candidate A/B used this private DRI directory after a fresh boot:

```bash
tools/vaapi-capture.sh private-instrumented \
  /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-candidate/prefix/lib/x86_64-linux-gnu/dri
```

## Private candidate A/B result — 2026-09-29

Capture directory:
`/home/keivan/nouveau-vaapi-captures/20260929T164635Z-private-instrumented-23623`.
The captured `SHA256SUMS` verifies:

| File | SHA-256 |
| --- | --- |
| `kernel.log` | `264ada0f829490fc900e25a8c351719dfadcdc9eaff74073387ce3e3d73673a8` |
| `ffmpeg.log` | `3fca5b1efb1cc5047153d28ec72b7d0e02116923057dedcd43d6520847ff8bf6` |
| `transcript.txt` | `63271c2266cc623aa3e327ec43756ed5f55d49921b872caf4d25aafebd87cc6f` |

The harness verified the private plugin path, `va_openDriver() returns 0`,
kernel 7.0.0-34-generic, and matching loaded/on-disk Nouveau `srcversion`
`57AE1B168D50DB546CD87A1`. The opt-in hook ran. It recorded nine surface
allocations with `SKIP_CLEAR_SURFACE=0`. The selected `clear_render_target`
callback pointer was `0x7d1ec44f2760`; all 36 calls entered
`nvc0_clear_render_target()`. The only RT dimensions were the correct
`1920x544` and `960x272` plane extents, including the two array layers. There
were **no GR/PROP traps** in the kernel capture. This is a strong single-run
A/B confirmation that supplying native Nouveau surfaces fixes the observed
`RT_WIDTH_OVERRUN` / `RT_HEIGHT_OVERRUN` clear-path failures.

FFmpeg completed 3000 output frames, decoded 3002 frames with zero decode
errors, and exited 0. The final progress timer was 38.73 seconds; wall time was
56.28 seconds. The exact process interval was monotonic `8381.104569726` to
`8437.240749893` (56.136180 s). The logger stopped at `8452.428219108`,
15.187469 seconds after process exit. The full clear/teardown/kernel timeline
is in
[`evidence/20260929-private-native-surface-fix-timeline.tsv`](evidence/20260929-private-native-surface-fix-timeline.tsv).

The scheduler failure remains, with a different shape from the preceding
unfixed-surface run:

| Monotonic time | Event | Since FFmpeg start | Relative to process exit |
| ---: | --- | ---: | ---: |
| `8422.106624806` | VP channel-object deletion begins | `+41.002055 s` | `-15.134125 s` |
| `8426.401149` | Runlist 2 `CTXSW_TIMEOUT` | `+45.296579 s` | `-10.839601 s` |
| `8426.458191` | Runlist 2/channel 5 recovery scheduled | `+45.353621 s` | `-10.782559 s` |
| `8426.458968` | Channel 5 disabled | `+45.354398 s` | `-10.781782 s` |
| `8426.459304` | Channel 5 killed | `+45.354734 s` | `-10.781446 s` |
| `8437.107052` | Channel 4 failed to idle | `+56.002482 s` | `-0.133698 s` |
| `8437.108320209` | VP channel-object deletion returns | `+56.003750 s` | `-0.132430 s` |
| `8437.108571165` | PPP channel-object deletion begins | `+56.004001 s` | `-0.132179 s` |
| `8437.112158734` | PPP channel-object deletion returns | `+56.007589 s` | `-0.128591 s` |
| `8437.240749893` | FFmpeg exits 0 | `+56.136180 s` | `0.000000 s` |

The VP deletion blocks for `15.001695 s`. Its context-switch timeout occurs
`4.294524 s` after deletion begins, and the channel is killed `4.352679 s`
after deletion begins; the userspace deletion call then remains blocked for
about another `10.649 s`. The channel-4 idle error occurs 1.268 ms before the
VP deletion returns. The PPP deletion takes 3.588 ms and has no matching
timeout. Source tracing shows the failed-idle record is printed inside the
synchronous channel-free ioctl for the VP slot, so channel 4 is the VP channel
in this run. Runlist 2/channel 5's decoder slot remains unknown; channel
numbers are runtime identities, not fixed engine identities. The source chain
and next trace procedure are recorded in
[`VP-CHANNEL-TEARDOWN-TRACE.md`](VP-CHANNEL-TEARDOWN-TRACE.md).

There is no BAR2/PTE fault in this capture. The scheduler timeout and idle
failure occur with corrected RT dimensions and no PROP traps, so the two
failure classes are experimentally separated in this run. The reason for the
VP context-switch timeout remains unresolved. The harness set `STOP_A_B=1`;
do not run another GPU condition in this boot.

## Clean Mesa rebuild and full-file hardware validation — 2026-09-30

To close the provenance gap left by the earlier incremental candidate build,
Mesa 26.0.8 was reconstructed from the original Ubuntu source archive in a new
user-owned source/build/prefix directory:

```text
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-clean-20260930/
```

The archive SHA-256 was
`caf1c0061a68e88dfa74967a7e780c0e85d65b6c4e334cd69095a5dc54ad78bc`. The
four patches were applied in order with `patch --fuzz=0`:

| Patch | SHA-256 |
| --- | --- |
| `nvc0-rt-clear-diagnostic.patch` | `e01b17f7f5e7557b6ed87fcc88d306924c2e10a64a49bbb50a83f36b24a502b8` |
| `nvc0-create-surface-for-va-clear.patch` | `d6315f8762f72b6cc45ab73807a9f8577d56ce1d255b5cd0d60d20ae48ae300b` |
| `nouveau-vp3-channel-id-diagnostic.patch` | `390b0e08798e9c1330b3ac572eb3ee25955d9fa9fcaeb40bbe015f683689dc34` |
| `nouveau-vp3-serialize-channel-teardown.patch` | `adf179f8cc6d93c8329d8303a38c9a1b02d3c1ea52255791d35251af17c07840` |

Both focused source checks passed, and the clean private Mesa build completed
935/935 steps with exit 0. The built plugin's dependencies resolve, and the
driver symlink resolves to the plugin in this private prefix:

```text
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-instrumented-native-surface-fix-serialized-teardown-clean-20260930/prefix/lib/x86_64-linux-gnu/dri/libgallium_drv_video.so
SHA-256: e3d539f79241c95af838c2944937afbd6146b43d8ae7e8e378fc8723ae55f3e2
```

The clean build log SHA-256 is
`c8348042c481efa95c4d4d2dc960ef693ec522668d19bfe2825f484f4f9dfa69`.

The clean plugin is not byte-identical to the previously tested incremental
plugin (`d841244e592f171adebdbeaa556c97cb2e1bf639fceba81b0a829c9720b1119f`).
The source trees have matching content for all 11,514 non-generated files;
the eight earlier recursive-diff entries were generated Python bytecode only.
Meson configuration differs only in the private install prefix, and all 1,009
Ninja commands match after normalizing the two build roots. The old binary
contains its earlier `/private-instrumented/prefix` configuration paths;
the clean binary contains its new clean-build prefix. Consequently, the ELF
build ID, embedded path strings, and address-dependent loadable data differ. A
comparison of the `.text` disassembly found 1,533,836 instructions and no
instruction or non-address operand differences after normalizing PC-relative
address displacements and comments. The clean artifact was nevertheless
exercised directly below; it is not being treated as interchangeable by hash
alone.

The full-file capture used the current diagnostic kernel without changing or
reloading it, and selected only the clean private Mesa prefix. The input SHA-256
was `d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2`.
Capture directory:

```text
/home/keivan/nouveau-vaapi-captures/20260930T122151Z-private-instrumented-23681
```

The harness verified the exact private `nouveau_drv_video.so` path and
`va_openDriver() returns 0`. It ran with kernel `7.0.0-34-generic`, loaded and
on-disk Nouveau `diag3` srcversion `9F90A7EB5A9E1505E0B6708`, and all three
diagnostic parameters enabled. SDDM/display-manager was inactive at the start
and end. FFmpeg produced and decoded all 40,561 frames, reported zero decode
errors, and exited 0 after 535.24 seconds wall time. The journal logger
continued for the required 15-second post-exit tail; the harness reported
`STOP_A_B=0`.

The Mesa trace confirmed `SKIP_CLEAR_SURFACE=0`, eight allocation entries, and
32 clear calls. All 16 luma clears used `1920x544`; all 16 chroma clears used
`960x272`. No other RT dimensions were recorded. The teardown trace completed
in BSP, VP, PPP order. BSP chid 3, VP chid 4, and PPP chid 5 idle fences each
returned success; the VP fence completed in 19 jiffies. Mesa's three channel
object-idle intervals were about 3.590 ms (BSP), 22.979 ms (VP), and 6.991 ms
(PPP). Decoder destruction took about 36.97 ms. The current-run kernel log had
no PROP overrun, `CTXSW_TIMEOUT`, channel kill, failed idle, `PRIV_VIOLATION`,
SIGBUS, GPU reset, or BAR2/PTE fault.

The `diag3` lifecycle trace logged map and destroy records for both target
ranges: the one-page VMA at `0x377000` and `[0x379000, 0x3a3000)`, which covers
historical address `0x388000`. It recorded successful writes through the live
`0x377000` mapping. This run is another clean non-reproduction of the old
BAR2/PTE faults; it does not identify why they occurred historically.

Capture checksum verification passed:

| File | SHA-256 |
| --- | --- |
| `boot-kernel.log` | `e1b48606e51e8154a1e2ec0193b39635ba4c95fc4dae1175d78d45642e6c5997` |
| `kernel.log` | `006d1a5e0f6a535f21a20f34291b054f040ca262ca69a5318d6a6972dc8399fc` |
| `ffmpeg.log` | `750459efb3ce830061201c5f9f460818f1a999fa6d15b68acc5bd07d0679f753` |
| `transcript.txt` | `b8d366d049c300870262c22b51408ae7e20ca48bb66d425affc162ba81ab54ec` |

This completes the clean-source build-to-hardware validation for the
native-surface and serialized-teardown Mesa candidate on this K4200 and test
file. BAR2's historical root cause remains unresolved; the clean run shows
non-reproduction, not a BAR2 fix. No system Mesa package or kernel/module was
installed or replaced for this build or capture.
