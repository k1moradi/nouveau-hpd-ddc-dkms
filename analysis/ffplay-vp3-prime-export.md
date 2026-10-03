# ffplay DRM PRIME export of Nouveau VP3 surfaces

Status as of 2026-10-01: **export failure mechanism confirmed; functional export fix not started**. This note is separate from the mpv decoder-recreation investigation.

## OBSERVED

The K4200's VP3 decoder creates and decodes H.264 VA surfaces. In the targeted ffplay GDB capture, FFmpeg called `vaExportSurfaceHandle()` for a decoded surface with DRM PRIME 2 and separate layers. Mesa reached the actual VP3 surface and returned `VA_STATUS_ERROR_INVALID_SURFACE` at the explicit `surf->buffer->interlaced` guard, before calling `resource_get_handle()`. The backtrace continued through FFmpeg's `av_hwframe_map()` path. No frame reached ffplay's Vulkan renderer, matching the blank video window.

Captured surface layout:

```text
VASurfaceID 1; VA format NV12; logical size 1920x1088

Y resource:  R8,  array_size=2; each layer 1920x544
UV resource: R8G8, array_size=2; each layer 960x272
```

Evidence is in `/home/keivan/nouveau-vaapi-app-validation/ffplay-interlaced-rejection-probe-v4-20261001T082058Z/`; the frozen probe SHA-256 was `e81fc29ed5014afc4934aedb667fef9f03fc6fb266aaf962f100b83c72f0871e`.

A BAR2/HOST_CPU/PTE fault occurred in that boot, but the GDB trace proves the VA exporter rejects the surface before resource export. The BAR2 fault is not established as the cause of this export rejection.

## SOURCE INVARIANT

In Mesa 26.0.8, `src/gallium/drivers/nouveau/nouveau_vp3_video.c` has a special NV12 video-buffer implementation. It requires the VP3 template to be interlaced, marks `pipe_video_buffer.interlaced = true`, and allocates two array layers for each of the Y and UV resources. `get_surfaces()` exposes those as four field views in order: Y layer 0, Y layer 1, UV layer 0, UV layer 1.

The generic VA exporter in `src/gallium/frontends/va/surface.c` rejects interlaced buffers before enumerating those views. That rejection is protective. Upstream libva's DRM PRIME 2 contract says `VA_EXPORT_SURFACE_SEPARATE_LAYERS` exports NV12 as two layers, one `DRM_FORMAT_R8` and one `DRM_FORMAT_GR88`. Removing the guard alone would let Mesa describe four field views as layers; that is not the promised two-layer NV12 representation.

Relevant API reference: [libva `va_drmcommon.h`](https://github.com/intel/libva/blob/master/va/va_drmcommon.h), `VADRMPRIMESurfaceDescriptor` and the separate-layer description.

## CONFIRMED FAILURE

```text
Nouveau VP3 decode
    -> field-array NV12 video buffer (`interlaced=true`)
    -> FFmpeg requests DRM PRIME 2 separate-layer mapping
    -> Mesa interlaced guard returns INVALID_SURFACE
    -> av_hwframe_map() fails
    -> ffplay's Vulkan renderer gets no frame
```

The runtime breakpoint captured the taken rejection branch for the actual decoded surface. This is stronger than inferring the branch from a generic status-6 error.

## REQUIRED REPRESENTATION

The exported descriptor must describe one full-size progressive NV12 image with exactly the layout requested by the consumer: a Y plane and an interleaved UV plane, not four half-height field views.

The likely image reconstruction is a field weave into a progressive resource: place corresponding Y rows from field 0 and field 1 on alternating output rows, and do the corresponding operation for UV rows. The exact field parity/order, chroma siting, and whether any vertical adjustment is required must be established from the VP3 decoder's writes and checked against a software-decoded reference. Do not assume layer 0 means top/even without evidence. Concatenating all rows from one array layer followed by all rows from the other is not accepted as proof of a correct progressive frame.

The same-format NV12 path in `vlVaGetImage()` is a useful source reference for the reconstruction. For each array layer `j`, it copies to `data[plane] + pitch * j` with destination stride `pitch * array_size`. With `array_size == 2`, that places layer 0 on even destination rows and layer 1 on odd destination rows. This is a row weave, not layer concatenation. It strongly supports the required mapping, but does not by itself establish VP3 layer parity for every frame. A later 300-frame hardware `hwdownload` comparison found 270/300 frame MD5s matching software; selected frames 41 and 200 differed by at most one luma level in 7 and 2 bytes respectively, with exact chroma, and the frame-200 converted images were byte-identical. This is useful pixel evidence for those samples, not proof of all-frame correctness or a fix for visible mpv corruption. When formats differ, `vlVaGetImage()` instead allocates a temporary surface and uses `vlVaPostProcCompositor()`.

Mesa's compositor has YUV progressive/deinterlace paths (`vl_compositor_yuv_deint_full()` and `set_yuv_layer()`), which are a candidate for a GPU-side staging conversion. Their behavior on the Nouveau VP3 array layout and the destination allocator's ability to provide a genuinely progressive NV12 resource still need proof. Do not assume `vlVaHandleSurfaceAllocate()` can create one: the Nouveau NV12 constructor currently asserts that its template is interlaced. A GPU staging path must preserve the row-weave mapping above without falling back to CPU readback.

## OWNERSHIP

**Candidate design:** create a distinct progressive staging allocation for each export operation, fill it from the decoded VP3 source, and export that allocation. A per-export snapshot avoids overwriting a cached staging image while an older external consumer still reads it.

The staging resource must remain valid at least until `resource_get_handle()` has returned a DRM PRIME FD that independently references the backing allocation. FFmpeg retains the descriptor FD with its mapped DRM frame and closes it when the map is released; see upstream [FFmpeg `hwcontext_vaapi.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavutil/hwcontext_vaapi.c), `vaapi_map_to_drm_esh()` and `vaapi_unmap_to_drm_esh()`.

The backing-allocation lifetime question is now source-verified for a successful DRM PRIME FD export. In the exact Ubuntu Linux source package `7.0.0-34.34` (archive SHA-256 `a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`), Nouveau's `nouveau_gem_prime_export()` calls `ttm_bo_setup_export()` and then `drm_gem_prime_export()`. The generic GEM exporter creates the dma-buf and takes references to both the `drm_device` and exported `drm_gem_object`; its release callback drops those references when the dma-buf is finally released. `drm_gem_prime_handle_to_fd()` installs the dma-buf's file as the returned FD. Thus, after a successful export, the dma-buf file holds the backing GEM/TTM allocation alive independently of the Gallium resource's own reference, through VA-surface destruction, until the last reference to that dma-buf file is released.

The Mesa Nouveau winsys FD-handle path reaches `drmPrimeHandleToFD()` through `nouveau_bo_set_prime()`. This confirms the lifetime property needed for a distinct per-export staging allocation, provided the exporter transfers and closes descriptor ownership correctly on every success and partial-failure path. It does **not** establish that the staging contents stay unchanged if the allocation is subsequently reused or written. The initial design therefore remains a fresh, non-reused staging BO per export while any consumer may hold its FD.

This source result does not order the staging GPU copy before a consumer reads the exported FD. The copy still needs a proven completion wait or a verified implicit-synchronization path before the descriptor is returned. PRIME backing-object lifetime is now source-proven; copy completion, exported-content immutability, actual VA-surface destruction with a live exported FD, and runtime staging/export behavior remain untested.

The reviewed kernel source files were `drivers/gpu/drm/nouveau/nouveau_prime.c`, `drivers/gpu/drm/drm_prime.c`, and `drivers/dma-buf/dma-buf.c` from the exact Ubuntu source archive above. Their extracted SHA-256 values are `15a8ed1665acde131a64d1c4608c599f9e407c8af88c96ba6654b496242245c5`, `2de6e43be39d86c5ff27f594b65dbfc34d166c1e1bf118593353b170dde1b09e`, and `28585e06581e2403362a78dd63c75e340c5d7e72492be90ca6de030db0476d67`, respectively. Mesa's winsys FD export was cross-checked in upstream commit [`96c11b68e7f479ecbdc715ab0f28fa0113e09e28`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/96c11b68e7f479ecbdc715ab0f28fa0113e09e28/src/gallium/winsys/nouveau/drm/nouveau.c); that source snapshot is a source reference, not a runtime test of the pinned installed DSO.

A staging cache attached to `vlVaSurface` is not the initial design: the exported FD can outlive that VA surface, and the driver has no demonstrated callback saying all external imports of a cached staging BO have finished. Reusing one cached BO could therefore overwrite a frame still in use. Reconsider caching only if the export/import lifetime and reuse contract can be proven.

## SYNCHRONIZATION

For read mapping, FFmpeg's VAAPI-to-DRM path calls `vaSyncSurface()` before `vaExportSurfaceHandle()`; this orders decoder writes before the exporter reads the VP3 source. This is visible in upstream [FFmpeg `hwcontext_vaapi.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavutil/hwcontext_vaapi.c), `vaapi_map_to_drm_esh()`.

A staging copy created *inside* `vaExportSurfaceHandle()` is later work than that source sync. The pinned VA frontend has an explicit Gallium completion pattern: `vlVaSurfaceFlush()` calls `drv->pipe->flush(drv->pipe, &surf->pipe_fence, ...)`, and `_vlVaSyncSurface()` waits with `screen->fence_finish(screen, NULL, surf->pipe_fence, timeout_ns)` before releasing the fence reference. The VA compositor and its state are initialized against `drv->pipe`, and the protected-context switch recreates both against the replacement `drv->pipe`. A staging exporter can therefore use a local fence returned by a flush of that same active pipe after the weave, wait for completion with `fence_finish`, and release the fence on success and failure before returning the PRIME descriptor. This is a concrete source-supported synchronization design; it removes the need to rely on undocumented implicit synchronization for that completed copy.

The implementation still has to check all API returns and handle a missing/failed fence without exporting the descriptor. A pipe flush without waiting is insufficient. This source audit does not prove the actual compositor copy path, its output bytes, the exporter resource format/modifier, or runtime fence behavior on the K4200. Those remain validation gates before accepting an implementation.

## REUSE

The native decode surface can return to the VA pool only after the source read/copy is correctly ordered against decode reuse. The progressive export snapshot must not be modified while an external consumer still owns/imports its FD. Destroying the source VA surface must not invalidate an already-exported snapshot. The test plan must deliberately hold the exported FD/import open while the decoder reuses the original surface, then verify the exported pixels remain unchanged.

## PATCH OPTIONS

1. **Progressive per-export snapshot (preferred candidate):** allocate an exportable progressive NV12 resource, convert/weave the field-array source into it, synchronize, then export its two-plane representation. This restores the API representation rather than suppressing the error.
2. **Direct descriptor encoding:** only viable if the VA DRM PRIME descriptor can represent the actual field storage as the consumer's requested two-plane NV12. The current two array layers per plane do not fit the documented separate-layer NV12 representation; no valid direct mapping has been identified.
3. **Keep rejecting unsupported exports:** remains correct until a representation with proven layout, ownership, synchronization, and reuse semantics exists.

No functional export patch has been written. In particular, the interlaced guard has not been removed.

## UPSTREAM CHECK (2026-10-02)

The VA frontend `surface.c` at Mesa commit
[`96c11b68e7f479ecbdc715ab0f28fa0113e09e28`](https://gitlab.freedesktop.org/mesa/mesa/-/blob/96c11b68e7f479ecbdc715ab0f28fa0113e09e28/src/gallium/frontends/va/surface.c)
(2026-09-02) still returns `VA_STATUS_ERROR_INVALID_SURFACE` when
`surf->buffer->interlaced` is true, before resource enumeration/export. The
upstream exporter commit
[`46bf88fb65e47fca213cdf17f9c7cf0f827c3e64`](https://gitlab.freedesktop.org/mesa/mesa/-/commit/46bf88fb65e47fca213cdf17f9c7cf0f827c3e64)
adds file-description checks to deduplicate exported plane FDs, but it does not
change that earlier rejection or weave field arrays into progressive NV12. It
therefore does not fix the observed VP3 export failure.

Upstream also replaced the VA frontend compositor with a post-processing
implementation and added codec references on surfaces/buffers for fence
wait/destruction in September 2026. Those are relevant source changes to review
for lifetime and staging design, but the current upstream export path still
rejects interlaced buffers. They do not provide a ready progressive VP3 export
fix or establish safe staging ownership/synchronization for this consumer.

## VALIDATION

Before a GPU player test:

1. Add a source-level test for the representation and descriptor shape; keep diagnostics out of functional code.
2. Use deterministic field-pattern input where each field and row is visibly distinguishable. Verify exact output row parity/order and UV placement against a software-decoded progressive reference; test chroma siting as well as luma.
3. Verify the copy fence completes before export becomes readable, including repeated export and error paths.
4. Verify FD ownership/closure, source VA-surface destruction, and source-pool reuse while an exported FD/import remains live. Check for leaks and stale/changed exported contents.
5. Only then run a short visible ffplay VAAPI+Vulkan test and require both advancing video frames and user-confirmed correct moving video. Follow with mpv; do not treat `hwdownload` success as a presentation fix.

## EXISTING GALLIUM WEAVE AND PROGRESSIVE ALLOCATION ROUTE (SOURCE-ONLY 2026-10-02)

A source-only review found a concrete existing Gallium route worth evaluating for a later staging prototype. It is a design lead, not a validated export fix.

`vl_video_buffer_create()` in `src/gallium/auxiliary/vl/vl_video_buffer.c` builds a generic video buffer directly from per-plane `pipe_resource`s using `pipe->screen->resource_create()`. For a progressive NV12 template (`interlaced=false`), it selects a 2D target and `array_size=1`; NV12 is represented as its ordinary Y and UV resource planes. Calling this generic helper directly would avoid the `pipe->create_video_buffer` callback that routes Nouveau NV12 to `nouveau_vp3_video_buffer_create()`, whose NV12 branch asserts that the template is interlaced. The resources would still be allocated by the same Nouveau screen, but their progressive layout and exportability must be checked at runtime.

The existing YUV compositor also has the required shape of operation. `vl_compositor_yuv_deint_full()` obtains destination plane surfaces, configures Y and UV passes, and renders each pass. With `VL_COMPOSITOR_WEAVE`, `set_yuv_layer()` selects the YUV weave shaders; both graphics and compute implementations sample 2D-array views and read both field layers. The VA post-processing path preserves its requested deinterlace mode when source and destination interlacing differ. This suggests a possible path:

```text
VP3 interlaced NV12 source
    -> generic progressive NV12 destination
    -> existing YUV WEAVE compositor
    -> fence completion
    -> DRM PRIME export of destination planes
```

The shader-coordinate source audit narrows the intended mapping. The graphics vertex shader scales luma coordinates by half the full source height and chroma coordinates by one quarter, then uses `+0.25` and `-0.25` field-line offsets. Its weave fragment shader samples array layers 0 and 1 and selects between them from the fractional row coordinate. For an identity, full-frame mapping, these equations imply layer 0 on even output rows and layer 1 on odd output rows, for both Y and UV. The compute weave shader expresses the same two field coordinates, explicit array-layer indices, and row-dependent blend. That is the same row parity as `vlVaGetImage()`'s CPU copy.

This establishes the compositor's intended source-level row mapping under those geometric assumptions; it does **not** prove the rendered GPU pixels, that every VP3 field is stored with the assumed spatial parity, or that chroma siting, crop/scale, pitch, and padding match the decoded progressive reference. A deterministic field-pattern test and frame-exact comparison against software NV12 are still required.

The existing DRM PRIME descriptor loop is structurally compatible with a generic progressive two-plane buffer. It iterates `get_surfaces()` until a plane resource is absent; with separate-layer export flags it creates one descriptor layer per encountered resource, each with one plane and its own object index. The generic progressive NV12 buffer exposes Y and UV resources followed by empty auxiliary slots, so the loop is expected to produce the two layers FFmpeg requested, provided both Nouveau resource handles and DRM format mappings succeed. On partial failure, the exporter closes object FDs already created. This is a source-level descriptor-shape argument, not an executed export result.

`vl_compositor_render()` dispatches graphics or compute work but does not itself wait for completion. Mesa's `_vlVaSyncSurface()` waits for the surface's pending pipe fence and then waits on the decoder fence before returning, so FFmpeg's pre-export `vaSyncSurface()` orders decoder writes before a staging read. The weave copy is new work issued after that synchronization; it needs its own supported completion fence/wait before the exported FD is returned to FFmpeg/Vulkan. `vlVaExportSurfaceHandle()` sets `drv->has_external_handles` only after successful descriptor construction; that flag makes later surface/decoder flushes non-async, but it does not prove completion of the staging copy currently being exported. Backing-allocation lifetime across a successful export is source-supported as described in OWNERSHIP; correct ownership handling on every partial-export path, content stability, and source-surface reuse remain open.

No code was changed by this review. Do not remove the `interlaced` guard or implement export staging until layout, synchronization, and exported-resource lifetime have been demonstrated.

## EXPORTABLE STAGING RESOURCE BIND (SOURCE-ONLY 2026-10-02)

The pinned Mesa VA frontend marks surfaces as shared when the application requests DRM PRIME 2/3 allocation without supplying an external descriptor: `vlVaCreateSurfaces2()` adds `PIPE_BIND_SHARED` to the video-buffer template because export is expected later. In the generic `vl_video_buffer_template()`, the resource bind is formed as `PIPE_BIND_SAMPLER_VIEW | PIPE_BIND_RENDER_TARGET | tmpl->bind`; `vl_video_buffer_create_ex()` passes those per-plane templates to `screen->resource_create()`. Therefore a generic progressive NV12 staging template must explicitly carry `PIPE_BIND_SHARED` if it is intended for PRIME export. Omitting that bind would depart from Mesa's existing VA exportable-surface allocation convention.

This bind also affects Nouveau allocation policy. In the reviewed NV50 miptree path, a linear resource with `PIPE_BIND_SHARED` is allocated in GART; in NVC0 the shared bind likewise selects GART when the resource has no block-linear storage type. The NVC0 handle method delegates to the NV50 GEM handle path and then reports the selected modifier. These source paths make `PIPE_BIND_SHARED` a necessary design input, but do not prove a particular progressive NV12 template, modifier, or pair of resource handles will be accepted/exportable on the K4200. A later prototype must check each resource creation and `resource_get_handle()` result and validate the returned descriptor's two-plane shape.

Reviewed source identities: pinned VA `surface.c` SHA-256 `5b961abc23314c119ea0d1633ffca0b9ff77ac4b392d0de8a32fe128e9c1753b`; Mesa 26.0.8 tag `vl_video_buffer.c` SHA-256 `0147d36208b53f1f92220f910b26d2964c7d3d6f349ec63e14f587bf7491bb04`; NV50 miptree SHA-256 `38e0a3299539a38213471f00cb0bc9a028206fd895e376f81a39203038df4a87`; NVC0 miptree SHA-256 `16ca564524b529401a12fb890862035a6fdbf3ba78f667a65152c098b6ef83fe`. The auxiliary `vl_video_buffer.c` was reviewed from the official Mesa 26.0.8 source snapshot because that file is absent from the partial local source cache.

No Mesa code was changed and no GPU allocation/export was attempted. This closes one source-design question only; progressive pixels, modifier/descriptor acceptance, synchronization on hardware, and exported-frame lifetime under consumer use remain runtime validation gates.

## YUV WEAVE COMPONENT-PRESERVATION AUDIT (SOURCE-ONLY 2026-10-02)

The open question about an RGB round trip is now source-resolved for the existing YUV-to-YUV weave path. `vlVaPostProcCompositor()` selects `vl_compositor_yuv_deint_full()` when both buffers are YUV. With `VL_COMPOSITOR_WEAVE`, `set_yuv_layer()` chooses `fs_yuv.weave.{y,uv}` or `cs_yuv.weave.{y,uv}`. These shaders sample the source Y/UV components and write those components to the destination planes. They do not invoke the separate YUV-to-RGB, RGB-to-YUV, primaries, or transfer-characteristic conversions used by the RGB presentation paths. An RGB color-space round trip is therefore not inherent to this YUV staging route.

There is a source-derived condition under which the weave should preserve component codes. For full-frame identity geometry (no crop, scale, rotation, or mirror), the graphics path projects each output luma row `r` into the half-height field texture at `r/2 + 0.25`; the shader's `+0.25` and `-0.25` field offsets produce candidate coordinates `r/2 + 0.5` and `r/2`. Its selector is `2 * abs(round(top_y) - top_y)`: it selects the first candidate for even output rows and the second for odd rows. The selected coordinate is the center of source row `floor(r/2)` in the corresponding array layer. The UV pass follows the same pattern at half the luma dimensions. The compute shader uses the same quarter-line offsets and row selector. Although the sampler state is linear, these selected texel-center coordinates imply no bilinear blend for this identity mapping. The graphics YUV shader writes the sampled Y or UV channels directly; its default layer-0 blend state has blending and dithering disabled. The compute YUV shader likewise selects sampled channels and image-stores them without a CSC step.

This narrows the design risk: **for exact identity geometry, the existing shaders are intended to weave stored Y/UV samples rather than convert through RGB or interpolate adjacent rows.** It is not runtime proof of byte-identical output on the K4200. Texture-coordinate precision, actual VP3 field parity, the real VA surface dimensions/crop (the observed surface is 1920x1088 while the stream display size is 1920x1080), and resource-format conversion still require deterministic pixel-pattern and reference-frame validation. With crop, scaling, or non-identity geometry, the linear sampler can interpolate and should not be assumed sample-preserving.

The staging caller must request `VL_COMPOSITOR_WEAVE` explicitly. In `set_yuv_layer()`, `VL_COMPOSITOR_NONE` has a compute-specific progressive shader branch, while the graphics path falls through to the YUV weave shader for an interlaced source. Relying on `NONE` would therefore make field-selection behavior depend on whether the compositor selected compute or graphics. The destination rectangle also needs to be reconstructed or copied before the compositor scales it for the UV pass, because `vl_compositor_yuv_deint_full()` mutates that rectangle in place.

The reviewed `vl_compositor.c`, `vl_compositor_gfx.c`, `vl_compositor_cs.c`, and `vl_video_buffer.c` are byte-identical between the functional Mesa 26.0.8 source tree and the retained clean 26.0.8 source snapshot. Their SHA-256 values are, respectively, `22169b77a760e86e05f32f40975a4ecc505bf29f055489a051700e4dd4c1ff9e`, `3544b2d297d6fa60968264f9b02409dc29ee0e49b42bb1c9337f5b2eec454f3d`, `98b176f97e15385bd602f7aa90cd3aaa0829b597db22f866482e5703f2b8c394`, and `0147d36208b53f1f92220f910b26d2964c7d3d6f349ec63e14f587bf7491bb04`. The reviewed VA `image.c` and `postproc.c` are also identical across those trees, with hashes `191b1ad01976fd71f53a89ad42129bfad7359533240a256c0425b4e6fcc2beb9` and `6ca76b96dbaabb457d91b4ed1f5faabc0d260adf1bde202373c6f6d8374e4787`. `surface.c` differs because the functional tree contains the retained VA surface changes; this review did not alter its exporter guard.

No functional source, module, package, initramfs, or branch was changed. No VA/GPU work was run. The YUV route now has source support for avoiding RGB conversion and, under identity geometry, selecting a single field sample per output row; actual pixels, K4200 execution, export descriptor, and lifetime remain unproven.
