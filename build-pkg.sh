#!/usr/bin/env bash
# =============================================================================
#  Local Arch package builder
#
#  Builds a <pkgname>-<ver>-<rel>-<arch>.pkg.tar.zst straight from the current
#  working tree (uncommitted changes included), using packaging/PKGBUILD. The
#  source tarball is generated locally — nothing is fetched from GitHub.
#
#  Usage:
#    ./build-pkg.sh              build the package into ./dist-pkg/
#    ./build-pkg.sh --install    build, then pacman -U the result
#    ./build-pkg.sh --clean      remove ./dist-pkg/ and exit
# =============================================================================
set -euo pipefail

# ── Top-level directory name inside the source tarball. Must match the dir that
#    packaging/PKGBUILD's package()/build() cd's into (i.e. "<TOPDIR>-<ver>"). ──
TOPDIR="entropy-shield"

# ── Locate the repo (this script lives at the repo root) ──────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_SRC="$ROOT/packaging"
OUT="$ROOT/dist-pkg"

# ── Colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
else
  GREEN=''; BLUE=''; YELLOW=''; RED=''; RESET=''
fi
info() { echo -e "${BLUE}[*]${RESET} $*"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
die()  { echo -e "${RED}[✗]${RESET} $*" >&2; exit 1; }

DO_INSTALL=false
for arg in "$@"; do
  case "$arg" in
    --install) DO_INSTALL=true ;;
    --clean)   rm -rf "$OUT"; ok "Removed $OUT"; exit 0 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^#\s\?//'; exit 0 ;;
    *)         die "Unknown option: $arg" ;;
  esac
done

command -v makepkg >/dev/null 2>&1 || die "makepkg not found — run this on Arch (pacman)."
[[ -f "$PKG_SRC/PKGBUILD" ]] || die "Missing $PKG_SRC/PKGBUILD"

# ── Read pkgname / pkgver from the PKGBUILD (single source of truth) ───────────
PKGNAME="$(sed -n 's/^pkgname=//p' "$PKG_SRC/PKGBUILD" | tr -d '"'"'"'"')"
VER="$(sed -n 's/^pkgver=//p' "$PKG_SRC/PKGBUILD" | tr -d '"'"'"'"')"
[[ -n "$PKGNAME" ]] || die "Could not read pkgname from packaging/PKGBUILD"
[[ -n "$VER" ]]     || die "Could not read pkgver from packaging/PKGBUILD"
info "Building $PKGNAME $VER from working tree"

# ── Fresh build directory ─────────────────────────────────────────────────────
BUILD="$OUT/build"
rm -rf "$BUILD" "$OUT/stage"
mkdir -p "$BUILD"

# ── Stage a clean copy of the working tree into "<TOPDIR>-<ver>" ───────────────
STAGE="$OUT/stage/${TOPDIR}-${VER}"
mkdir -p "$STAGE"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude='/packaging' \
    --exclude='/dist-pkg' \
    --exclude='/.git' \
    --exclude='/build' \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.egg-info/' \
    --exclude='/screenshots' \
    --exclude='/Screenshots' \
    --exclude='*.AppImage' \
    --exclude='.mypy_cache/' \
    --exclude='.pytest_cache/' \
    "$ROOT"/ "$STAGE"/
else
  cp -a "$ROOT"/. "$STAGE"/
  rm -rf "$STAGE/packaging" "$STAGE/dist-pkg" "$STAGE/.git" "$STAGE/build"
  find "$STAGE" -type d -name venv -prune -exec rm -rf {} +
  find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
  find "$STAGE" -type d -name '*.egg-info' -prune -exec rm -rf {} +
  find "$STAGE" -name '*.pyc' -delete
fi

info "Creating source tarball"
tar czf "$BUILD/${PKGNAME}-${VER}.tar.gz" -C "$OUT/stage" "${TOPDIR}-${VER}"
rm -rf "$OUT/stage"

# ── Assemble the makepkg working dir: PKGBUILD + any install/aux files ─────────
for f in "$PKG_SRC"/*; do
  [[ "$(basename "$f")" == PKGBUILD ]] && continue
  cp "$f" "$BUILD/"
done
cp "$PKG_SRC/PKGBUILD" "$BUILD/"
# Pin pkgver to the version we just built (leaves packaging/PKGBUILD untouched).
sed -i "s/^pkgver=.*/pkgver=${VER}/" "$BUILD/PKGBUILD"

# ── Build ─────────────────────────────────────────────────────────────────────
info "Running makepkg ..."
( cd "$BUILD" && makepkg -f --noconfirm )

# ── Collect the artifacts ─────────────────────────────────────────────────────
PKG="$(find "$BUILD" -maxdepth 1 -name '*.pkg.tar.zst' ! -name '*-debug-*' -print -quit)"
[[ -n "$PKG" ]] || die "makepkg finished but produced no .pkg.tar.zst"
# Move every produced package (incl. debug) + the source tarball into dist-pkg/.
find "$BUILD" -maxdepth 1 -name '*.pkg.tar.zst' -exec mv -f {} "$OUT/" \;
mv -f "$BUILD/${PKGNAME}-${VER}.tar.gz" "$OUT/"
FINAL="$OUT/$(basename "$PKG")"
rm -rf "$BUILD"

ok "Package ready: $FINAL"
echo "    Source:  $OUT/${PKGNAME}-${VER}.tar.gz"

if $DO_INSTALL; then
  info "Installing with pacman ..."
  sudo pacman -U --noconfirm "$FINAL"
  ok "Installed $PKGNAME $VER"
else
  echo "    Install: sudo pacman -U \"$FINAL\""
fi
