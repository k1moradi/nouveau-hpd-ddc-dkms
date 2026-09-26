# GK104 K4200 PNVIO hardware I²C transcript

This transcript is the evidence gate for the opt-in --diag-pnvio-hw-ddc path.
It records the K4200 GOP implementation recovered from the board ROM. It does
not establish that other GF119-derived GPUs use the same protocol.

The ROM itself is not stored in this repository. The analyzed file
k4200-vbios.rom was 184,320 bytes, SHA-256:

    6c768eb2d34ab65bdde6137e66b5fbe750a750f9553aabff51e614e1168c4ed9

The UEFI PCI ROM image starts at raw ROM offset 0xf400. Its PE32+ EFI image
is compressed. The decompressed image was 133,536 bytes and had SHA-256:

    ba478d3458e8323d20ac18de1034885aaf099a3d2c35edb0485a4c52599d8684

The extraction was reproduced with
[UEFIRomExtract](https://github.com/ccharon/UEFIRomExtract); the output size
and hash matched the analyzed image. The relevant code was disassembled with
GNU objdump using the PE image's RVA addresses, for example:

    objdump -d -Mintel --start-address=0x110cc --stop-address=0x11254 gop.efi

The original ROM and extracted EFI image remain outside the repository.

The function locations below are RVAs in that decompressed PE image. The
.text section's raw file offset and RVA are both 0x260, matching the
disassembly addresses.

## Board and selector scope

The K4200 DCB I²C entry begins with bytes 30 00 00 05. Nouveau previously
kept only the low nibble of byte 0 (drive=0) and discarded the high nibble
(speed_sel=3). The GOP speed-selection routine at RVA 0x11254 maps 400000
to a D008 field of 0x43. The GOP request path at RVA 0x11c60 selects hardware
I²C at speeds of at least 100000. This establishes selector 3 as 400 kHz for
this K4200/GK104 implementation only; the selector meaning is not generalized
to other chipsets.

The diagnostic is additionally limited to:

- GK104 (chipset == 0xe4);
- physical PNVIO port 0 (bus->addr == 0x00d014);
- speed_sel == 3;
- one of the two ordinary Linux DRM EDID block-0 transfers: msg0 address
  0x50, flags 0, length 1, byte 0; msg1 address 0x50, flags I2C_M_RD, and
  length 1 or 128.

All other messages, addresses, selectors, ports, and chipsets use the normal
Linux i2c-algo-bit algorithm.

Linux v7.0 DRM performs a one-byte block-0 DDC probe before requesting the
full EDID block. Both use the ordinary message flags: the offset write has
flags 0, and the read has I2C_M_RD. See
[drm_edid.c](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/drm_edid.c#L2088-L2134)
for the message construction and
[the drm_probe_ddc caller](https://github.com/torvalds/linux/blob/v7.0/drivers/gpu/drm/drm_edid.c#L2505-L2534)
for the one-byte probe.

## GOP locations and behavior

| RVA | Routine | Relevant behavior recovered from the instructions |
| --- | --- | --- |
| 0x10d78 | Controller status wait | Reads the per-port D000 register, waits while bit 31 is set, compares elapsed timer units with 0x9c4 (2500), and decodes status as (D000 >> 29) & 3. Status 0 is the success result used by the callers. There is no fixed poll count or sleep in the loop. |
| 0x10eb4 | Enter hardware mode | Writes literal 0x00000003 to 0xd014 + port*0x20 and clears the GOP software-mode flag. |
| 0x110cc | Hardware read | Calls the status waiter once before programming D004 when the destination and length are nonzero; that return value is ignored. It then packs D004 from `((wire_address >> 1) & 0x3ff) OR (mode_field << 11)`. The requested length controls the remaining-byte loop; each iteration puts `min(4, remaining)` in the D000 count field, issues the command, polls completion, reads one 32-bit word from D00C on success, and copies the requested low-order bytes first. |
| 0x11254 | Speed programming | For 400 kHz, writes `(old_D008 & 0xfffff043) OR 0x43`. Other recognized GOP values are 300 kHz `(... OR 0x50)` and 100/60 kHz `(... OR 0x10e)`; this diagnostic enables only selector 3 / 400 kHz. |
| 0x11330 | Controller initialization | Writes 0x000f4240 to D010, enters hardware mode, and programs D008 with `(old_D008 & 0xff0a7fff) OR 0x010a0000`. It performs E50C/E500 hybrid-pad changes only when its hybrid-mode flag is set; this DVI-I port-0 path does not use that branch. |
| 0x11903 | Hardware write | Uses the same D004 address packing and four-byte FIFO chunking, writes data to D00C, and issues D000 write commands. This write operation is documented but is not used by the diagnostic. |
| 0x11c60 | GOP request dispatcher | Uses the hardware path at speeds of at least 100 kHz. Its lower-speed and retry paths can switch to software I²C. On hardware failure it retries 400/300 kHz at 100 kHz, then can retry 100 kHz at 60 kHz. The diagnostic intentionally does not copy those retries. |
| 0x1aec0 | EDID reader | Reads target wire address 0xa0, segment address 0x60, block index 0, and length 0x80. For block 0 there is no segment-pointer preamble; extension blocks use a separate path. |

The GOP uses per-port registers at a 0x20 stride. For physical port 0 the
registers are D000, D004, D008, D00C, D010, and D014.

### Status-wait call sites in the hardware-read helper

The call-site order was checked in the extracted 133,536-byte EFI image whose
SHA-256 is recorded above. Disassembly of RVA 0x110cc shows:

- At 0x11112 and 0x1111b, the helper checks for a non-null destination and a
  nonzero length. When both are true, it calls the status waiter at 0x10d78
  from 0x11127.
- The return from that pre-command waiter is not tested. The helper continues
  and writes D004 at 0x11158, then emits the D000 read command at 0x111b6.
- It calls the waiter again at 0x111bc immediately after issuing the command.
  The return is tested at 0x111c1; a nonzero result skips the D00C data read.
- After the chunk loop, it calls the waiter at 0x11211 before issuing the
  D000 cleanup command at 0x11224. This final wait's return is also ignored.

Therefore, the pre-command status is observational/drain behavior, not a gate
that cancels the read. A nonzero status there must be recorded but must not
prevent the D004 write and first D000 read command if the Linux path is to
match this GOP helper. The per-command waiter is the failure-producing check.

## Controller command details

D004 for the block-0 read is 0x00000050: wire address 0xa0 shifted right
by one, with mode/block field zero. The GOP's native block-0 operation is the
basis for translating Linux's offset-zero EDID request. No general register
subaddress or arbitrary write-read transaction support is claimed.

The recovered helper keeps the address/mode in D004; the requested data
length controls the loop and the byte count encoded into each D000 command.
The exact DRM presence probe (`length=1`) takes one defined final-chunk
iteration:

    initial remaining = 1, accumulator = 1
    n = min(4, 1) = 1
    delta = ((1 << 6) ^ 1) & 0x003fffc0 = 0x40
    accumulator = 1 ^ 0x40 = 0x41
    read command before final marker = 0x80000014 | 0x41 = 0x80000055
    final read command = 0x80000055 | 0x10000002 = 0x90000057

For a one-byte request, the helper calls the waiter before D004 once (ignoring
its return), issues one final read command, calls the waiter after that command
and checks its return, reads D00C once on success, then calls the waiter once
more before STOP (ignoring that return). There is no intermediate chunk or
continuation. This establishes that the one-byte probe is a defined instance
of the same recovered hardware-read operation as the 128-byte request.

For a read chunk of n bytes, the GOP updates an accumulator that begins at 1:

    delta       = ((n << 6) ^ accumulator) & 0x003fffc0
    accumulator = accumulator ^ delta
    command     = 0x80000014 | accumulator
    if this is the final chunk:
        command |= 0x10000002

For each command, the GOP polls D000 and then reads D00C once. Bytes are
unpacked from bits 7:0, 15:8, 23:16, and 31:24. The 128-byte block therefore
uses 32 four-byte chunks. The first 31 commands are 0x80000115; the final
command is 0x90000117. The final marker is set only on the last chunk, so the
GOP continues the same logical read across intermediate chunks and ends it on
the final command. The diagnostic preserves that framing.

The corresponding write command base observed at RVA 0x11903 is 0x80000018;
it uses the same count accumulator. Its four-byte intermediate and final
commands are 0x80000119 and 0x9000011b. A one-byte final write is 0x9000005b.
These write commands are not emitted by this diagnostic.

D000 bit 31 is the busy indicator. The GOP waits up to 0x9c4 timer units
before interpreting status bits 30:29. The timer is used as a microsecond
clock by the GOP, so the Linux implementation uses a 2500 μs bound. The GOP
treats decoded status 0 as success. Other completed status values are not
assigned a portable meaning here; Nouveau reports them as -EIO. A still-busy
controller after the bound is reported as -ETIMEDOUT.

After the data operation, the GOP issues D000 command 0x8000000c to finish
the controller operation. D00C is a data/FIFO register; the diagnostic never
reads it for a pre-transfer snapshot because that could consume returned data.

## Linux implementation and replay boundary

--diag-pnvio-hw-ddc adds an opt-in adapter dispatcher. It chooses the backend
before taking the NVKM bus mutex:

- Either exact eligible block-0 shape acquires the NVKM bus/pad once, runs the
  controller operation using the requested read length, and releases it.
- Unsupported message shapes call i2c_bit_algo.master_xfer() directly
  without first acquiring the mutex. Its initialized pre_xfer and post_xfer
  callbacks perform the normal acquire/release. The adapter reports the same
  functionality as i2c_bit_algo.
- The only hardware-to-bitbang fallback is a read-only D014 preflight
  rejection before controller setup or an I²C transaction. Any error after
  setup begins is returned directly; the request is not replayed after a
  potentially partial transfer.

An unsupported two-message request logs only the first write byte and the two
message headers, for example:

    DDC_DIAG: PNVIO_HW_DDC path=bitbang reason=unsupported-shape selector=3 num=2 msg0 addr=50 flags=0000 len=1 data0=00 msg1 addr=50 flags=0001 len=1

The dispatcher then calls Linux `i2c_bit_algo.master_xfer()` without holding
the NVKM bus lock. The log shape makes the rejected transaction visible while
keeping message data bounded.

Before entry the code requires D014's low control bits to be 0x7. It then
snapshots D000 before any controller write, writes the GOP controller
initialization values, and runs the pre-command status waiter. As established
by the call-site audit above, a nonzero return from that waiter is recorded
but ignored. The code then writes D004, reads the base-block data through
D000/D00C, records the checked per-command status and unchecked final-wait
result, issues the GOP D000 cleanup command, and restores the saved D014 low
three control bits with Nouveau's existing masked-register access pattern.
D008, D010, and D004 remain at the GOP-programmed values, as they do in the
GOP flow.

The patch is applied only when the DKMS source contains
diagnostic-pnvio-hw-ddc.enabled, created by
install.sh --diag-pnvio-hw-ddc. For that boot the installer stages only:

    options nouveau config=NvI2CHw=1

It does not force NvI2C=1. A normal install or uninstall removes the
temporary module option. The monitor remains connected throughout the test.

## Diagnostic interpretation

A successful one-byte DRM presence-probe record is:

    DDC_DIAG: PNVIO_HW_DDC phase=start ... selector=3 address=50 offset=00 length=1 ...
    DDC_DIAG: PNVIO_HW_DDC phase=result ret=2 bytes=1 chunks=1 status=00000000 ...

This means the controller completed the one-byte probe. DRM should then
request the full base block through the same dispatcher, producing
`length=128`, `ret=2 bytes=128 chunks=32` if that transfer succeeds. Check
that the DVI-I EDID sysfs file is 128 bytes and begins with
00 ff ff ff ff ff ff 00. A completed controller transfer does not by itself
prove the returned bytes form a valid EDID.

No samples means the run is inconclusive. A failure record is useful controller evidence,
but the diagnostic deliberately does not fall back to bitbang after the
hardware transaction may have begun. The connected-only result cannot locate
a missing ACK between the monitor and the GPU.
