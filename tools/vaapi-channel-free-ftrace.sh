#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage:
  sudo tools/vaapi-channel-free-ftrace.sh <private-instrumented-dri-directory>
  pkexec tools/vaapi-channel-free-ftrace.sh <private-instrumented-dri-directory>

Temporarily enables a narrowly filtered kernel function-graph trace around
Nouveau's legacy channel-free ioctl, runs the existing private-instrumented
VAAPI capture as the invoking desktop user, attaches the trace to that capture,
and restores the previous idle tracefs configuration. It does not install or
reload any driver.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 2
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    usage
    exit 0
fi
if (($# != 1)); then
    usage >&2
    exit 2
fi
if [[ $EUID -ne 0 ]]; then
    fail 'run this helper with sudo so it can use tracefs'
fi
capture_user=${SUDO_USER:-}
if [[ -z $capture_user && ${PKEXEC_UID:-} =~ ^[0-9]+$ ]]; then
    capture_user=$(getent passwd "$PKEXEC_UID" | cut -d: -f1)
fi
if [[ -z $capture_user || $capture_user == root ]]; then
    fail 'invoke through sudo or pkexec from the desktop user; the VAAPI capture must not run as root'
fi

user_record=$(getent passwd "$capture_user") || fail "cannot resolve invoking user $capture_user"
IFS=: read -r _ _ capture_uid capture_gid _ capture_home _ <<<"$user_record"
[[ -n $capture_home && -d $capture_home ]] || fail "invalid home for $capture_user"

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
capture_script="$repo_dir/tools/vaapi-capture.sh"
dri_dir=$(realpath -e -- "$1") || fail 'private DRI directory does not exist'
[[ -x $capture_script ]] || fail "capture helper missing: $capture_script"
[[ -e $dri_dir/nouveau_drv_video.so && -f $dri_dir/libgallium_drv_video.so ]] ||
    fail 'private DRI directory must contain nouveau_drv_video.so and libgallium_drv_video.so'
[[ $(readlink -f "$dri_dir/nouveau_drv_video.so") == "$dri_dir/libgallium_drv_video.so" ]] ||
    fail 'nouveau_drv_video.so does not resolve to this private Gallium plugin'
expected_plugin_sha=defee11fcedabc2b81730bfcc0d8d425133cccc76751e5c17652a9420b56268f
actual_plugin_sha=$(sha256sum "$dri_dir/libgallium_drv_video.so" | awk '{print $1}')
[[ $actual_plugin_sha == "$expected_plugin_sha" ]] ||
    fail "private plugin SHA-256 mismatch: expected $expected_plugin_sha, found $actual_plugin_sha"
runuser -u "$capture_user" -- test -r "$dri_dir/nouveau_drv_video.so" ||
    fail "private plugin is not readable by $capture_user"
runuser -u "$capture_user" -- test -r /home/keivan/test_1080p.mkv ||
    fail 'the fixed VAAPI input is not readable by the invoking user'

tracefs=
for candidate in /sys/kernel/tracing /sys/kernel/debug/tracing; do
    if [[ -r $candidate/available_filter_functions && -w $candidate/tracing_on ]]; then
        tracefs=$candidate
        break
    fi
done
[[ -n $tracefs ]] || fail 'tracefs function tracing is unavailable or not writable'
for file in current_tracer tracing_on set_graph_function set_graph_notrace \
            set_ftrace_filter set_ftrace_notrace trace trace_clock; do
    [[ -e $tracefs/$file ]] || fail "tracefs lacks $file"
done
[[ $(<"$tracefs/tracing_on") == 0 ]] ||
    fail 'tracing is already active; refusing to disturb another trace session'
[[ $(<"$tracefs/current_tracer") == nop ]] ||
    fail 'a non-nop tracer is active; refusing to replace it'

has_function() {
    awk -v name="$1" '$1 == name { found = 1 } END { exit !found }' \
        "$tracefs/available_filter_functions"
}
has_function nouveau_abi16_ioctl_channel_free ||
    fail 'nouveau_abi16_ioctl_channel_free is not function-graph traceable in this kernel'

filter_has_functions() {
    awk '!/^[[:space:]]*($|#|####)/ { found = 1 } END { exit !found }' "$1"
}
if filter_has_functions "$tracefs/set_graph_function" ||
   filter_has_functions "$tracefs/set_graph_notrace" ||
   filter_has_functions "$tracefs/set_ftrace_filter" ||
   filter_has_functions "$tracefs/set_ftrace_notrace"; then
    fail 'function-graph filters are already configured; refusing to replace them'
fi

tmpdir=$(mktemp -d /tmp/nouveau-vaapi-ftrace.XXXXXX)
chmod 700 "$tmpdir"
trace_roots=(nouveau_abi16_ioctl_channel_free)
if has_function gf100_fifo_intr_sched_ctxsw; then
    trace_roots+=(gf100_fifo_intr_sched_ctxsw)
fi
# The fence wait polls nouveau_fence_done() repeatedly. Exclude that hot leaf
# from graph output while retaining the enclosing wait's entry/return duration.
trace_notrace=()
if has_function nouveau_fence_done; then
    trace_notrace+=(nouveau_fence_done)
fi

trace_clock_original=$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$tracefs/trace_clock" | head -n 1)
[[ -n $trace_clock_original ]] || fail 'cannot determine the active trace clock'
trace_clock_selected=$trace_clock_original
if grep -qw mono "$tracefs/trace_clock"; then
    trace_clock_selected=mono
fi

# Do not erase a previous trace buffer. The default post-boot buffer reports
# zero entries; any recorded data requires the owner to inspect it first.
if grep -Eq 'entries-in-buffer/entries-written:[[:space:]]*([1-9][0-9]*/|[0-9]+/[1-9])' \
    "$tracefs/trace"; then
    rm -rf -- "$tmpdir"
    fail 'trace buffer is not empty; refusing to clear existing trace data'
fi

configured=0
reader_pid=
cleanup() {
    set +e
    printf '0\n' > "$tracefs/tracing_on" 2>/dev/null
    if [[ -n ${reader_pid:-} ]] && kill -0 "$reader_pid" 2>/dev/null; then
        kill "$reader_pid" 2>/dev/null
        wait "$reader_pid" 2>/dev/null
    fi
    if ((configured)); then
        if ((${#trace_notrace[@]})); then
            printf '!%s\n' "${trace_notrace[@]}" > "$tracefs/set_graph_notrace" 2>/dev/null
        fi
        printf '!%s\n' "${trace_roots[@]}" > "$tracefs/set_graph_function" 2>/dev/null
        printf 'nop\n' > "$tracefs/current_tracer" 2>/dev/null
        if [[ $trace_clock_selected != "$trace_clock_original" ]]; then
            printf '%s\n' "$trace_clock_original" > "$tracefs/trace_clock" 2>/dev/null
        fi
    fi
    rm -rf -- "$tmpdir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

configured=1
printf '%s\n' "${trace_roots[@]}" > "$tracefs/set_graph_function"
if ((${#trace_notrace[@]})); then
    printf '%s\n' "${trace_notrace[@]}" > "$tracefs/set_graph_notrace"
fi
if [[ $trace_clock_selected != "$trace_clock_original" ]]; then
printf '%s\n' "$trace_clock_selected" > "$tracefs/trace_clock"
fi
printf 'function_graph\n' > "$tracefs/current_tracer"

for root in "${trace_roots[@]}"; do
    grep -Fxq "$root" "$tracefs/set_graph_function" ||
        fail "tracefs did not accept graph root $root"
done
for function in "${trace_notrace[@]}"; do
    grep -Fxq "$function" "$tracefs/set_graph_notrace" ||
        fail "tracefs did not accept hot-leaf exclusion $function"
done

printf 'tracefs=%s\n' "$tracefs"
printf 'trace_clock=%s\n' "$trace_clock_selected"
printf 'function_graph_roots=%s\n' "${trace_roots[*]}"
printf 'function_graph_notrace=%s\n' "${trace_notrace[*]:-(none)}"
printf 'capture_user=%s uid=%s gid=%s\n' "$capture_user" "$capture_uid" "$capture_gid"
printf 'private_dri=%s\n' "$dri_dir"
printf 'private_plugin_sha256=%s\n' "$actual_plugin_sha"

: > "$tracefs/trace"
printf '1\n' > "$tracefs/tracing_on"

set +e
runuser -u "$capture_user" -- env \
    HOME="$capture_home" USER="$capture_user" LOGNAME="$capture_user" \
    "$capture_script" private-instrumented "$dri_dir" 2>&1 |
    tee "$tmpdir/capture-output.txt"
pipeline_status=("${PIPESTATUS[@]}")
set -e
capture_status=${pipeline_status[0]}
tee_status=${pipeline_status[1]}

printf '0\n' > "$tracefs/tracing_on"
trace_text="$tmpdir/function-graph.log"
cat "$tracefs/trace" > "$trace_text"

capture_dir=$(sed -n 's/^capture_directory=//p' "$tmpdir/capture-output.txt" | tail -n 1)
if [[ -z $capture_dir || ! -d $capture_dir ]]; then
    printf 'ERROR: VAAPI harness did not report a capture directory\n' >&2
    exit 4
fi

identity_status=complete
for identity in 'index=0 engine=bsp' 'index=1 engine=vp' 'index=2 engine=ppp'; do
    if ! grep -Fq "phase=channel-identity $identity" "$capture_dir/ffmpeg.log"; then
        identity_status=missing
    fi
done

install -o "$capture_uid" -g "$capture_gid" -m 0644 "$trace_text" \
    "$capture_dir/function-graph.log"
meta_file="$tmpdir/function-graph-meta.txt"
{
    printf 'kernel=%s\n' "$(uname -r)"
    printf 'tracefs=%s\n' "$tracefs"
    printf 'trace_clock=%s\n' "$trace_clock_selected"
    printf 'function_graph_roots=%s\n' "${trace_roots[*]}"
    printf 'function_graph_notrace=%s\n' "${trace_notrace[*]:-(none)}"
    printf 'capture_user=%s uid=%s gid=%s\n' "$capture_user" "$capture_uid" "$capture_gid"
    printf 'capture_status=%s\n' "$capture_status"
    printf 'tee_status=%s\n' "$tee_status"
    printf 'mesa_channel_identity_records=%s\n' "$identity_status"
} > "$meta_file"
install -o "$capture_uid" -g "$capture_gid" -m 0644 "$meta_file" \
    "$capture_dir/function-graph-meta.txt"
runuser -u "$capture_user" -- bash -c \
    'cd -- "$1" && sha256sum function-graph.log function-graph-meta.txt >> SHA256SUMS' \
    _ "$capture_dir"
printf 'function_graph_capture=%s/function-graph.log\n' "$capture_dir"
printf 'mesa_channel_identity_records=%s\n' "$identity_status"

if [[ $tee_status -ne 0 ]]; then
    exit 4
fi
if [[ $capture_status -eq 0 && $identity_status != complete ]]; then
    printf 'capture invalid: missing Mesa BSP/VP/PPP channel identity records\n' >&2
    exit 4
fi
exit "$capture_status"
