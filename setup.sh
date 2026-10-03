#!/usr/bin/env bash
# shellcheck shell=bash
# ════════════════════════════════════════════════════════════════════
# ║                                                                  ║
# ║  setup.sh — one-shot environment setup for PDF Layout Studio     ║
# ║                                                                  ║
# ║  Installs:                                                       ║
# ║    system  : qpdf, ghostscript, python3+venv (via your PM, sudo) ║
# ║    python  : ./.venv  from requirements.txt                      ║
# ║                                                                  ║
# ║  Progress is rendered by setup_progress.py (rich/tqdm/plain).    ║
# ║  Root password is asked up-front, on /dev/tty, and only when a   ║
# ║  privileged install is genuinely required.                       ║
# ║                                                                  ║
# ║  Usage:                                                          ║
# ║    ./setup.sh                  full setup                        ║
# ║    ./setup.sh --no-system      skip qpdf/ghostscript/venv pkgs   ║
# ║    ./setup.sh --recreate-venv  rebuild .venv from scratch        ║
# ║    ./setup.sh --skip-verify    skip the import + smoke checks    ║
# ════════════════════════════════════════════════════════════════════
# ── Strict mode ───────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
VENV_PY="$VENV/bin/python"
REQ_FILE="$SCRIPT_DIR/requirements.txt"
RENDERER="$SCRIPT_DIR/setup_progress.py"

NO_SYSTEM=0
RECREATE_VENV=0
SKIP_VERIFY=0

# Output helpers
#
# `say` writes straight to the terminal and is only used before the live
# renderer starts (or after it stops).  Everything else is funnelled into
# the renderer as a note so it can never corrupt the live table.
# ──────────────────────────────────────────────────────────────────────
if [[ -t 1 && "${NO_COLOR:-}" == "" ]]; then
    RST=$'\033[0m'; BLD=$'\033[1m'; DIM=$'\033[2m'
    RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; CYN=$'\033[36m'
else
    RST=""; BLD=""; DIM=""; RED=""; GRN=""; YEL=""; CYN=""
fi

say()  { printf '%s\n' "$*"; }
info() { note info "$*"; }
warn() { note warn "$*"; }
err()  { note err  "$*"; }
ok()   { note ok   "$*"; }
die()  { note err "$*"; stop_renderer; printf '%s%serror:%s %s\n' "$RED" "$BLD" "$RST" "$*" >&2; exit 1; }

# ══════════════════════════════════════════════════════════════════════
# Progress plumbing
#
# Every task is recorded in T_* arrays so the whole table can be replayed
# into a freshly spawned renderer.  That lets us upgrade plain → rich as
# soon as .venv exists without losing the display.
# ══════════════════════════════════════════════════════════════════════
FIFO=""; RENDER_PID=""; RENDER_PY=""; RENDER_MODE="none"
CURRENT_TASK=""

declare -a T_ID=() T_LABEL=() T_STATE=() T_DETAIL=()

json_escape() {
    local s
    s="$(printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037')"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
    s="${s//$'\t'/ }";  s="${s//$'\r'/ }";  s="${s//$'\n'/ }"
    printf '%s' "$s"
}

send() {
    [[ -n "$FIFO" ]] || return 0
    { printf '%s\n' "$1" >&3; } 2>/dev/null || true
}

t_index() {
    local i
    for i in "${!T_ID[@]}"; do
        [[ "${T_ID[$i]}" == "$1" ]] && { printf '%s' "$i"; return 0; }
    done
    return 1
}

_emit_add()   { local i=$1; send "{\"op\":\"task_add\",\"id\":\"$(json_escape "${T_ID[$i]}")\",\"label\":\"$(json_escape "${T_LABEL[$i]}")\"}"; }
_emit_start() { local i=$1; send "{\"op\":\"task_start\",\"id\":\"$(json_escape "${T_ID[$i]}")\",\"detail\":\"$(json_escape "${T_DETAIL[$i]}")\"}"; }
_emit_end()   {
    local i=$1 op=$2
    send "{\"op\":\"task_${op}\",\"id\":\"$(json_escape "${T_ID[$i]}")\",\"detail\":\"$(json_escape "${T_DETAIL[$i]}")\"}"
}

# Re-emit full task table into the (new) renderer
replay() {
    local i
    for i in "${!T_ID[@]}"; do
        _emit_add "$i"
        case "${T_STATE[$i]}" in
            running) _emit_start "$i"; _emit_end "$i" done   ;;
            done)    _emit_start "$i"; _emit_end "$i" done   ;;
            failed)  _emit_start "$i"; _emit_end "$i" fail   ;;
            skipped) _emit_start "$i"; _emit_end "$i" skip   ;;
        esac
    done
}

task_add() {
    T_ID+=("$1"); T_LABEL+=("$2"); T_STATE+=("pending"); T_DETAIL+=("")
    local i=$((${#T_ID[@]} - 1))
    _emit_add "$i"
}

task_start() {
    local i; i="$(t_index "$1")" || return 0
    T_STATE[$i]="running"; T_DETAIL[$i]="${2:-}"
    CURRENT_TASK="$1"
    _emit_start "$i"
}

task_log() {
    [[ -n "$1" ]] || return 0
    local i; i="$(t_index "$1")" || return 0
    [[ "${T_STATE[$i]}" == "running" ]] || return 0
    T_DETAIL[$i]="$2"
    send "{\"op\":\"task_log\",\"id\":\"$(json_escape "$1")\",\"line\":\"$(json_escape "$2")\"}"
}

task_done()  { local i; i="$(t_index "$1")" || return 0
               T_STATE[$i]="done"; [[ -n "${2:-}" ]] && T_DETAIL[$i]="$2"
               _emit_end "$i" done; }
task_fail()  { local i; i="$(t_index "$1")" || return 0
               T_STATE[$i]="failed"; T_DETAIL[$i]="${2:-}"; _emit_end "$i" fail; }
task_skip()  { local i; i="$(t_index "$1")" || return 0
               T_STATE[$i]="skipped"; T_DETAIL[$i]="${2:-}"; _emit_end "$i" skip; }

note() {
    local level="${1:-info}" msg="$2"
    if [[ -n "$FIFO" ]]; then
        send "{\"op\":\"note\",\"level\":\"$(json_escape "$level")\",\"msg\":\"$(json_escape "$msg")\"}"
    else
        # No renderer yet (or it died) — never swallow the message.
        case "$level" in
            err|warn) printf '%s\n' "$msg" >&2 ;;
            *)        printf '%s\n' "$msg" ;;
        esac
    fi
}

# Stream a command's output into the current task's detail line
_stream() { local line; while IFS= read -r line; do task_log "$CURRENT_TASK" "$line"; done; }

# Run a command under a task, streaming its output into the live display
run_logged() {
    local id=$1; shift
    task_start "$id" "$*"
    local rc=0
    set +e
    "$@" 2>&1 | _stream
    rc=${PIPESTATUS[0]}
    set -e
    if (( rc == 0 )); then task_done "$id"; else task_fail "$id" "exit $rc"; fi
    return "$rc"
}

start_renderer() {
    local py="${1:-}"
    [[ -n "$py" && -x "$py" ]] || py="$(command -v python3 || true)"
    [[ -n "$py" && -x "$py" ]] || return 1
    [[ -f "$RENDERER" ]] || return 1

    local f; f="$(mktemp -u "${TMPDIR:-/tmp}/pdftools-setup.XXXXXX")" || return 1
    mkfifo "$f" 2>/dev/null || { rm -f "$f"; return 1; }

    FIFO="$f"
    "$py" "$RENDERER" <"$FIFO" &
    RENDER_PID=$!
    RENDER_PY="$py"
    exec 3>"$FIFO"
    replay
    return 0
}

stop_renderer() {
    [[ -n "$FIFO" ]] || return 0
    { exec 3>&-; } 2>/dev/null || true
    wait "$RENDER_PID" 2>/dev/null || true
    rm -f "$FIFO"
    FIFO=""; RENDER_PID=""
}

cleanup() { stop_renderer; }
trap cleanup EXIT INT TERM

# ══════════════════════════════════════════════════════════════════════
# Privileged install helpers
# ══════════════════════════════════════════════════════════════════════
SUDO_PASS=""
PM=""

need_root() {
    (( NO_SYSTEM )) && return 1
    [[ "$PM" != "brew" ]] || return 1
    [[ $EUID -eq 0 ]] && return 1
    command -v sudo >/dev/null 2>&1 || return 1
    return 0
}

# Ask once, on the real terminal, before any privileged command runs
ask_sudo() {
    [[ $EUID -eq 0 ]] && return 0
    command -v sudo >/dev/null 2>&1 || die "Need root but 'sudo' is not installed."

    if sudo -n true 2>/dev/null; then
        info "sudo: passwordless access, no prompt needed"
        SUDO_PASS=""
        return 0
    fi

    # We must be able to read the password from the real terminal.  Fail loudly
    # instead of blocking on a tty that will never answer.
    if ! { exec 3<>/dev/tty; } 2>/dev/null; then
        die "sudo needs a password but /dev/tty is unavailable. Run from an interactive terminal, or install the system packages yourself."
    fi
    { exec 3>&-; } 2>/dev/null || true

    local pass="" attempt
    for attempt in 1 2 3; do
        printf '%s' "${BLD}sudo password${RST} (attempt ${attempt}/3): " >/dev/tty
        if ! IFS= read -r -s pass </dev/tty; then
            printf '\n' >/dev/tty
            die "Could not read a password from /dev/tty."
        fi
        printf '\n' >/dev/tty
        if [[ -z "$pass" ]]; then
            printf '%s\n' "${RED}Password must not be empty.${RST}" >/dev/tty
            continue
        fi
        if printf '%s\n' "$pass" | sudo -S -v 2>/dev/null; then
            SUDO_PASS="$pass"
            ok "sudo credentials accepted"
            return 0
        fi
        pass=""
        printf '%s\n' "${RED}Incorrect password.${RST}" >/dev/tty
    done
    die "Could not obtain sudo credentials."
}

# sudo_do <cmd...>  — run privileged, feeding the password we captured
sudo_do() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    elif [[ -z "$SUDO_PASS" ]]; then
        sudo -n "$@"
    else
        printf '%s\n' "$SUDO_PASS" | sudo -S -p '' "$@"
    fi
}

# ══════════════════════════════════════════════════════════════════════
# Platform detection
# ══════════════════════════════════════════════════════════════════════
detect_pm() {
    local c
    for c in apt-get dnf yum pacman zypper apk brew; do
        if command -v "$c" >/dev/null 2>&1; then PM="$c"; return 0; fi
    done
    return 1
}

# Canonical name -> this distro's package name(s)
pkg_for() {
    case "$1" in
        qpdf)        case "$PM" in brew) echo qpdf ;; *) echo qpdf ;; esac ;;
        ghostscript) case "$PM" in brew) echo ghostscript ;; *) echo ghostscript ;; esac ;;
        python3-venv)
            case "$PM" in
                apt-get) echo python3-venv python3-pip ;;
                dnf|yum) echo python3 python3-pip ;;
                pacman)  echo python ;;
                zypper)  echo python3 python3-pip ;;
                apk)     echo python3 py3-pip ;;
                brew)    echo python@3.12 ;;
            esac ;;
    esac
}

install_sys() {   # install_sys <canonical-pkg>...
    local canon; canon="$1"; shift
    local pkgs=() p
    for p in "$@"; do pkgs+=("$p"); done

    case "$PM" in
        apt-get)
            sudo_do env DEBIAN_FRONTEND=noninteractive apt-get update -qq || return 1
            sudo_do env DEBIAN_FRONTEND=noninteractive \
                apt-get install -y --no-install-recommends "${pkgs[@]}"
            ;;
        dnf)      sudo_do dnf install -y "${pkgs[@]}" ;;
        yum)      sudo_do yum install -y "${pkgs[@]}" ;;
        pacman)   sudo_do pacman -Sy --noconfirm --needed "${pkgs[@]}" ;;
        zypper)   sudo_do zypper --non-interactive install "${pkgs[@]}" ;;
        apk)      sudo_do apk add --no-cache "${pkgs[@]}" ;;
        brew)     brew install "${pkgs[@]}" ;;
        *)        err "No supported package manager (found '${PM}')."; return 1 ;;
    esac
}

py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }

find_python() {
    local c
    for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$c" >/dev/null 2>&1 && py_ok "$(command -v "$c")"; then
            command -v "$c"; return 0
        fi
    done
    return 1
}

# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════
usage() {
    sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^#[[:space:]]\{0,1\}//; s/^# ║\{0,1\}//'
}

main() {

while (( $# )); do
    case "$1" in
        --no-system)     NO_SYSTEM=1 ;;
        --recreate-venv) RECREATE_VENV=1 ;;
        --skip-verify)   SKIP_VERIFY=1 ;;
        -h|--help)       usage; exit 0 ;;
        *) err "Unknown option: $1"; say ""; usage; exit 2 ;;
    esac
    shift
done

# ── Banner ─────────────────────────────────────────────────────────────
say ""
say "${BLD}PDF Layout Studio — setup${RST}"
say "${CYN}$(uname -srm)${RST}  •  $SCRIPT_DIR"
say ""

start_renderer || warn "Live progress unavailable (no python3 yet) — using plain output."

# ── Task: preflight ────────────────────────────────────────────────────
task_add pre  "Preflight checks"
task_add sys  "System packages"
task_add venv "Virtual environment (.venv)"
task_add deps "Python dependencies"
task_add node "Node dependencies"
task_add ver  "Verification"

if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4) )); then
    task_fail pre "bash ${BASH_VERSION} too old"
    die "bash >= 4.4 required (merge_and_convert.sh uses 'mapfile -d')."
fi
detect_pm || { task_fail pre "no package manager"; die "No supported package manager found."; }

PY="$(find_python || true)"
if [[ -n "$PY" ]]; then
    task_done pre
    info "bash $BASH_VERSION  •  $PM  •  $("$PY" -V 2>&1)"
else
    task_skip pre "bash $BASH_VERSION  •  $PM  •  python3 not found yet"
fi

# ── Task: system packages ──────────────────────────────────────────────
NEED_PY_PKG=0
command -v python3 >/dev/null 2>&1 || NEED_PY_PKG=1

MISSING=()
command -v qpdf >/dev/null 2>&1 || MISSING+=(qpdf)
command -v gs   >/dev/null 2>&1 || MISSING+=(ghostscript)
(( NEED_PY_PKG )) && MISSING+=(python3-venv)

if [[ ${#MISSING[@]} -eq 0 ]]; then
    task_skip sys "qpdf $(qpdf --version 2>/dev/null | head -1 | cut -d' ' -f2), gs $(gs --version 2>/dev/null | head -1)"
elif (( NO_SYSTEM )); then
    task_skip sys "--no-system"
    note warn "Skipping system packages. Install them manually:"
    note info "  $PM: qpdf ghostscript python3-venv   (adjust for your distro)"
else
    note step "Installing: ${MISSING[*]}"
    # Ask for the root password up-front, before the first privileged command
    need_root && ask_sudo

    for canon in "${MISSING[@]}"; do
        mapfile -t names < <(pkg_for "$canon")
        [[ ${#names[@]} -gt 0 ]] || continue
        run_logged sys install_sys "$canon" "${names[@]}" \
            || note warn "Failed to install ${names[*]} — continuing."
    done

    # Re-verify; anything still missing becomes a warning, not a hard failure
    STILL=()
    command -v qpdf >/dev/null 2>&1 || STILL+=(qpdf)
    command -v gs   >/dev/null 2>&1 || STILL+=(ghostscript)
    if [[ ${#STILL[@]} -gt 0 ]]; then
        task_fail sys "missing: ${STILL[*]}"
        note warn "Still missing after install: ${STILL[*]}"
        note warn "  merge_and_convert.sh needs these; process.py does not."
    else
        task_done sys
    fi
fi

# ── Task: virtualenv ───────────────────────────────────────────────────
PY="$(find_python || true)"
if [[ -z "$PY" ]]; then
    task_fail venv "no python3 >= 3.10"
    note err "python3 >= 3.10 is required (see README.md)."
    die "Cannot continue without Python 3.10+."
fi

if (( RECREATE_VENV )) && [[ -d "$VENV" ]]; then
    note step "Recreating $VENV"
    rm -rf "$VENV"
fi

if [[ -x "$VENV_PY" ]]; then
    task_skip venv "$("$VENV_PY" -V 2>&1) (existing)"
else
    run_logged venv "$PY" -m venv "$VENV" \
        || { task_fail venv "venv module unavailable"
             note err "Install it, then re-run:  $PM install python3-venv"
             die "venv creation failed."; }
fi

# ── Upgrade the renderer to rich now that .venv exists ─────────────────
if [[ "$RENDER_MODE" != "rich" && -x "$VENV_PY" ]]; then
    if "$VENV_PY" -c 'import rich' 2>/dev/null; then
        stop_renderer
        start_renderer "$VENV_PY" && RENDER_MODE="rich"
    fi
fi

# ── Task: pip dependencies ─────────────────────────────────────────────
if [[ ! -f "$REQ_FILE" ]]; then
    task_fail deps "requirements.txt not found"
    die "Missing $REQ_FILE"
fi

run_logged deps "$VENV_PY" -m pip install --quiet --upgrade pip \
    || note warn "pip self-upgrade failed — continuing with the bundled pip."

run_logged deps "$VENV_PY" -m pip install -r "$REQ_FILE" \
    || die "pip install failed — see the output panel above."

# ── Task: verification ─────────────────────────────────────────────────
if (( SKIP_VERIFY )); then
    task_skip ver "--skip-verify"
else
    task_start ver "importing modules"
    if "$VENV_PY" - <<'PY' 2>&1 | _stream
import io, sys, contextlib

MODS = [("pymupdf", "fitz"), ("numpy", "numpy"), ("yt-dlp", "yt_dlp"),
        ("regex", "regex"), ("gdown", "gdown"), ("pathvalidate", "pathvalidate"),
        ("rich", "rich")]

# PyMuPDF prints a deprecation notice on stdout when process.py's `import fitz`
# runs. Capture it so it does not scramble the live display, then report it.
notices = []


def check(dist, mod):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m = __import__(mod)
    except Exception as e:
        print("  %-24s MISSING  (%s)" % ("%s  (%s)" % (dist, mod), e))
        return False
    for line in buf.getvalue().splitlines():
        if "deprecated" in line.lower():
            notices.append(line.strip())
    ver = getattr(m, "__version__", None) or getattr(m, "version", "")
    print("  %-24s %s" % ("%s  (%s)" % (dist, mod), ver or "installed"))
    return True


print("  %-24s %s" % ("python", sys.version.split()[0]))
missing = [d for d, m in MODS if not check(d, m)]
for n in notices:
    print("  note: %s" % n)
sys.exit(1 if missing else 0)
PY
    then
        task_start ver "process.py config.json --info"
        if "$VENV_PY" "$SCRIPT_DIR/process.py" "$SCRIPT_DIR/config.json" --info 2>&1 | _stream; then
            task_done ver "all modules import; process.py --info ok"
        else
            task_fail ver "process.py --info failed"
        fi
    else
        task_fail ver "missing modules"
    fi
fi

# ── Make shell entry points executable ────────────────────────────────
chmod +x "$SCRIPT_DIR"/*.sh 2>/dev/null || true

# ── Close out the display ──────────────────────────────────────────────
stop_renderer
say ""

QPDF_BIN="$(command -v qpdf || true)"
if [[ -z "$QPDF_BIN" ]]; then
    say "${YEL}⚠  qpdf is not installed — merge_and_convert.sh cannot merge PDFs.${RST}"
    say "${CYN}   $PM install qpdf${RST}"
fi
say "${GRN}${BLD}✔ Setup complete.${RST}"
say ""
say "${BLD}Activate the environment${RST}"
say "  source $VENV/bin/activate"
say ""
say "${BLD}Or call the interpreter directly${RST}"
say "  $VENV_PY process.py config.json --info"
say ""
say "${BLD}Typical workflow${RST}"
say "  ${CYN}1.${RST} scrape Drive links   $VENV_PY scrape_playlist.py \"<playlist url>\" -o links.json"
say "  ${CYN}2.${RST} download the PDFs  $VENV_PY download_pdfs.py links.json -d chem_pdfs"
say "  ${CYN}3.${RST} merge + A4 landscape  ./merge_and_convert.sh ./chem_pdfs"
say "  ${CYN}4.${RST} N-up layout        $VENV_PY process.py config.json ./chem_pdfs/"
say "  ${CYN}5.${RST} visual preview     open pdf-layout-studio.html"
say ""

}

# Only run when executed, not when sourced (sourcing is used by the tests).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi