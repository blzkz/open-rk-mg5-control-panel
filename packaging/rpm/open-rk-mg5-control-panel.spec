Name:           open-rk-mg5-control-panel
Version:        0.2.0
Release:        1%{?dist}
Summary:        Control panel for the Royal Kludge MG5 mouse
License:        GPL-3.0-or-later
URL:            https://github.com/blzkz/open-rk-mg5-control-panel
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita

%description
Open source control panel for the Royal Kludge MG5 mouse: DPI levels, polling rate,
sensor settings, sleep time, button remapping and profiles switchable from the tray.

%prep
%autosetup

%build

%install
DESTDIR=%{buildroot} PREFIX=/usr ./packaging/install.sh

%post
udevadm control --reload-rules >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=hidraw >/dev/null 2>&1 || :

%files
%license LICENSE
%doc README.md docs/PROTOCOL.md
%{_bindir}/rk-mg5-panel
%{_datadir}/open-rk-mg5/
%{_datadir}/applications/io.github.blzkz.OpenRkMg5.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.blzkz.OpenRkMg5.svg
%{_datadir}/metainfo/io.github.blzkz.OpenRkMg5.metainfo.xml
/usr/lib/udev/rules.d/70-rk-mg5.rules

%changelog
* Sat Oct 10 2026 blzkz - 0.2.0-1
- Initial package
