# Termination schema consistency validation — 2026-10-03

This checkpoint hardens the NVIF A/B capture manifest and pair parser. It is
CPU-only. No VA/GPU workload, module install/load, initramfs update, or reboot
was performed.

## Changes

- `read_manifest()` now requires a `kernel-hard-stop` reason to include at
  least one kernel-source stop record and a `workload-hard-stop` reason to
  include at least one workload-source stop record.
- Postrun stop records must be objects with a recognized `kernel` or
  `workload` source. Malformed or unknown source values fail closed.
- A workload termination reason may coexist with a kernel signature in the
  passive post-stop journal tail. The reason records what first stopped the
  workload; the pair comparator independently reclassifies all captured
  records and gives any kernel hard stop precedence in the final outcome.
- A complete parser CLI test now passes a workload `Bus error` record from the
  pinned `nouveau-nvif-lifetime` journal identifier through manifest loading,
  journal hash/boot checks, hard-stop reclassification, pair comparison, and
  exit status. It requires `WORKLOAD_FAILURE` and status 4.

The capture and parser continue to use the same
`VALID_TERMINATION_REASONS` contract. The existing capture test checks that
shared contract and verifies kernel precedence when both source classes are
present.

## Validation

All validation below ran without skips:

| Suite | Result |
|---|---:|
| Mesa NVIF lifetime package (`unittest discover`) | **87/87** |
| Mesa delete-fd source/patch contract | **3/3** |
| Linux ABI16 duplicate-key source contract | **6/6** |
| Kernel duplicate-layer diagnostic source contract | **2/2** |
| ffplay progressive-export source contract | **3/3** |
| **Total across five invocations** | **101/101** |

The source-pinned suites used these saved inputs:

```text
Mesa nouveau.c:
/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/source/mesa-26.0.8/src/gallium/winsys/nouveau/drm/nouveau.c

Linux v1-v8 source:
/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002

ffplay baseline surface.c (SHA-256 f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0):
/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/source/mesa-26.0.8/src/gallium/frontends/va/surface.c
```

Both changed Python files passed AST parsing, and `git diff --check` passed.
An initial ffplay source-contract invocation pointed to a different saved
`surface.c` snapshot and correctly failed its baseline SHA check before any
tests ran. The invocation was repeated against the pinned baseline above and
passed 3/3.

## Exact validation commands and hashes

The following five commands were rerun on review HEAD
`7728cf26462e9edf1b8c54dac49d658f143dab47`. Each was run from the named test
directory with `PYTHONDONTWRITEBYTECODE=1`; all exited zero, and none reported
skips.

From
`/home/keivan/nouveau-hpd-ddc-dkms/validation/mesa-nvif-lifetime-diagnostic/tests`:

```sh
MESA_NOUVEAU_C_SOURCE=/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/source/mesa-26.0.8/src/gallium/winsys/nouveau/drm/nouveau.c PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

Result: **87/87**.

From
`/home/keivan/nouveau-hpd-ddc-dkms/validation/mesa-nvif-delete-fd-candidate/tests`:

```sh
MESA_NOUVEAU_C_SOURCE=/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/source/mesa-26.0.8/src/gallium/winsys/nouveau/drm/nouveau.c PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_nvif_delete_fd_patch.py
NOUVEAU_KERNEL_SOURCE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_linux_abi16_duplicate_key.py
```

Results: **3/3** and **6/6**, respectively.

From
`/home/keivan/nouveau-hpd-ddc-dkms/validation/nouveau-nvif-duplicate-diagnostic/tests`:

```sh
NOUVEAU_KERNEL_SOURCE_ROOT=/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_nvif_duplicate_diagnostic.py
```

Result: **2/2**.

From
`/home/keivan/nouveau-hpd-ddc-dkms/validation/mesa-ffplay-progressive-export-candidate/tests`:

```sh
MESA_SURFACE_C_SOURCE=/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/source/mesa-26.0.8/src/gallium/frontends/va/surface.c PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_progressive_export_patch.py
```

Result: **3/3**. The pinned input `surface.c` SHA-256 is
`f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0`.

Pinned source and patch hashes:

```text
Mesa nouveau.c:                    2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f
Linux nouveau_abi16.c:              3d4c81dd87c8e426b7e948eb448a49075d1df54ab22c68b8e181a0b88bd28fec
Linux nvkm/core/ioctl.c:            2a18013840cee2298edadd19171b083cfed2bb7227a13303ac9afad34a107e18
Linux nvkm/core/object.c:           7dc1fca97b143da107a1234d467774608b0808ca84f88f368313941126890c6d
Mesa lifetime diagnostic patch:    f89c752fc445d4534f46ce45813a877b4229efdeae7d6d306429fb7462f913b7
Mesa delete-fd patch:               6ff3fdf5b3a38f07246830c78acf46cf222dd3ec2114a87a03526d5f8721df2c
Kernel duplicate-layer patch:       442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc
ffplay progressive-export patch:    e8e7611f591c237bf4c2d5c993b93625af1d5610a3277b6698c1c501d5858560
```

The following in-memory Python compilation checked the three lifetime tools,
their four tests, and the four source-contract tests (11 files total):

```sh
python3 - <<'PY'
from pathlib import Path
paths = [
    Path('validation/mesa-nvif-lifetime-diagnostic/build_nvif_lifetime_ab.py'),
    Path('validation/mesa-nvif-lifetime-diagnostic/capture_nvif_lifetime_run.py'),
    Path('validation/mesa-nvif-lifetime-diagnostic/parse_nvif_lifetime_ab.py'),
    Path('validation/mesa-nvif-lifetime-diagnostic/tests/test_build_nvif_lifetime_ab.py'),
    Path('validation/mesa-nvif-lifetime-diagnostic/tests/test_capture_nvif_lifetime_run.py'),
    Path('validation/mesa-nvif-lifetime-diagnostic/tests/test_nvif_lifetime_diagnostic.py'),
    Path('validation/mesa-nvif-lifetime-diagnostic/tests/test_parse_nvif_lifetime_ab.py'),
    Path('validation/mesa-nvif-delete-fd-candidate/tests/test_linux_abi16_duplicate_key.py'),
    Path('validation/mesa-nvif-delete-fd-candidate/tests/test_nvif_delete_fd_patch.py'),
    Path('validation/nouveau-nvif-duplicate-diagnostic/tests/test_nvif_duplicate_diagnostic.py'),
    Path('validation/mesa-ffplay-progressive-export-candidate/tests/test_progressive_export_patch.py'),
]
for path in paths:
    compile(path.read_bytes(), str(path), 'exec')
print(f'PASS: in-memory compile of {len(paths)} Python files')
PY
```

Result: **PASS, 11 files**. A repository scan found no generated
`__pycache__` or `.pyc` files. `git diff --check` passed for this evidence
update.

## Runtime boundary

The current service has no `DISPLAY`. The observed boot is
`8a436489-d363-4749-a3e0-2f134e34931a`, running `7.0.0-34-generic` with the
older loaded Nouveau srcversion `354DCD95A5804DE00E20EFB`. It is not the reviewed
diagnostic module identity, so no playback or hardware selftest is authorized
from this session. BAR2/PTE root cause and the MPV wrong-fd causal A/B remain
unresolved; visible hardware playback remains FAIL and `main` remains HOLD.
