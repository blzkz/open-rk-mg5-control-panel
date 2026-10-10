"""Minimal hidraw access via ioctl, no third-party dependencies."""

import fcntl
import glob
import os
from dataclasses import dataclass

_IOC_READ_WRITE = 3


def _ioc(nr: int, size: int) -> int:
    return (_IOC_READ_WRITE << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(size: int) -> int:
    return _ioc(0x06, size)


def HIDIOCGFEATURE(size: int) -> int:
    return _ioc(0x07, size)


@dataclass
class HidrawInfo:
    path: str
    vendor_id: int
    product_id: int
    name: str
    report_descriptor: bytes


def enumerate_hidraw() -> list[HidrawInfo]:
    devices = []
    for sys_dir in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sys_dir, "device/uevent")) as f:
                uevent = dict(line.strip().split("=", 1) for line in f if "=" in line)
            with open(os.path.join(sys_dir, "device/report_descriptor"), "rb") as f:
                descriptor = f.read()
        except OSError:
            continue
        _bus, vid, pid = uevent.get("HID_ID", "0:0:0").split(":")
        devices.append(
            HidrawInfo(
                path="/dev/" + os.path.basename(sys_dir),
                vendor_id=int(vid, 16),
                product_id=int(pid, 16),
                name=uevent.get("HID_NAME", ""),
                report_descriptor=descriptor,
            )
        )
    return devices


class Hidraw:
    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send_feature_report(self, report_id: int, data: bytes) -> None:
        buf = bytearray([report_id]) + bytearray(data)
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buf)), buf)

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        """Returns the report payload without the report id byte."""
        buf = bytearray(length + 1)
        buf[0] = report_id
        n = fcntl.ioctl(self.fd, HIDIOCGFEATURE(len(buf)), buf, True)
        # ioctl returns the number of bytes, including the report id.
        return bytes(buf[1:n]) if isinstance(n, int) and n > 0 else bytes(buf[1:])

    def read(self, size: int = 64) -> bytes | None:
        try:
            return os.read(self.fd, size)
        except BlockingIOError:
            return None
