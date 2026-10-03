# Mesa Nouveau BSP NVIF lifetime A/B diagnostic

The saved application failure boundaries are summarized in
[`evidence/FIRST-FAILURE-COMPARISON.md`](evidence/FIRST-FAILURE-COMPARISON.md).

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
A: DEL key K uses the parent channel handle instead of the DRM fd
A: channel free for that parent channel succeeds
replacement NEW key K returns -EEXIST, with the kernel duplicate layer identified

Variant B: same lifecycle; DEL key K uses the real DRM fd and succeeds;
          replacement BSP NEW succeeds.
```

The baseline DEL return is recorded but may be either zero or nonzero. In the
pinned Linux 7.0 ABI16 handler, DEL returns zero when the addressed DRM file
does not contain the requested key. Therefore a zero-return DEL on the
mismatched fd does not prove the intended per-file key was removed. The
correlator requires the later same-key ABI16 duplicate and matching channel
route; it reports the DEL return in each matched lifecycle. A nonzero return
without that duplicate is not a reproduced stale-key chain.

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

### Historical failure profile

[`runtime-profile.json`](runtime-profile.json) pins the exact MPV argument
vector and input SHA-256 recovered from the saved stage-4 GDB log. The saved
command was:

```text
/usr/bin/gdb -q -batch \
  -x /home/keivan/.cache/vaapi-app-source-review/probes/mpv-second-nvc0-create-stage-v5.gdb \
  --args /usr/bin/mpv --no-config --vo=gpu --gpu-api=opengl \
  --hwdec=vaapi --hwdec-codecs=h264 --hwdec-threads=1 \
  --hwdec-software-fallback=no --no-audio --frames=1 \
  /home/keivan/test_1080p.mkv
```

The saved failure was constructor #2,
stage 4, BSP class `0x95b1`, returning `-17 (EEXIST)`; the GDB probe stopped the
inferior after recording it. The runner had an 18-second absolute deadline.
The exact failure timestamp was not recorded, so 18 seconds is the preserved
outer bound, not a measured time-to-failure guarantee. Variant A must reproduce
the complete lifecycle chain; if it does not, B is inconclusive.

The historical evidence records `DISPLAY=:0` and the old private Mesa driver
directory. It does not record the historical working directory or the actual
`XAUTHORITY` value; the GDB runner inherited that value. The controlled profile
therefore pins `/home/keivan` as both `HOME` and its explicit working
directory, and records a hash of the current Xauthority file while normalizing
its value in the A/B environment comparison. This is a controlled reproduction
profile, not a claim that every historical environment detail was recovered.

The historical post-run journal contained a BAR2/HOST_CPU/PTE fault at
`0x5a9000`. That boot was contaminated and is not valid for a clean A/B run.
The capture tool rejects a current boot whose full-boot preflight finds any
hard-stop signature.

### Generated capture and journal boundary

Do not hand-edit run manifests. `capture_nvif_lifetime_run.py` generates
schema-2 manifests from the current boot, loaded Nouveau srcversion, the module
file selected by `modinfo`, its post-install file SHA-256, srcversion and
vermagic, the module parameter, pinned media, resolved private VA DSO,
environment, and journal cursor. It checks that the selected file's srcversion
matches the loaded module and that its vermagic targets the pinned kernel. The
parser re-hashes that same module file when validating the manifest and requires
its path, hash and metadata to match across A/B. It writes the full-boot
preflight journal as
`journal-preflight.jsonl`, then stores only the records after the captured
cursor in `journal-delta.jsonl`. The parser verifies both files against the
manifest hashes and boot ID. Preflight also requires visible `_TRANSPORT=kernel`
records so restricted journal access cannot masquerade as a clean kernel log.

The module-file SHA is observed after installation/signing/compression on each
run, rather than compared against a deployment hash frozen before install.
That deployment-specific pin cannot be made until the reviewed diagnostic
module is installed. The current checks prove the selected installed file did
not change between capture and analysis and that A/B used the same file
identity; they do not yet prove that file is the planned signed/compressed
deployment artifact.

The cursor is obtained before the full-boot snapshot, avoiding a gap between
the clean-baseline scan and the journal boundary. The workload is launched
through `systemd-cat` to capture Mesa diagnostics with kernel records. The
runner stops the entire MPV process group, including surviving descendants
after the launcher exits, on the first pinned BSP NEW `-EEXIST`, a classified
hard stop, a monitor error, or the 18-second bound. It verifies that no live
non-zombie process remains in the group after signal escalation. It then
passively collects journal records for 30 seconds. No additional decoder is
launched.
The generated manifest records hashes and resolved paths for the MPV and
`systemd-cat` executables; the pair comparator requires them to match. The
hard-stop classifier uses journal source metadata. Kernel signatures such as
`PRIV_VIOLATION`, GPU reset, CTXSW, BAR2/PTE, channel kill, failed-idle,
sanitizer, lockdep, kernel `WARNING`, and PROP RT-overrun are accepted only on
`_TRANSPORT=kernel` records. `SIGBUS`/`Bus error` is accepted as a workload
failure only when the record's `SYSLOG_IDENTIFIER` is the pinned
`nouveau-nvif-lifetime` runner identifier. Unrelated userspace text containing
words such as `WARNING`, `BAR2`, or `PTE` is ignored. Any kernel hard stop
overrides workload/lifecycle success; a tagged workload fatal also prevents a
successful A/B outcome.

The current kernel exposes `diag_ctxsw` as a root-readable-only sysfs
parameter. The runner reads it directly when allowed and otherwise uses only
`sudo -n /usr/bin/cat` for that single read. If noninteractive read access is
unavailable, capture fails before launching MPV. Do not run the capture script
as root: MPV runs as the logged-in desktop user with a constructed minimal
environment.

Execution is deliberately gated by the explicit `--execute` option. Example
commands for a later, separately approved clean diagnostic boot are:

```bash
python3 validation/mesa-nvif-lifetime-diagnostic/capture_nvif_lifetime_run.py \
    --variant A \
    --dso /path/to/private-A/libgallium_drv_video.so \
    --output-dir /path/to/new-capture-A \
    --execute
```

Use the same command in a separate B boot, with the B DSO and a new output
directory. The tool refuses an existing output directory. It also refuses an
unexpected kernel or Nouveau srcversion, a changed media/DSO hash, a dirty
whole-boot hard-stop scan, an unreadable or enabled `diag_ctxsw`, a non-`:0`
display, inaccessible X11 session, competing video process, or contaminated
debug environment. Neither variant has been run by preparing this profile or
the capture code.

### Pair comparison

After independently captured A and B runs, compare the deltas and generated
manifests:

```bash
python3 validation/mesa-nvif-lifetime-diagnostic/parse_nvif_lifetime_ab.py \
    --journal-a /path/to/capture-A/journal-delta.jsonl \
    --manifest-a /path/to/capture-A/manifest.json \
    --journal-b /path/to/capture-B/journal-delta.jsonl \
    --manifest-b /path/to/capture-B/manifest.json
```

The parser requires separate boots, matching workload/environment/kernel and
observed installed-module identity, pinned per-variant DSO hashes, verified
journal hashes and clean preflight artifacts. A kernel hard-stop event from
either run overrides the NVIF lifecycle result. It accepts the A baseline only for a successful first
NEW, wrong-fd DEL attempt, successful old-channel free, replacement NEW
returning `-EEXIST`, and a same-key/channel ABI16 duplicate marker. The DEL
return is retained and can be zero or nonzero, as described above. An NVKM
duplicate without process/client identity is explicitly inconclusive. A's
intentional process-group stop on `-EEXIST` is permitted; other nonzero exits,
timeouts, or unproven journal boundaries make the pair incomplete. The
capture CLI returns nonzero for an unexplained workload failure, a dirty
kernel result, an incomplete journal boundary, a timeout, or an A run that
does not reproduce the expected `-EEXIST`. A zero capture status only means
the artifact is usable for pair comparison; it is not a causal pass.

The B candidate requires a successful correct-fd DEL, successful channel
free, and successful replacement NEW without a matching duplicate marker.
Even then the result is only NVIF object-lifecycle evidence; it does not prove
visible playback or a production fix. Parser and capture-runner tests are in
[`tests/test_parse_nvif_lifetime_ab.py`](tests/test_parse_nvif_lifetime_ab.py)
and [`tests/test_capture_nvif_lifetime_run.py`](tests/test_capture_nvif_lifetime_run.py).
