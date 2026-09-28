#!/bin/bash

# DKMS 3.2.2 invokes the build script from a temporary copy at
# /var/lib/dkms/<module>/<version>/build and later removes that directory.
# Its sibling source link points to the persistent /usr/src package tree.
nouveau_nonstall_source_from_build() {
    local build_dir=$1
    local source_dir

    source_dir=$(readlink -f -- "$build_dir/../source") || return 1
    [ -d "$source_dir" ] || return 1
    printf '%s\n' "$source_dir"
}

nouveau_nonstall_state_file() {
    local source_dir=$1
    local kernelver=$2
    printf '%s/legacy-fifo-nonstall-state-%s\n' "$source_dir" "$kernelver"
}

nouveau_nonstall_write_state() {
    local state_file=$1
    local state=$2
    local temporary

    case "$state" in
        build-incomplete|patched|already-fixed|disabled) ;;
        *)
            echo "ERROR: invalid nonstall build state '$state'" >&2
            return 2
            ;;
    esac

    [ -d "${state_file%/*}" ] || return 1
    temporary=$(mktemp -- "$state_file.tmp.XXXXXX") || return 1
    if ! printf '%s\n' "$state" > "$temporary" || ! chmod 0644 "$temporary"; then
        rm -f -- "$temporary"
        return 1
    fi
    if ! mv -f -- "$temporary" "$state_file"; then
        rm -f -- "$temporary"
        return 1
    fi
    return 0
}

nouveau_nonstall_read_state() {
    local source_dir=$1
    local kernelver=$2
    local state_file

    state_file=$(nouveau_nonstall_state_file "$source_dir" "$kernelver") || return 1
    [ -r "$state_file" ] || return 1
    cat -- "$state_file"
}
