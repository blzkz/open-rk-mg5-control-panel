#!/bin/sh
# Builds an architecture-independent .deb: ./packaging/deb/build-deb.sh VERSION OUTDIR
set -eu
VERSION=$1
OUT=${2:-.}
SRC=$(cd "$(dirname "$0")/../.." && pwd)
PKG=open-rk-mg5-control-panel
ROOT=$(mktemp -d)

DESTDIR="$ROOT" PREFIX=/usr "$SRC/packaging/install.sh"
install -Dm644 "$SRC/LICENSE" "$ROOT/usr/share/doc/$PKG/copyright"

install -d "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5)
Maintainer: blzkz <https://github.com/blzkz>
Homepage: https://github.com/blzkz/open-rk-mg5-control-panel
Description: Control panel for the Royal Kludge MG5 mouse
 Open source control panel for the Royal Kludge MG5 mouse: DPI levels, polling
 rate, sensor settings, sleep time, button remapping and profiles switchable
 from the system tray.
CONTROL
cat > "$ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    udevadm control --reload-rules >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=hidraw >/dev/null 2>&1 || true
fi
POSTINST
chmod 755 "$ROOT/DEBIAN/postinst"

mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$ROOT" "$OUT/${PKG}_${VERSION}_all.deb"
rm -rf "$ROOT"
