# Open RK MG5 Control Panel

Panel de control para Linux del ratón **Royal Kludge MG5**: DPI por niveles, colores, frecuencia de sondeo,
LOD, debounce, modo del sensor, Motion Sync, Ripple Control, corrección de ángulo, modo cristal y reposo.

Funciona con el receptor 2.4G (`372e:103f`) y, en principio, por cable (`372e:1073`, aún sin probar).
El protocolo está documentado en [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Requisitos

- Python 3.10 o superior
- GTK 4 y libadwaita con PyGObject (en Arch/CachyOS: `python-gobject gtk4 libadwaita`)

No hay más dependencias: la comunicación con el ratón va directamente por `/dev/hidraw`.

## Permisos

Para acceder al ratón sin root hace falta la regla udev de `udev/70-rk-mg5.rules`:

```bash
sudo cp udev/70-rk-mg5.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Un paquete de distribución la instalaría en `/usr/lib/udev/rules.d/`.

## Uso

```bash
python3 -m rk_mg5
```

Los cambios se guardan en el ratón en cuanto se modifican en la ventana.
Si el ratón está dormido, muévelo y la app se reconectará sola.
