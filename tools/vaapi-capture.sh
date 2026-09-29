#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  tools/vaapi-capture.sh system
  tools/vaapi-capture.sh private-uninstrumented <private-dri-directory>
  tools/vaapi-capture.sh private-instrumented <private-dri-directory>

Runs one fixed 3000-frame VA-API decode, records kernel/FFmpeg logs and
metadata, and leaves the kernel journal follower active for 15 seconds after
FFmpeg exits. Each run writes a unique directory below
${NOUVEAU_CAPTURE_ROOT:-$HOME/nouveau-vaapi-captures}.
EOF
}

mono_now() {
    python3 -c 'import time; print(f"{time.monotonic_ns() / 1e9:.9f}")'
}

record_time() {
    printf '%s realtime=%s monotonic=%s\n' \
        "$1" "$(date --iso-8601=ns)" "$(mono_now)"
}

if [[ ${1:-} == --run-pipeline ]]; then
    status_file=$2
    time_file=$3
    pid_file=$4
    exit_file=$5
    ffmpeg_log=$6
    shift 6

    set +e
    /usr/bin/time -f 'wall_seconds=%e' -o "$time_file" \
        bash -c '
            pid_file=$1
            exit_file=$2
            shift 2
            "$@" &
            ffmpeg_pid=$!
            printf "%s\n" "$ffmpeg_pid" > "$pid_file"
            wait "$ffmpeg_pid"
            ffmpeg_status=$?
            printf "realtime=%s monotonic=%s status=%s\n" \
                "$(date --iso-8601=ns)" \
                "$(python3 -c '\''import time; print(f"{time.monotonic_ns() / 1e9:.9f}")'\'')" \
                "$ffmpeg_status" > "$exit_file"
            exit "$ffmpeg_status"
        ' _ "$pid_file" "$exit_file" "$@" 2>&1 | tee "$ffmpeg_log"
    pipeline_status=("${PIPESTATUS[@]}")
    printf 'ffmpeg_PIPESTATUS[0]=%s\ntee_PIPESTATUS[1]=%s\n' \
        "${pipeline_status[0]}" "${pipeline_status[1]}" > "$status_file"
    exit "${pipeline_status[0]}"
fi

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    usage
    exit 0
fi

if (($# < 1 || $# > 2)); then
    usage >&2
    exit 2
fi

mode=$1
dri_dir=${2:-}
system_plugin=${SYSTEM_NOUVEAU_PLUGIN:-/usr/lib/x86_64-linux-gnu/dri/nouveau_drv_video.so}
expected_plugin=
run_env=(-u LD_LIBRARY_PATH -u LIBVA_DRIVER_NAME -u NOUVEAU_DIAG_RT_CLEAR -u NOUVEAU_DIAG_VA_SURFACE)

case "$mode" in
    system)
        if (($# != 1)); then
            usage >&2
            exit 2
        fi
        if [[ ! -e $system_plugin ]]; then
            echo "system Nouveau VA driver not found: $system_plugin" >&2
            exit 2
        fi
        expected_plugin=$system_plugin
        run_env+=(-u LIBVA_DRIVERS_PATH)
        ;;
    private-uninstrumented)
        if [[ -z $dri_dir || ! -e $dri_dir/nouveau_drv_video.so ]]; then
            echo "private DRI directory must contain nouveau_drv_video.so" >&2
            exit 2
        fi
        dri_dir=$(realpath -e "$dri_dir")
        expected_plugin=$dri_dir/nouveau_drv_video.so
        run_env+=("LIBVA_DRIVERS_PATH=$dri_dir")
        ;;
    private-instrumented)
        if [[ -z $dri_dir || ! -e $dri_dir/nouveau_drv_video.so ]]; then
            echo "private DRI directory must contain nouveau_drv_video.so" >&2
            exit 2
        fi
        dri_dir=$(realpath -e "$dri_dir")
        expected_plugin=$dri_dir/nouveau_drv_video.so
        run_env+=("LIBVA_DRIVERS_PATH=$dri_dir" "NOUVEAU_DIAG_VA_SURFACE=1")
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

input=/home/keivan/test_1080p.mkv
if [[ ! -r $input ]]; then
    echo "decode input is not readable: $input" >&2
    exit 2
fi
for tool in ffmpeg journalctl modinfo sha256sum tee; do
    command -v "$tool" >/dev/null || {
        echo "required command not found: $tool" >&2
        exit 2
    }
done

capture_root=${NOUVEAU_CAPTURE_ROOT:-$HOME/nouveau-vaapi-captures}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$capture_root/${stamp}-${mode}-$$"
mkdir -p "$run_dir"
transcript="$run_dir/transcript.txt"
kernel_log="$run_dir/kernel.log"
ffmpeg_log="$run_dir/ffmpeg.log"
journal_error="$run_dir/journal-stderr.log"
time_log="$run_dir/time.txt"
status_log="$run_dir/pipeline-status.txt"
pid_file="$run_dir/ffmpeg.pid"
exit_file="$run_dir/ffmpeg-exit.txt"
logger_pid=

cleanup_logger() {
    if [[ -n ${logger_pid:-} ]] && kill -0 "$logger_pid" 2>/dev/null; then
        kill "$logger_pid" 2>/dev/null || true
        wait "$logger_pid" 2>/dev/null || true
    fi
}
trap cleanup_logger EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

exec 3>&1
exec > >(tee "$transcript") 2>&1
transcript_tee_pid=$!

record_time run-start
printf 'mode=%s\n' "$mode"
printf 'uname_r=%s\n' "$(uname -r)"
module_path=$(modinfo -n nouveau)
loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
disk_srcversion=$(modinfo -F srcversion nouveau 2>/dev/null || true)
printf 'modinfo_path=%s\n' "$module_path"
printf 'loaded_nouveau_srcversion=%s\n' "${loaded_srcversion:-unavailable}"
printf 'disk_nouveau_srcversion=%s\n' "${disk_srcversion:-unavailable}"
if [[ -z $loaded_srcversion || -z $disk_srcversion || $loaded_srcversion != "$disk_srcversion" ]]; then
    echo 'loaded and on-disk Nouveau srcversion differ; refusing an uncontrolled A/B run' >&2
    exit 3
fi
printf 'selected_LIBVA_DRIVERS_PATH=%s\n' "${dri_dir:-unset (system default)}"
printf 'expected_plugin=%s\n' "$expected_plugin"
printf 'input=%s\ninput_sha256=' "$input"
sha256sum "$input"
printf 'journal_command=journalctl -kf -n 0 -o short-monotonic\n'

journalctl -kf -n 0 -o short-monotonic > "$kernel_log" 2> "$journal_error" &
logger_pid=$!
sleep 1
if ! kill -0 "$logger_pid" 2>/dev/null; then
    echo 'journal follower failed to start; refusing to run FFmpeg' >&2
    cat "$journal_error" >&2
    exit 3
fi
if [[ -s $journal_error ]]; then
    echo 'journal follower reported an error; refusing to run FFmpeg' >&2
    cat "$journal_error" >&2
    exit 3
fi
record_time logger-start
printf 'journal_pid=%s\n' "$logger_pid"

ffmpeg_args=(
    -hide_banner -loglevel verbose
    -init_hw_device vaapi=va:/dev/dri/renderD128
    -hwaccel vaapi
    -hwaccel_device va
    -hwaccel_output_format vaapi
    -i "$input"
    -an -frames:v 3000 -f null -
)
run_command=(env "${run_env[@]}" ffmpeg "${ffmpeg_args[@]}")
printf 'ffmpeg_command='
printf '%q ' "${run_command[@]}"
printf '\n'

"$0" --run-pipeline "$status_log" "$time_log" "$pid_file" \
    "$exit_file" "$ffmpeg_log" "${run_command[@]}" &
runner_pid=$!
for _ in {1..100}; do
    [[ -s $pid_file ]] && break
    kill -0 "$runner_pid" 2>/dev/null || break
    sleep 0.1
done
if [[ ! -s $pid_file ]]; then
    echo 'FFmpeg PID was not recorded; capture is invalid' >&2
    kill "$logger_pid" 2>/dev/null || true
    wait "$logger_pid" 2>/dev/null || true
    exit 3
fi
ffmpeg_pid=$(<"$pid_file")
record_time ffmpeg-start
printf 'ffmpeg_pid=%s\n' "$ffmpeg_pid"

set +e
wait "$runner_pid"
runner_status=$?
set -e
record_time ffmpeg-pipeline-finished
printf 'pipeline_wrapper_status=%s\n' "$runner_status"
printf 'ffmpeg_exit_timestamp='
cat "$exit_file" 2>/dev/null || echo 'ffmpeg_exit_timestamp=unavailable'
cat "$time_log" 2>/dev/null || echo 'wall_time=unavailable'
cat "$status_log" 2>/dev/null || echo 'PIPESTATUS=unavailable'
tee_status=$(sed -n 's/^tee_PIPESTATUS\[1\]=//p' "$status_log" 2>/dev/null || true)

printf 'driver_open_expected_path='
if grep -F "Trying to open $expected_plugin" "$ffmpeg_log"; then
    driver_path_ok=1
else
    echo MISSING
    driver_path_ok=0
fi
printf 'driver_open_success='
if grep -F 'va_openDriver() returns 0' "$ffmpeg_log"; then
    driver_open_ok=1
else
    echo MISSING
    driver_open_ok=0
fi
printf 'selected_driver_line='
grep -F 'VAAPI driver:' "$ffmpeg_log" || echo MISSING

record_time post-ffmpeg-tail-start
printf 'post_ffmpeg_journal_tail_seconds=15\n'
sleep 15
record_time logger-stop
kill "$logger_pid" 2>/dev/null || true
wait "$logger_pid" 2>/dev/null || true
logger_pid=

trace_invalid=0
if [[ $mode == private-instrumented ]]; then
    trace_hook_active=0
    trace_allocate_entry=0
    if grep -Fq 'NOUVEAU_DIAG_VA_SURFACE hook=active' "$ffmpeg_log"; then
        trace_hook_active=1
    fi
    if grep -Fq 'NOUVEAU_DIAG_VA_SURFACE phase=allocate-entry' "$ffmpeg_log"; then
        trace_allocate_entry=1
    fi
    printf 'instrumented_hook_active=%s\n' "$trace_hook_active"
    printf 'instrumented_allocate_entry=%s\n' "$trace_allocate_entry"
    if [[ $trace_hook_active -ne 1 || $trace_allocate_entry -ne 1 ]]; then
        echo 'capture invalid: instrumented Mesa trace was not positively exercised' >&2
        trace_invalid=1
    fi
fi

if [[ -s $journal_error ]]; then
    printf 'journal_stderr='
    cat "$journal_error"
fi
printf 'kernel_log_bytes=%s\n' "$(wc -c < "$kernel_log")"
if [[ $runner_status -ne 0 ]] || grep -Eiq 'CTXSW_TIMEOUT|errored - disabling channel|channel [0-9]+ killed|fault .*\[BAR2\].*\[PTE\]|PRIV_VIOLATION|SIGBUS|GPU reset' "$kernel_log" "$ffmpeg_log"; then
    echo 'STOP_A_B=1 nonzero FFmpeg exit or critical Nouveau/VAAPI failure signature; do not run another condition without a fresh boot'
else
    echo 'STOP_A_B=0 no stop-signature matched in this capture'
fi
record_time run-end

exec 1>&3 2>&1
if [[ -n ${transcript_tee_pid:-} ]]; then
    wait "$transcript_tee_pid" || true
fi
sha256sum "$kernel_log" "$ffmpeg_log" "$transcript" > "$run_dir/SHA256SUMS"
cat "$run_dir/SHA256SUMS"
printf 'capture_directory=%s\n' "$run_dir"

if [[ $trace_invalid -ne 0 ]]; then
    exit 4
fi

if [[ $driver_path_ok -ne 1 || $driver_open_ok -ne 1 ]]; then
    echo 'capture invalid: libva did not prove the expected driver selection' >&2
    exit 4
fi
if [[ $tee_status != 0 ]]; then
    echo 'capture invalid: tee failed while recording FFmpeg output' >&2
    exit 4
fi
if [[ $runner_status -ne 0 ]]; then
    exit "$runner_status"
fi
if grep -Eiq 'CTXSW_TIMEOUT|errored - disabling channel|channel [0-9]+ killed|fault .*\[BAR2\].*\[PTE\]|PRIV_VIOLATION|SIGBUS|GPU reset' "$kernel_log" "$ffmpeg_log"; then
    exit 5
fi
