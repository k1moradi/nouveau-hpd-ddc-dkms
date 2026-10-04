# BAR2 v3 offline correlator and CPU-only validation

## Scope

This checkpoint adds only a read-only offline parser and synthetic tests for
the existing v3 diagnostic log format. It does not change the kernel patch or
module. No VA/GPU workload or player was started; no module, package, initramfs,
or boot configuration was changed; no reboot was initiated.

The parser requires kernel-transport journal records from one boot with
monotonic timestamps. It matches the decoded `VAHI:VALO` BAR2 address only
against `src=current` ranges observed while a map reference is active. Cached
ranges, failed snapshots, equal-time ordering, release intervals, changed
ranges, overlapping candidates, missing release boundaries, and incomplete
lifecycles do not produce a causal pass. A numerical candidate is explicitly
not allocation identity or proof that the hardware used that mapping.

The parser follows the existing patch's current schema; it does not add the
recommended per-cleanup-release kernel log. Therefore it treats
`verified` through `destroying` as a release-transition interval.

## CPU-only test results

The BAR2 matcher and detached supervisor suite was run from
`validation/detached-mpv-supervisor/tests`:

```sh
PYTHONPATH=.. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  test_bar2_baseline.py test_detached_mpv_supervisor.py
```

Result: **51/51 passed, no skips**. This includes the saved three-line BAR2
fixture and the structured journal-snapshot timeout/OSError path.

The v3 source-contract suite was run against the pinned source tree:

```sh
NOUVEAU_VMA_BASE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 \
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_bar2_vma_diagnostic.py
```

Result: **8/8 passed, no skips**.

The new offline parser suite:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
```

Result: **17/17 passed, no skips**. Cases include a numeric active-range
candidate, cached range after release, release windows, equal timestamps,
overlapping active ranges, unavailable snapshots, VMA changes while mapped,
missing final-release records, missing destruction, mixed boots, malformed
records, and CLI status semantics including mixed-boot and invalid-text
rejection.

The PTS-exact hardware/software comparator was run from
`/home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/pixelation-reference-20261002`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_compare_hw_nv12.py
```

Result: **8/8 passed, no skips**. Total across these four suites: **84/84**.

The detached-supervisor synthetic stop test reported:

```text
SUPERVISOR_SELF_TEST_PASS matcher=pass hard-stop-cases=18 journal-trigger=pass process-group-tree=stopped
```

AST parsing passed for the new parser and its tests. `git diff --check` passed.
No `__pycache__` directory was created in this diagnostic package.

## Pixelation evidence review

The pinned user screenshot
`/home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/desktop-user-reported-pixelation.png`
with SHA-256 `6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270`
was inspected again. It shows a coherent scene without an obvious block-grid
artifact. Software NV12 reference frames at PTS 530.063 and 530.105 seconds
were rendered from the retained packed reference and depict the same scene as
the screenshot's approximate 530.083-second alignment. This is only an
approximate screenshot-to-software visual comparison; it is not a
frame-synchronized hardware comparison.

The adjacent software frames were rendered without hardware acceleration
from the packed software reference with:

```sh
ffmpeg -hide_banner -loglevel error -hwaccel none \
  -f rawvideo -pixel_format nv12 -video_size 1920x1080 -framerate 24 \
  -i /home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/pixelation-reference-20261002/software-pts-530021-530188.nv12 \
  -vf "select='eq(n,1)+eq(n,2)'" -fps_mode passthrough -frames:v 2 \
  /tmp/nouveau-software-ref-530-%02d.png -y
```

The software-reference manifest maps those frame indexes to PTS 530.063 and
530.105 seconds. The rendered PNGs were temporary inspection aids in `/tmp`.

The five-frame software NV12 reference payload has SHA-256
`7773decbceeb9f9d39a2538da54e797e9934e723f7ec734ed7d0a931ded0af09`.

The saved approximate comparison metrics remain luma SSIM-style 0.996215,
correlation 0.996239, MAE 2.528/255, and RMSE 5.213/255. The exact-PTS
hardware/software raw pairs remain limited to early PTS 42 and 201: 7 and 2
one-level Y differences, respectively, with exact UV. No hardware NV12 frame
is retained around 530 seconds, so the user-reported late pixelation remains
unresolved.

## Read-only post-reboot gate

`detached_mpv_supervisor.py check-only --no-desktop` returned:

```text
boot_id=85152730-d7e1-49d9-b65d-00d01dbdada5
kernel=7.0.0-34-generic
loaded_srcversion=354DCD95A5804DE00E20EFB
BAR2_HOST_CPU_PTE_COUNT=0
BASELINE_CHECK_PASS
DEPLOYMENT_VERIFIED=false
RUN_ELIGIBLE=false
```

This is a zero-count baseline check only. It is not run authorization: the
loaded module is still the old diag4-v3 identity, no reviewed deployment
manifest is present, and the headless service has no desktop environment.
The reviewed diagnostic module remains undeployed. `main` stays HOLD.

## Artifact hashes

```text
ce721335cffb9f64d6f3a479d2e1b210e61d7e602f8d400bf380ac005613baf2  validation/nouveau-bar2-vma-diagnostic/README.md
23f1138f459028b40c38d5c82b3f6fb2ae0290e9a2c7823223e99d773a764464  validation/nouveau-bar2-vma-diagnostic/correlate_v3.py
7e545d63593466e777973a6578d27e7dbea8c535b3b0b233d5f1fa90a89df232  validation/nouveau-bar2-vma-diagnostic/tests/test_correlate_v3.py
472886814c7dd67ff2685bc50791030f24f4267f6a22be16f57ce5c9e1b4818e  validation/nouveau-bar2-vma-diagnostic/patches/0010-drm-nouveau-correlate-instmem-vma-selftest.patch
```
