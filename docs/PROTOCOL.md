# Protocolo HID del Royal Kludge MG5

Obtenido de dos fuentes:

1. Descriptores HID leídos en `/sys/class/hidraw/*/device/report_descriptor` (sin root).
2. El configurador web oficial (`https://drive.rkgaming.com`, bundle `assets/index-B7RMKT8U.js`),
   clases del protocolo "BeiYing" que usa el MG5.

Probado contra un MG5 real por el receptor 2.4G (lecturas y escrituras de polling, DPI, color y nivel activo).

## Identificadores USB

| Conexión     | VID:PID     | Nombre USB          |
|--------------|-------------|---------------------|
| Receptor 2.4G| `372e:103f` | BY TECH RK MG5      |
| Cable USB    | `372e:1073` | (no visto aún)      |

El receptor `103f` también lo usa el RK-K3 en el configurador, así que el PID solo no identifica el ratón.

## Interfaces (receptor)

| Interfaz | hidraw (aquí) | Función                                           |
|----------|---------------|---------------------------------------------------|
| 0        | hidraw5       | Ratón (botones, X/Y 16 bit, rueda, AC pan)        |
| 1        | hidraw6       | Teclado + consumer + system control (macros)      |
| 2        | hidraw7       | Vendor `0xFF00`/`0xFF06`: canal de configuración  |

Descriptor de la interfaz 2:

- Usage page `0xFF00`, usage 1: **Feature report ID 3**, 63 bytes. Canal de comandos.
- Usage page `0xFF06`, usage 1: **Input report ID 9** (31 bytes, eventos) y Output report ID 9 (63 bytes, no lo usa la web).

## Transporte

Comando: `SET_FEATURE` report 3 con 63 bytes de datos.
Respuesta: `GET_FEATURE` report 3 (la web hace polling cada ~20 ms durante hasta 2 s).

En Linux: `ioctl(HIDIOCSFEATURE)` / `ioctl(HIDIOCGFEATURE)` sobre `/dev/hidrawN`, con buffer `[0x03] + 63 bytes`.

### Trama de comando simple (cabecera `0x50`)

| Byte | Campo                                                    |
|------|----------------------------------------------------------|
| 0    | checksum = suma(bytes 1..61) & 0xFF                      |
| 1    | `0x50` (fijo)                                            |
| 2    | longitud del payload                                     |
| 3    | sn (número de secuencia; cada comando usa uno concreto)  |
| 4    | cmdId                                                    |
| 5    | dataOffset                                               |
| 6..  | payload                                                  |

Respuesta (GET_FEATURE, sin el byte de report id): `[0x50, estado (0 = OK), len, sn, página/cmd, datos...]`.
Mientras no hay respuesta pendiente el ratón devuelve un report corto de 4 bytes (`00 02 00 sn`).
La respuesta llega en unos 30-50 ms.

Ojo: en WebHID `receiveFeatureReport` incluye el report id en el byte 0, así que los índices del JS van uno por delante.

### Trama de transferencia (cabecera `0x65`)

Usada para versión de firmware, macros y OTA:
`[checksum, 0x65, 58, sn, device (0 = ratón por cable / 1 = vía receptor), cmdId, rfSn, payloadLen, opCode, ...]`.
Respuesta `>41` bytes: `[?, 0x65, cmdRsp, len, sn, otaRsp, result, payloadLen, payload...]`, `result == 14` = OK.

## Comandos

| cmdId | sn  | offset | Payload                                      | Qué hace                         | Tipo        |
|-------|-----|--------|----------------------------------------------|----------------------------------|-------------|
| 0x47  | 1   | 0      | —                                            | Estado online / contraseña de emparejamiento | lectura |
| 0x4F  | 2   | región\|página | —                                  | Lectura paginada (ver abajo); la web solo usa 0x81 para la batería | lectura |
| 0x92 (trama 0x65) | 7 | — | `[0x92, 0]`                            | Versión de firmware (resp. payload[18],[17]) | lectura |
| 0x3A  | 80  | 0      | `[nivel/max, dpi u16 LE, R, G, B]`           | DPI y color del nivel activo     | escritura   |
| 0x35  | 49/50 | campo | bytes del bloque de config en ese offset   | Escribe campos de config (ver abajo) | escritura |
| 0x30  | 32  | idx*4  | u32 keyRaw (BE)                              | Reasignar botón `idx` (0..5)     | escritura   |
| 0x06  | 0   | 0      | `[0xFF]`                                     | Reset de fábrica                 | escritura   |

### Lectura paginada (0x4F)

La web oficial no lee la configuración (guarda el perfil en el navegador), pero `0x4F` sirve para leerla.
El offset es `región | página`; cada respuesta trae 10 bytes: `[0x50, 0, 0x0b, 2, página, d0..d9]`.

- Región `0x00`: tabla de botones, 6 × u32 big-endian desde el byte 0 (`A5 5A` en el byte 112).
- Región `0x80`:
  - bytes 0..29: estado. Byte 1 de la página 0 = conectado, bytes 2..7 = contraseña de emparejamiento, byte 10 = batería (bit 7 cargando, bits 0-6 %).
  - bytes 30..93: bloque de configuración (64 bytes, ver abajo).
  - bytes 94..: copia de la tabla de botones.

Valores de botón (`keyRaw`): izquierdo `0x01010100`, derecho `0x01020100`, central `0x01030100`, atrás `0x01040100`, adelante `0x01050100`, ciclo DPI `0x05040000`.

## Bloque de configuración (64 bytes)

Es lo que `0x35` escribe por offset. Valores por defecto de la web entre paréntesis.

| Offset | Campo                | Codificación                                                         |
|--------|----------------------|----------------------------------------------------------------------|
| 0      | SensorInfo           | (1)                                                                  |
| 1      | Polling rate         | bits 2-0 = id: 3=125, 2=250, 1=500, 0=1000, 6=2000, 5=4000, 4=8000 Hz. El firmware copia el id al nibble alto al escribir |
| 2      | Nivel DPI            | bits 7-4 = nivel activo (1..6), bits 3-0 = nº de niveles (0x26)       |
| 3      | LOD                  | 1 = 0,7 mm, 2 = 1,0 mm, 3 = 2,0 mm                                   |
| 4      | Debounce             | ms, 1..20 (5)                                                        |
| 5      | Flags de rendimiento | bit0 angle snapping, bit1 glass mode, bit4 ripple control, bit5 motion sync, bits7-6 modo sensor (0 oficina, 1 alto rendimiento, 2 competitivo) (0x50) |
| 6      | BroadcastSleepTime   | (32)                                                                 |
| 7      | ReconnectSleepTime   | (16)                                                                 |
| 8      | Reposo               | segundos / 30; opciones 0, 30, 60, 120, 180, 300, 600, 900, 1800, 3600 s (4 = 120 s) |
| 9      | Perfil actual        | (1)                                                                  |
| 11..22 | DPI niveles 1..6     | u16 LE; si DPI ≤ 30000 se guarda `DPI/50 - 1`, si no el valor tal cual; rango 50..42000 paso 50 |
| 27..44 | Color de cada nivel  | RGB × 6                                                              |
| 54..55 | Firma                | `A5 5A` (la web tiene un valor por defecto de 54 bytes con la firma en 52) |

`0x35` acepta cualquier offset del bloque (probado con DPI y colores de niveles no activos).
`0x3A` cambia el nivel activo y su DPI/color; el ratón emite un evento de nivel DPI.

Agrupaciones que usa la web con `0x35`:
- Polling rate: sn 49, offset 1, 1 byte.
- LOD + debounce + flags: sn 50, offset 3, 3 bytes.
- Reposo: sn 49, offset 8, 1 byte.

## Reposo

Cuando el ratón duerme, `0x47` responde con byte 5 = 0 y las lecturas `0x4F` no responden.
Las escrituras enviadas en la transición al reposo las acepta el receptor pero se pierden, así que hay que comprobar `0x47` antes de escribir.

## Eventos (input report 9, 31 bytes)

Byte 0 = `0xFA`. Byte 1 = tipo:
- 2: estado de conexión del receptor (byte 2: 1 = conectado).
- 3: cambio de nivel DPI (byte 2 = nivel).
- 5: batería (byte 2: bit7 cargando, bits0-6 %).

## Permisos en Linux

`/dev/hidraw7` es `root:root 0600`. Para usarlo sin root hace falta una regla udev, por ejemplo:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="372e", ATTRS{idProduct}=="103f|1073", TAG+="uaccess"
```
