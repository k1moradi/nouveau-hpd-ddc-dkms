# VA-API initialization control record — 2026-09-28

This records a control run with the kernel journal logger active before,
during, and after FFmpeg. It tests basic VA-API driver initialization only; it
does not create an H.264 decoder, allocate decode surfaces, or exercise the
NVE4 video engines.

## Commands and ordering

The logger was started first, checked to still be running after a one-second
startup interval, and stopped only after FFmpeg exited:

```bash
journalctl -kf -n 0 -o short-monotonic > ~/nouveau-vaapi-init-control-20260928-kernel.log 2>&1 &
logger_pid=$!
sleep 1
kill -0 "$logger_pid"

ffmpeg -hide_banner -loglevel verbose \
    -init_hw_device vaapi=va:/dev/dri/renderD128 \
    -f lavfi -i 'nullsrc=s=16x16:d=0.1' \
    -frames:v 1 -f null - \
    > ~/nouveau-vaapi-init-control-20260928-ffmpeg.log 2>&1

kill -INT "$logger_pid"
wait "$logger_pid"
```

The FFmpeg command ran from **2026-09-28 15:52:25.020884169 -07:00** to
**15:52:25.222539694 -07:00**. The journal follower had started at
**15:52:24.001431294 -07:00**.

## Recorded result

- FFmpeg exit status: **0**.
- FFmpeg initialized VA-API 1.23 and reported **Mesa Gallium driver
  26.0.8-1ubuntu0.3 for NVE4**.
- The command emitted one output frame; its CPU `lavfi` input reported zero
  decode errors.
- `journalctl` remained active through FFmpeg and exited 0 after SIGINT.
- Kernel capture size: **0 bytes**; SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- The scan for `trap`, `rt_width`, `rt_height`, `fault`, `pte`, `bar2`, `priv`,
  and `killed` found no matches.

The exact run transcript is also saved at
`/home/keivan/nouveau-vaapi-init-control-20260928-transcript.txt`; the FFmpeg
and kernel output files are adjacent to it in `/home/keivan/`.

This supports only the narrow conclusion that this VA-API initialization
control did not produce a matching kernel record. It does not show that H.264
decoder creation or video-surface allocation is trap-free. The earlier
`~/nouveau-vaapi-init-kernel.log` remains an invalid control because its logger
was stopped before FFmpeg ran.
