# Exact-PTS late-scene NV12 capture and comparison

This tool prepares separate stock-FFmpeg hardware and software raw-NV12
captures for the reported late scene. It pins
`/home/keivan/test_1080p.mkv` by SHA-256 and requests frames near:

```text
530.021  530.063  530.105  530.146  530.188 seconds
```

The output is a timestamped NUT container, not anonymous raw bytes. The
capture input-seeks to 527 seconds, limits input decoding to ten seconds and
output to five frames, preserves original timestamps with `-copyts`, and uses
`-fps_mode passthrough` plus a 1 ms encoder time base to avoid NUT's default
24 fps timestamp rounding. Comparison reads each container's actual frame
timestamps with ffprobe and rejects missing/out-of-tolerance PTS, wrong
input/FFmpeg hashes, changed dimensions, and changed output bytes. The hardware
path explicitly uses VAAPI decode followed by `hwdownload,format=nv12`. The
software path does not enable hardware acceleration.

No screenshot is used as a decoded-frame timestamp. Successful
`hwdownload`/container output is not itself proof of pixel correctness.

## Software reference capture

CPU-only reference capture (no VAAPI options):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 \
  validation/late-pixelation/capture_compare_nv12.py capture \
  --mode software \
  --input /home/keivan/test_1080p.mkv \
  --output-dir /home/keivan/nouveau-vaapi-app-validation/late-pixelation-sw-530-20261004 \
  --execute
```

The command refuses a mismatched input or existing output directory. It saves
the exact argv, FFmpeg identity, target PTS, capture window, output hash, and
log in `manifest.json`. The retained software-only reference manifest is
[`evidence/software-capture-manifest-20261004.json`](evidence/software-capture-manifest-20261004.json);
the large `.nut` payload remains outside this repository.

## Future hardware capture and comparison

Only after the reviewed module has passed the isolated kernel primitive stages,
and from a fresh eligible local desktop boot, capture five independent
hardware repetitions. Do not combine this with the MPV NVIF A/B run. The loop
archives one timestamped NUT and one comparison report per repetition:

```sh
set -eu
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id)
for repetition in 1 2 3 4 5; do
  output_dir="/home/keivan/nouveau-vaapi-app-validation/late-pixelation-hw-530-${BOOT_ID}-${repetition}"
  report="/home/keivan/nouveau-vaapi-app-validation/late-pixelation-compare-${BOOT_ID}-${repetition}.json"
  timeout --signal=TERM --kill-after=2s 900s \
    env PYTHONDONTWRITEBYTECODE=1 python3 \
    validation/late-pixelation/capture_compare_nv12.py capture \
    --mode hardware \
    --input /home/keivan/test_1080p.mkv \
    --output-dir "$output_dir" \
    --device /dev/dri/renderD128 \
    --execute
  timeout --signal=TERM --kill-after=2s 120s \
    env PYTHONDONTWRITEBYTECODE=1 python3 \
    validation/late-pixelation/capture_compare_nv12.py compare \
    --hardware "$output_dir/hardware.nut" \
    --software /home/keivan/nouveau-vaapi-app-validation/late-pixelation-sw-530-20261004/software.nut \
    --output "$report"
done
```

Each requested target must have a paired hardware/software frame within 2 ms;
otherwise comparison returns inconclusive instead of silently pairing a
neighboring frame. Reports include hardware/software PTS, frame hashes, changed
Y and UV byte counts, maximum deltas, RMSE values, and separate Y/UV difference
bounding boxes. These are decode-surface metrics, not presentation proof.
Every repetition must match every target independently; a neighboring frame is
not substituted when a target is missing.

The CPU-only metric/argument/provenance suite is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/late-pixelation/tests/test_capture_compare_nv12.py
```
