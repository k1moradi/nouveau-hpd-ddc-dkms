# Mesa Nouveau BSP NVIF lifetime A/B diagnostic

**Status: matched A/B VA DSOs linked; neither installed nor run.** This patch
prepares the missing userspace records for the stage-4 BSP NEW `-EEXIST`
investigation. It complements the kernel duplicate-layer markers in
`validation/nouveau-nvif-duplicate-diagnostic/`.

The diagnostic records only class `0x95b1`, the BSP class observed by the v5
constructor probe. It captures:

- BSP NEW: the ioctl object key and object token, route token, parent pointer
  and channel handle, real DRM fd, class, and ioctl return;
- BSP DEL: the exact object key sent, object/parent pointers, parent channel
  handle, object handle, fd passed to `drmCommandWrite()`, real DRM fd, class,
  and ioctl return;
- channel free: channel ID, real DRM fd, and ioctl return.

## Matched build variants

Apply this patch to the pinned Mesa 26.0.8 source after the same two retained
functional Mesa patches used by the earlier experiments. Build both variants
from identical source and configuration, with these compiler definitions:

```text
A (baseline):  -DNOUVEAU_DIAG_NVIF_LIFETIME
B (candidate): -DNOUVEAU_DIAG_NVIF_LIFETIME
               -DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD
```

The diagnostic patch leaves the original parent-handle DEL fd as the default.
The candidate define switches only that fd to `nouveau_drm(obj->parent)->fd`.
Both variants compile the same logging code. Defining the candidate macro
without the diagnostic macro is a compile error. Keep each build tree and
result manifest separate; record all compiler arguments and the produced DSO
hashes. Do not combine this diagnostic with unrelated Mesa changes.

## Decisive capture

Pair these userspace lines with the kernel `NOUVEAU_DIAG_NVIF_DUP`
`layer=abi16` / `layer=nvkm` line from the same decoder construction. The
causal sequence sought is:

```text
A: NEW key K succeeds
A: DEL key K uses the parent channel handle as fd and fails
A: channel free for that parent channel succeeds
B: NEW key K returns -EEXIST, with the kernel duplicate layer identified

Candidate: same lifecycle; DEL key K uses the real DRM fd and succeeds;
           replacement BSP NEW succeeds.
```

A successful replacement alone is not enough. If the old codec stays alive
longer because surface-held `pipe_video_codec` references change teardown
timing, record that lifecycle too. Keep this NVIF experiment separate from
the VP3 reference-slot assertion and ffplay PRIME export work.

No DEL failure is asserted or hidden: the ioctl result is logged, and the
existing wrapper teardown/return behavior remains intact. This instrumentation
does not claim to fix visible playback. `main` remains HOLD.

## CPU-only source check

Use the pristine pinned source path:

```bash
MESA_NOUVEAU_C_SOURCE=/path/to/mesa-26.0.8/src/gallium/winsys/nouveau/drm/nouveau.c \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest -v tests/test_nvif_lifetime_diagnostic.py
```

The test checks the pristine source identity, applies the patch with zero fuzz,
and verifies the shared logging fields and the two fd-selection branches.
This is a source-contract check; it does not replace compilation or runtime
causality evidence.

The patched `nouveau.c` translation unit first passed `-fsyntax-only` in both
A/B macro configurations using saved Mesa compile-database flags with
`-Werror=format`. At that initial checkpoint no DSO had been linked. The later
matched VA DSO builds and their source/configuration identities are recorded
separately below.

The first source-validation checkpoint is recorded in
[`evidence/CPU-VALIDATION.md`](evidence/CPU-VALIDATION.md). The diagnostic
patch SHA-256 is:

```text
f89c752fc445d4534f46ce45813a877b4229efdeae7d6d306429fb7462f913b7
```

The matched VA DSO link builds are recorded in
[`evidence/DSO-BUILD-VALIDATION.md`](evidence/DSO-BUILD-VALIDATION.md). They
are build-only artifacts; neither variant was installed or run.
