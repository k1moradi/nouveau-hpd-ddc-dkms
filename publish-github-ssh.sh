#!/usr/bin/env bash
set -Eeuo pipefail

REPO_SSH="git@github.com:k1moradi/nouveau-hpd-ddc-dkms.git"
BRANCH="main"
COMMIT_MESSAGE="Initial import: Nouveau HPD/DDC DKMS patch"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ALLOW_UPDATE=0
DRY_RUN=0

usage() {
    cat <<USAGE
Usage: $(basename "$0") [options]

Publish the Nouveau HPD/DDC DKMS source tree to GitHub using your local
SSH key/ssh-agent. No GitHub token is used.

Options:
  --source DIR        Source directory to publish.
                      Default: $SOURCE_DIR
  --repo SSH_URL      Git SSH remote.
                      Default: $REPO_SSH
  --branch NAME       Branch to publish. Default: $BRANCH
  --message TEXT      Commit message.
  --update            Allow publishing into an already non-empty repository.
                      Existing unrelated files are preserved; matching project
                      files are updated.
  --dry-run           Prepare and show the commit, but do not push.
  -h, --help          Show this help.

Examples:
  ./$(basename "$0")
  ./$(basename "$0") --update
USAGE
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --source)
            (($# >= 2)) || die "--source requires a directory"
            SOURCE_DIR="$2"; shift 2 ;;
        --repo)
            (($# >= 2)) || die "--repo requires an SSH URL"
            REPO_SSH="$2"; shift 2 ;;
        --branch)
            (($# >= 2)) || die "--branch requires a name"
            BRANCH="$2"; shift 2 ;;
        --message)
            (($# >= 2)) || die "--message requires text"
            COMMIT_MESSAGE="$2"; shift 2 ;;
        --update)
            ALLOW_UPDATE=1; shift ;;
        --dry-run)
            DRY_RUN=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            die "unknown option: $1" ;;
    esac
done

for cmd in git ssh tar mktemp; do
    command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
done

SOURCE_DIR="$(cd -- "$SOURCE_DIR" 2>/dev/null && pwd)" || die "source directory not found: $SOURCE_DIR"

# Refuse to publish a random directory by requiring the core project files.
required=(
    README.md
    BUG-REPORT.md
    install.sh
    uninstall.sh
    verify-after-reboot.sh
    dkms/dkms.conf
    dkms/dkms-build.sh
    dkms/fix-nouveau-hpd-ddc.patch
)
for f in "${required[@]}"; do
    [[ -f "$SOURCE_DIR/$f" ]] || die "source tree is missing required file: $f"
done

case "$REPO_SSH" in
    git@github.com:*|ssh://git@github.com/*) ;;
    *)
        printf 'WARNING: repository is not the usual GitHub SSH form: %s\n' "$REPO_SSH" >&2
        ;;
esac

printf 'Source: %s\n' "$SOURCE_DIR"
printf 'Remote: %s\n' "$REPO_SSH"
printf 'Branch: %s\n' "$BRANCH"

printf '\nChecking SSH access and repository visibility...\n'
if ! remote_refs="$(git ls-remote "$REPO_SSH" 2>&1)"; then
    printf '%s\n' "$remote_refs" >&2
    die "cannot access the GitHub repository over SSH. Check your SSH key with: ssh -T git@github.com"
fi

if [[ -n "$remote_refs" && $ALLOW_UPDATE -ne 1 ]]; then
    cat >&2 <<'MSG'
ERROR: the remote repository is not empty.
Re-run with --update if you intentionally want to update the existing repository.
The script will preserve unrelated existing files and will not force-push.
MSG
    exit 1
fi

work="$(mktemp -d -t nouveau-hpd-ddc-publish.XXXXXX)"
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT

printf '\nCloning repository into a temporary worktree...\n'
if ! git clone --origin origin "$REPO_SSH" "$work/repo"; then
    die "git clone failed"
fi
repo="$work/repo"

# Empty repositories have no checked-out branch. Existing repositories may use
# another default branch, so explicitly create/switch to the requested branch.
if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git -C "$repo" switch -C "$BRANCH" --track "origin/$BRANCH"
elif git -C "$repo" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$repo" switch "$BRANCH"
else
    git -C "$repo" switch --orphan "$BRANCH"
fi

printf '\nCopying project source...\n'
# Copy the source tree while deliberately excluding generated/package artifacts
# and any accidental VCS/build state. tar preserves executable mode bits.
tar -C "$SOURCE_DIR" \
    --exclude='./.git' \
    --exclude='./.git/*' \
    --exclude='./*.deb' \
    --exclude='./*.tar.gz' \
    --exclude='./*.tgz' \
    --exclude='./*.zip' \
    --exclude='./*.SHA256' \
    --exclude='./debian/usr' \
    --exclude='./debian/usr/*' \
    --exclude='./build' \
    --exclude='./build/*' \
    --exclude='./.dkms-build' \
    --exclude='./.dkms-build/*' \
    -cf - . | tar -C "$repo" -xf -

# Add a conservative ignore file if the project does not already provide one.
if [[ ! -e "$repo/.gitignore" ]]; then
    cat > "$repo/.gitignore" <<'GITIGNORE'
# Generated release/package artifacts
*.deb
*.tar.gz
*.tgz
*.zip
*.SHA256

# Local build/staging output
build/
.dkms-build/
debian/usr/

# Editor/OS clutter
*~
*.swp
.DS_Store
GITIGNORE
fi

# Ensure the scripts that must be executable remain executable even if the
# source was unpacked on a filesystem that lost mode bits.
chmod +x \
    "$repo/install.sh" \
    "$repo/uninstall.sh" \
    "$repo/verify-after-reboot.sh" \
    "$repo/dkms/dkms-build.sh" \
    "$repo/dkms/dkms-clean.sh" \
    "$repo/dkms/dkms-post-install.sh" \
    "$repo/dkms/dkms-post-remove.sh"

[[ ! -f "$repo/debian/DEBIAN/postinst" ]] || chmod +x "$repo/debian/DEBIAN/postinst"
[[ ! -f "$repo/debian/DEBIAN/prerm" ]] || chmod +x "$repo/debian/DEBIAN/prerm"

git -C "$repo" add -A

if git -C "$repo" diff --cached --quiet; then
    printf '\nNothing to commit; repository already matches the source tree.\n'
    exit 0
fi

# Git requires an identity for commits. Respect the user's existing local/global
# config rather than inventing an identity.
if ! git -C "$repo" config user.name >/dev/null || ! git -C "$repo" config user.email >/dev/null; then
    cat >&2 <<'MSG'
ERROR: Git commit identity is not configured.
Set it once, for example:
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
Then run this script again.
MSG
    exit 1
fi

printf '\nFiles staged for publication:\n'
git -C "$repo" status --short

printf '\nCreating commit...\n'
git -C "$repo" commit -m "$COMMIT_MESSAGE"

printf '\nCommit created:\n'
git -C "$repo" --no-pager log -1 --oneline --decorate

if ((DRY_RUN)); then
    printf '\nDry run requested; not pushing.\n'
    exit 0
fi

printf '\nPushing over SSH (no force-push)...\n'
git -C "$repo" push -u origin "$BRANCH"

printf '\nPublished successfully.\n'
printf 'Repository: https://github.com/k1moradi/nouveau-hpd-ddc-dkms\n'
