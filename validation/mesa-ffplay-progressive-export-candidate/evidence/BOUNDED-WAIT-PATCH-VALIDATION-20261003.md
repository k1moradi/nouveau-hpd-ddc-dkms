# Bounded Nouveau fence-poll patch validation

## Scope

This checkpoint validates patch 0002's strict application and source contract.
It is CPU-only evidence. It does not show that the candidate compiles, links,
runs on Nouveau, or produces correct frames.

## Pinned inputs and output

| Artifact | SHA-256 |
|---|---|
| Pinned baseline `surface.c` | `f66d404ad556926a37caffa5d120a04a74b4a36af63e322ce578b6840e8ffcc0` |
| Patch 0001 progressive staging | `e8e7611f591c237bf4c2d5c993b93625af1d5610a3277b6698c1c501d5858560` |
| Patch 0002 bounded fence polling | `6865f60a222043ddd2e31f5c8cfad84d4bd3c7dff588988a39eda03281d5d0e1` |
| Candidate after patch 0001 | `ca7e3b5fbd1f0c915d0efb6143ee620a883a17507d2a90442ef2b512b516c3da` |
| Candidate after patches 0001 and 0002 | `dc84e79326ee5e546f296279e65eb9dc468080833de875b82391fba09c26706c` |

The test pins the source, both patches, the intermediate candidate, and the
final candidate. Both patches must apply in order using GNU `patch --fuzz=0`.
The source contract requires `fence_finish(..., 0)` polling, monotonic deadline
checks and the Mesa monotonic sleep helper, and rejects the previous direct
nonzero-timeout call.

## Validation

An initial invocation using the module name `test_progressive_export_patch.py`
ran zero tests because that name was not importable from the chosen working
directory. It is retained here as a failed invocation, not counted as
validation. The file-path invocation below is the successful run.

Working directory:

```text
/home/keivan/nouveau-hpd-ddc-dkms
```

Command:

```sh
MESA_SURFACE_C_SOURCE=/home/keivan/.cache/nouveau-vaapi-followup-20260928/private-functional-no-mesa-diag-20260930/source/mesa-26.0.8/src/gallium/frontends/va/surface.c \
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest -v \
  validation/mesa-ffplay-progressive-export-candidate/tests/test_progressive_export_patch.py
```

Result: **3/3 passed, zero skips, exit status 0**. Covered tests:

- zero-fuzz application and pinned final source hash;
- staging, zero-timeout polling/deadline and progressive-export ordering;
- Windows interlaced rejection and success/failure cleanup checks.

`git diff --check` also passed. The host lacks a usable configured Mesa Ninja
build for this candidate; patch 0002 was not compiled or linked. No DSO was
installed or loaded. No VA/GPU workload, module operation, package install, or
reboot was performed.

## Claim boundary

The poll loop checks the monotonic deadline between nonblocking Nouveau fence
polls and sleeps for at most 1 ms per iteration. Scheduler delays and userspace
fence-lock contention mean it is not a real-time return guarantee. The export
caller continues to hold `drv->mutex` during the wait, with a configured
five-second interval; this remains a production latency/concurrency concern.
Output pixels, field parity, repeated reuse, FD lifetime, and visible ffplay
playback remain unvalidated.
