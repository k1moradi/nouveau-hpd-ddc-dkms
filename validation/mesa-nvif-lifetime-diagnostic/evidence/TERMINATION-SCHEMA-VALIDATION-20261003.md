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

## Runtime boundary

The current service has no `DISPLAY`. The observed boot is
`8a436489-d363-4749-a3e0-2f134e34931a`, running `7.0.0-34-generic` with the
older loaded Nouveau srcversion `354DCD95A5804DE00E20EFB`. It is not the reviewed
diagnostic module identity, so no playback or hardware selftest is authorized
from this session. BAR2/PTE root cause and the MPV wrong-fd causal A/B remain
unresolved; visible hardware playback remains FAIL and `main` remains HOLD.
