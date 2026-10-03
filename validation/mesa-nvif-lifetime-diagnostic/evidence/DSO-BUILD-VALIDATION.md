# Mesa NVIF lifetime A/B DSO build validation

## Result

Both diagnostic variants linked the VA driver DSO successfully from separate
private copies of the same pinned Mesa build directory. The target was
`src/gallium/targets/va/libgallium_drv_video.so`. Builds ran serially with
`nice -n 10` and `ninja -j1` to limit resource pressure.

| Artifact | Defines | Exit | SHA-256 |
|---|---|---:|---|
| Variant A | `NOUVEAU_DIAG_NVIF_LIFETIME` | 0 | `1d8f71c5ad0884a43496cfb199e7a38eb5528441e3f24daf4dbf3f7a7c4a28f9` |
| Variant B | `NOUVEAU_DIAG_NVIF_LIFETIME`, `NOUVEAU_DIAG_NVIF_CORRECT_DEL_FD` | 0 | `0fe4c64811e3bc77ddd00842796dbd9a8c3906599c74612ec81433ba6ebfd056` |

Both DSOs contain the BSP NEW, DEL, and channel-free diagnostic strings. The
build logs contain one `nouveau.c` compile and one VA DSO link per variant, with
no compiler warnings or errors.

## A/B equivalence checks

- The unmodified pinned Mesa `nouveau.c` input SHA-256 is
  `2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f`.
- The lifetime diagnostic patch SHA-256 is
  `f89c752fc445d4534f46ce45813a877b4229efdeae7d6d306429fb7462f913b7`.
- Both variants compiled the same zero-fuzz-patched `nouveau.c` copy, SHA-256
  `df3725267fc30b77ce80f49ffea86e7db55281000dc411ca707f53f292424940`.
- The baseline DSO used by the saved FFmpeg/mpv traces has SHA-256
  `aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536`; this
  is also the DSO from which the two private build directories were cloned.
- Meson `coredata.dat` is byte-identical for the baseline and both variants,
  SHA-256 `5fa21fef3f842c90196b57a5e1a9b8aefefcff39db25cef0c58a0c3e872dfed7`.
  The captured Meson build-options JSON has SHA-256
  `db6e244c090d39149c476e885eadb3c0799e231e49d8193ae6c45db6b5a47fc6`.
- The two `nouveau.c` compiler commands are identical after removing only
  `-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD` from B.
- The full Ninja command graphs for the VA DSO target contain 940 commands.
  Exactly one command differs: the `nouveau.c.o` compile, and it differs only
  by that candidate define. The link command and other compile commands match.

The two Meson build directories were copied from the same verified baseline
build and kept separate. This was a bounded incremental target build, not two
clean-from-source Mesa rebuilds: each copy rebuilt the diagnostic `nouveau.c`
object, rebuilt a VP3 object already stale in the baseline copy, refreshed the
dependent static archives, and relinked the VA DSO. Both variants traversed
the same build steps and configuration.

## Reproduction and retained evidence

The build helper is
[`../build_nvif_lifetime_ab.py`](../build_nvif_lifetime_ab.py). It verifies the
pinned source, diagnostic patch, baseline DSO and baseline build metadata;
applies the diagnostic patch with `--fuzz=0`; creates separate build copies;
checks the one-define A/B command delta before and after linking; verifies the
original baseline tree stayed unchanged; and checks the diagnostic strings.
It performs no install, loader, module, or GPU action.

The full logs, exact compiler commands, Meson options, manifest, and SHA256
manifest are in [`linked-ab-20261003/`](linked-ab-20261003/). The linked DSO
files and complete 940-command Ninja listings remain under:

```text
/home/keivan/nouveau-vaapi-app-validation/mesa-nvif-lifetime-ab-build-20261003T082047Z/
```

Their SHA-256 values are recorded above and in that directory's `manifest.json`.

## Post-link verification and future-run pins

After the `87d44e9` link checkpoint, the existing A/B build trees were checked
again without invoking a build. The post-link target command graphs still have
940 entries and differ at exactly one command, index 746: the `nouveau.c.o`
compile, with only B's `NOUVEAU_DIAG_NVIF_CORRECT_DEL_FD` define. Both current
post-link graphs byte-match the command listings captured for the original
build. The baseline VA DSO still hashes to
`aceac163eabeefe5e4c1f2eb38469239044e30671aebcec743e4277f5897b536`.

The one reference to the original build tree in each command graph is the
read-only linker input `src/gallium/targets/va/va.sym`, passed as a GNU ld
version-script argument. The output DSO and intermediate outputs are relative
to each private A/B build directory; no command references another baseline
build-tree output. The updated helper checks this explicitly.

The following baseline metadata hashes were measured during this post-link
audit and are now mandatory pins for future helper runs:

| Baseline input | SHA-256 |
|---|---|
| `build.ninja` | `ba2acdaa119fb43ef5c98780e19bc59af2d5cfc51bf4cf34769af06534c71ff7` |
| `meson-private/coredata.dat` | `5fa21fef3f842c90196b57a5e1a9b8aefefcff39db25cef0c58a0c3e872dfed7` |
| `compile_commands.json` | `c6824053b21c8d8f6b7d68021da7f75e4ad947a41008201259543e4576206151` |

The read-only VA version-script input `src/gallium/targets/va/va.sym` hashes
to `61fc96386f8ab4ca3473c7d48d2aaec54681b2ac6192bd21752518949d34a7f2` and
is checked before and after future A/B link runs.

These are post-link observations; they are **not** claimed as pre-link checks
for the original build. The machine-readable audit is
[`linked-ab-20261003/post-link-audit-20261003.json`](linked-ab-20261003/post-link-audit-20261003.json).
The build-helper invariant suite passes **9/9**, the combined focused NVIF
suite passes **27/27**, and `py_compile` passes for all six Python files.

## Runtime status

Neither DSO was installed, selected by libva, or executed. No kernel module or
initramfs was changed; no reboot or VA/GPU workload was run. The corrected-fd
variant remains a diagnostic A/B candidate, not a validated playback fix.
`main` remains HOLD.

For the eventual runtime comparison, Variant A must first reproduce the
relevant `-EEXIST` lifecycle. If A does not reproduce it, a successful B run
is inconclusive and cannot establish that the fd change fixed the failure.
