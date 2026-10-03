# Mesa Nouveau NVIF subchannel delete-fd candidate

**Status: review candidate only; not an accepted playback fix.** This isolates
one source-backed correction associated with mpv's replacement-decoder BSP
object `NEW` returning `-EEXIST`.

## Proposed invariant

`drmCommandWrite()` takes a DRM file descriptor as its first argument. The
Nouveau subchannel `NEW` path uses the root Nouveau DRM fd. The matching `DEL`
path currently passes `obj->parent->handle`; channel creation assigns that
handle from `req.channel`, so for a channel parent it is a channel identifier,
not the DRM fd. The source-level candidate changes only the `DEL` fd argument
to `nouveau_drm(obj->parent)->fd`, matching the root-fd route used for `NEW`.

The pinned `xf86drm.h` prototype is `drmCommandWrite(int fd, ...)`. The pinned
Mesa 26.0.8 source file SHA-256 before the patch is:

```text
2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f
```

The patch SHA-256 is:

```text
6ff3fdf5b3a38f07246830c78acf46cf222dd3ec2114a87a03526d5f8721df2c
```

## Existing validation evidence

The exact candidate was applied with `patch --fuzz=0` to the pinned Mesa
26.0.8 source, with only `src/gallium/winsys/nouveau/drm/nouveau.c` changed.
The isolated X11 VA target built successfully. The resulting private
`libgallium_drv_video.so` SHA-256 was:

```text
938dc0ff6c22cc2141e111431f6d56344529b3507e0d7d121ad7333ceaf80512
```

It was tested privately with production Nouveau `.13` and mpv
`--hwdec=vaapi-copy`. VAAPI was selected and playback advanced, but the user
reported a triangular blank/corrupt image. A later short replay looked black
over its dark opening. **Visible playback failed.** That run did not capture
decoder #2's constructor stage, object key, or the `DEL` ioctl result, so it
does not prove that this change removes the observed `-EEXIST`.

The original build and runtime record is included at
`evidence/VALIDATION.md`, along with the build log and private plugin hash.
The plugin itself is not included or installed by this review bundle, and this
candidate must not be treated as a release patch.

## Remaining causal test

Capture the same pointer-derived NVIF key through decoder A `NEW`, decoder A
`DEL` (fd and return value), and decoder B `NEW`, alongside the kernel's exact
duplicate-key return layer. Compare baseline and candidate with only the `DEL`
fd changed. The strongest confirmation is a failed baseline `DEL` followed by
the same key's `-EEXIST`, and a successful candidate `DEL` followed by
successful decoder B creation. Channel teardown and the ABI16 per-file object
table's behavior must remain part of the review; source plausibility alone is
not causal proof.

This candidate does not address the separately observed VP3 H.264 reference
slot assertion or ffplay's interlaced VA-surface export rejection. Visible
hardware playback remains unaccepted, and `main` remains HOLD.

## CPU-only regression check

Point the test at the pristine pinned source file:

```bash
MESA_NOUVEAU_C_SOURCE=/path/to/mesa-26.0.8/src/gallium/winsys/nouveau/drm/nouveau.c \
  python3 -m unittest -v tests/test_nvif_delete_fd_patch.py
```

The test checks the source SHA, applies the patch with zero fuzz to a temporary
copy, and verifies that the only source-line change is the first argument to
the subchannel `DEL` ioctl.
