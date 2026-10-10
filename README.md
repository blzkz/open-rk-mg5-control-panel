# Open RK MG5 Control Panel

Panel de control para Linux del ratón **Royal Kludge MG5**: DPI por niveles, colores, frecuencia de sondeo,
LOD, debounce, modo del sensor, Motion Sync, Ripple Control, corrección de ángulo, modo cristal, reposo
y reasignación de los seis botones (ratón, DPI, multimedia o cualquier tecla con modificadores).

Funciona con el receptor 2.4G (`372e:103f`) y, en principio, por cable (`372e:1073`, aún sin probar).
El protocolo está documentado en [docs/PROTOCOL.md](docs/PROTOCOL.md).

![Personalizar](docs/screenshot.png)
![Rendimiento](docs/screenshot-performance.png)

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

Con `--hidden` arranca directamente en la bandeja.

### Perfiles

Un perfil guarda todo lo que el ratón almacena: botones, niveles y colores de DPI, sondeo, LOD, debounce,
modo del sensor, interruptores y reposo. El selector de la barra superior cambia de perfil y el menú `⋮`
crea, duplica, renombra o elimina perfiles. Los cambios que hagas se guardan en el perfil activo.

Se guardan como JSON en `~/.config/open-rk-mg5/profiles/`.

### Bandeja del sistema

El icono de la bandeja muestra la batería en el tooltip, cambia a un aviso con batería baja (y lanza una
notificación) y su menú permite cambiar de perfil al vuelo. Usa el protocolo StatusNotifierItem, así que
funciona en KDE Plasma y en GNOME con la extensión AppIndicator.

En Preferencias se elige si cerrar la ventana la minimiza a la bandeja, si arranca con la sesión y el umbral
de batería baja.

## Licencia

GPL-3.0-or-later. Royal Kludge y MG5 son marcas de sus propietarios; este proyecto no está afiliado a Royal Kludge.
