"""Read-only diagnostics: `python3 -m rk_mg5 --probe`.

Prints what the panel detects and reads, without writing anything to the mouse. Meant for
checking a new or experimental model (the RK M30) before trusting the panel with writes.
"""

import os

from . import protocol as p


def _hex(data: bytes) -> str:
    return "\n".join(f"  {i:03d}: " + " ".join(f"{b:02x}" for b in data[i : i + 16]) for i in range(0, len(data), 16))


def main() -> int:
    try:
        info = p.find_device()
    except p.DeviceNotFound as e:
        print(e)
        return 1
    model = info.model
    print(f"Modelo:    {model.name}{' (experimental)' if model.experimental else ''}")
    print(f"Dispositivo: {info.path} ({model.vendor_id:04x}:{info.product_id:04x}, "
          f"{'receptor 2.4G' if info.wireless else 'cable USB'})")
    sys_path = f"/sys/class/hidraw/{os.path.basename(info.path)}/device/report_descriptor"
    with open(sys_path, "rb") as f:
        print(f"Descriptor HID ({sys_path}):\n{_hex(f.read())}")

    try:
        mouse = p.open_device(info)
    except PermissionError:
        print("Sin permiso para abrir el dispositivo: instala la regla udev.")
        return 1
    with mouse:
        try:
            if info.wireless and not mouse.is_online(timeout=1.0):
                print("El ratón está dormido: muévelo y vuelve a probar.")
                return 1
            config = mouse.read_config()
        except p.MG5Error as e:
            print(f"Error al leer la configuración: {e}")
            return 1
        print(f"Bloque de configuración ({len(config.raw)} bytes):\n{_hex(config.raw)}")
        levels = ", ".join(f"{config.dpi(i)}" for i in range(1, config.dpi_level_count + 1))
        print(f"DPI: nivel {config.dpi_level} de {config.dpi_level_count} ({levels})")
        print(f"Sondeo: {config.report_rate} Hz · LOD: {config.lod} · Debounce: {config.debounce} ms · "
              f"Reposo: {config.sleep_seconds} s")
        print(f"Motion Sync: {config.flag(p.PERF_MOTION_SYNC)} · Ripple: {config.flag(p.PERF_RIPPLE_CONTROL)} · "
              f"Ángulo: {config.flag(p.PERF_ANGLE_SNAPPING)}")
        if model.battery:
            battery = mouse.read_battery()
            print(f"Batería: {battery.percent} %{' (cargando)' if battery.charging else ''}")
        if model.key_remap:
            print("Botones: " + ", ".join(f"0x{k:08x}" for k in mouse.read_keys()))
        if isinstance(mouse, p.M30):
            firmware = mouse.read_firmware()
            print(f"Firmware: {'ratón %s, receptor %s' % firmware if firmware else 'sin respuesta'}")
    return 0
