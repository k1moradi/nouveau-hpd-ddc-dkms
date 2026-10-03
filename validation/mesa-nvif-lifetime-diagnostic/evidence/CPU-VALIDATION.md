# CPU-only validation record

## Pinned inputs

```text
Mesa source file:
  Mesa 26.0.8 src/gallium/winsys/nouveau/drm/nouveau.c
  SHA-256 2140bca6de1666e4517ebabf97a63b417db43bfbc575198d7835d40ad92dc57f

Diagnostic patch:
  patches/0001-nouveau-nvif-bsp-lifetime-diagnostic.patch
  SHA-256 f89c752fc445d4534f46ce45813a877b4229efdeae7d6d306429fb7462f913b7

Kernel duplicate-layer diagnostic:
  validation/nouveau-nvif-duplicate-diagnostic/patches/0009-drm-nouveau-log-nvif-duplicate-layer.patch
  SHA-256 442b14bfb53201dc7c9e7ac8bcfd6a1f74bb94280a8d766ffc119bcf20d327fc
```

## Validation performed

- The new Mesa patch applies to the pinned source with `patch --fuzz=0`.
- The new source-contract suite passes **7/7**.
- The existing Mesa fd-candidate and Linux ABI16/NVKM source-contract tests
  pass **9/9**.
- The existing kernel duplicate-layer patch/source tests pass **2/2**.
- Combined result: **18/18 tests passed**.
- `py_compile` passed for all four test files.
- Added-line whitespace checks passed for the new files and patch payload.
- The patched `nouveau.c` translation unit passed `-fsyntax-only` twice with
  the saved Mesa build flags, including `-Werror=format`: once with the
  baseline diagnostic macro and once with the correct-DEL-fd candidate macro.

The tests confirm that both A/B builds use the same BSP NEW/DEL and channel
free logging, that the diagnostic baseline retains the existing parent-handle
DEL fd, that the candidate macro selects the DRM fd, and that the ordinary
non-diagnostic code preserves its original calls. They also verify the sent
object key/token fields and ioctl return values are included in the records.

## Not performed

The syntax-only compiler check used this existing compile database:

```text
/home/keivan/.cache/nouveau-vaapi-followup-20260928/
private-functional-no-mesa-diag-20260930/build-x11-va-sysroot/compile_commands.json
```

It compiled a temporary copy of the pinned source after zero-fuzz patch
application. It did not link the Mesa library/DSO. No module was installed, loaded, or
unloaded; no initramfs was changed; no reboot or GPU workload was run. This is
an instrumentation source-review checkpoint, not hardware evidence and not a
BAR2/PTE or playback fix.

The current boot remains on the older diag4-v3 Nouveau module, srcversion
`354DCD95A5804DE00E20EFB`, with boot ID
`8a436489-d363-4749-a3e0-2f134e34931a`. The separate v1-v8 kernel stack and
kernel duplicate-layer marker have build evidence, but are not the module
currently loaded. Do not use this boot for the planned A/B reproduction.

`main` remains HOLD.
