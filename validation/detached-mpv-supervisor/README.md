# Detached MPV supervisor and BAR2 baseline gate

This package contains a CPU-tested supervisor for a future, bounded, visible MPV
run. It has not been armed or run. The runtime path remains unavailable until a
reviewed Nouveau deployment and a matching deployment manifest exist.

## Safety boundary

Do not use `start --arm` until the reviewed module has been installed under its
distinct identity, its initramfs provenance has been captured, a fresh logged-in
desktop boot passes the complete preflight, and BAR2/HOST_CPU/PTE plus other
kernel hard-stop counts are zero.

The `start` command requires both `--arm` and `--deployment-manifest`. The
detached service's hidden `run` action also requires a one-use token created by
`start`; calling `run` directly refuses before it captures a journal cursor.
The service checks the token and deployment manifest again before starting MPV.

`check-only` without a deployment manifest is a read-only boot-baseline check,
not proof that the installed/loaded module is the reviewed target. It never
opens a VA device.

## Deployment manifest

Create the manifest only after the reviewed module has been signed/compressed and
installed, and after the selected initramfs has been rebuilt. Pin the exact
installed module path, hash, srcversion, vermagic, initramfs hash and embedded
module hash. Also pin every expected diagnostic parameter, the Mesa DSO and VA
alias target, MPV, and input.

For this MPV run, the parameter map is required to be exactly
`{"diag_ctxsw": "N"}`. Missing it, adding a different parameter, or enabling
CTXSW logging makes the manifest invalid.

The schema is strict and versioned:

```json
{
  "schema": 1,
  "kernel": "<kernel release>",
  "nouveau": {
    "srcversion": "<installed module srcversion>",
    "module_path": "<canonical modinfo-selected module path>",
    "module_sha256": "<installed signed/compressed module hash>",
    "vermagic": "<modinfo vermagic>",
    "parameters": {"diag_ctxsw": "N"}
  },
  "initramfs": {
    "path": "<selected initramfs path>",
    "sha256": "<initramfs hash>",
    "module_sha256": "<embedded Nouveau module hash>"
  },
  "mesa": {
    "dso_path": "<canonical libgallium_drv_video.so path>",
    "dso_sha256": "<DSO hash>"
  },
  "mpv": {"path": "/usr/bin/mpv", "sha256": "<mpv hash>"},
  "input": {
    "path": "/home/keivan/test_1080p.mkv",
    "sha256": "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2"
  }
}
```

The supervisor checks the `modinfo`-selected module path and file hash, file and
loaded srcversion, vermagic, initramfs and embedded module hashes, module
parameters, the configured DSO/hash and `nouveau_drv_video.so` alias, and MPV
and input paths/hashes. It then checks `/proc/<mpv-pid>/maps` for the expected
DSO path, device/inode, and hash before it counts hardware decoding as confirmed.
If sysfs protects `diag_ctxsw` from the user service, the check uses only
`sudo -n /usr/bin/cat` and fails closed when noninteractive read access is not
available.

No deployment manifest is supplied with this source checkpoint. The currently
observed loaded module is diag4-v3 (`354DCD95A5804DE00E20EFB`), not the reviewed
v1-v8 diagnostic stack. This supervisor must refuse to run until a later
deployment creates and reviews the exact manifest.

## Stop and result semantics

The follower reads the kernel journal and stops MPV's complete process group on
the first BAR2/HOST_CPU/PTE, Nouveau FIFO MMU fault, `SCHED_ERROR 0a`, or listed
GPU/kernel hard-stop signature. The stop path sends SIGINT, SIGTERM, then
SIGKILL; after SIGKILL it checks that the process group has no live members and
records cleanup failure if any remain. The journal follower reads kernel
messages only; a userspace SIGBUS is classified from MPV's process return code.

After MPV stops, the supervisor passively observes the journal for 30 seconds.
An operator stop cancels that window and is classified as incomplete. A zero
exit status requires a natural end of the configured bounded MPV run, child exit
status zero, a complete passive journal window, no hard stop, confirmed active
VAAPI H.264 decode, and proof that the pinned VA DSO is mapped by MPV. It is not
full-file playback acceptance; the current command intentionally covers a
bounded six-minute scene interval.

Screenshot attempts use deterministic `capture-NNNN.png` names and
`screenshot-to-file`. Each JSONL record contains `time-pos` queried immediately
before the command and after the output file has become size-stable, the exact
path, the command response, and the saved file SHA-256. The two positions
bracket screenshot creation; they are presentation-time bounds, not
decoded-frame PTS.

The result record reports decode_run_complete,
screenshot_evidence_complete, and visible_video_confirmation=USER_REQUIRED
separately. A missing screenshot does not by itself turn an otherwise valid
bounded decode run into failure; screenshots are evidence artifacts, and a
person must confirm visible output.

## CPU-only validation

From this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py self-test
```

`test_bar2_baseline.py` includes the three exact saved fault lines from
`preflight-correction.txt`, the saved post-reboot `0x471000` event, literal
bracket matching, near misses, empty-input rejection, and a nonzero
contaminated-baseline result. `bar2_baseline.py` parses input supplied by file
or stdin; it does not query the journal. For a live read-only scan, supply the
current-boot kernel journal explicitly:

```sh
journalctl -k -b --no-pager -o cat | python3 bar2_baseline.py --require-zero
```

Empty or whitespace-only input is `UNKNOWN_EMPTY_INPUT` with exit status 3, not
a clean baseline. The supervisor tests use only CPU-side synthetic processes,
temporary artifacts, and mocked deployment metadata.
