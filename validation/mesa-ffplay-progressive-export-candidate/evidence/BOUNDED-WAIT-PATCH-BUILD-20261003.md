# Bounded-fence candidate target compile and plugin link

## Scope and result

Patch 0002 was applied to the pinned patch-0001 candidate, compiled as the Mesa
VA `surface.c` translation unit, inserted into a private copy of the candidate
VA archive, and linked into a private `libgallium_drv_video.so`. Compile and
link both exited 0 with empty diagnostic logs. This is a target compile and
plugin relink using the existing configured Mesa build; it is not a clean
full-Mesa rebuild. The DSO was not installed, loaded, or executed.

No VA/GPU workload, module operation, package installation, or reboot was
performed.

## Pinned inputs and outputs

| Artifact | SHA-256 |
|---|---|
| Patch 0002 | `6865f60a222043ddd2e31f5c8cfad84d4bd3c7dff588988a39eda03281d5d0e1` |
| Candidate `surface.c` after patches 0001 + 0002 | `dc84e79326ee5e546f296279e65eb9dc468080833de875b82391fba09c26706c` |
| Compiled `surface.c.o` | `f8746d843411d62ca384d2942280215422fb0f4b47b8258a4f37c875bf83b7a1` |
| Candidate `libva_st.a` | `e5f060df8d4e7779e1264b4c94df2c559feaa42b54dc1cec3661ad0dbb4809a1` |
| Candidate `libgallium_drv_video.so` | `53f4ef5e5a87257fbc0253acdd4dcb3869d3a93d9be86ab682a62475eaabd176` |
| Compile command record | `6c865648f0860d2d9891112d509a53b64ab93a3c720eb37642301c33bf160208` |
| Compile log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Link command record | `5c430a4d4c17661d6b38c9b9c8f798439722d6ae6f5afc9b152fcb73574e67d6` |
| Link log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Build outputs and full command/log records are retained outside the repository
at:

```text
/home/keivan/nouveau-vaapi-app-validation/ffplay-progressive-export-candidate-20261002/build-x11-va-direct/rebuild-patch2/
```

The candidate source, object, archive, and DSO are separate from the configured
baseline tree.

## Build method

Configured source/build identity:

```text
source:
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/source/mesa-26.0.8

build:
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/build-x11-va-sysroot
```

The exact compile and link commands were read from the configured Ninja graph:

```sh
ninja -C /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/build-x11-va-sysroot \
  -t commands src/gallium/frontends/va/libva_st.a

ninja -C /home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/build-x11-va-sysroot \
  -t commands src/gallium/targets/va/libgallium_drv_video.so
```

For the compile, the candidate `surface.c` replaced only the source argument;
`-o`, `-MF`, and `-MQ` pointed to the new private output directory. All
configured warning flags, defines, and include paths were preserved. For the
link, only the output DSO path and the `libva_st.a` input path were changed.
The candidate archive began as a copy of the previously linked patch-0001
archive; the new patch-0002 `surface.c.o` replaced its same-named member.

The configured baseline was checked before and after. These SHA-256 values
were identical across the build:

| Baseline artifact | SHA-256 before and after |
|---|---|
| Baseline `surface.c` | `f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0` |
| Configured `surface.c.o` | `f0215a10155383a6eda8b09afa8afe1406e89c792a0080634469725271c22b22` |
| Configured `libva_st.a` | `dc2d75b2f39763b476cfdd78f1fc2d1a52e532c2411cf5307101513ad7dc773d` |
| Configured VA plugin | `aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536` |

## A/B archive and ABI checks

Baseline, patch-0001, and patch-0002 archives each contain the same 11 members
in the same order. Member-content comparison found only `surface.c.o` changed
from baseline to patch 0001, and only `surface.c.o` changed from patch 0001 to
patch 0002.

The patch-0002 DSO is ELF64 for x86-64. Its dynamic dependency/SONAME contract
matches the patch-0001 DSO, and the defined dynamic export set is unchanged at
one symbol. The content hash differs as expected because `surface.c` changed.

## Limits

This build result verifies compilation and linking only. It does not validate
the polling loop at runtime, actual fence completion, output pixels, field
parity, chroma, color metadata, repeated surface reuse, dma-buf FD lifetime,
visible rendering, or FFmpeg/ffplay behavior. The caller still holds
`drv->mutex` across the deadline loop; this remains a production
latency/concurrency issue. No playback fix or release candidate is accepted by
this evidence.
