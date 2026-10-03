# VP3 H.264 reference-slot diagnostic candidate

This is a diagnostic-only patch against the Ubuntu Mesa 26.0.8-1ubuntu0.3
source. The pinned upstream `nouveau_vp3_video_vp.c` SHA-256 is
`3c44d8153d08764aacf012cd46186819a120979c34f87adb0a3bee592d898c9f`.

The patch emits one `NOUVEAU_DIAG_VP3_REF_MISMATCH` line through Mesa's
unconditional `_debug_printf()` helper, only when a non-null H.264 reference
does not match the current decoder's `refs[valid_ref].vidbuf` slot. The
unconditional helper is necessary because the existing Mesa build uses
`MESA_DEBUG=0`, where `debug_printf()` is compiled to a no-op. The existing
identity assertion remains unchanged and still runs. The diagnostic records
decoder and buffer pointers, current frame number, source/compacted reference
indices, `valid_ref`, slot pointer, reference count, and fence sequence. An
out-of-range slot triggers a bounds assertion before the retained identity
assertion can index the table. It does not add a VA surface ID because this
Nouveau VP3 callback does not have one.

`candidate/nouveau_vp3_video_vp.c` is the generated post-patch source used by
the static regression checks in `tests/test_vp3_reference_diagnostic.py`. The
patch applies with zero fuzz to the pinned source. The affected Mesa object
compiled successfully in the existing functional-only scratch build with
`MESA_DEBUG=0`; the build log records that the source file was restored byte
for byte afterwards. Only the translation unit was compiled; the DSO was not
linked or installed. The patch SHA-256 is
`a7d6628bf8708e0f8f8c4a3457c4ac3bd49038fd7153464bce2ad46bacf84fc7`, and
the source candidate SHA-256 is
`5595bd35e2b93285274993dec154f88694b55f96c69b73bcba4bd3d7f22b15d1`.

Run the static checks with:

```sh
python3 -m unittest -v tests/test_vp3_reference_diagnostic.py
```

This is instrumentation, not a functional fix. It does not suppress or weaken
the assertion, change reference selection, or alter decoder submissions. A
runtime mismatch would distinguish an invalid slot index from a slot occupied
by a different buffer, but would not alone prove why the mismatch occurred.
No module, package, initramfs, or live GPU state was changed to prepare it.
