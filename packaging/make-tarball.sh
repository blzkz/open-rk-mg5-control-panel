#!/bin/sh
# Writes the source tarball the Arch and RPM builds expect: ./packaging/make-tarball.sh VERSION OUTDIR
set -eu
V=$1
OUT=$2
NAME=open-rk-mg5-control-panel
cd "$(dirname "$0")/.."
mkdir -p "$OUT"
tar czf "$OUT/$NAME-$V.tar.gz" --exclude=.git --exclude=__pycache__ --transform "s|^\./|$NAME-$V/|" .
