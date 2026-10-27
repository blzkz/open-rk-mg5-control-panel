# Protocolo HID del Royal Kludge MG5 (y M30, experimental)

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

---

# RK M30 (experimental, sin probar con hardware)

Todo lo de esta sección sale del configurador web oficial (`https://drive.rkgaming.com`, bundle
`assets/index-BSYKKems.js`, clase `_ye` y la página del M30). **No se ha probado con un M30 real**: los
offsets y comandos son los que usa la web, no observaciones del ratón.

Aunque la web lo etiqueta como protocolo "BeiYing", igual que el MG5, la comunicación es otra.

## Identificadores USB

| Conexión      | VID:PID     |
|---------------|-------------|
| Receptor 2.4G | `258a:0138` |
| Cable USB     | `258a:0136` |

Usage page `0xFF00`, usage 1 (como el MG5). `0x258A` es el VID genérico de SinoWealth, así que la app además
exige que el descriptor de esa interfaz declare el report ID 9 (`85 09`).

La web identifica los receptores de VID `372e` por la "contraseña" de emparejamiento leída como u64
big-endian: `0x040000000026` corresponde a "rk m30 24G" y `0x090000000036` a "rk mg5 24G". Con los PID propios
del M30 no hace falta.

## Transporte

`SET_FEATURE` / `GET_FEATURE` con el **report 9**, tramas de **519 bytes** (sin contar el report id):

| Byte  | Campo                                                     |
|-------|-----------------------------------------------------------|
| 0     | checksum = suma(bytes 1..517) & 0xFF (el último no cuenta) |
| 1     | cmdId                                                     |
| 2     | cmdPara                                                   |
| 3     | número de paquetes (1)                                    |
| 4     | índice del paquete (0)                                    |
| 5..6  | longitud del payload, u16 LE                              |
| 7..   | payload                                                   |

La respuesta tiene la misma forma; la web la reconoce porque el byte 1 repite el cmdId (en WebHID el índice va
uno por delante por el report id). No hay comando de estado online, ni batería, ni eventos que la web use.

## Comandos

| cmdId       | cmdPara | Payload               | Qué hace                                               |
|-------------|---------|-----------------------|--------------------------------------------------------|
| 0x44 (68)   | 0       | 400 bytes a cero      | Lee el bloque de configuración (respuesta: 400 bytes desde el byte 7) |
| 0x04 (4)    | 0 / 1   | bloque entero (400)   | Escribe el bloque. La web prueba con 0 y, si al releer no coincide, con 1 |
| 0x01 (1)    | 0       | tabla de botones (80) | Escribe la tabla de botones entera (no hay forma de leerla) |
| 0x03 (3)    | —       | macros, paquetes de 512 | Macros                                               |
| 0x48 (72)   | 1       | `[0]`                 | Versión de firmware: payload[0..1] ratón, [2..3] receptor |
| 0x09 (9)    | —       | —                     | Contraseña de emparejamiento (payload[0..5])           |

Tras escribir, la web espera un ACK (`byte 1 == 4`, resultado en el byte 2, sin significado conocido), relee el
bloque y compara el byte de nivel DPI y el de reposo.

## Bloque de configuración (400 bytes)

| Offset | Campo            | Codificación                                                           |
|--------|------------------|------------------------------------------------------------------------|
| 2      | Polling rate     | bits 3-0: 1 = 125, 2 = 250, 3 = 500, 4 = 1000 Hz                       |
| 3      | Nivel DPI        | bits 7-4 = nivel activo, bits 3-0 = nº de niveles (hasta 6)            |
| 4      | LOD              | bits 3-0: 1 = 0,7 mm, 2 = 1,0 mm, 3 = 2,0 mm                           |
| 5      | Debounce + motion sync | bits 3-0 = debounce en ms, bit 7 = motion sync                   |
| 6      | Flags            | bit 0 = angle snapping, bit 7 = ripple control                         |
| 7..    | DPI X niveles 1..6 | u16 LE, DPI = (valor + 1) × 100; rango 100..24000 paso 100           |
| 27..   | DPI Y niveles 1..6 | igual que X (la web escribe los dos)                                 |
| 47..   | Color de cada nivel | RGB × 6                                                             |
| 74     | Reposo           | minutos; opciones 0, 1, 2, 3, 5, 10, 15, 30, 60                         |
| 75, 76 | Luz DPI          | efecto y brillo                                                         |

Los bytes 256..399 del bloque por defecto de la web repiten la cabecera del principio (¿segundo perfil o copia
del receptor?); la app los conserva tal cual.

La página del M30 muestra modo del sensor y modo cristal, pero sus funciones no tocan el bloque, así que la app
no los ofrece.

## Botones

Revisado en un bundle posterior, `assets/index-CTQPyNpU.js` (la clase del M30 pasa a llamarse `Oye`, mismo protocolo):

- `0x01`, cmdPara 0, un paquete, payload de **80 bytes**: un `keyRaw` u32 big-endian por botón en el orden
  izquierdo, derecho, central, adelante, atrás, DPI (índices 0..5), y el resto a cero. La web manda siempre la tabla
  entera, copiada del perfil que guarda en el navegador; nunca un botón suelto.
- Tabla de fábrica: `01010100 01020100 01030100 01050100 01040100 05040000` (igual que el MG5).
- Los `keyRaw` y la forma de construirlos son los mismos que en el MG5 (mismo código en la página de cada modelo):
  ratón `0x01BB0100`, DPI `0x05xx0000`, multimedia `0x0200uuuu`, teclado `0x00MM00UU` (modificadores en el byte 1).
  El M30 ofrece las mismas acciones que el MG5 salvo el brillo de pantalla. La web impide quitar el último clic
  izquierdo.
- **No hay comando para leer la tabla**: ni la clase del M30 ni la página leen nunca los botones, y la web no espera
  respuesta al `0x01` (la manda por su cola de escritura). `setFactory()` no envía nada; la web restaura los botones
  escribiendo la tabla de fábrica.

## Fallos de la web oficial (que la app no copia)

- `setDpiColor` escribe el canal rojo en los tres bytes del color.
- `setReportRate` y `setLodHeight` parten del byte 5 en vez del byte que modifican, así que copian sus bits
  altos (motion sync) en el byte de polling o de LOD.
- El debounce admite hasta 20 ms, pero solo hay 4 bits: de 16 a 20 da la vuelta a 0..4. La app lo limita a 15.
- La web escribe el bloque entero a partir de su copia guardada en el navegador (o de los valores por defecto),
  no del que tiene el ratón.

## Cómo escribe la app

Cada cambio lee el bloque con `0x44`, comprueba que es válido (longitud, checksum, no todo ceros/`FF`, byte
de nivel DPI coherente), cambia solo los bits del ajuste, escribe con `0x04` y relee para verificar. Si la
relectura no coincide reintenta una vez con cmdPara 1, como la web, y si tampoco, da error y no insiste.
Botones: como no se pueden leer, la app guarda la distribución que conoce: la del perfil activo de ese modelo
(la última que se puso desde la app) o, si no hay, la de fábrica. Cada cambio comprueba antes que el ratón responde
(lectura `0x44` válida) y manda la tabla entera con `0x01`; aplicar un perfil la reescribe siempre. Nunca se manda
una tabla sin clic izquierdo. Las macros no están implementadas.

Regla udev:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0136|0138", TAG+="uaccess"
```
