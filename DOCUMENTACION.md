# Documentación Técnica Completa: A9 Pro Smart Charging Box & Earbuds Studio

> **Versión del Documento:** 2.0.0  
> **Fecha de Actualización:** Octubre de 2026  
> **Dispositivo Objetivo:** Caja de Carga Inteligente con Pantalla Táctil A9 Pro LCD (320×172 px) y Auriculares TWS  
> **Plataforma de Control:** Linux (BlueZ / Raw L2CAP ATT Sockets) / Python 3.9+ / HTML5 Canvas Web Studio  

---

## Tabla de Contenidos

1. [Resumen Ejecutivo e Introducción](#1-resumen-ejecutivo-e-introducción)
2. [Arquitectura del Hardware y SoC JieLi (Jerry)](#2-arquitectura-del-hardware-y-soc-jieli-jerry)
3. [Ingeniería Inversa del Protocolo BLE y RCSP](#3-ingeniería-inversa-del-protocolo-ble-y-rcsp)
   - [Transporte L2CAP y Handshake ATT](#transporte-l2cap-y-handshake-att)
   - [Canales, Handles y UUIDs GATT](#canales-handles-y-uuids-gatt)
   - [Estructura del Encabezado de Trama RCSP](#estructura-del-encabezado-de-trama-rcsp)
   - [Catálogo Detallado de Comandos y Opcodes](#catálogo-detallado-de-comandos-y-opcodes)
4. [Especificación del Formato Binario de Esferas (.bin)](#4-especificación-del-formato-binario-de-esferas-bin)
   - [Cabecera General del Archivo (16 bytes)](#cabecera-general-del-archivo-16-bytes)
   - [Tabla de Descriptores de Capa (20 bytes c/u)](#tabla-de-descriptores-de-capa-20-bytes-cu)
   - [Formato de Compresión de Imagen PA (Transposed RLE)](#formato-de-compresión-de-imagen-pa-transposed-rle)
   - [Límite Físico de Memoria Flash (150 KB)](#límite-físico-de-memoria-flash-150-kb)
5. [El Enigma de la Sincronización Horaria (RTC)](#5-el-enigma-de-la-sincronización-horaria-rtc)
   - [Análisis del Desensamblado de la App Oficial](#análisis-del-desensamblado-de-la-app-oficial)
   - [La Causa Raíz: Firmware con Hora de Beijing (UTC+8)](#la-causa-raíz-firmware-con-hora-de-beijing-utc8)
   - [Deducción y Fórmula de Compensación para Colombia / Perú (UTC-5)](#deducción-y-fórmula-de-compensación-para-colombia--perú-utc-5)
   - [Ciclo de Redibujado de la Pantalla LCD](#ciclo-de-redibujado-de-la-pantalla-lcd)
6. [Arquitectura del Software](#6-arquitectura-del-software)
   - [Servidor HTTP y Gestor Concurrente (`a9pro_studio.py`)](#servidor-http-y-gestor-concurrente-a9pro_studiopy)
   - [Prevención de Interbloqueos con `RLock` y Multi-threading](#prevención-de-interbloqueos-con-rlock-y-multi-threading)
   - [Motor Web Canvas y Estudio Visual WYSIWYG](#motor-web-canvas-y-estudio-visual-wysiwyg)
   - [Motor de Optimización de Animaciones GIF (Capa 0x13)](#motor-de-optimización-de-animaciones-gif-capa-0x13)
   - [Herramienta CLI de Bajo Nivel (`a9pro_tool.py`)](#herramienta-cli-de-bajo-nivel-a9pro_toolpy)
7. [Referencia de la API REST](#7-referencia-de-la-api-rest)
8. [Despliegue y Servicio Systemd en Linux](#8-despliegue-y-servicio-systemd-en-linux)
9. [Guía de Solución de Problemas (Troubleshooting)](#9-guía-de-solución-de-problemas-troubleshooting)

---

## 1. Resumen Ejecutivo e Introducción

La caja de carga para auriculares **A9 Pro** se distingue de los estuches convencionales por integrar una pantalla LCD táctil a color de 320×172 píxeles, operada directamente por un microcontrolador Bluetooth SoC de bajo coste fabricado por **Zhuhai JieLi Technology** (comúnmente conocido como Jerry o JL).

La aplicación complementaria oficial para smartphones (`com.zxl.mate`) presenta graves deficiencias:
- Requiere acceso a servidores en la nube en China para consultar y descargar temas.
- Limita severamente la personalización visual (solo permite cambiar fondos estáticos, recortados a resoluciones fijas).
- Presenta un defecto arquitectónico en la sincronización del reloj de hardware (RTC) para cualquier usuario fuera de la zona horaria de China (CST, UTC+8).
- No permite el uso de animaciones GIF personalizadas fluidas ni fuentes tipográficas externas.

**A9 Pro Smart Charging Box & Earbuds Studio** es una suite completa desarrollada en Python y tecnologías web abiertas que prescinde al 100% de la aplicación móvil oficial y de servidores externos. Permite:
1. Conexión Bluetooth Low Energy directa mediante sockets L2CAP nativos del kernel Linux.
2. Telemetría bidireccional en tiempo real (porcentajes de carga de cada auricular y del estuche, estado de tapas, modo de trabajo y cancelación de ruido activa).
3. Estudio visual multicapa tipo Photoshop/Canva con generación de código binario compatible con el decodificador DMA del chip JieLi.
4. Motor de conversión y muestreo temporal de archivos GIF con ajuste exacto de ticks de hardware.
5. Sincronización horaria de precisión calibrada a la milésima de segundo corrigiendo el desfase de fábrica de 13 horas.
6. Instalación directa de un catálogo de 80 esferas oficiales extraídas y documentadas.

---

## 2. Arquitectura del Hardware y SoC JieLi (Jerry)

### Microcontrolador (SoC)
El sistema interno de la caja está gobernado por un SoC de la familia **JieLi AC69xx / AC701n** (arquitectura RISC propietaria de 32 bits a 160–240 MHz). El chip integra en un solo encapsulado:
- Radio módem de doble modo: Bluetooth Classic (para audio) y BLE 5.x (para control y telemetría).
- Controlador de display integrado con acelerador de copiado directo a memoria (DMA).
- Módulo RTC (Real Time Clock) alimentado continuamente por la celda de litio del estuche.
- Controlador de pantalla táctil capacitiva (I2C).
- Memoria Flash SPI externa/interna compartida para firmware y particiones de recursos de usuario.

### Especificaciones de la Pantalla LCD
- **Resolución nativa:** 320 píxeles de ancho $\times$ 172 píxeles de alto.
- **Relación de aspecto:** Paisaje ultra-ancho ($\approx 1.86:1$).
- **Profundidad de color:** RGB565 (16 bits por píxel: 5 bits Rojo, 6 bits Verde, 5 bits Azul).
- **Controlador de barrido:** Orientación transpuesta (column-major order). Los píxeles no se escriben fila por fila de izquierda a derecha, sino columna por columna de arriba hacia abajo ($x=0..W-1, y=0..H-1$).

### Límite de Almacenamiento Flash para Temas
El firmware reserva una partición fija para la esfera personalizada de usuario:
$$\text{Tamaño Máximo Permitido} = 153.600 \text{ bytes (150,0 KB exactos)}$$
Si un archivo `.bin` subido supera los 153.600 bytes por tan solo 1 byte, el gestor de descargas de la caja rechaza el paquete con error de validación o corrompe el bloque de memoria de pantalla.

---

## 3. Ingeniería Inversa del Protocolo BLE y RCSP

La comunicación inalámbrica entre el host y la caja inteligente emplea el **RCSP (Remote Control System Protocol)** de JieLi encapsulado sobre el protocolo ATT (Attribute Protocol) de BLE.

### Transporte L2CAP y Handshake ATT

En lugar de requerir pesadas pilas GATT en espacio de usuario (como `bleak` o `dbus`), la suite se conecta directamente mediante un socket L2CAP nativo de Linux:
```python
sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
sock.connect((target_mac, socket.BDADDR_LE_PUBLIC))
```

Al establecer conexión con el canal ATT (CID 4), se lleva a cabo el intercambio de MTU:
1. El host envía una solicitud de intercambio de MTU (`ATT_OP_MTU_REQ = 0x02`) proponiendo un MTU de **512 bytes**.
2. La caja responde solicitando un MTU de **672 bytes** (`ATT_OP_MTU_RSP = 0x03`).
3. El enlace acuerda típicamente un MTU ATT efectivo de **188 bytes** (185 bytes de carga útil del protocolo).

### Canales, Handles y UUIDs GATT

A través de la inspección del firmware y el desensamblado del APK oficial, se aislaron los handles determinantes:

| Handle | Tipo | UUID Característica | Propósito | Operación ATT |
|---|---|---|---|---|
| `0x0008` | Characteristic Value | `0000ff17-0000-1000-8000-00805f9b34fb` (0xFF17) | **Canal TX (Host $\to$ Caja)**: Envío de comandos y bloques de datos. | `ATT_OP_WRITE_CMD` (`0x52`) o `ATT_OP_WRITE_REQ` (`0x12`) |
| `0x0003` | Characteristic Value | `0000ff18-0000-1000-8000-00805f9b34fb` (0xFF18) | **Canal RX (Caja $\to$ Host)**: Notificaciones espontáneas y respuestas TLV. | `ATT_OP_HANDLE_NOTIF` (`0x1B`) |
| `0x0004` | Descriptor CCCD | `00002902-0000-1000-8000-00805f9b34fb` | **Control de Notificaciones**: Se escribe `\x01\x00` para activar el flujo RX. | `ATT_OP_WRITE_REQ` (`0x12`) |

> **Nota Crítica sobre Write Request vs Write Command:**  
> Las transferencias masivas de archivos emplean `ATT_OP_WRITE_CMD` (`0x52`) para alcanzar alta velocidad sin esperar confirmación. Sin embargo, comandos de estado del sistema (como la hora `0x93`) requieren obligatoriamente `ATT_OP_WRITE_REQ` (`0x12`), esperando la respuesta de confirmación `ATT_OP_WRITE_RSP` (`0x13`).

### Estructura del Encabezado de Trama RCSP

Cada paquete de control enviado al handle `0x0008` sigue la siguiente estructura binaria:

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Número Sec.   | ID Comando    |      0x01     |      0x00     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Longitud (N)  |             Carga Útil (Payload) de N bytes   |
+-+-+-+-+-+-+-+-+               ...                             +
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Byte 0 (`seq`):** Contador incremental de secuencia (0x00 a 0xFF).
- **Byte 1 (`cmd`):** Opcode de la operación RCSP.
- **Bytes 2–3:** Flags de protocolo del framework JieLi (generalmente fijos en `0x01, 0x00`).
- **Byte 4 (`len`):** Tamaño de los datos de la carga útil en bytes.
- **Bytes 5..5+N:** Carga útil específica de cada función.

### Catálogo Detallado de Comandos y Opcodes

#### 1. `0x27` — Consulta de Información del Dispositivo (`CMD_DEVICE_INFO`)
Permite solicitar el estado general mediante un listado de etiquetas TLV (Type-Length-Value).  
**Payload enviado:** Lista de pares `[Tag, 0x00]` para solicitar tags:
- `Tag 0x01`: Nivel de batería Auricular Izquierdo (%) y estado de carga.
- `Tag 0x02`: Nivel de batería Auricular Derecho (%) y estado de carga.
- `Tag 0x03`: Nivel de batería Estuche A9 Pro (%) y estado de carga.
- `Tag 0x08`: Preset y valores de las 10 bandas del ecualizador.
- `Tag 0x0C`: Modo de cancelación activa de ruido (ANC).
- `Tag 0x0D`: Estado de conexión TWS (True Wireless Stereo).
- `Tag 0x0E`: Estado detallado de la caja/auriculares en estuche.
- `Tag 0x21`: Nivel de volumen actual.
- `Tag 0x80`: Parámetros de pantalla (ancho, alto, tipo de display, tamaño máximo de tema).
- `Tag 0xFF`: Tamaño MTU de protocolo soportado por el firmware.

#### 2. `0x28` — Notificaciones Push en Tiempo Real (`CMD_STATUS_NOTIF`)
Emitido espontáneamente por la caja por el handle `0x0003` cuando un auricular se extrae, se inserta, cambia de porcentaje de carga o se altera un ajuste desde la pantalla táctil. El parser interno de la suite decodifica los tags TLV y actualiza el estado atómico en memoria.

#### 3. `0x93` — Sincronización del Reloj de Hardware RTC (`CMD_TIMESTAMP`)
Transfiere una marca de tiempo en segundos para ajustar el reloj de hardware integrado:
- **Carga útil (4 bytes):** Entero de 32 bits Little-Endian (`struct.pack("<I", target_timestamp)`).
- **Método de transmisión:** ATT Write Request (`0x12`).

#### 4. `0x20` — Configuración del Ecualizador (`CMD_EQ`)
Configura el filtro DSP de audio:
- **Byte 0:** Preset (0: Normal, 1: Rock, 2: Pop, 3: Clásica, 4: Jazz).
- **Bytes 1..10:** 10 bandas de ganancia en decibelios representadas como enteros con signo (`int8`, rango -12 a +12 dB).

#### 5. `0x2C` — Control de Modo de Ruido (`CMD_NOISE_MODE`)
- **Byte 0:** `0x00` = ANC (Cancelación Activa de Ruido), `0x01` = Normal / Apagado, `0x02` = Modo Transparencia.

#### 6. `0x25` — Modo de Trabajo (`CMD_WORK_MODE`)
- **Byte 0:** `0x00` = Normal / Música, `0x01` = Modo Juego (Baja Latencia), `0x02` = Modo Película.

#### 7. `0x37` — Búsqueda Acústica de Auriculares (`CMD_FIND_EARBUDS`)
Dispara un tono piezoeléctrico de alta frecuencia en los auriculares:
- **Byte 0 (Máscara de bits):** Bit 0 = Auricular Izquierdo, Bit 1 = Auricular Derecho (`0x01`: Izq, `0x02`: Der, `0x03`: Ambos, `0x00`: Detener).

#### 8. `0x22` — Protocolo de Transferencia de Esferas a Memoria Flash (`CMD_DIAL_TRANSFER`)
Gestiona el flujo de paquetes para grabar el binario del tema:
1. **Inicio de sesión (`0x00`):** Envía el tamaño total del binario y solicita permiso de escritura en la memoria Flash SPI.
2. **Transferencia de bloques (`0x01`):** Paquetes segmentados en chunks de 112 bytes numerados secuencialmente.
3. **Fin de transferencia (`0x02`):** Señala al cargador de arranque que verifique el checksum del tema y refresque la pantalla de visualización.

---

## 4. Especificación del Formato Binario de Esferas (.bin)

El microcontrolador JieLi no interpreta formatos estándar como PNG, JPEG o WebP en tiempo de ejecución. Requiere una estructura propietaria de capas pre-ensamblada identificada con la firma mágica `WF`.

```text
+--------------------------------------------------------------+
| Cabecera General del Tema (16 bytes)                         |
| Firma "WF", versión, dimensiones y número de descriptores    |
+--------------------------------------------------------------+
| Tabla de Descriptores de Capas (N x 20 bytes)                |
| Descriptor 0: Fondo o Imagen Base                            |
| Descriptor 1: Miniatura (Thumb)                              |
| Descriptor 2..N: Capas de Reloj, Fecha, Batería o Animación  |
+--------------------------------------------------------------+
| Bloque de Datos de Imagen 0 (Formato PA RLE Transpuesto)     |
+--------------------------------------------------------------+
| Bloque de Datos de Imagen 1 (Formato PA RLE Transpuesto)     |
+--------------------------------------------------------------+
| Bloque de Datos de Imagen ...                                |
+--------------------------------------------------------------+
```

### Cabecera General del Archivo (16 bytes)

| Offset | Tamaño | Tipo | Valor Típico | Descripción |
|---|---|---|---|---|
| `0x00 - 0x01` | 2 bytes | ASCII | `'W', 'F'` (`0x57, 0x46`) | Firma mágica del archivo de tema (WatchFace) |
| `0x02 - 0x03` | 2 bytes | Hex | `\x02\x04` | Versión del formato de visualización |
| `0x04 - 0x05` | 2 bytes | `uint16_le` | `320` (`\x40\x01`) | Ancho del lienzo en píxeles |
| `0x06 - 0x07` | 2 bytes | `uint16_le` | `172` (`\xAC\x00`) | Alto del lienzo en píxeles |
| `0x08` | 1 byte | `uint8` | `N` (ej. 2 a 12) | Cantidad de capas presentes en la tabla de descriptores |
| `0x09 - 0x0F` | 7 bytes | Bytes | `\x00 * 7` | Relleno nulo de alineación de memoria de 64 bits |

### Tabla de Descriptores de Capa (20 bytes c/u)

Cada capa definida en la esfera tiene una entrada estricta de 20 bytes en la tabla:

| Offset | Tamaño | Tipo | Descripción |
|---|---|---|---|
| `0..3` | 4 bytes | `uint32_le` | **Offset absoluto en bytes** al inicio del bloque de imagen `PA` correspondiente dentro del archivo `.bin`. |
| `4` | 1 byte | `uint8` | **Modo de renderizado**: `2` para sprites, animaciones e imágenes; `4` para números de dígitos dinámicos. |
| `5` | 1 byte | `uint8` | **Tipo de Capa**: Determina el comportamiento en el sistema operativo gráfico (ver tabla abajo). |
| `6` | 1 byte | `uint8` | **Contador de fotogramas (`cnt`)**: Cantidad de frames en la tira de imágenes (1 para estáticos, 10 para dígitos 0-9, $N$ para GIFs). |
| `7` | 1 byte | `uint8` | **Formato de píxel**: Fijo en `1` (Comprimido con algoritmo `PA` RLE transpuesta). |
| `8..9` | 2 bytes | `uint16_le` | **Posición Central X**: Coordenada horizontal central de la capa en pantalla ($0..320$). |
| `10..11` | 2 bytes | `uint16_le` | **Posición Central Y**: Coordenada vertical central de la capa en pantalla ($0..172$). |
| `12..19` | 8 bytes | `extra[8]` | Arreglo de parámetros adicionales del hardware. |

#### Tipos de Capa Reconocidos por el Chip:
- `0x10`: **Fondo o Gráfico Estático.**
- `0x11`: **Miniatura (Thumbnail).** Utilizada en el carrusel táctil de selección de esfera en la pantalla de la caja.
- `0x12`: **Iconos del Sistema.**
- `0x13`: **Animación en bucle.** Reproduce secuencialmente los fotogramas del strip.
- `0x01`: **Decenas de Hora (0..2).**
- `0x02`: **Unidades de Hora (0..9).**
- `0x03`: **Separador de Dos Puntos (:).**
- `0x04`: **Decenas de Minuto (0..5).**
- `0x05`: **Unidades de Minuto (0..9).**
- `0x06`: **Mes del Año.**
- `0x07`: **Día del Mes.**
- `0x08`: **Día de la Semana.**
- `0x09`: **Indicador de Batería.**

#### El Parámetro `extra[2]` en Animaciones (`0x13`):
En las capas de tipo `0x13`, el tercer byte del arreglo de extras (`extra[2]`) define el retardo entre fotogramas en ticks del temporizador del microcontrolador:
$$\text{Duración por Frame} \approx \text{extra}[2] \times 10 \text{ milisegundos}$$
- Si `extra[2] = 15` $\to$ retardo de 150 ms por fotograma ($\approx 6,66 \text{ FPS}$).
- Si `extra[2] = 7` $\to$ retardo de 70 ms por fotograma ($\approx 14,28 \text{ FPS}$).

### Formato de Compresión de Imagen PA (Transposed RLE)

Cada bloque de datos apuntado por un descriptor comienza con la cabecera `PA`:

| Offset | Tamaño | Tipo | Descripción |
|---|---|---|---|
| `0..1` | 2 bytes | ASCII | Firma mágica `'P', 'A'` (`0x50, 0x41`) |
| `2` | 1 byte | `uint8` | Bits por píxel (Fijo en `16` para RGB565) |
| `3` | 1 byte | `uint8` | Bandera de compresión (Fijo en `1`: RLE Transpuesto) |
| `4..5` | 2 bytes | `uint16_le` | Ancho total del sprite strip en píxeles |
| `6..7` | 2 bytes | `uint16_le` | Alto del sprite strip en píxeles |
| `8..11` | 4 bytes | `uint32_le` | Longitud en bytes de los datos completamente descomprimidos |
| `12..15` | 4 bytes | `uint32_le` | Longitud en bytes del bloque comprimido |

#### Algoritmo de Compresión Transpuesta:
1. **Transposición de Coordenadas:**  
   La imagen se itera en orden por columnas ($X$ externo de $0$ a $W-1$, $Y$ interno de $0$ a $H-1$). De este modo, los datos quedan ordenados verticalmente, permitiendo que la controladora LCD transfiera tiras de barrido directamente al display mediante DMA sin búfer de frame completo intermedio en la memoria RAM del microcontrolador.
2. **Canal Alfa / Píxeles Transparentes:**  
   Los píxeles con valor alfa menor a un umbral (o coincidentes con el color de clave) se codifican como series de salto transparente para evitar renderizar píxeles invisibles y reducir drásticamente el peso del binario.
3. **Empaquetado RLE:**  
   Las secuencias consecutivas de píxeles idénticos se comprimen mediante tokens de conteo de repetición.

---

## 5. El Enigma de la Sincronización Horaria (RTC)

Uno de los problemas más desconcertantes reportados por los usuarios era que, al intentar sincronizar el reloj desde la aplicación oficial o mediante comandos estándar de Unix, la caja mostraba siempre una hora desfasada e incorrecta (ejemplo: marcaba las **8:25** cuando la hora real eran las **11:28 pm**, o saltaba a las **10:12** siendo las **00:15 am**).

### Análisis del Desensamblado de la App Oficial

Mediante ingeniería inversa con `blutter` sobre el binario compilado de Dart/Flutter de la aplicación original (`com.zxl.mate`), se localizó la función `HeadsetCommand::setTimestamp` en `package_mate/mate/bluetooth/headset_command.dart` (líneas 852–946):

```dart
// Desensamblado ARM64 simplificado
r0 = DateTime._getCurrentMicros();
r2 = r0 ~/ 1000000; // Segundos de la época Unix (UTC)
b0 = r2 & 0xFF;
b1 = (r2 >> 8) & 0xFF;
b2 = (r2 >> 16) & 0xFF;
b3 = (r2 >> 24) & 0xFF;
// Empaqueta en trama RCSP: [seq, 0x93, 0x01, 0x00, 0x04, b0, b1, b2, b3]
HeadsetCommand::_write(0x93, payload);
```

Se descubrió que la aplicación oficial:
1. Obtiene la marca de tiempo Unix estándar en segundos (`time.time()`).
2. Lo empaqueta en 4 bytes Little-Endian.
3. Lo transmite con el comando `0x93`.
4. Esta llamada se efectúa de manera automática tras recibir la respuesta al comando `0x27` (`_parseDeviceInfo`). En la app oficial ni siquiera existe un botón de ajuste manual.

### La Causa Raíz: Firmware con Hora de Beijing (UTC+8)

¿Por qué este código funcionaba en China pero fallaba en el resto del mundo?
- Los ingenieros de JieLi en Zhuhai, China, asumieron que el reloj siempre funcionaría bajo la **Hora Estándar de China (Beijing, CST = UTC+8)**.
- El firmware del microcontrolador en la caja calcula la hora que pinta en pantalla sumando de forma fija **8 horas** a la marca de tiempo Unix recibida:
  $$\text{Hora Mostrada en Caja} = \text{Timestamp Recibido (UTC)} + 8\text{ horas}$$
- En China, como el teléfono está en UTC+8, al enviar la marca de tiempo UTC el firmware le sumaba 8 horas y el resultado coincidía exactamente con la hora de su país.
- Sin embargo, en países de América Latina como **Colombia o Perú (UTC-5)**:
  $$\text{Hora Local Real} = \text{UTC} - 5\text{ horas}$$
  Al recibir el timestamp estándar, la caja sumaba 8 horas, generando un desfase masivo hacia adelante:
  $$\text{Desfase Total} = (+8\text{h}) - (-5\text{h}) = \mathbf{+13\text{ horas de error}}$$

### Deducción y Fórmula de Compensación para Colombia / Perú (UTC-5)

La confirmación empírica definitiva ocurrió cuando se aplicó una prueba con `-15h` a la 01:05 am:
$$\text{Timestamp Enviado} = 01:05\text{ am} - 15\text{h} = 10:05\text{ am (día previo)}$$
$$\text{Hora Calculada por Caja} = 10:05\text{ am} + 8\text{h} = 11:05\text{ pm}$$
Exactamente 15 minutos después (a la 01:20 am), el usuario reinició la caja y la pantalla mostró con exactitud **11:20 pm**.

Esto demostró que:
1. El chip RTC sí almacena y avanza el tiempo con el comando `0x93`.
2. Para que la pantalla de la caja muestre la hora local del usuario en UTC-5, la ecuación es:
   $$\text{Hora Deseada en Pantalla} = \text{Timestamp Enviado} + 8\text{ horas}$$
   $$\text{Hora Local} = \text{Timestamp Enviado} + 8\text{ horas}$$
   $$\text{Timestamp Enviado} = \text{Hora Local} - 8\text{ horas}$$
   Y dado que en Python `time.time()` devuelve UTC, y la hora local es $\text{UTC} - 5\text{h}$:
   $$\text{Timestamp Enviado} = (\text{time.time()} - 5\text{h}) - 8\text{h} = \mathbf{\text{time.time()} - 13\text{ horas}}$$

**Fórmula Maestra Implementada:**
```python
def sync_time(self, offset_hours: float = -13.0) -> bool:
    target_ts = int(time.time() + (offset_hours * 3600))
    payload = struct.pack("<I", target_ts)
    return self.send_raw_command(0x93, payload, use_req=True)
```

### Ciclo de Redibujado de la Pantalla LCD

El microcontrolador JieLi actualiza sus registros internos de RTC inmediatamente al recibir el comando `0x93`. No obstante, el subsistema de interfaz gráfica (UI) no siempre redibuja los números en el mismo milisegundo si la pantalla está encendida y estática.

La pantalla LCD refresca los dígitos visuales desde el chip RTC en cualquiera de los siguientes eventos:
1. Al suspender y reactivar la pantalla (presionar el botón lateral para apagar la pantalla y volverlo a presionar).
2. Al deslizar el dedo hacia otra pantalla del sistema (ej. linterna o ajustes) y regresar a la esfera principal.
3. Al alcanzar el siguiente cambio de minuto en el temporizador (`:00`).
4. Al apagar o reiniciar el estuche.

---

## 6. Arquitectura del Software

```text
+---------------------------------------------------------------------------------+
|                                 NAVEGADOR WEB                                   |
|   Estudio Visual 320x172 (HTML5 Canvas)  |  Monitor de Batería y Control BLE    |
+----------------------------------------+----------------------------------------+
                                         | REST API (HTTP JSON)
                                         v
+---------------------------------------------------------------------------------+
|                       A9 PRO STUDIO SERVER (Python 3)                           |
|                                                                                 |
|  +-----------------------------------+   +-----------------------------------+  |
|  | StudioHTTPHandler                 |   | A9ProDeviceManager (Singleton)    |  |
|  | - Endpoints REST                  |   | - Reentrant Locking (RLock)       |  |
|  | - Compilador en memoria           |   | - Socket L2CAP ATT Persistente    |  |
|  | - Optimizador de GIF / Cuantizado |   | - Worker Notificaciones TLV       |  |
|  +-----------------------------------+   +-----------------------------------+  |
+----------------------------------------------------+----------------------------+
                                                     | Sockets L2CAP BLE (CID 4)
                                                     v
                                      +-------------------------------+
                                      |    A9 PRO CHARGING BOX LCD    |
                                      |  (Pantalla 320x172 / SoC JL)  |
                                      +-------------------------------+
```

### Servidor HTTP y Gestor Concurrente (`a9pro_studio.py`)
El núcleo del sistema es un servidor HTTP ligero multihilo (`ThreadingHTTPServer` con `StudioHTTPHandler`) que opera en el puerto `8765`. Aloja la interfaz web completa en una sola página (SPA) construida con Tailwind CSS y Vanilla JavaScript reactivo.

### Prevención de Interbloqueos con `RLock` y Multi-threading

El gestor de comunicaciones `A9ProDeviceManager` maneja llamadas concurrentes procedentes de:
1. Peticiones HTTP del usuario en el navegador (cambios de tema, volumen, ecualizador, hora).
2. El hilo en segundo plano `_notification_worker` que sondea el estado de la batería cada 5 segundos y procesa notificaciones en el handle `0x0003`.

> **Problema Crítico Resuelto:**  
> Originalmente se utilizaba `threading.Lock()`. Cuando una función como `send_raw_command` adquiría el cerrojo e invocaba internamente a `connect()`, este último intentaba adquirir el mismo cerrojo, provocando un interbloqueo (*deadlock*) irreversible.  
> La migración a **`threading.RLock()`** (cerrojo reentrante) garantiza que el mismo hilo pueda adquirir el bloqueo múltiples veces de forma segura sin congelar el socket L2CAP.

### Motor Web Canvas y Estudio Visual WYSIWYG
El editor en el navegador permite trabajar con capas independientes sobre un lienzo virtual de 320×172 píxeles:
- **Capas de Imagen:** Subida de PNG/JPG con ajuste de opacidad, escala, posición central y recorte.
- **Capas de Reloj Digital:** Renderizado de fuentes TrueType (`.ttf`) locales con generador dinámico de strips de dígitos (0 a 9) y dos puntos, con soporte para borde/trazo de alto contraste (*stroke*).
- **Capas de Animación GIF:** Reproducción sincronizada con control de fotogramas.

### Motor de Optimización de Animaciones GIF (Capa 0x13)
Insertar animaciones GIF completas en la caja A9 Pro era inviable con las herramientas de fábrica por el límite estricto de 150 KB. La suite incorpora un pipeline de procesamiento adaptativo implementado con Pillow:
1. **Muestreo Temporal Uniforme:** Permite seleccionar factores de reducción (`÷2`, `÷3`, `÷4`) o fijar un número máximo de fotogramas, conservando íntegra la duración total del video.
2. **Cálculo de Ticks de Hardware:** Ajusta automáticamente el valor `extra[2]` para que la velocidad percibida en la pantalla LCD coincida exactamente con la velocidad original del archivo GIF.
3. **Cuantización Adaptativa de Color:** Reduce la paleta global de cada frame (entre 8 y 256 colores) mediante difusión de error Floyd-Steinberg.
4. **Escalado Porcentual de Resolución:** Si la animación excede los 150 KB, el usuario puede reducir la escala (ej. al 85% o 70%) para que el compilador empaquete la animación respetando el espacio físico disponible.

### Herramienta CLI de Bajo Nivel (`a9pro_tool.py`)
Para operaciones avanzadas y entornos sin entorno gráfico, `a9pro_tool.py` ofrece utilidades directas:
- `python3 a9pro_tool.py info --mac 41:42:6C:C2:DD:3C`: Inspecciona las dimensiones de pantalla, capacidades y MTU.
- `python3 a9pro_tool.py time --offset -13.0`: Sincroniza la hora con la compensación deseada.
- `python3 a9pro_tool.py build --bg fondo.png --thumb icono.png`: Compila una esfera personalizada desde la terminal.

---

## 7. Referencia de la API REST

El servidor expone una API JSON en `http://localhost:8765/api/`:

| Método | Endpoint | Parámetros / Cuerpo | Respuesta Exitosa | Descripción |
|---|---|---|---|---|
| `GET` | `/api/status` | Ninguno | `{"connected": bool, "battery_box": int, ...}` | Devuelve la telemetría actual y el estado de la conexión BLE. |
| `GET` | `/api/connect` | Ninguno | `{"success": true}` | Conecta el socket L2CAP a la dirección MAC configurada. |
| `GET` | `/api/disconnect` | Ninguno | `{"success": true}` | Cierra el socket L2CAP de forma limpia y detiene el worker. |
| `GET` | `/api/query` | Ninguno | `{"success": true}` | Fuerza un sondeo completo de todos los tags TLV a la caja. |
| `POST` | `/api/control/sync_time` | `{"offset_hours": -13.0}` | `{"success": true, "offset_hours": -13.0}` | Sincroniza el reloj RTC del microcontrolador. |
| `POST` | `/api/control/noise_mode` | `{"mode": 0}` (0=ANC, 1=Normal, 2=Transp.) | `{"success": true}` | Modifica el modo de cancelación de ruido activa. |
| `POST` | `/api/control/work_mode` | `{"mode": 0}` (0=Música, 1=Juego, 2=Cine) | `{"success": true}` | Modifica el perfil de latencia y ecualización de trabajo. |
| `POST` | `/api/control/eq` | `{"preset": 1, "bands": [0,0,0,...]}` | `{"success": true}` | Ajusta el perfil del ecualizador DSP de 10 bandas. |
| `POST` | `/api/control/find` | `{"left": true, "right": false}` | `{"success": true}` | Activa el pitido localizador en los auriculares. |
| `GET` | `/api/themes/list` | Ninguno | `{"themes": [{"id": "...", "name": "..."}, ...]}` | Lista las 80 esferas oficiales con URL de vista previa. |
| `POST` | `/api/themes/upload_official` | `{"theme_id": "012"}` | `{"success": true}` | Transfiere una esfera oficial de la galería a la caja. |
| `POST` | `/api/themes/compile_and_upload` | JSON con la definición del proyecto multicapa | `{"success": true, "size": 105234}` | Compila el proyecto actual a binario `.bin` e inicia la subida. |
| `GET` | `/api/themes/upload_status` | Ninguno | `{"active": bool, "percent": 75, "speed_kbps": 4.8}` | Estado en tiempo real del progreso de transferencia BLE. |
| `POST` | `/api/themes/render_strip` | `{"font_family": "...", "font_size": 40, ...}` | `{"success": true, "strip_data": "data:image/png..."}` | Renderiza una tira de números con la fuente solicitada. |
| `POST` | `/api/themes/convert_gif` | `{"gif_data": "data:...", "max_frames": 24}` | `{"success": true, "count": 12, "strip_data": "..."}` | Extrae y optimiza los fotogramas de un archivo GIF. |
| `POST` | `/api/themes/optimize_image` | `{"image_data": "...", "target_w": 320, ...}` | `{"success": true, "image_data": "..."}` | Redimensiona y cuantiza una imagen para reducir su peso. |
| `GET` | `/api/fonts/list` | Ninguno | `{"fonts": [{"name": "Orbitron", ...}, ...]}` | Devuelve las fuentes TrueType disponibles en el sistema. |

---

## 8. Despliegue y Servicio Systemd en Linux

Para operar el sistema de manera desatendida como un demonio en segundo plano, se implementó una unidad de servicio de usuario en systemd.

### Archivo de Configuración
Ubicación: `~/.config/systemd/user/a9pro-studio.service`

```ini
[Unit]
Description=A9 Pro Box & Earbuds Studio Service
After=bluetooth.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -u /home/varzonelp/.gemini/antigravity/scratch/a9pro_themes/a9pro_studio.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

### Comandos de Administración
- **Recargar configuración de systemd:**
  ```bash
  systemctl --user daemon-reload
  ```
- **Iniciar o reiniciar el servicio:**
  ```bash
  systemctl --user restart a9pro-studio.service
  ```
- **Consultar estado en vivo:**
  ```bash
  systemctl --user status a9pro-studio.service
  ```
- **Inspeccionar registros de ejecución en tiempo real:**
  ```bash
  journalctl --user -u a9pro-studio.service -f
  ```

---

## 9. Guía de Solución de Problemas (Troubleshooting)

### 1. Error: `"Transport endpoint is not connected"` o fallas de timeout
- **Causa:** La pantalla táctil de la caja A9 Pro entró en modo de suspensión profunda (*deep sleep*) y apagó el radio BLE para ahorrar batería.
- **Solución:** Pulsa el botón físico lateral de la caja para iluminar la pantalla LCD. Una vez activa, la conexión BLE se reanuda de inmediato.

### 2. El reloj no cambia de inmediato en la pantalla al pulsar sincronizar
- **Causa:** El microcontrolador grabó la hora en el chip RTC, pero el hilo de dibujado gráfico de la interfaz no forzó un repintado inmediato del frame buffer.
- **Solución:** Pulsa el botón lateral para apagar la pantalla y vuelve a pulsarlo para encenderla, o desliza a la pantalla contigua (ej. linterna) y regresa. Los nuevos dígitos aparecerán reflejados al instante.

### 3. Error en compilación de tema: `"El tamaño binario (XXX KB) supera el límite de 150 KB"`
- **Causa:** La combinación de fotogramas de animación o la complejidad de la paleta de colores sobrepasa la partición física de 153.600 bytes.
- **Solución:**
  1. En las capas de animación GIF, activa el muestreo `÷2` o `÷3`.
  2. Reduce la cantidad de colores en el panel de optimización (ej. de 256 a 64 o 32 colores).
  3. Reduce la escala porcentual de la animación (ej. a 85% o 80%).

### 4. Permisos insuficientes para abrir sockets L2CAP en Linux
- **Causa:** Abrir sockets crudos `socket.SOCK_SEQPACKET` con `BTPROTO_L2CAP` puede requerir capacidades de red si no se ejecuta en una distribución con reglas estándar de BlueZ.
- **Solución:** Concede capacidades al binario de Python:
  ```bash
  sudo setcap 'cap_net_raw,cap_net_admin+eip' $(readlink -f $(which python3))
  ```

---

*Desarrollado y mantenido como parte del proyecto de ingeniería inversa y personalización de hardware para la comunidad abierta.*
