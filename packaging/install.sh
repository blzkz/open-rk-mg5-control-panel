#!/bin/sh
# Installs the app into $DESTDIR$PREFIX. Shared by the Arch, RPM, Debian and Flatpak builds.
#   DESTDIR=/tmp/pkg PREFIX=/usr ./packaging/install.sh
#   NO_UDEV=1 skips the udev rule (Flatpak cannot install one).
set -eu

SRC=$(cd "$(dirname "$0")/.." && pwd)
DESTDIR=${DESTDIR:-}
PREFIX=${PREFIX:-/usr}
APP_ID=io.github.blzkz.OpenRkMg5
ROOT="$DESTDIR$PREFIX"

install -d "$ROOT/share/open-rk-mg5/rk_mg5"
install -m644 "$SRC"/rk_mg5/*.py "$ROOT/share/open-rk-mg5/rk_mg5/"

install -d "$ROOT/bin"
cat > "$ROOT/bin/rk-mg5-panel" <<LAUNCHER
#!/bin/sh
export PYTHONPATH="$PREFIX/share/open-rk-mg5\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m rk_mg5 "\$@"
LAUNCHER
chmod 755 "$ROOT/bin/rk-mg5-panel"

install -d "$ROOT/share/applications"
sed 's|^Exec=.*|Exec=rk-mg5-panel|' "$SRC/data/$APP_ID.desktop" > "$ROOT/share/applications/$APP_ID.desktop"
chmod 644 "$ROOT/share/applications/$APP_ID.desktop"
install -Dm644 "$SRC/data/icons/$APP_ID.svg" "$ROOT/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -Dm644 "$SRC/data/$APP_ID.metainfo.xml" "$ROOT/share/metainfo/$APP_ID.metainfo.xml"

if [ -z "${NO_UDEV:-}" ]; then
    install -Dm644 "$SRC/udev/70-rk-mg5.rules" "$ROOT/lib/udev/rules.d/70-rk-mg5.rules"
fi
