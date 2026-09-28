# GK104 GPIO20, PMU IFR, and GOP address audit

## Scope and inputs

This is an offline static follow-up to the K4200 DDC investigation. It uses
the hash-verified K4200 `80.04.FE.00.03` ROM and its extracted GOP; no live BAR
access, register write, or hardware experiment was performed.

```text
K4200 ROM: 184320 bytes
SHA-256:   6c768eb2d34ab65bdde6137e66b5fbe750a750f9553aabff51e614e1168c4ed9

GOP:       133536 bytes
SHA-256:   ba478d3458e8323d20ac18de1034885aaf099a3d2c35edb0485a4c52599d8684

PMU IFR image: 24564 bytes (0x5ff4)
SHA-256:       eab925408691f34683c6e6abae369f96782b426051f7e34c91a7a965937f2f2a

Flattened FUC4 code: 20992 bytes (0x5200; 82 x 256-byte blocks)
SHA-256:             10f4ce7410bf64a01adf94b7978948d542af1e5887de27bff870cf266b8e85cd
```

The ROM and extracted binaries remain outside Git. The GOP hash matches the
previously recorded extraction. EnvyTools source revision
[`f102b82381f3f11cee113d16374c87091db039d9`](https://github.com/envytools/envytools/tree/f102b82381f3f11cee113d16374c87091db039d9)
was used for ROM parsing and FUC4 disassembly.

## GPIO20 in the decoded VBIOS init graph

The GPIO table at ROM offset `0x5483` is version 4.1. Its line-20 record is:

```text
14 30 00 00 ef
line=20, function=0x30 (GENERIC_INITIALIZED), default=0
```

EnvyTools decodes this as a normal input with default logical state 0. Under
the GF119 GPIO encoding, that state is input/released; the record does not
describe an asserted GPIO or a DDC power-enable state.

The main init table at `0x4f11` points its first script to `0x8696`. That
script contains this branch at fixed offsets:

```text
0x89b7 CONDITION 0x30
0x89b9 GPIO_NE, excluding functions 04, 05, 06, 1a, 73, 74, 75, 76
0x89c3 NOT
0x89de INIT_GPIO (opcode 0x8e)
```

The Linux v7.0 BIOS interpreter defines `GPIO_NE` (opcode `0xa9`) to reset
every non-excluded GPIO function when the current script execution state is
true. `INIT_GPIO` resets every non-unused GPIO function when execution is
true. Function `0x30` is not in the exclusion list. The two arms are
complementary:

| Condition `0x30` | `GPIO_NE` | `NOT` then `INIT_GPIO` | GPIO20 effect if the script executes |
|---|---|---|---|
| true | Runs; includes function `0x30` | Disabled | Reset function `0x30` to its table default |
| false | Skipped | Runs | Reset all defined GPIOs, including line 20, to table defaults |

Thus, if execution reaches this point with script execution enabled, either
arm resets GPIO20 to its default input/released state. A search of the decoded
main and display script output finds no later GPIO opcode or literal
`0xd660` write that assigns GPIO20 a different state. The interpreter
semantics are in Linux v7.0
[`init_gpio()` and `init_gpio_ne()`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/bios/init.c).

This answers the static branch question, not the firmware execution-history
question. On the captured Linux boot, `0x2240c=2` made Nouveau choose
`post=false`; its trace showed `execute=0` and no executed GPIO31 writes.
Nouveau therefore did not execute this GPIO20 reset path on that boot. The
trace does not reveal whether motherboard firmware or the GOP executed it
before Linux, nor whether the pre-Linux path reached this exact branch.

## GOP-computed GPIO addresses

The GOP's `.text` includes a generic GPIO method table at RVA `0x10d0`; its
entry at table offset `0x30` points to the generic initializer at RVA
`0x122b8`. The line access helpers compute the per-line register as:

```text
0xd610 + 4 * line
```

The relevant indexed access sites in the decoded `.text` are `0x11fb2`,
`0x120cf`, `0x121db`, `0x12279`, and `0x1253e`. These are methods in the
generic GPIO group, not DDC-controller code. The helper at RVA `0x11f94`
reads the selected line register, updates direction/output fields, and writes
it through the GOP's MMIO interface. The initializer at `0x122b8` iterates
GPIO-table records and calls the line helpers for matching records; its
associated callback also handles special GPIO fields and may signal changes
through `0xd604` (accesses at `0x12066` and `0x124e0`). The line number is
taken from a parsed record, not fixed in the instruction stream. Function-
specific fields select among `0xd740–0xd79c` in the code at `0x12402–0x12477`.

For the K4200 table's line 20, the formula yields:

```text
0xd610 + 4 * 20 = 0xd660
```

Therefore the GOP has a computed path capable of accessing GPIO20's register
when the generic initializer processes that record. The script/table audit
above describes when the VBIOS asks for the default reset. The disassembly
does not, by itself, prove that this callback was invoked for GPIO20 during
the particular pre-Linux boot. This closes the earlier literal-only audit
gap without claiming an observed write to `0xd660`.

The same generic formula could form `0xd68c` for line 31, but the decoded
K4200 GPIO table contains records only for lines 0 through 24; it does not
provide a table record for line 31. The separate script's explicit
`0xd68c` pulse remains a different operation with unknown board function.

The DDC helpers separately compute controller addresses from the selected
port (`0x10e4f`, `0x10e93`, `0x10ed1`, `0x10f20`, `0x10f9d`, `0x11015`,
`0x11089`), including `0xd014 + 0x20 * port`. Related controller accesses
are `0xd004` at `0x1114f`/`0x11975`, `0xd00c` at `0x111dd`/`0x119e5`,
`0xd008` at `0x11278`/`0x112e4`, and `0xd010` at `0x113dd`. The
`0xe500`/`0xe50c` accesses
around `0x11350–0x114ad` belong to the already documented shared-pad path.
No data flow was identified from these GPIO/DDC helper inputs to `0xe1b8`,
`0xe600–0xe620`, or `0x1590`. This records the relevant computed address
families and their instruction sites; it is not a proof that every opaque
callback or firmware-private mechanism in the GOP has been understood.

## PMU Init-From-ROM code, UAS, and per-port I2C setup

BIT `p` v1 identifies the deprecated pre-core82 PMU Init-From-ROM image at
logical pointer `0x15198`, size `0x5ff4`, image ID `0x0c`, with info structure
at `0x64cf`. The extracted image matches its ROM slice. Its Kepler container
contains 82 numbered 256-byte code blocks; flattening those blocks gives the
FUC4 code hash above. This replaces the earlier preliminary note that a
generic linear disassembly was invalid: the container must first be
reassembled by block, after which EnvyTools' FUC4 decoder yields a coherent
instruction stream.

The address-space and instruction references are EnvyTools' Falcon
[data-space](https://github.com/envytools/envytools/blob/f102b82381f3f11cee113d16374c87091db039d9/docs/hw/falcon/data.rst),
[I/O-space](https://github.com/envytools/envytools/blob/f102b82381f3f11cee113d16374c87091db039d9/docs/hw/falcon/io.rst),
[branch](https://github.com/envytools/envytools/blob/f102b82381f3f11cee113d16374c87091db039d9/docs/hw/falcon/branch.rst),
and [register-map](https://github.com/envytools/envytools/blob/f102b82381f3f11cee113d16374c87091db039d9/rnndb/falcon.xml)
documentation. The branch reference defines the `be` condition as unsigned
below-or-equal, which is the basis for the port `0` through `5` reachability
result below.

The first address-space pass missed the Falcon UAS form used by this image.
Ordinary FUC `D[]` accesses address Falcon-local data, while `I[]` operations
address Falcon I/O. EnvyTools documents those spaces separately, and its
Falcon register map also identifies the `UC_CAPS.UAS` capability and UAS
configuration/fault registers. This IFR constructs UAS-style `D[]` addresses
by ORing register offsets with `0x14000000`; those accesses must not be
classified as local data merely because the instruction mnemonic is `ld D[]`
or `st D[]`. The detailed UAS translation is not fully documented in the
public EnvyTools material, so the address interpretation below is grounded in
the code's construction and its cross-check against the same K4200 GOP
registers, rather than a complete public UAS specification.

The IFR's per-port setup helper at FUC RVA `0x2b8` receives a port index. It
calls the status helper at `0x289`, which reads UAS address
`0x14000680 + port * 0x20` and polls bit 31. It then compares the port with
`5` and branches to `0x2fd` on unsigned-below-or-equal. Thus ports `0` through
`5` skip the `e320/e32c` block. For ports greater than `5`, the helper
computes `port * 0x50`, reads `0x1400e320 + port * 0x50`, ORs `0x0000c001`,
and writes it back; it also reads `0x1400e32c + port * 0x50`, clears bit 0,
and writes it back. After that conditional block, all ports take the normal
controller setup writes through UAS addresses corresponding to
`0xd010 + port * 0x20` (`0x000f4240`), `0xd014 + port * 0x20` (`3`), and
`0xd008 + port * 0x20` (`0x010a010e`). The DDC register offsets, stride, and
values match the K4200 GOP's PNVIO controller setup.

The companion helper at `0x33d` has the same `port <= 5` early-exit. For
ports above 5 it applies `0xffff3ffe` to `0x1400e320 + port * 0x50`, clearing
exactly the `0xc001` bits, and clears bit 0 at `0x1400e32c + port * 0x50`.
The `0xe320/e32c` pair therefore has an enable/disable-shaped per-port
operation, but EnvyTools does not name that register family. Its bit-pattern
similarity to Nouveau's `0xe500/0xe50c` hybrid-pad operation is not enough to
assign it the same function.

For the failing K4200 DVI-I CCB0 path, the port argument is 0: it is also the
index used in the `D010/D014/D008 + port * 0x20` calculations. Both IFR
helpers skip `e320/e32c` for this port. Linux v7.0's GF119-derived driver
constructs an exclusive pad for this CCB; its `gf119_i2c_pad_x_func` has no
`.mode` callback, while the shared-pad variant has
`g94_i2c_pad_mode()`, which operates only on `e500/e50c`. The common pad-mode
wrapper invokes hardware mode code only when that callback exists. So Linux
does not write `e320/e32c` for CCB0, but the recovered IFR path does not write
them for port 0 either. This finding does **not** support a missing CCB0
enable; it only identifies an as-yet-unnamed operation for IFR port indices
above 5. The Linux v7.0 implementation is in
[`padgf119.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/i2c/padgf119.c),
[`padg94.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/i2c/padg94.c),
and [`pad.c`](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/nouveau/nvkm/subdev/i2c/pad.c).

The IFR also reaches `0xd014` from its software-line helpers and has the
separate per-port `0x680` polling/control family described above. The latter's
role is not identified here. No literal or computed access to GPIO20's
`0xd660` was identified in the decoded PMU body. The observed boot does not
establish whether this specific Init-From-ROM image ran. Nouveau's separately
compiled GK104 PMU firmware is a different runtime image and is outside this
ROM-image conclusion.

## Result and remaining boundary

The static audit now establishes:

1. The K4200 VBIOS contains a reachable, conditionally interpreted init
   branch whose two arms both reset GPIO20 to the table's released/input
   default, if execution is enabled and reaches the branch.
2. Nouveau skipped POST on the observed Linux boot, so Linux did not perform
   that reset there. Pre-Linux execution remains unobserved.
3. The GOP contains a table-driven GPIO path that can compute `0xd660`; an
   actual pre-Linux write to that address is not established.
4. The decoded PMU IFR uses UAS-tagged `D[]` accesses for the PNVIO
   controller's `D010/D014/D008` register offsets.
5. Its `e320/e32c` RMW sequence is gated to port indices greater than 5 and
   is skipped for K4200 CCB0 / physical port 0.

These findings do not identify a missing CCB0 software DDC enable and do not
justify writes to GPIO20, GPIO31, `0xe1b8`, `0xe320/0xe32c`,
`0xe600–0xe620`, `0x1590`, or undocumented D014 bits. The remaining dynamic
discriminator is whether firmware obtains EDID before `ExitBootServices()`
through `EFI_EDID_ACTIVE_PROTOCOL` or `EFI_EDID_DISCOVERED_PROTOCOL`; otherwise
physical SDA/SCL observation remains the decisive way to separate a board
path failure from a GPU receive-path problem.
