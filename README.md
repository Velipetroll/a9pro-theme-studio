# A9 Pro Smart Charging Box & Earbuds Studio

Suite integral de ingeniería inversa, personalización de temas (esferas de reloj) y telemetría Bluetooth Low Energy (BLE) para la caja de carga inteligente **A9 Pro** (pantalla LCD 320×172 px) y auriculares inalámbricos compatibles con chips Jerry (JL).

---

## 🚀 Características Principales

1. **Estudio Web Visual (320×172 px)**:
   - Lienzo multicapa interactivo con drag-and-drop, redimensionado y alineación inteligente.
   - Soporte para capas estáticas, miniatura automática, hora, fecha, batería y animaciones en bucle.
   - Sincronización en vivo a 30 FPS en el navegador.

2. **Soporte Avanzado para Animaciones GIF (Layer 0x13)**:
   - Extracción de fotogramas directamente de archivos GIF.
   - Muestreo temporal uniforme (`÷2`, `÷3`, `÷4` o frames exactos) conservando la duración total del video.
   - **Sincronización automática de velocidad**: Ajuste automático del temporizador de hardware (`extra[2]` en el descriptor binario) para reproducir a la velocidad real tanto en web como en pantalla física.
   - Compresión acumulativa de paleta (8c..256c) y pixel binning porcentual atado a la resolución nativa.

3. **Catálogo de 80 Esferas Oficiales**:
   - Galería de 80 temas oficiales extraídos del servidor de la app original.
   - Vista previa e instalación directa a la caja con un solo clic.

4. **Soporte para Fuentes Tipográficas Personalizadas**:
   - Carga directa de fuentes TrueType (`.ttf`).
   - Renderizado dinámico de capas de reloj analógicas/digitales.

5. **Telemetría BLE en Tiempo Real**:
   - Monitoreo en vivo de batería de auriculares (Izquierdo/Derecho) y estuche de carga.
   - Envío de esferas mediante protocolo BLE segmentado (ATT MTU acordado / control de flujo de paquetes).

6. **Herramienta CLI de Bajo Nivel (`a9pro_tool.py`)**:
   - Decodificador y desensamblador de paquetes `WF\x02\x04` y compresión propietaria `PA` (RLE + paleta RGB565 / RGBA).
   - Conversor bidireccional BIN $\leftrightarrow$ PNG.

---

## 📦 Estructura del Proyecto

```text
a9pro_themes/
├── a9pro_studio.py         # Servidor HTTP y API del estudio web interactivo
├── a9pro_tool.py           # CLI para decompilación/compilación y comunicación BLE
├── current_theme.json      # Proyecto actualmente activo en el personalizador
├── start_studio.sh         # Script de arranque rápido
├── backups/                # Colección completa de 80 temas oficiales (.bin y .png)
├── standalone_web/         # Versión ligera del estudio para uso web offline
├── web_assets/             # Iconos, fuentes base y assets de interfaz
├── custom_fonts/           # Directorio para fuentes .ttf personalizadas
└── .gitignore              # Reglas de exclusión de binarios temporales y decompilación APK
```

---

## 🛠️ Instalación y Uso

### Requisitos
- Python 3.9+
- Linux (con BlueZ para conectividad BLE por socket L2CAP / Bluetooth HCI)
- Bibliotecas Python:
  ```bash
  pip install Pillow
  ```

### Iniciar el Estudio Web
```bash
python3 a9pro_studio.py
```
Accede desde tu navegador en: **`http://localhost:8765`**

### Uso como Servicio Systemd (Modo Daemon)
```bash
systemctl --user status a9pro-studio.service
```

---

## 📄 Formato del Descriptor de Capas (20 bytes)

| Offset | Tamaño | Tipo | Descripción |
|---|---|---|---|
| 0..3 | 4 bytes | `uint32_le` | Offset de datos de imagen en el archivo .bin |
| 4 | 1 byte | `uint8` | Modo (generalmente `2` para sprites o `4` para números) |
| 5 | 1 byte | `uint8` | Tipo de capa (`0x10`: imagen, `0x11`: thumb, `0x13`: animación) |
| 6 | 1 byte | `uint8` | Cantidad de fotogramas en el sprite strip (`cnt`) |
| 7 | 1 byte | `uint8` | Formato (`1` para PA / RLE) |
| 8..9 | 2 bytes | `uint16_le` | Posición X central en pantalla |
| 10..11 | 2 bytes | `uint16_le` | Posición Y central en pantalla |
| 12..19 | 8 bytes | `extra[8]` | En `0x13`: `extra[2]` define los ticks de hardware ($\sim 10\text{ ms/tick}$) |
