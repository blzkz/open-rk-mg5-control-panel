"""Tests for the experimental RK M30 backend, against a simulated device (no hardware needed).

Run with: python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

from rk_mg5 import protocol as p
from rk_mg5.hidraw import HidrawInfo


def sample_block() -> bytearray:
    """A plausible M30 block: level 2 of 6, 500 Hz, DPI 400..12000, debounce 3 ms."""
    block = bytearray(p.M30_CONFIG_LEN)
    block[0], block[1] = 100, 24
    block[p.M30_OFF_REPORT_RATE] = 3
    block[p.M30_OFF_DPI_LEVEL] = 0x26
    block[p.M30_OFF_DEBOUNCE] = 3
    for i, dpi in enumerate((400, 800, 1600, 3200, 6400, 12000)):
        raw = p.encode_m30_dpi(dpi).to_bytes(2, "little")
        block[p.M30_OFF_DPI_X + i * 2 : p.M30_OFF_DPI_X + i * 2 + 2] = raw
        block[p.M30_OFF_DPI_Y + i * 2 : p.M30_OFF_DPI_Y + i * 2 + 2] = raw
    colors = [(0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 255, 0), (0, 0, 255), (255, 255, 255)]
    for i, rgb in enumerate(colors):
        block[p.M30_OFF_DPI_COLORS + i * 3 : p.M30_OFF_DPI_COLORS + i * 3 + 3] = bytes(rgb)
    block[200:210] = bytes(range(1, 11))  # unknown bytes that must survive every write
    return block


def response(cmd: int, para: int, payload: bytes) -> bytes:
    return p.build_m30_frame(cmd, para, payload)


class FakeM30:
    """Answers report 9 like the official app expects. `accept` lists the cmdPara values that store a write."""

    def __init__(self, block: bytes, accept=(0,)):
        self.block = bytearray(block)
        self.accept = accept
        self.pending = b""
        self.writes = []
        self.key_writes = []

    def send_feature_report(self, report_id: int, data: bytes) -> None:
        assert report_id == p.M30_REPORT_ID and len(data) == p.M30_FRAME_LEN
        assert data[0] == p.m30_checksum(data)
        cmd, para = data[1], data[2]
        length = int.from_bytes(data[5:7], "little")
        payload = data[p.M30_PAYLOAD_OFFSET : p.M30_PAYLOAD_OFFSET + length]
        if cmd == p.M30_CMD_GET_CONFIG:
            self.pending = response(cmd, 0, bytes(self.block))
        elif cmd == p.M30_CMD_SET_KEYS:
            self.key_writes.append((para, bytes(payload)))
            self.pending = response(cmd, 0, b"")
        elif cmd == p.M30_CMD_SET_CONFIG:
            self.writes.append((para, bytes(payload)))
            if para in self.accept:
                self.block = bytearray(payload)
            self.pending = response(cmd, 0, b"")

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        return self.pending

    def read(self, size: int = 64):
        return None


def make_mouse(fake: FakeM30) -> p.M30:
    mouse = p.M30.__new__(p.M30)
    mouse.info = p.DeviceInfo("/dev/null", p.M30_MODEL.dongle_pid, p.M30_MODEL)
    mouse.dev = fake
    mouse.keys = list(p.M30_DEFAULT_KEYS)
    return mouse


class FrameTests(unittest.TestCase):
    def test_frame_layout(self):
        frame = p.build_m30_frame(p.M30_CMD_SET_CONFIG, 1, b"\x01\x02\x03")
        self.assertEqual(len(frame), 519)
        self.assertEqual(frame[1:7], bytes([0x04, 1, 1, 0, 3, 0]))
        self.assertEqual(frame[7:10], b"\x01\x02\x03")
        self.assertEqual(frame[0], (0x04 + 1 + 1 + 3 + 1 + 2 + 3) & 0xFF)

    def test_checksum_ignores_last_byte(self):
        frame = bytearray(p.build_m30_frame(p.M30_CMD_GET_CONFIG, 0, bytes(400)))
        frame[-1] = 0x55
        self.assertEqual(frame[0], p.m30_checksum(frame))

    def test_mg5_frame_unchanged(self):
        frame = p.build_frame(p.CMD_WRITE_CONFIG, 49, p.OFF_REPORT_RATE, b"\x00")
        self.assertEqual(frame[:7], bytes([0xB8, 0x50, 1, 49, 0x35, 1, 0]))


class ParseTests(unittest.TestCase):
    def test_valid_block(self):
        block = sample_block()
        self.assertEqual(p.parse_m30_config(response(p.M30_CMD_GET_CONFIG, 0, bytes(block))), block)

    def test_rejects_bad_reads(self):
        good = response(p.M30_CMD_GET_CONFIG, 0, bytes(sample_block()))
        bad_checksum = bytearray(good)
        bad_checksum[0] ^= 0xFF
        bad_level = sample_block()
        bad_level[p.M30_OFF_DPI_LEVEL] = 0x72  # active level above the count
        for resp in (
            good[:300],
            response(p.M30_CMD_SET_CONFIG, 0, bytes(sample_block())),
            bytes(bad_checksum),
            response(p.M30_CMD_GET_CONFIG, 0, bytes(400)),
            response(p.M30_CMD_GET_CONFIG, 0, b"\xff" * 400),
            response(p.M30_CMD_GET_CONFIG, 0, bytes(bad_level)),
        ):
            with self.assertRaises(p.MG5Error):
                p.parse_m30_config(resp)


class ConfigTests(unittest.TestCase):
    def test_decode(self):
        block = sample_block()
        block[p.M30_OFF_DEBOUNCE] |= 0x80
        block[p.M30_OFF_FLAGS] = 0x81
        block[p.M30_OFF_SLEEP] = 5
        c = p.M30Config(block)
        self.assertEqual((c.dpi_level, c.dpi_level_count), (2, 6))
        self.assertEqual([c.dpi(i) for i in range(1, 7)], [400, 800, 1600, 3200, 6400, 12000])
        self.assertEqual(c.dpi_color(4), (255, 255, 0))
        self.assertEqual(c.report_rate, 500)
        self.assertEqual(c.debounce, 3)
        self.assertEqual(c.sleep_seconds, 300)
        self.assertTrue(c.flag(p.PERF_MOTION_SYNC) and c.flag(p.PERF_RIPPLE_CONTROL) and c.flag(p.PERF_ANGLE_SNAPPING))
        self.assertFalse(c.flag(p.PERF_GLASS_MODE))

    def test_dpi_encoding(self):
        self.assertEqual(p.encode_m30_dpi(100), 0)
        self.assertEqual(p.encode_m30_dpi(24000), 239)
        self.assertEqual(p.encode_m30_dpi(50000), 239)
        self.assertEqual(p.encode_m30_dpi(1649), 15)
        self.assertEqual(p.decode_m30_dpi(15), 1600)

    def test_masked_changes(self):
        block = bytes([0xA3] * 8)
        new = p.apply_m30_changes(block, {2: (0x04, 0x0F), 5: (0x80, 0x80)})
        self.assertEqual(new[2], 0xA4)
        self.assertEqual(new[5], 0xA3 | 0x80)
        self.assertEqual(new[:2] + new[3:5] + new[6:], block[:2] + block[3:5] + block[6:])


@mock.patch("rk_mg5.protocol.time.sleep", lambda _s: None)
class WriteTests(unittest.TestCase):
    def test_only_touched_bytes_change(self):
        fake = FakeM30(sample_block())
        mouse = make_mouse(fake)
        before = bytes(fake.block)
        mouse.set_report_rate(1000)
        mouse.set_dpi_value(3, 2000)
        mouse.set_dpi_color(1, (1, 2, 3))
        mouse.set_performance(p.PERF_MOTION_SYNC | p.PERF_GLASS_MODE)
        mouse.set_sleep(600)
        mouse.set_debounce(20)
        after = mouse.read_config()
        self.assertEqual(after.report_rate, 1000)
        self.assertEqual(after.dpi(3), 2000)
        self.assertEqual(after.raw[p.M30_OFF_DPI_Y + 4 : p.M30_OFF_DPI_Y + 6], (19).to_bytes(2, "little"))
        self.assertEqual(after.dpi_color(1), (1, 2, 3))
        self.assertTrue(after.flag(p.PERF_MOTION_SYNC))
        self.assertFalse(after.flag(p.PERF_RIPPLE_CONTROL))
        self.assertEqual(after.sleep_seconds, 600)
        self.assertEqual(after.debounce, 15)
        changed = {i for i in range(p.M30_CONFIG_LEN) if before[i] != fake.block[i]}
        touched = {2, 5, 6, 74, 11, 12, 31, 32, 47, 48, 49}
        self.assertLessEqual(changed, touched)
        self.assertEqual(fake.block[200:210], bytes(range(1, 11)))

    def test_no_write_when_nothing_changes(self):
        fake = FakeM30(sample_block())
        make_mouse(fake).set_report_rate(500)
        self.assertEqual(fake.writes, [])

    def test_retries_with_cmd_para_1(self):
        fake = FakeM30(sample_block(), accept=(1,))
        make_mouse(fake).set_lod(2)
        self.assertEqual([para for para, _ in fake.writes], [0, 1])
        self.assertEqual(fake.block[p.M30_OFF_LOD], 2)

    def test_gives_up_when_readback_differs(self):
        fake = FakeM30(sample_block(), accept=())
        with self.assertRaises(p.MG5Error):
            make_mouse(fake).set_lod(2)
        self.assertEqual(len(fake.writes), 2)

    def test_never_writes_after_a_bad_read(self):
        fake = FakeM30(bytes(400))
        with self.assertRaises(p.MG5Error):
            make_mouse(fake).set_lod(2)
        self.assertEqual(fake.writes, [])

    def test_offline(self):
        fake = FakeM30(sample_block())
        fake.get_feature_report = lambda *_a: b"\x00\x02\x00"
        with self.assertRaises(p.MouseOffline):
            make_mouse(fake).read_config()


@mock.patch("rk_mg5.protocol.time.sleep", lambda _s: None)
class ButtonTests(unittest.TestCase):
    def test_key_table(self):
        table = p.build_m30_key_table([0x01010100, 0x01020100, 0x01030100, 0x00010006, 0x020000E9, 0x05040000])
        self.assertEqual(len(table), 80)
        self.assertEqual(table[:24].hex(), "01010100010201000103010000010006020000e905040000")
        self.assertEqual(table[24:], bytes(56))
        frame = p.build_m30_frame(p.M30_CMD_SET_KEYS, 0, p.build_m30_key_table(list(p.M30_DEFAULT_KEYS)))
        # Same frame as js/protocol.js in the web version.
        self.assertEqual(frame[:31].hex(), "74010001005000010101000102010001030100010501000104010005040000")
        with self.assertRaises(p.MG5Error):
            p.build_m30_key_table([0x01020100] * 6)  # no left click
        with self.assertRaises(p.MG5Error):
            p.build_m30_key_table([0x01010100])

    def test_set_key_writes_whole_table(self):
        fake = FakeM30(sample_block())
        mouse = make_mouse(fake)
        mouse.keys = [0x01010100, 0x01020100, 0x01030100, 0x020000E9, 0x01040100, 0x05040000]
        mouse.set_key(4, 0x020000EA)
        self.assertEqual(len(fake.key_writes), 1)
        para, payload = fake.key_writes[0]
        self.assertEqual(para, 0)
        self.assertEqual(payload[:24].hex(), "010101000102010001030100020000e9020000ea05040000")
        self.assertEqual(mouse.read_keys(), [0x01010100, 0x01020100, 0x01030100, 0x020000E9, 0x020000EA, 0x05040000])
        self.assertEqual(fake.writes, [])  # the config block is untouched

    def test_refuses_bad_state(self):
        asleep = FakeM30(bytes(400))
        with self.assertRaises(p.MG5Error):
            make_mouse(asleep).set_key(3, 0x020000E9)
        self.assertEqual(asleep.key_writes, [])
        fake = FakeM30(sample_block())
        mouse = make_mouse(fake)
        with self.assertRaises(p.MG5Error):
            mouse.set_key(0, 0x01020100)  # would remove the only left click
        self.assertEqual(fake.key_writes, [])
        self.assertEqual(mouse.read_keys(), list(p.M30_DEFAULT_KEYS))

    def test_profile_rewrites_table(self):
        from rk_mg5.settings import Profile

        fake = FakeM30(sample_block())
        mouse = make_mouse(fake)
        profile = Profile.from_mouse("M30", p.M30Config(sample_block()), list(p.M30_DEFAULT_KEYS), p.M30_MODEL.name)
        profile.apply_to(mouse)
        self.assertEqual(len(fake.key_writes), 1)
        self.assertEqual(fake.key_writes[0][1][:24].hex(), "010101000102010001030100010501000104010005040000")


class ModelTests(unittest.TestCase):
    def test_find_device_picks_model(self):
        mg5 = HidrawInfo("/dev/hidraw7", 0x372E, 0x103F, "BY TECH RK MG5", b"\x06\x00\xff\x09\x01")
        m30 = HidrawInfo("/dev/hidraw3", 0x258A, 0x0136, "RK M30", b"\x05\x01\x06\x00\xff\x09\x01\x85\x09")
        other = HidrawInfo("/dev/hidraw1", 0x258A, 0x0136, "other", b"\x06\x00\xff\x09\x01\x85\x05")
        with mock.patch.object(p, "enumerate_hidraw", return_value=[other, m30]):
            info = p.find_device()
            self.assertEqual((info.path, info.model, info.wireless), ("/dev/hidraw3", p.M30_MODEL, False))
        with mock.patch.object(p, "enumerate_hidraw", return_value=[mg5]):
            info = p.find_device()
            self.assertEqual((info.model, info.wireless), (p.MG5_MODEL, True))
        with mock.patch.object(p, "enumerate_hidraw", return_value=[m30]):
            with self.assertRaises(p.DeviceNotFound):
                p.find_device((p.MG5_MODEL,))


if __name__ == "__main__":
    unittest.main()
