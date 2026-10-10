#!/bin/sh
# Stamps VERSION into the app and the package recipes: ./packaging/set-version.sh 1.2.3
set -eu
V=$1
cd "$(dirname "$0")/.."
sed -i "s/^__version__ = .*/__version__ = \"$V\"/" rk_mg5/__init__.py
sed -i "s/^pkgver=.*/pkgver=$V/" packaging/arch/PKGBUILD
sed -i "s/^Version: .*/Version:        $V/" packaging/rpm/open-rk-mg5-control-panel.spec
