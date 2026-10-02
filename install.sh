#!/usr/bin/env bash
# subpool installer: every ChatGPT seat you have, behind one Codex.
# https://github.com/memfactorduke/codex-load-balancer
#
#   curl -fsSL https://raw.githubusercontent.com/memfactorduke/codex-load-balancer/main/install.sh | bash
#
# With options, pass them after `bash -s --`:
#
#   curl -fsSL https://raw.githubusercontent.com/memfactorduke/codex-load-balancer/main/install.sh | bash -s -- --dry-run
#
# What it does, in order:
#   1. Checks this Mac: macOS 13 or later, Apple silicon or Intel, not run as root, and whether the Codex app is
#      installed (a warning if not; the real installer decides).
#   2. Finds a Python 3.11+ already on this Mac. If there is none, it gets one through uv, installing uv into
#      ~/.local/bin first if needed (it asks before doing that).
#   3. Downloads the subpool source of the latest release (or --version TAG) from GitHub as a tarball into a
#      temporary directory. Without any release it uses the main branch.
#   4. Runs `bin/subpool install` from that source. That is the real installer: it copies the code into
#      ~/.subpool, builds the pool, sets up the launchd agents and records the Codex config; the first seat's
#      sign-in points the Codex app at the pool. Every step of it is safe to repeat, so running this script again
#      upgrades in place and keeps your seats and settings.
#   5. Opens the Setup assistant, where you add your ChatGPT accounts, and prints what to do next.
#
# It never uses sudo, never edits your shell profile, and deletes its temporary directory when it exits.
# To undo an install: subpool uninstall --yes

set -euo pipefail

REPO="memfactorduke/codex-load-balancer"
REPO_URL="https://github.com/$REPO"
API_LATEST="https://api.github.com/repos/$REPO/releases/latest"
CODELOAD="https://codeload.github.com/$REPO/tar.gz"
UV_INSTALLER="https://astral.sh/uv/install.sh"
CODEX_BUNDLE_ID="com.openai.codex"
MIN_MACOS=13
UV_PYTHON="3.13"             # the Python uv provides when this Mac has no 3.11+ (subpool's installer uses the same)
: "${HOME:?HOME is not set}"
CODEXPOOL_HOME="$HOME/.subpool"

# Options
VERSION=""                   # --version TAG; empty = the latest release
GUI=1                        # --no-gui sets 0
DRY_RUN=0                    # --dry-run sets 1
YES=0                        # -y / --yes sets 1

# State, filled in as the script runs
HAVE_TTY=0                   # 1 when /dev/tty can be opened, so questions can be asked even under `curl | bash`
PROBLEMS=0                   # problems found in a dry run (a real run stops at the first)
UPGRADE=0                    # 1 when ~/.subpool already holds an install with at least one seat
PYTHON=""                    # a Python 3.11+ found on this Mac
UV=""                        # uv, when it provides the Python instead
NEED_UV=0                    # 1 when uv has to be installed first
PY_CMD=()                    # how to run the installer's Python: ("$PYTHON") or ("$UV" run ... python)
REF=""                       # the tag to install, or "main"
TARBALL_URL=""
WORK=""                      # temporary directory (created on first use, removed on exit)
SRC=""                       # the extracted source inside WORK
GUI_OPENED=0

usage() {
  cat <<EOF
subpool installer

Usage:
  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- [options]
  bash install.sh [options]

Options:
  --version TAG   install this release tag (for example v1.0.0), or main; default: the latest release
  --no-gui        don't open the Setup assistant at the end
  --dry-run       print every step and change nothing (nothing is downloaded either)
  -y, --yes       answer yes to every question, including installing uv when this Mac has no Python 3.11+
  -h, --help      show this help

Re-running upgrades an existing install in place. Undo with: subpool uninstall --yes
EOF
}

# -- output -----------------------------------------------------------------------------------------------

init_output() {
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    BOLD=$'\033[1m' DIM=$'\033[2m' GREEN=$'\033[32m' ORANGE=$'\033[33m' RED=$'\033[31m' RESET=$'\033[0m'
  else
    BOLD="" DIM="" GREEN="" ORANGE="" RED="" RESET=""
  fi
}

say() { printf '%s\n' "$*"; }
heading() { printf '\n%s%s%s\n' "$BOLD" "$1" "$RESET"; }
ok() { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }

# warn TEXT [FIX]
warn() {
  printf '  %s!%s %s\n' "$ORANGE" "$RESET" "$1"
  if [ -n "${2:-}" ]; then printf '      %s→ %s%s\n' "$DIM" "$2" "$RESET"; fi
}

# die TEXT [FIX]: stop here. The EXIT trap removes the temporary directory.
die() {
  printf '  %s✗%s %s\n' "$RED" "$RESET" "$1" >&2
  if [ -n "${2:-}" ]; then printf '      → %s\n' "$2" >&2; fi
  exit 1
}

# problem TEXT [FIX]: something that stops a real install. A dry run notes it and carries on printing the plan.
problem() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"
    if [ -n "${2:-}" ]; then printf '      → %s\n' "$2"; fi
    PROBLEMS=$((PROBLEMS + 1))
  else
    die "$@"
  fi
}

# change TEXT: announce a step that changes something. In a dry run that is all that happens.
change() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '  %s[dry-run]%s %s\n' "$DIM" "$RESET" "$1"
  else
    printf '  + %s\n' "$1"
  fi
}

# tilde PATH: PATH with the home folder shown as ~.
tilde() {
  case "$1" in
    "$HOME") printf '%s\n' "~" ;;
    "$HOME"/*) printf '%s/%s\n' "~" "${1#"$HOME"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

# -- questions --------------------------------------------------------------------------------------------

detect_tty() {
  # Under `curl | bash` stdin is the script itself, so questions are read from the terminal instead.
  # Opening /dev/tty fails when there is no controlling terminal (CI, cron, some SSH sessions).
  if (exec </dev/tty) 2>/dev/null; then HAVE_TTY=1; fi
}

# ask QUESTION SAFE: 0 for yes. The default answer is yes. --yes answers yes to everything. Without a terminal
# to ask on, a safe step (SAFE=1: subpool's own install, which is idempotent and undone by uninstall) goes
# ahead, and any other step is declined.
ask() {
  local question=$1 safe=$2 reply=""
  if [ "$YES" = 1 ]; then
    say "  $question [Y/n] yes (--yes)"
    return 0
  fi
  if [ "$HAVE_TTY" != 1 ]; then
    if [ "$safe" = 1 ]; then
      say "  $question [Y/n] yes (no terminal to ask on; this step is safe to repeat and to undo)"
      return 0
    fi
    say "  $question [Y/n] no (no terminal to ask on; re-run with --yes to allow it)"
    return 1
  fi
  while true; do
    printf '  %s [Y/n] ' "$question" >/dev/tty
    read -r reply </dev/tty || reply="n"
    case "$reply" in
      "" | [Yy] | [Yy][Ee][Ss]) return 0 ;;
      [Nn] | [Nn][Oo]) return 1 ;;
      *) printf '  Please answer y or n.\n' >/dev/tty ;;
    esac
  done
}

# -- temporary directory ----------------------------------------------------------------------------------

ensure_workdir() {
  if [ -z "$WORK" ]; then
    local tmp=${TMPDIR:-/tmp}
    WORK=$(mktemp -d "${tmp%/}/subpool-install.XXXXXX")
  fi
}

cleanup() {
  if [ -n "$WORK" ] && [ -d "$WORK" ]; then rm -rf "$WORK"; fi
}

on_interrupt() {
  printf '\nStopped. Running this again is safe: every step checks first and picks up where it left off.\n' >&2
  exit 130
}

# -- 1. this Mac ------------------------------------------------------------------------------------------

check_mac() {
  heading "This Mac"
  if [ "$(uname -s)" != "Darwin" ]; then
    die "subpool runs on macOS only (it relies on launchd, the Keychain and the menu bar). Nothing was changed."
  fi
  if [ "$(id -u)" = 0 ]; then
    problem "this is running as root" \
      "run it as yourself, without sudo: subpool installs into your home folder and never needs sudo"
  fi

  local version major arch chip
  version=$(sw_vers -productVersion 2>/dev/null || echo "0")
  major=${version%%.*}
  case "$major" in "" | *[!0-9]*) major=0 ;; esac
  arch=$(uname -m)
  case "$arch" in
    arm64) chip="Apple silicon" ;;
    x86_64)
      if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
        chip="Apple silicon (this shell runs under Rosetta)"
      else
        chip="Intel"
      fi
      ;;
    *) chip=$arch ;;
  esac
  if [ "$major" -lt "$MIN_MACOS" ]; then
    problem "macOS $version on $chip: subpool needs macOS $MIN_MACOS (Ventura) or later" \
      "update macOS (Software Update), then run this again"
  else
    ok "macOS $version on $chip"
  fi
  case "$chip" in
    Intel) warn "Intel Macs should work but are untested" ;;
    Apple*) ;;
    *) problem "unsupported processor: $arch" "subpool supports Apple silicon and Intel Macs" ;;
  esac

  check_codex_app

  if [ -d "$CODEXPOOL_HOME/.git" ]; then
    problem "$(tilde "$CODEXPOOL_HOME") is a git checkout, so this script would overwrite tracked files" \
      "update it with git instead: cd ~/.subpool && git pull && ./bin/subpool install"
  elif [ -f "$CODEXPOOL_HOME/bin/subpool" ] && has_seats; then
    UPGRADE=1
    ok "subpool is installed in $(tilde "$CODEXPOOL_HOME"): this upgrades it in place (seats and settings are kept)"
  elif [ -f "$CODEXPOOL_HOME/bin/subpool" ]; then
    # `subpool install` copies its code in before the long steps, so this is also what an install that was
    # stopped part way leaves behind. Either way the first-install steps (add accounts, reopen Codex) still apply.
    ok "subpool is in $(tilde "$CODEXPOOL_HOME") with no seats yet: this finishes the install in place"
  else
    ok "subpool is not installed yet: it goes into $(tilde "$CODEXPOOL_HOME")"
  fi
}

# app_cli APP: prints the codex CLI inside the app bundle APP and succeeds, or fails when it has none. The Codex app
# 26.924 and later keep it in Contents/Resources/codex-cli/bin; older builds in Contents/Resources.
app_cli() {
  local rel
  for rel in Contents/Resources/codex-cli/bin/codex Contents/Resources/codex; do
    if [ -f "$1/$rel" ] && [ -x "$1/$rel" ]; then
      printf '%s\n' "$1/$rel"
      return 0
    fi
  done
  return 1
}

check_codex_app() {
  local app="" cli="" candidate id
  local candidates=()
  # The Codex app is the bundle with id $CODEX_BUNDLE_ID, whatever it is called (ChatGPT.app today). Spotlight
  # finds it wherever it is; the usual places cover a Mac with Spotlight indexing turned off.
  if command -v mdfind >/dev/null 2>&1; then
    while IFS= read -r candidate; do
      candidates+=("$candidate")
    done < <(mdfind "kMDItemCFBundleIdentifier == '$CODEX_BUNDLE_ID'" 2>/dev/null | grep '\.app$' || true)
  fi
  candidates+=(/Applications/ChatGPT.app /Applications/Codex.app \
    "$HOME/Applications/ChatGPT.app" "$HOME/Applications/Codex.app")
  for candidate in "${candidates[@]}"; do
    [ -d "$candidate" ] || continue
    id=$(plutil -extract CFBundleIdentifier raw -o - "$candidate/Contents/Info.plist" 2>/dev/null || true)
    [ "$id" = "$CODEX_BUNDLE_ID" ] || continue
    [ -n "$app" ] || app=$candidate
    if cli=$(app_cli "$candidate"); then
      app=$candidate
      break
    fi
  done
  if [ -n "$cli" ]; then
    ok "Codex app: $(tilde "$app") (its codex CLI: ${cli#"$app"/})"
  elif [ -n "$app" ] && command -v codex >/dev/null 2>&1; then
    warn "Codex app: $(tilde "$app"), with no codex CLI inside; subpool uses $(tilde "$(command -v codex)")" \
      "update the Codex app to use the CLI it ships with"
  elif [ -n "$app" ]; then
    warn "Codex app: $(tilde "$app"), with no codex CLI inside" \
      "update the Codex app, or set \"codex_bin\" in ~/.subpool/settings.json to a codex CLI"
  elif command -v codex >/dev/null 2>&1; then
    warn "Codex app not found (bundle id $CODEX_BUNDLE_ID); the codex CLI at $(tilde "$(command -v codex)") will do" \
      "subpool is made for the Codex app: install it from OpenAI and sign in to use the pool from the app"
  else
    warn "Codex app not found (bundle id $CODEX_BUNDLE_ID)" \
      "install the Codex app from OpenAI and sign in first; subpool install stops at its preflight without it"
  fi
}

# setting KEY: prints KEY from subpool's settings.json (~ expanded) and succeeds, or fails when the file, the
# key or a value is missing (null counts as missing). plutil reads JSON, so this needs no Python.
setting() {
  local file="${CODEXPOOL_SETTINGS:-$CODEXPOOL_HOME/settings.json}" value
  [ -f "$file" ] || return 1
  value=$(plutil -extract "$1" raw -o - "$file" 2>/dev/null) || return 1
  [ -n "$value" ] || return 1
  case "$value" in
    \~) value=$HOME ;;
    \~/*) value="$HOME/${value#\~/}" ;;
  esac
  printf '%s\n' "$value"
}

# has_seats: succeeds when ~/.subpool/auth holds a seat login, the same test `subpool install` uses to tell
# an upgrade from a first install. It only checks that a file exists; it never reads one.
has_seats() {
  local f
  for f in "$CODEXPOOL_HOME"/auth/codex-*.json; do
    [ -e "$f" ] && return 0
  done
  return 1
}

# -- 2. Python --------------------------------------------------------------------------------------------

# python_ok PYTHON: prints the version and succeeds when PYTHON is 3.11+ with tarfile's data filter, which is
# what subpool's installer needs to build the pool, and it has CA certificates, since that installer downloads
# over HTTPS. Exit 3: too old. Exit 4: new enough, but no CA certificates (a python.org Python whose
# "Install Certificates.command" was never run); get_default_verify_paths() reports a missing file as None.
python_ok() {
  "$1" -c 'import os, ssl, sys, tarfile
print("%d.%d.%d" % sys.version_info[:3])
if sys.version_info < (3, 11) or not hasattr(tarfile, "data_filter"):
    sys.exit(3)
p = ssl.get_default_verify_paths()
sys.exit(0 if p.cafile or (p.capath and os.listdir(p.capath)) else 4)' \
    </dev/null 2>/dev/null
}

have_command_line_tools() {
  xcode-select -p >/dev/null 2>&1
}

find_python() {
  local candidate path version installed rc noted=0
  # An existing install's own interpreter first (a venv uv made is often the only 3.11+ on the Mac).
  installed=$(setting python || printf '%s\n' "$CODEXPOOL_HOME/.venv/bin/python")
  for candidate in "$installed" python3.13 python3.12 python3.11 python3.14 python3 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    path=$(command -v "$candidate" 2>/dev/null) || continue
    case "$path" in
      # Without the Command Line Tools, /usr/bin/python3 is a stub that pops up an install dialog.
      /usr/bin/*) have_command_line_tools || continue ;;
    esac
    version=$(python_ok "$path") && rc=0 || rc=$?
    if [ "$rc" = 0 ]; then
      PYTHON=$path
      ok "Python $version ($(tilde "$PYTHON"))"
      return 0
    fi
    # Several names (python3, python3.13, the framework path) usually lead to the same Python: say it once.
    if [ "$rc" = 4 ] && [ "$noted" = 0 ]; then
      noted=1
      warn "Python $version ($(tilde "$path")) has no CA certificates, so it cannot download over HTTPS; skipped" \
        "if it is python.org's Python, run \"Install Certificates.command\" in /Applications/Python 3.x to use it"
    fi
  done
  return 1
}

find_uv() {
  local candidate
  for candidate in "$(command -v uv 2>/dev/null || true)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
    /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      UV=$candidate
      return 0
    fi
  done
  return 1
}

choose_python() {
  heading "Python"
  if find_python; then
    PY_CMD=("$PYTHON")
    return
  fi
  if find_uv; then
    ok "no usable Python 3.11+ here; uv ($(tilde "$UV")) provides Python $UV_PYTHON"
  else
    NEED_UV=1
    UV="$HOME/.local/bin/uv"
    warn "no usable Python 3.11+ and no uv on this Mac" \
      "this script can install uv, Astral's Python manager, into ~/.local/bin; uv then provides Python $UV_PYTHON"
    say "      uv comes from $UV_INSTALLER; it needs no sudo and this script leaves your shell profile alone."
    say "      To use your own Python instead, install Python 3.11+ (python.org, or Homebrew) and run this again."
    if [ "$HAVE_TTY" != 1 ] && [ "$YES" != 1 ]; then
      warn "without a terminal to ask on, installing uv needs --yes" \
        "re-run with: curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- --yes"
    fi
  fi
  PY_CMD=("$UV" run --no-project --python "$UV_PYTHON" python)
}

install_uv() {
  if [ "$DRY_RUN" = 1 ]; then
    change "download the uv installer from $UV_INSTALLER and install uv into ~/.local/bin"
    return
  fi
  ensure_workdir
  local script="$WORK/uv-install.sh"
  change "download the uv installer from $UV_INSTALLER"
  curl -fsSL --proto '=https' --tlsv1.2 --connect-timeout 15 -o "$script" "$UV_INSTALLER" ||
    die "could not download the uv installer from $UV_INSTALLER" \
      "check your internet connection, or install Python 3.11+ yourself, then run this again"
  change "install uv into ~/.local/bin (your shell profile is left alone)"
  UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh "$script" </dev/null ||
    die "the uv installer failed (its output is above)" "install Python 3.11+ yourself, then run this again"
  [ -x "$UV" ] || die "uv is not at $(tilde "$UV") after installing it" \
    "install Python 3.11+ yourself (python.org, or Homebrew), then run this again"
  ok "uv: $(tilde "$UV")"
}

fetch_uv_python() {
  # uv run would download Python on first use anyway; doing it here makes a failure easy to read.
  # UV_PYTHON_INSTALL_BIN=0 keeps uv from also putting a python3.13 command in ~/.local/bin (and warning that
  # the folder is not on PATH): subpool only needs uv's managed copy. It is an environment variable rather
  # than --no-bin so that an older uv ignores it instead of stopping at an unknown flag.
  change "get Python $UV_PYTHON through uv (uv python install $UV_PYTHON)"
  if [ "$DRY_RUN" = 1 ]; then return; fi
  UV_PYTHON_INSTALL_BIN=0 "$UV" python install "$UV_PYTHON" </dev/null ||
    die "uv could not get Python $UV_PYTHON (its output is above)" \
      "check your internet connection, or install Python 3.11+ yourself, then run this again"
}

# -- 3. which version -------------------------------------------------------------------------------------

valid_ref() {
  case "$1" in
    "" | -* | .* | *..* | *[!A-Za-z0-9._-]*) return 1 ;;
    *) return 0 ;;
  esac
}

tarball_url() {
  if [ "$1" = "main" ]; then
    printf '%s/refs/heads/main\n' "$CODELOAD"
  else
    printf '%s/refs/tags/%s\n' "$CODELOAD" "$1"
  fi
}

# latest_release_tag: the newest release's tag from the GitHub API. It fails when there is no release (404) and
# also when the API refuses (403: the 60 requests an hour it allows each address, shared behind office NAT or a VPN).
latest_release_tag() {
  curl -fsSL --proto '=https' --connect-timeout 15 --max-time 30 \
    -H 'Accept: application/vnd.github+json' "$API_LATEST" 2>/dev/null |
    grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/'
}

# latest_release_tag_web: the same answer from github.com's /releases/latest redirect, which has no API rate
# limit. Without a release GitHub lands on /releases, and this fails.
latest_release_tag_web() {
  local url
  url=$(curl -fsSL --proto '=https' --connect-timeout 15 --max-time 30 -o /dev/null -w '%{url_effective}' \
    "$REPO_URL/releases/latest" 2>/dev/null) || return 1
  case "$url" in
    */releases/tag/*) printf '%s\n' "${url##*/releases/tag/}" ;;
    *) return 1 ;;
  esac
}

resolve_version() {
  heading "Version"
  if [ -n "$VERSION" ]; then
    valid_ref "$VERSION" || die "not a valid tag: $VERSION" "use a release tag such as v1.0.0 (see $REPO_URL/releases), or main"
    REF=$VERSION
    TARBALL_URL=$(tarball_url "$REF")
    if [ "$REF" = "main" ]; then
      ok "main branch (from --version main): the newest code, not a release"
    else
      ok "$REF (from --version)"
    fi
    return
  fi
  if [ "$DRY_RUN" = 1 ]; then
    change "look up the latest release on GitHub (its API, or $REPO_URL/releases/latest); without one, use main"
    REF="<latest release>"
    TARBALL_URL=$(tarball_url "$REF")
    return
  fi
  local tag=""
  if { tag=$(latest_release_tag) || tag=$(latest_release_tag_web); } && valid_ref "$tag"; then
    REF=$tag
    ok "latest release: $REF"
  else
    REF="main"
    warn "no release found on GitHub (none published yet, or GitHub did not answer)" \
      "installing from the main branch instead; pass --version TAG to pick a release"
  fi
  TARBALL_URL=$(tarball_url "$REF")
}

# -- 4. download and install ------------------------------------------------------------------------------

confirm() {
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  local name="subpool $REF" what question
  if [ "$REF" = "main" ]; then name="subpool (main branch)"; fi
  what="install $name into ~/.subpool"
  question="Install $name into ~/.subpool?"
  if [ "$UPGRADE" = 1 ]; then
    what="upgrade ~/.subpool to $name"
    question="Upgrade ~/.subpool to $name?"
  fi
  if [ "$NEED_UV" = 1 ]; then
    # uv is someone else's software, so this answer is not assumed without a terminal.
    ask "Install uv into ~/.local/bin, then $what?" 0 ||
      die "Nothing was changed." "install Python 3.11+ (python.org, or Homebrew) or uv yourself, then run this again"
  else
    ask "$question" 1 || die "Nothing was changed."
  fi
}

fetch_source() {
  heading "Download"
  if [ "$DRY_RUN" = 1 ]; then
    change "download $TARBALL_URL"
    change "extract it into a temporary directory (deleted when this script exits)"
    return
  fi
  ensure_workdir
  change "download $TARBALL_URL"
  if ! curl -fsSL --proto '=https' --connect-timeout 15 --retry 2 -o "$WORK/source.tar.gz" "$TARBALL_URL"; then
    if [ -n "$VERSION" ]; then
      die "could not download $REF" "check that the tag exists ($REPO_URL/tags) and that you are online"
    fi
    die "could not download $TARBALL_URL" "check your internet connection, then run this again"
  fi
  mkdir "$WORK/src"
  tar -xzf "$WORK/source.tar.gz" -C "$WORK/src" --strip-components 1 ||
    die "could not extract the download" "run this again; if it keeps failing, open an issue: $REPO_URL/issues"
  [ -f "$WORK/src/bin/subpool" ] || die "the download has no bin/subpool" "is $REF a subpool release? See $REPO_URL/releases"
  SRC="$WORK/src"
  ok "source extracted to a temporary directory"
}

run_install() {
  heading "Install"
  if [ "$DRY_RUN" = 1 ]; then
    local how
    if [ -n "$PYTHON" ]; then how=$(tilde "$PYTHON"); else how="uv run --python $UV_PYTHON python"; fi
    change "run: $how bin/subpool install (from the downloaded source)"
    if [ "$UPGRADE" = 1 ]; then
      say "      It checks this Mac again, copies the new code into ~/.subpool and changes only what differs."
      say "      Seats, settings, logins and lanes stay as they are."
    else
      say "      It checks this Mac again, then: copies the code into ~/.subpool, gets Go from go.dev, builds the"
      say "      gated CLIProxyAPI pool, stores a management key in the Keychain, starts the launchd agents (pool,"
      say "      guard, menu bar), records the Codex config (the first account's sign-in points Codex at the pool)"
      say "      and writes the subpool command to ~/.local/bin."
    fi
    return
  fi
  say "  Running subpool's installer (bin/subpool install); every step below is safe to repeat."
  local rc=0
  # stdin comes from /dev/null: under `curl | bash` it is the rest of this script. CODEXPOOL_VIA_INSTALLER tells
  # it to leave the next steps to finish() below, so a first install shows one list of them.
  (cd "$SRC" && CODEXPOOL_VIA_INSTALLER=1 "${PY_CMD[@]}" bin/subpool install) </dev/null || rc=$?
  if [ "$rc" != 0 ]; then
    die "subpool install stopped (exit $rc); its output above says why" \
      "fix that and run this again; the install picks up where it stopped"
  fi
}

# -- 5. Setup assistant and next steps --------------------------------------------------------------------

# The interpreter with PyObjC: settings.json's menubar_python, else its python, else subpool's own venv.
menubar_python() {
  setting menubar_python || setting python || printf '%s\n' "$CODEXPOOL_HOME/.venv/bin/python"
}

open_setup_assistant() {
  heading "Setup assistant"
  local app="$CODEXPOOL_HOME/menubar/subpool_settings.py" py pid status
  if [ "$GUI" != 1 ]; then
    ok "skipped (--no-gui)"
    return
  fi
  if [ "$UPGRADE" = 1 ]; then
    ok "skipped (your seats are set up; to add more, choose Add a ChatGPT account… in the menu bar)"
    return
  fi
  if [ "$HAVE_TTY" != 1 ]; then
    ok "skipped (not run from a terminal)"
    return
  fi
  if [ -n "${SSH_CONNECTION:-}" ]; then
    ok "skipped (this is an SSH session; open it on the Mac itself)"
    return
  fi
  py=$(menubar_python)
  if [ "$DRY_RUN" = 1 ]; then
    change "open it: $(tilde "$py") $(tilde "$app") --pane setup-welcome"
    return
  fi
  if [ ! -f "$app" ]; then
    warn "this version has no Setup assistant" "add each ChatGPT account with: subpool login \"<Label>\" --priority <n>"
    return
  fi
  if [ ! -x "$py" ]; then
    warn "no Python for the Setup assistant at $(tilde "$py")" \
      "run subpool doctor; then open it with: <python with PyObjC> ~/.subpool/menubar/subpool_settings.py"
    return
  fi
  change "open the Setup assistant"
  nohup "$py" "$app" --pane setup-welcome </dev/null >/dev/null 2>&1 &
  pid=$!
  sleep 2
  # Still running, or exited 0 because a window that was already open (the menu bar app opens the Setup
  # assistant by itself when the pool has no seats) took over: either way it is on screen.
  status=0
  if kill -0 "$pid" 2>/dev/null || { wait "$pid" || status=$?; [ "$status" = 0 ]; }; then
    GUI_OPENED=1
    ok "it is open: add your ChatGPT accounts there"
  else
    warn "the Setup assistant closed right away (exit $status)" \
      "open it yourself to see why: $(tilde "$py") ~/.subpool/menubar/subpool_settings.py --pane setup-welcome"
  fi
}

finish() {
  if [ "$DRY_RUN" = 1 ]; then
    say ""
    if [ "$PROBLEMS" -gt 0 ]; then
      say "Dry run: nothing was downloaded or changed. The $PROBLEMS problem(s) marked ✗ would stop a real install."
    else
      say "Dry run: nothing was downloaded or changed."
    fi
    # No git clone and no ./bin/subpool here: without the Command Line Tools both are stubs that pop up the
    # Xcode tools dialog, on exactly the Macs this script is made for. The Python found above runs it instead.
    local py=""
    if [ -n "$PYTHON" ]; then
      py=$(tilde "$PYTHON")
    elif [ "$NEED_UV" = 0 ] && [ -n "$UV" ]; then
      py="$(tilde "$UV") run --no-project --python $UV_PYTHON python"
    fi
    if [ -n "$py" ]; then
      # The dry run doesn't look up the latest release, so this previews main unless --version named a tag.
      local which="the main branch"
      if [ -n "$VERSION" ] && [ "$VERSION" != main ]; then which=$VERSION; fi
      say "subpool's own installer checks again and prints each of its steps. To see those for $which without"
      say "changing anything (no git or Xcode tools needed):"
      say "  mkdir subpool-src && curl -fsSL $(tarball_url "${VERSION:-main}") | tar -xz -C subpool-src --strip-components 1"
      say "  $py subpool-src/bin/subpool install --dry-run"
      if [ -z "$VERSION" ]; then
        say "(The real install uses the latest release. To preview a release, add --version <tag> to this script.)"
      fi
    fi
    if [ "$PROBLEMS" -gt 0 ]; then exit 1; fi
    return
  fi

  # How to type the command: ~/.local/bin/subpool until that folder is on PATH (the installer says how).
  local cmd="subpool" label
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) cmd="$(tilde "$HOME/.local/bin")/subpool" ;;
  esac
  heading "Next"
  if [ "$UPGRADE" = 1 ]; then
    say "  subpool is up to date ($REF). Your seats, settings and logins are as they were."
    label=$(setting menubar_label || echo "com.subpool.menubar")
    say "  Restart the menu bar app to load its new version:"
    say "    launchctl kickstart -k gui/\$(id -u)/$label"
    say "  Check everything: $cmd doctor (it should end with OK)"
  else
    if [ "$GUI_OPENED" = 1 ]; then
      say "  1. Add your ChatGPT accounts in the Setup assistant."
    else
      say "  1. Add each ChatGPT account: $cmd login \"<Label>\" --priority <n>"
      say "     (for an account your browser is not signed in to, add --no-open and use a private window)"
    fi
    say "  2. After the first account is in, quit the Codex app fully (⌘Q) and reopen it, so it goes through"
    say "     the pool. Until then Codex keeps its own login and works as before."
    say "  3. Check everything: $cmd doctor (it should end with OK)"
  fi
  say "  Docs: $REPO_URL · Undo: $cmd uninstall --yes"
}

# -- main -------------------------------------------------------------------------------------------------

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --version)
        [ $# -ge 2 ] || die "--version needs a tag, for example: --version v1.0.0"
        VERSION=$2
        shift 2
        ;;
      --version=*)
        VERSION=${1#--version=}
        shift
        ;;
      --no-gui) GUI=0; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -y | --yes) YES=1; shift ;;
      -h | --help) usage; exit 0 ;;
      *) die "unknown option: $1" "see: bash install.sh --help" ;;
    esac
  done
}

main() {
  init_output
  parse_args "$@"
  trap cleanup EXIT
  trap on_interrupt INT TERM
  detect_tty

  say "${BOLD}subpool installer${RESET}: every ChatGPT seat you have, behind one Codex."
  if [ "$DRY_RUN" = 1 ]; then
    say "Dry run: nothing is downloaded or changed; each step says what it would do."
  fi

  check_mac
  choose_python
  resolve_version
  confirm
  if [ -z "$PYTHON" ]; then
    heading "uv"
    if [ "$NEED_UV" = 1 ]; then install_uv; fi
    fetch_uv_python
  fi
  fetch_source
  run_install
  open_setup_assistant
  finish
}

# Everything above only defines functions, so a download cut short cannot run half a script.
main "$@"
