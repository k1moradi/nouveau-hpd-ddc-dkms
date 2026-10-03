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

## Runtime capture and fail-closed correlation

The runtime capture must include both Mesa stderr records and kernel messages
in one line-delimited JSON journal file from a single boot. Run the same pinned
bounded application command through `systemd-cat` on separate A and B boots so
Mesa stderr is forwarded to the journal. After the A run exits, export that
boot's journal:

```bash
journalctl -b -o json --no-pager > variant-a.journal.jsonl
```

Launch the actual pinned command as
`systemd-cat --identifier=nouveau-nvif-a -- <exact-command-and-arguments>`;
the angle-bracket expression is a placeholder, not literal shell syntax. Use
the same command and environment on the separate B boot, changing only the
private DSO selected for A/B.

Retain the whole capture including kernel records. On the separate B boot,
repeat with the B DSO and save `variant-b.journal.jsonl`. The parser rejects
malformed tagged records, missing monotonic/boot/process metadata, and
diagnostic events from multiple boot IDs. Create one run manifest per boot
with the observed boot ID, input hash, exact argv, normalized relevant
environment (replace the private A/B driver-directory paths with the same
placeholder), kernel release, loaded Nouveau srcversion, and the hash of the
DSO actually loaded by the player. The expected input and A/B DSO hashes are
pinned by the parser.

Manifest shape:

```json
{
  "schema": 1,
  "variant": "A",
  "boot_id": "observed-boot-id",
  "input_sha256": "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2",
  "command_argv": ["mpv", "<same-bounded-arguments>", "/home/keivan/test_1080p.mkv"],
  "normalized_environment": {
    "DISPLAY": ":0",
    "LIBVA_DRIVER_NAME": "nouveau",
    "LIBVA_DRIVERS_PATH": "<variant-private-directory>"
  },
  "kernel": "7.0.0-34-generic",
  "nouveau_srcversion": "29C4D0E409ABB2711FC9A10",
  "dso_sha256": "1d8f71c5ad0884a43496cfb199e7a38eb5528441e3f24daf4dbf3f7a7c4a28f9"
}
```

Replace the `boot_id` and command/environment examples with values recorded
for that run. The kernel release, Nouveau srcversion, input SHA and per-variant
DSO SHA must match the pinned diagnostic build and the hashes enforced by the
parser. B uses the same manifest fields and normalized environment,
`variant: "B"`, and the pinned B DSO hash.

Compare both runs and manifests together:

```bash
python3 validation/mesa-nvif-lifetime-diagnostic/parse_nvif_lifetime_ab.py \
    --journal-a variant-a.journal.jsonl --manifest-a variant-a.json \
    --journal-b variant-b.journal.jsonl --manifest-b variant-b.json
```

It rejects mismatched input, argv, normalized environment, kernel release,
Nouveau srcversion, DSO identity, journal/manifest boot ID, or reuse of the
same boot. It reports the baseline chain as reproduced only when the same key
has a successful first NEW, failed wrong-fd DEL, successful old-channel free,
replacement NEW returning `-EEXIST`, and a matching ABI16 or NVKM duplicate
marker. ABI16 includes a channel token, which is matched to NEW's route token;
the NVKM marker does not log a channel, so it is correlated by key, class, and
its position between channel free and replacement NEW. It reports the
candidate chain as successful only when the reused key
has a successful correct-fd DEL, successful channel free, and successful
replacement NEW without a matching duplicate marker. A missing baseline
reproduction makes the paired result inconclusive even if B succeeds.

These outcomes establish only the logged object-lifecycle pattern. If Variant
A does not reproduce the chain, Variant B cannot establish the DEL-fd
hypothesis. The result does not prove visible playback. Parser regression tests
are in [`tests/test_parse_nvif_lifetime_ab.py`](tests/test_parse_nvif_lifetime_ab.py).
