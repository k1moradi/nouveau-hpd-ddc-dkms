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
| 0x10d78 | Controller status wait | Reads the per-port D000 register, waits while bit 31 is set, compares elapsed timer units with 0x9c4 (2500), and decodes status as (D000 >> 29) & 3. Status 1 returns the internal result 2 on the normal path. Status 2/3 branch on an object flag at offset 0x30; status 0/1 also consult that flag on the timeout path. There is no fixed poll count or sleep in the loop. |
| 0x10eb4 | Enter hardware mode | Writes literal 0x00000003 to 0xd014 + port*0x20 and clears the GOP software-mode flag. |
| 0x110cc | Hardware read | Calls the status waiter once before programming D004 when the destination and length are nonzero; that return value is ignored. It then packs D004 from `((wire_address >> 1) & 0x3ff) OR (mode_field << 11)`. The requested length controls the remaining-byte loop; each iteration puts `min(4, remaining)` in the D000 count field, issues the command, polls completion, reads one 32-bit word from D00C on success, and copies the requested low-order bytes first. |
| 0x11254 | Speed programming | For 400 kHz, writes `(old_D008 & 0xfffff043) OR 0x43`. For 100 and 60 kHz, writes `(old_D008 & 0xfffff10e) OR 0x10e`. It then calls the D000 waiter, ignores its return, and writes STOP `0x8000000c`. |
| 0x11330 | Controller initialization | Calls the D000 waiter and ignores its return; writes `0x000f4240` to D010; enters hardware mode; invokes the speed setter for rates of at least 100 kHz (including its wait and STOP); then programs D008 with `(old_D008 & 0xff0a7fff) OR 0x010a0000`. It performs E50C/E500 hybrid-pad changes only when its hybrid-mode flag is set; this DVI-I port-0 path does not use that branch. |
| 0x11903 | Hardware write | Uses the same D004 address packing and four-byte FIFO chunking, writes data to D00C, and issues D000 write commands. This write operation is documented but is not used by the diagnostic. |
| 0x11c60 | GOP request dispatcher | Uses hardware at speeds of at least 100 kHz and the software transfer at 0x11b34 below 100 kHz. A failed 400/300 kHz read changes to 100 kHz, calls vtable slot +0x70, and retries if recovery succeeds. A failed 100 kHz read changes to 60 kHz, calls the same recovery method, and recurses into software transfer. It does not distinguish status 1 from other helper errors. |
| 0x11dc0 | Retry line recovery | The bus vtable at RVA 0x1120 has the transfer method 0x11c60 at slot +0x68 and this recovery method at +0x70. It enters software line mode, then performs at most 16 SCL recovery cycles and returns success when SDA reads high. Each cycle also calls the line sequence at 0x11588. |
| 0x1aec0 | EDID reader | Gets its bus object from `[this+8]`, calls vtable slots +0x10 (initialize) and +0x70 (line recovery), and calls +0x68 (transfer) only if recovery succeeds. The block-0 transfer arguments are segment-address byte 0x60, segment/block value 0, target wire address 0xa0, and length 0x80. The hardware read helper ignores the segment-address parameter; the software fallback consumes it. |

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

### Meaning of completed status 1

The waiter decodes `(D000 >> 29) & 3` after the busy loop. Status 1 reaches
the comparison at 0x10e0c and selects internal return value 2, unless the
special timeout branch returns -1 first. Status 2 and 3 branch to 0x10e01,
which returns -1 when the byte at object offset 0x30 is zero and otherwise
continues to the zero-return path. Status 0 returns zero unless the timeout
branch and a zero object flag produce -1. The meaning of the object flag is
not established here.

At 0x11ce1, the request dispatcher tests the read-helper result and uses
`sete` to mark success only when it is zero. It does not assign an
address-NACK meaning to result 2 or branch on that value separately; any
nonzero result enters its failure/retry path. Therefore the
observed `D000=0x30000050` status field 1 is a completed GOP-level transfer
failure, but the ROM does not identify it as an EDID NACK or another specific
electrical condition. The Linux diagnostic currently maps every nonzero
status field to `-EIO`; that agrees with the GOP's success/failure outcome for
status 1, but should not be generalized to status fields 2 and 3, whose GOP
handling depends on the unresolved object flag.

### GOP 400-to-100 kHz retry

The dispatcher call sites at RVA 0x11c60 were audited through the recursive
retry. After the hardware-read helper returns, zero is marked successful at
0x11ce1; any nonzero result reaches the rate fallback. A current rate of
400000 or 300000 selects 100000 at 0x11d6c. The dispatcher calls the speed
setter at 0x11254, then calls its own vtable slot +0x70 at 0x11d7c. The
constructor at 0xcc60 installs the vtable at 0x1120 at instruction 0xccaa;
that table entry points slot +0x70 to recovery routine 0x11dc0. Only a true return recurses into the
same dispatcher at 0x11da7. The separate 100000-to-60000 transition is not
enabled by this diagnostic.

For the 100 kHz speed setter, 0x11254 writes
`(old_D008 & 0xfffff10e) OR 0x10e`, waits on D000, ignores the wait result,
and writes the D000 STOP command `0x8000000c` before the line-recovery call.
It does not call controller initialization 0x11330 or rewrite D010. The
recovery method 0x11dc0 switches to software line mode by copying D014 sense
bits 4/5 into output bits 0/1. The bus constructor initializes the recovery
period at object offset 0x24 to 1; the 400-to-100 dispatcher path calls
0x11254 directly and does not change that period.

The recovery method delays 10 units, drives SCL low, then performs at most 16
cycles. Each cycle drives SCL high, delays 10 units, samples SDA after a
one-period delay, and invokes 0x11588 even when SDA is already high. That
sequence drives SDA low, cycles SCL low/high, waits for SCL high for at most
six one-period polls, then (if SCL rose) drives SDA high, delays two periods,
and returns SDA/SCL low. The caller ignores the sequence's return and reports
recovery success if any sampled SDA value was high.

On successful recovery, the recursive call enters the same 0x110cc read
helper. Because recovery left the object in software mode, the helper invokes
0x10eb4 at 0x1110d to write D014=3, then rewrites D004 and issues the same
length-derived read command. Thus the retry changes D008, performs the
bounded D014 line recovery, re-enters hardware mode, and repeats the same
read; it does not rerun D010/controller initialization. The Linux diagnostic
mirrors this one read-only retry for its exact offset-zero EDID probe/base
block shapes and retains the 100 kHz rate for later transfers. It does not
replay other I²C messages and does not add the GOP's 60 kHz fallback.

### EDID-reader bus object and caller sequence

At RVA 0x1aec0, the function loads the bus receiver from `[this+8]` and calls
its vtable slots +0x10, +0x70, and +0x68. The bus vtable at RVA 0x1120 maps
+0x10 to controller initialization 0x11330, +0x68 to dispatcher 0x11c60, and
+0x70 to recovery 0x11dc0. Constructor 0x0cc60 installs this vtable at
instruction 0x0ccaa. The caller ignores the initialization return, requires
recovery to return true, and only then invokes the transfer method. The
caller-side methods are
therefore identified; they are not an unknown method that can be assigned an
undocumented effect.

For this non-hybrid K4200 path, initialization calls the status waiter (its
return is ignored), writes D010=`0x000f4240`, enters hardware mode with
D014=`3`, calls the current speed setter, then applies the D008 initialization
mask. The speed setter writes its D008 field, calls the waiter (also ignored),
and writes STOP `0x8000000c`. The EDID caller then invokes line recovery
0x11dc0 before the first transfer. That recovery enters software line mode,
performs its bounded SCL/SDA sequence, and gates the read on a true return.

The Linux diagnostic writes D010, D014, and the D008 init/speed fields itself.
It does not reproduce the initializer's speed-setter wait+STOP before the init
D008 write, or the EDID caller's initial recovery before the first 400 kHz
command. Its 400 kHz result therefore is not yet an exact comparison with the
GOP's initial transfer sequence. The later 400-to-100 retry does perform a
wait+STOP and line recovery, but in a different position after the failed
400 kHz command.

### GOP 100-to-60 kHz software fallback

After a failed 100 kHz hardware transfer, dispatcher 0x11c60 sets the rate
field to 60 kHz with speed setter 0x11254, then calls vtable slot +0x70
(recovery 0x11dc0) and recurses. At 60 kHz the dispatcher selects software
transfer routine 0x11b34. The 60 and 100 kHz setter cases use the same D008
mask/value `(old & 0xfffff10e) | 0x10e`; the 60 kHz branch changes the rate
field, emits the setter's wait+STOP, and then switches to software line mode
through recovery. The bitbang line delay uses the bus object's `+0x24` period.
The separate helper 0x11e58 can recalculate that period, but this fallback
calls 0x11254 directly and does not call 0x11e58. The requested 60 kHz rate is
therefore established, while the exact software clock period for this fallback
is not.

For the block-0 arguments supplied by 0x1aec0, software routine 0x11b34 emits
this transaction (wire bytes shown):

```text
START
  0x60  segment-pointer address (7-bit 0x30, write)
  0x00  segment number
REPEATED START
  0xa0  EDID address (7-bit 0x50, write)
  0x00  EDID byte offset
REPEATED START
  0xa1  EDID address (7-bit 0x50, read)
  read block bytes, ACK each byte except the last; NACK the last
STOP
```

This has an additional segment-pointer write (`0x60, 0x00`) compared with
Linux's tested two-message block-0 transfer (`0xa0, 0x00`, repeated-start,
`0xa1`, read). The earlier Nouveau bitbang tests therefore did not exercise
the full GOP software fallback transaction shape. The added pre-read recovery
and this segment-pointer preamble are material sequence differences; they
justify a bounded diagnostic follow-up, but do not predict that it will ACK.

## Controller command details

For the hardware-read helper, D004 for block 0 is 0x00000050: wire address
0xa0 shifted right by one, with mode/block field zero. This hardware operation
does not emit the software path's segment-pointer or offset bytes. No general
register subaddress or arbitrary write-read support is claimed for the
hardware helper.

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
clock by the GOP, so the Linux diagnostic uses a 2500 μs bound. The GOP
waiter's return mapping and object-flag condition are detailed above; the
raw status field is not a portable I²C error enum. The diagnostic currently
maps every nonzero decoded status field to `-EIO`, which is known to match
the GOP success/failure outcome for status 1 but is not established for
status fields 2 and 3. A still-busy controller after the bound is reported
as `-ETIMEDOUT` by the diagnostic.

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

Before entry the code requires D014's low control bits to be 0x7, then
snapshots D000 and records the initial D014/D008 state. It reproduces the GOP
caller initialization in order: wait on D000 and ignore the result; write
D010=0x000f4240; enter hardware mode with D014=3; program the selected D008
rate; wait on D000 and ignore that result; write STOP `0x8000000c`; then apply
the initializer D008 mask. The `phase=caller-init` record includes both wait
results/statuses, the STOP marker, and D008 before/after each programming step.

Next, it runs caller recovery `0x11dc0`. The `phase=caller-recovery` record
reports its result, cycle count, and D014 state. A failed recovery prevents
the EDID command and 400-to-100 retry. On success, the code re-enters hardware
mode with D014=3 as the GOP read helper does; that helper then performs its own
pre-read waiter (recorded and ignored), writes D004, reads the requested data
through D000/D00C, records checked per-command status and the unchecked final
wait result, and issues the GOP D000 cleanup command. The existing 400-to-100
hardware retry remains unchanged after a failed primary command. Finally, the
code restores the saved D014 low three control bits with Nouveau's existing
masked-register access pattern. D008, D010, and D004 remain at their last
GOP-programmed values, as they do in the GOP flow.

The verifier counts caller initialization/recovery records and prints these
phase records alongside the per-rate command attempts. The 0.1.8 boot remains
historical evidence for the earlier sequence; it did not include this
caller-equivalent setup and therefore is not the next exact GOP comparison.

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
