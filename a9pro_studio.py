#!/usr/bin/env python3
"""
A9 Pro Studio - Suite Completa para A9 Pro Box & Audífonos
1. Interfaz Oficial con Batería en Vivo (Caja + L + R), ANC, Modos de Juego/Música, EQ 10 bandas y Gestos.
2. Personalizador de Temas Visual Mejorado con lienzo 320x172 interactivo, multicapas, efecto de profundidad,
   animaciones en bucle (GIF -> 0x13), editor de fuentes/colores y subida directa por BLE.
3. Biblioteca de los 80 Temas Oficiales con descompilador de capas a 1 clic.
"""

import sys
import os
import time
import json
import base64
import io
import struct
import socket
import threading
import math
import glob
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Optional, Dict, Any, List, Tuple

from PIL import Image, ImageDraw, ImageFont

# Importar funciones de bajo nivel de a9pro_tool
import a9pro_tool as tool

# Constantes del Dispositivo
DEFAULT_BOX_MAC = "41:42:6C:C2:DD:3C"
DEFAULT_BUDS_MAC = "41:42:FF:DE:A4:D1"
MAX_THEME_SIZE = 153600
PORT = 8765

BASE_DIR = Path(__file__).resolve().parent
OFFICIAL_DIR = BASE_DIR / "backups" / "official_320x172"
WEB_ASSETS_DIR = BASE_DIR / "web_assets"
CUSTOM_FONTS_DIR = BASE_DIR / "custom_fonts"
CUSTOM_FONTS_DIR.mkdir(parents=True, exist_ok=True)

AVAILABLE_FONTS = {
    "opensans": {"name": "Open Sans ExtraBold", "path": "/usr/share/fonts/TTF/OpenSans-CondensedExtraBold.ttf"},
    "orbitron": {"name": "Orbitron Sci-Fi (Futurista)", "path": str(CUSTOM_FONTS_DIR / "Orbitron-Bold.ttf")},
    "tech": {"name": "Share Tech Mono (Digital)", "path": str(CUSTOM_FONTS_DIR / "ShareTechMono-Regular.ttf")},
    "bebas": {"name": "Bebas Neue (Condensada Alta)", "path": str(CUSTOM_FONTS_DIR / "BebasNeue-Regular.ttf")},
    "pixel": {"name": "Press Start 2P (Retro 8-Bit)", "path": str(CUSTOM_FONTS_DIR / "PressStart2P-Regular.ttf")},
    "fira": {"name": "Fira Sans Bold", "path": "/usr/share/fonts/TTF/FiraSans-Bold.ttf"},
    "hack": {"name": "Hack Monospace", "path": "/usr/share/fonts/TTF/Hack-Bold.ttf"},
    "dejavu": {"name": "DejaVu Sans Bold", "path": "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"},
}


# ==============================================================================
# 1. GESTOR DE DISPOSITIVO BLE (A9 Pro Box & Audífonos)
# ==============================================================================
class A9ProDeviceManager:
    def __init__(self, mac: str = DEFAULT_BOX_MAC):
        self.mac = mac
        self.client: Optional[tool.A9ProL2CAPClient] = None
        self.lock = threading.Lock()
        self.connected = False
        self.listener_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

        # Estado del dispositivo
        self.state: Dict[str, Any] = {
            "connected": False,
            "mac": mac,
            "name": "A9 Pro",
            "firmware": "Desconocido",
            "battery_left": 0,
            "left_charging": False,
            "battery_right": 0,
            "right_charging": False,
            "battery_box": 0,
            "box_charging": False,
            "tws_connected": False,
            "tws_state": 0,
            "noise_mode": 1,      # 0: ANC, 1: Normal, 2: Transparente
            "work_mode": 0,       # 0: Música, 1: Juego (Baja Latencia), 2: Película
            "screen_width": 320,
            "screen_height": 172,
            "max_theme_size": MAX_THEME_SIZE,
            "protocol_mtu": 185,
            "eq_preset": 0,
            "eq_bands": [0] * 10,
            "taps": {
                "leftTap": 7, "rightTap": 7,
                "leftDouble": 4, "rightDouble": 4,
                "leftTriple": 5, "rightTriple": 5,
                "leftLongPress": 9, "rightLongPress": 9
            },
            "last_update": 0,
        }

        # Estado de subida de tema
        self.upload_state: Dict[str, Any] = {
            "active": False,
            "status": "idle",
            "percent": 0,
            "current_packet": 0,
            "total_packets": 0,
            "speed_kbps": 0.0,
            "error": None,
        }

    def connect(self) -> bool:
        with self.lock:
            if self.connected and self.client:
                return True
            try:
                print(f"[DeviceManager] Conectando a {self.mac}...")
                c = tool.A9ProL2CAPClient(self.mac)
                c.connect()
                self.client = c
                self.connected = True
                self.state["connected"] = True
                self.stop_event.clear()

                # Iniciar hilo de notificaciones
                self.listener_thread = threading.Thread(target=self._notification_worker, daemon=True)
                self.listener_thread.start()

                # Consultar toda la información
                self._send_query_all_locked()
                return True
            except Exception as e:
                print(f"[DeviceManager] Error conectando: {e}")
                self.connected = False
                self.state["connected"] = False
                if self.client:
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None
                return False

    def disconnect(self) -> None:
        with self.lock:
            self.stop_event.set()
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
            self.connected = False
            self.state["connected"] = False
            print("[DeviceManager] Desconectado.")

    def _notification_worker(self) -> None:
        """Escucha continua de notificaciones BLE (cmd 0x27 / 0x28) y sondeo periódico."""
        last_poll_time = 0.0
        while not self.stop_event.is_set():
            if not self.client or not self.connected:
                break
            try:
                # Periódicamente (cada 2 seg) enviar consulta de batería y estado
                now = time.time()
                if now - last_poll_time >= 2.0:
                    last_poll_time = now
                    # Tag 0x01 = Baterías, Tag 0x0C = ANC, Tag 0x08 = Modo Trabajo
                    query_tags = [1, 2, 8, 12, 13, 14]
                    payload = b"".join(bytes([t, 0]) for t in query_tags)
                    try:
                        self.client.write_cmd(self.client.build_cmd(0x27, payload))
                    except Exception:
                        pass

                raw = self.client.recv_notification(timeout=0.3)
                if raw and len(raw) >= 5:
                    cmd = raw[1]
                    if cmd in (0x27, 0x28):
                        self._parse_device_tlv(raw)
            except Exception:
                pass

    def _send_query_all_locked(self) -> None:
        if not self.client:
            return
        tags = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 21, 254, 22, 128, 129, 130, 131, 255]
        payload = b"".join(bytes([tag, 0]) for tag in tags)
        try:
            self.client.write_cmd(self.client.build_cmd(0x27, payload))
        except Exception as e:
            print(f"[DeviceManager] Error solicitando info: {e}")

    def query_all(self) -> bool:
        with self.lock:
            if not self.connected or not self.client:
                if not self.connect():
                    return False
            self._send_query_all_locked()
            return True

    def _parse_device_tlv(self, raw: bytes) -> None:
        """Parsea TLV records de las respuestas de estado del dispositivo."""
        idx = 5
        while idx + 2 <= len(raw):
            tag = raw[idx]
            ln = raw[idx + 1]
            val = raw[idx + 2 : idx + 2 + ln]
            idx += 2 + ln

            if tag == 0x01 and len(val) >= 3:
                # Batería: [left, right, box] con bit 7 como indicador de carga
                b_left, b_right, b_box = val[0], val[1], val[2]
                self.state["battery_left"] = b_left & 0x7F
                self.state["left_charging"] = bool(b_left & 0x80)
                self.state["battery_right"] = b_right & 0x7F
                self.state["right_charging"] = bool(b_right & 0x80)
                self.state["battery_box"] = b_box & 0x7F
                self.state["box_charging"] = bool(b_box & 0x80)
            elif tag == 0x02 and len(val) >= 4:
                self.state["firmware"] = f"{val[0]}.{val[1]}.{val[2]}.{val[3]}"
            elif tag == 0x03 and len(val) >= 1:
                try:
                    self.state["name"] = val.decode("utf-8")
                except Exception:
                    pass
            elif tag == 0x04 and len(val) >= 11:
                self.state["eq_preset"] = val[1]
                self.state["eq_bands"] = [struct.unpack("b", bytes([b]))[0] for b in val[2:12]]
            elif tag == 0x08 and len(val) >= 1:
                self.state["work_mode"] = val[0]
            elif tag == 0x0C and len(val) >= 1:
                self.state["noise_mode"] = val[0]
            elif tag == 0x0D and len(val) >= 1:
                self.state["tws_connected"] = bool(val[0])
            elif tag == 0x0E and len(val) >= 1:
                self.state["tws_state"] = val[0]
            elif tag == 0x80 and len(val) >= 10:
                self.state["screen_width"] = (val[0] << 8) | val[1]
                self.state["screen_height"] = (val[2] << 8) | val[3]
                self.state["max_theme_size"] = struct.unpack_from("<I", val, 5)[0]
            elif tag == 0xFF and len(val) >= 1:
                self.state["protocol_mtu"] = val[0]

        self.state["last_update"] = time.time()

    def send_raw_command(self, cmd: int, payload: bytes) -> bool:
        with self.lock:
            if not self.connected or not self.client:
                if not self.connect():
                    return False
            try:
                self.client.write_cmd(self.client.build_cmd(cmd, payload))
                return True
            except Exception as e:
                print(f"[DeviceManager] Error enviando cmd 0x{cmd:02X}: {e}")
                return False

    def set_noise_mode(self, mode: int) -> bool:
        # 0: ANC, 1: Normal, 2: Transparencia
        ok = self.send_raw_command(0x2C, bytes([mode & 0xFF]))
        if ok:
            self.state["noise_mode"] = mode
        return ok

    def set_work_mode(self, mode: int) -> bool:
        # 0: Normal / Música, 1: Juego (Baja Latencia), 2: Película
        ok = self.send_raw_command(0x25, bytes([mode & 0xFF]))
        if ok:
            self.state["work_mode"] = mode
        return ok

    def set_eq(self, preset: int, bands: List[int]) -> bool:
        # 12 bytes: [10, preset, b0..b9]
        clamped = [max(-12, min(12, int(b))) for b in (bands + [0]*10)[:10]]
        payload = bytearray([10, preset & 0xFF])
        for b in clamped:
            payload.append(struct.pack("b", b)[0])
        ok = self.send_raw_command(0x20, bytes(payload))
        if ok:
            self.state["eq_preset"] = preset
            self.state["eq_bands"] = clamped
        return ok

    def find_earbuds(self, left: bool, right: bool) -> bool:
        mask = (1 if left else 0) | (2 if right else 0)
        return self.send_raw_command(0x37, bytes([mask]))

    def sync_time(self) -> bool:
        now = time.time()
        tz_offset = -int(time.timezone / 3600)
        payload = struct.pack(">Ii", int(now), tz_offset)
        return self.send_raw_command(0x93, payload)

    def upload_theme_async(self, bin_data: bytes) -> None:
        """Inicia la subida del archivo .bin en segundo plano con reporte de progreso."""
        if self.upload_state["active"]:
            raise RuntimeError("Ya hay una subida en curso.")

        def _worker():
            self.upload_state["active"] = True
            self.upload_state["status"] = "uploading"
            self.upload_state["percent"] = 0
            self.upload_state["error"] = None
            start_t = time.time()

            try:
                # Asegurar desconexión previa de la escucha regular
                self.disconnect()
                time.sleep(0.5)

                # Envoltura del progreso
                def _prog_hook(cur, tot, pct):
                    self.upload_state["current_packet"] = cur
                    self.upload_state["total_packets"] = tot
                    self.upload_state["percent"] = round(pct, 1)
                    elapsed = max(0.001, time.time() - start_t)
                    self.upload_state["speed_kbps"] = round((cur * 128 * 8) / (elapsed * 1024), 1)

                uploader_client = tool.A9ProL2CAPClient(self.mac)
                ok = uploader_client.upload_watchface(bin_data, progress_callback=_prog_hook)
                if not ok:
                    raise RuntimeError("La caja rechazó la transferencia o se agotó el tiempo de espera.")
                self.upload_state["status"] = "completed"
                self.upload_state["percent"] = 100
            except Exception as e:
                print(f"[Upload] Error: {e}")
                self.upload_state["status"] = "error"
                self.upload_state["error"] = str(e)
            finally:
                self.upload_state["active"] = False
                # Reconectar cliente regular
                time.sleep(1.0)
                try:
                    self.connect()
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()


# ==============================================================================
# 2. MOTOR DEL PERSONALIZADOR DE TEMAS (Theme Studio Compiler)
# ==============================================================================
class ThemeStudioEngine:
    @staticmethod
    def decompile_bin(bin_bytes: bytes) -> Dict[str, Any]:
        """Descompone un .bin oficial o personalizado en capas editables con imágenes PNG."""
        if len(bin_bytes) < 52 or bin_bytes[0:2] != b"WF":
            raise ValueError("El archivo no es una esfera WF válida")

        num_items = struct.unpack_from("<H", bin_bytes, 46)[0]
        items = []
        for i in range(num_items):
            pos = 52 + i * 20
            off, mode, typ, cnt, fmt, x, y = struct.unpack_from("<IBBBBHH", bin_bytes, pos)
            extra = bin_bytes[pos + 12 : pos + 20]
            items.append({
                "index": i, "offset": off, "mode": mode, "type": typ,
                "count": cnt, "fmt": fmt, "x": x, "y": y, "extra": list(extra)
            })

        all_offs = sorted(set([it["offset"] for it in items] + [len(bin_bytes)]))
        off_to_sz = {all_offs[k]: all_offs[k+1] - all_offs[k] for k in range(len(all_offs)-1)}

        layers = []
        chunk_cache = {}
        thumbnail_b64 = None
        for it in items:
            off = it["offset"]
            sz = off_to_sz.get(off, 0)
            chunk = bin_bytes[off : off + sz]
            if off not in chunk_cache:
                if chunk[:2] == b"PA":
                    img = tool.decode_pa_to_image(chunk)
                    has_a = bool(chunk[3])
                elif chunk[:2] == b"BM":
                    img = Image.open(io.BytesIO(chunk)).transpose(Image.Transpose.ROTATE_270)
                    has_a = False
                else:
                    img = Image.new("RGBA", (10, 10))
                    has_a = False
                chunk_cache[off] = (img, has_a)

            img, has_a = chunk_cache[off]
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

            # La miniatura (type 0x11) es sólo para el menú de selección del firmware, no es una capa visible en pantalla
            if it["type"] == 0x11:
                thumbnail_b64 = b64_png
                continue

            name = tool.ITEM_TYPE_NAMES.get(it["type"], f"Capa 0x{it['type']:02X}")
            layers.append({
                "id": f"layer_{it['index']}",
                "name": name,
                "type": it["type"],
                "mode": it["mode"],
                "count": it["count"],
                "fmt": it["fmt"],
                "x": it["x"],
                "y": it["y"],
                "has_alpha": has_a,
                "width": img.width,
                "height": img.height,
                "image_data": b64_png,
                "extra": it["extra"],
                "visible": True,
            })

        return {
            "width": 320,
            "height": 172,
            "num_items": len(layers),
            "layers": layers,
            "total_size": len(bin_bytes)
        }

    @staticmethod
    def _get_font(font_family: str, font_size: int) -> ImageFont.ImageFont:
        font_path = None
        if font_family in AVAILABLE_FONTS:
            font_path = AVAILABLE_FONTS[font_family]["path"]
        elif (CUSTOM_FONTS_DIR / font_family).exists():
            font_path = str(CUSTOM_FONTS_DIR / font_family)
        elif Path(font_family).exists():
            font_path = font_family

        if font_path and Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, font_size)
            except Exception:
                pass
        try:
            return ImageFont.truetype("/usr/share/fonts/TTF/OpenSans-CondensedExtraBold.ttf", font_size)
        except Exception:
            return ImageFont.load_default()

    @staticmethod
    def render_digit_strip(font_size: int = 50, color_hex: str = "#FFFFFF",
                           stroke_w: int = 2, stroke_hex: str = "#000000",
                           font_family: str = "opensans") -> str:
        """Genera una tira de 10 dígitos (0..9) en formato PNG con posicionamiento preciso sin recortes."""
        font = ThemeStudioEngine._get_font(font_family, font_size)

        # Calcular dimensiones máximas de todos los dígitos (0..9)
        max_w = 0
        max_h = 0
        for d in range(10):
            b = font.getbbox(str(d))
            max_w = max(max_w, b[2] - b[0])
            max_h = max(max_h, b[3] - b[1])

        dw = max_w + stroke_w * 2 + 8
        dh = max_h + stroke_w * 2 + 8
        strip_w = dw * 10
        strip_h = dh

        img = Image.new("RGBA", (strip_w, strip_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for d in range(10):
            txt = str(d)
            b = font.getbbox(txt)
            w = b[2] - b[0]
            h = b[3] - b[1]
            # Compensar el desplazamiento top-left natural del glifo de la fuente (b[0] y b[1])
            tx = d * dw + (dw - w) // 2 - b[0]
            ty = (dh - h) // 2 - b[1]
            draw.text((tx, ty), txt, font=font, fill=color_hex,
                      stroke_width=stroke_w, stroke_fill=stroke_hex if stroke_w > 0 else None)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def render_weekday_strip(font_size: int = 16, color_hex: str = "#FFFFFF",
                             stroke_w: int = 1, stroke_hex: str = "#000000",
                             lang: str = "es", font_family: str = "opensans") -> str:
        """Genera una tira de 7 días (SUN..SAT o DOM..SAB) perfectamente centrada sin recortes."""
        days = ["DOM", "LUN", "MAR", "MIE", "JUE", "VIE", "SAB"] if lang == "es" else ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
        font = ThemeStudioEngine._get_font(font_family, font_size)

        max_w = 0
        max_h = 0
        for txt in days:
            b = font.getbbox(txt)
            max_w = max(max_w, b[2] - b[0])
            max_h = max(max_h, b[3] - b[1])

        dw = max(58, max_w + stroke_w * 2 + 8)
        dh = max_h + stroke_w * 2 + 8
        strip_w = dw * 7
        strip_h = dh

        img = Image.new("RGBA", (strip_w, strip_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        for idx, txt in enumerate(days):
            b = font.getbbox(txt)
            w = b[2] - b[0]
            h = b[3] - b[1]
            tx = idx * dw + (dw - w) // 2 - b[0]
            ty = (dh - h) // 2 - b[1]
            draw.text((tx, ty), txt, font=font, fill=color_hex,
                      stroke_width=stroke_w, stroke_fill=stroke_hex if stroke_w > 0 else None)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _quantize_frame(frame: Image.Image, max_colors: int = 64) -> Image.Image:
        """Cuantiza y reduce la profundidad de color para comprimir la información de la imagen sin alterar la resolución."""
        if max_colors <= 0 or max_colors >= 256:
            return frame
        try:
            # Si tiene transparencia
            if frame.mode in ("RGBA", "LA") or (frame.mode == "P" and "transparency" in frame.info):
                alpha = frame.split()[-1]
                rgb = frame.convert("RGB")
                quant_rgb = rgb.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT).convert("RGB")
                quant_rgb.putalpha(alpha)
                return quant_rgb
            else:
                rgb = frame.convert("RGB")
                return rgb.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT).convert("RGBA")
        except Exception:
            return frame

    @staticmethod
    def _resample_internal_resolution(img: Image.Image, res_pct: int = 100,
                                     base_w: Optional[int] = None, base_h: Optional[int] = None) -> Image.Image:
        """
        Reduce la resolución real de la imagen/fotograma en porcentaje,
        disminuyendo drásticamente el tamaño del archivo binario y de la tira de sprites.
        """
        if res_pct >= 100 or res_pct <= 5:
            return img
        curr_w, curr_h = img.size
        new_w = max(16, int(round(curr_w * res_pct / 100.0)))
        new_h = max(16, int(round(curr_h * res_pct / 100.0)))
        return img.resize((new_w, new_h), Image.Resampling.NEAREST)

    @staticmethod
    def convert_gif_to_strip(gif_bytes: bytes, max_frames: int = 8, fit_screen: bool = False,
                             target_w: Optional[int] = None, target_h: Optional[int] = None,
                             max_colors: int = 64, res_pct: int = 100) -> Tuple[str, int, int]:
        """Extrae y muestrea uniformemente los frames de un GIF a lo largo de todo el video/animación para no recortar la duración."""
        # Extraer retrasos reales analizando los bloques Graphic Control Extension (21 F9 04)
        raw_delays = []
        pos = 0
        while True:
            idx = gif_bytes.find(b"\x21\xf9\x04", pos)
            if idx == -1:
                break
            if idx + 6 < len(gif_bytes):
                delay_cs = struct.unpack_from("<H", gif_bytes, idx + 4)[0]
                raw_delays.append(delay_cs * 10) # a milisegundos
            pos = idx + 7

        im = Image.open(io.BytesIO(gif_bytes))
        raw_frames = []
        frame_durations = []
        f_idx = 0
        try:
            while True:
                raw_frames.append(im.convert("RGBA"))
                # Retraso del frame: usar bloque GCE o im.info
                d = 0
                if f_idx < len(raw_delays):
                    d = raw_delays[f_idx]
                if d <= 10:
                    d = im.info.get("duration", 0) or 0
                # Si el GIF tiene delay 0 o menor a 20ms (bug común de navegadores antiguos), estándar 100ms (10 fps)
                if d < 20:
                    d = 100
                frame_durations.append(d)
                f_idx += 1
                im.seek(im.tell() + 1)
        except EOFError:
            pass

        if not raw_frames:
            raise ValueError("No se encontraron fotogramas válidos en el GIF")

        total_available = len(raw_frames)
        total_video_duration_ms = sum(frame_durations) if frame_durations else (total_available * 100)

        # Muestreo uniforme: Si hay más fotogramas que max_frames, seleccionar índices distribuidos a lo largo de todo el GIF
        if max_frames and total_available > max_frames:
            # Distribuir exactamente max_frames desde el inicio (índice 0) hasta el final (índice total_available - 1)
            step = (total_available - 1) / float(max_frames - 1) if max_frames > 1 else float(total_available)
            selected_indices = [int(round(i * step)) for i in range(max_frames)]
            # Asegurar unicidad y límites
            selected_indices = [min(idx, total_available - 1) for idx in selected_indices]
            raw_frames = [raw_frames[i] for i in selected_indices]

        # Mantener automáticamente la duración total exacta del video original:
        # Si el video dura 1000ms con 14 frames, al dividir a 4 frames cada uno dura 250ms -> El video tarda exactamente los mismos 1000ms
        sampled_count = len(raw_frames)
        adjusted_frame_duration_ms = max(40, int(round(total_video_duration_ms / sampled_count)))

        # Extraer resolución nativa del video original
        base_w, base_h = raw_frames[0].size

        frames = []
        for frame in raw_frames:
            if fit_screen:
                # Ajuste proporcional exacto a pantalla completa (320x172) sin aplastar ni deformar (crop centrado con NEAREST)
                from PIL import ImageOps
                frame = ImageOps.fit(frame, (320, 172), method=Image.Resampling.NEAREST, centering=(0.5, 0.5))
            elif target_w and target_h:
                frame = frame.resize((target_w, target_h), Image.Resampling.NEAREST)

            # Reducir resolución real en porcentaje (mantiene la escala en pantalla pero reduce drásticamente el tamaño .bin)
            if res_pct < 100:
                frame = ThemeStudioEngine._resample_internal_resolution(frame, res_pct=res_pct)

            # Comprimir información de imagen (cuantización de paleta)
            if max_colors < 256:
                frame = ThemeStudioEngine._quantize_frame(frame, max_colors=max_colors)

            frames.append(frame)

        fw, fh = frames[0].size
        strip = Image.new("RGBA", (fw * len(frames), fh), (0, 0, 0, 0))
        for idx, f in enumerate(frames):
            strip.paste(f, (idx * fw, 0))

        buf = io.BytesIO()
        strip.save(buf, format="PNG")
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        return b64, len(frames), adjusted_frame_duration_ms, total_available, total_video_duration_ms, base_w, base_h

    @staticmethod
    def optimize_image(img_bytes: bytes, target_w: Optional[int] = None, target_h: Optional[int] = None,
                       mode: str = "fit_exact", max_colors: int = 64, res_pct: int = 100) -> str:
        """Comprime la información de la imagen ajustando resolución interna en porcentaje atado a la resolución original."""
        im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        base_w, base_h = im.size
        if target_w and target_h:
            if mode == "cover":
                from PIL import ImageOps
                im = ImageOps.fit(im, (target_w, target_h), method=Image.Resampling.NEAREST, centering=(0.5, 0.5))
            elif mode == "fit_exact":
                im = im.resize((target_w, target_h), Image.Resampling.NEAREST)
            elif mode == "contain":
                resized = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                im.thumbnail((target_w, target_h), Image.Resampling.NEAREST)
                rx = (target_w - im.width) // 2
                ry = (target_h - im.height) // 2
                resized.paste(im, (rx, ry))
                im = resized

        # Reducir resolución real en porcentaje (reduce drásticamente el tamaño .bin conservando escala)
        if res_pct < 100:
            im = ThemeStudioEngine._resample_internal_resolution(im, res_pct=res_pct)

        # Comprimir información de color reduciendo entropía para el algoritmo RLE de la caja
        if max_colors < 256:
            im = ThemeStudioEngine._quantize_frame(im, max_colors=max_colors)

        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def crop_layer_to_viewport(im: Image.Image, x: int, y: int, cnt: int = 1,
                               target_w: Optional[int] = None, target_h: Optional[int] = None) -> Optional[Tuple[Image.Image, int, int]]:
        """
        Recorta la imagen o tira de animación para que se ajuste exactamente al viewport visible
        de la pantalla (320x172 px), evitando enviar información de píxeles que no se van a ver.
        Retorna (new_im, new_x, new_y) o None si la capa está completamente fuera de pantalla.
        """
        orig_fw = im.width // cnt if cnt > 0 else im.width
        orig_fh = im.height
        dw = int(target_w) if (target_w is not None and target_w > 0) else orig_fw
        dh = int(target_h) if (target_h is not None and target_h > 0) else orig_fh

        screen_x0 = x - dw // 2
        screen_y0 = y - dh // 2
        screen_x1 = screen_x0 + dw
        screen_y1 = screen_y0 + dh

        # Si está completamente dentro de la pantalla, no requiere recorte
        if screen_x0 >= 0 and screen_y0 >= 0 and screen_x1 <= 320 and screen_y1 <= 172:
            return im, x, y

        # Intersección con los límites visibles de la pantalla (320x172)
        vis_x0 = max(0, screen_x0)
        vis_y0 = max(0, screen_y0)
        vis_x1 = min(320, screen_x1)
        vis_y1 = min(172, screen_y1)

        # Si está completamente fuera de la pantalla
        if vis_x1 <= vis_x0 or vis_y1 <= vis_y0:
            return None

        new_dw = vis_x1 - vis_x0
        new_dh = vis_y1 - vis_y0
        new_x = vis_x0 + new_dw // 2
        new_y = vis_y0 + new_dh // 2

        scale_x = orig_fw / float(dw)
        scale_y = orig_fh / float(dh)

        crop_x0 = max(0, min(orig_fw - 1, int(round((vis_x0 - screen_x0) * scale_x))))
        crop_y0 = max(0, min(orig_fh - 1, int(round((vis_y0 - screen_y0) * scale_y))))
        crop_x1 = max(crop_x0 + 1, min(orig_fw, int(round((vis_x1 - screen_x0) * scale_x))))
        crop_y1 = max(crop_y0 + 1, min(orig_fh, int(round((vis_y1 - screen_y0) * scale_y))))

        crop_fw = crop_x1 - crop_x0
        crop_fh = crop_y1 - crop_y0

        new_strip = Image.new("RGBA", (crop_fw * cnt, crop_fh), (0, 0, 0, 0))
        for i in range(cnt):
            frame = im.crop((i * orig_fw + crop_x0, crop_y0, i * orig_fw + crop_x1, crop_y1))
            new_strip.paste(frame, (i * crop_fw, 0))

        return new_strip, new_x, new_y

    @staticmethod
    def compile_project(layers_data: List[Dict[str, Any]]) -> Tuple[bytes, Dict[str, Any]]:
        """Compila capas interactivas a un archivo .bin con compresión PA y deduplicación de chunks."""
        import hashlib

        # Filtrar capas visibles
        active_layers = [dict(l) for l in layers_data if l.get("visible", True)]
        if not active_layers:
            raise ValueError("No hay capas visibles para compilar.")

        # Recortar solo al compilar/enviar las capas que se salen del área visible (320x172),
        # y reescalar a resolución de pantalla completa (320x172) si tienen fit_screen activado.
        cropped_active_layers = []
        for l in active_layers:
            # Excluir capas de fecha/reloj en mode=4 (dígitos numéricos) ya que son tiras de glifos 0..9
            if l.get("mode") == 4 or l.get("type") in (1, 2, 3, 4, 5, 6, 7):
                cropped_active_layers.append(l)
                continue

            try:
                img_b64 = l["image_data"].split(",", 1)[-1]
                im = Image.open(io.BytesIO(base64.b64decode(img_b64)))
                cnt = l.get("count", 1)
                l_copy = dict(l)

                # Si la capa tiene activada la opción de pantalla completa (fit_screen):
                # Reescalar cada frame exactamente a 320x172 usando NEAREST (mantiene los píxeles nítidos
                # y comprime de forma óptima con el algoritmo RLE/PA del hardware).
                if l.get("fit_screen"):
                    orig_fw = im.width // cnt if cnt > 0 else im.width
                    orig_fh = im.height
                    if (orig_fw, orig_fh) != (320, 172):
                        scaled_strip = Image.new("RGBA", (320 * cnt, 172), (0, 0, 0, 0))
                        for i in range(cnt):
                            frame = im.crop((i * orig_fw, 0, (i + 1) * orig_fw, orig_fh))
                            frame_scaled = frame.resize((320, 172), Image.Resampling.NEAREST)
                            scaled_strip.paste(frame_scaled, (i * 320, 0))
                        im = scaled_strip
                    l_copy["x"] = 160
                    l_copy["y"] = 86
                    l_copy["target_w"] = 320
                    l_copy["target_h"] = 172

                crop_res = ThemeStudioEngine.crop_layer_to_viewport(
                    im,
                    int(l_copy.get("x", 160)),
                    int(l_copy.get("y", 86)),
                    cnt=cnt,
                    target_w=l_copy.get("target_w"),
                    target_h=l_copy.get("target_h")
                )
                if crop_res is None:
                    # Capa 100% fuera de pantalla: omitir
                    continue
                crop_im, nx, ny = crop_res
                buf = io.BytesIO()
                crop_im.save(buf, format="PNG")
                l_copy["image_data"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                l_copy["x"] = nx
                l_copy["y"] = ny
                cropped_active_layers.append(l_copy)
            except Exception:
                cropped_active_layers.append(l)

        active_layers = cropped_active_layers
        if not active_layers:
            raise ValueError("Todas las capas están fuera del área visible de la pantalla.")

        # Generar thumbnail automático (type=0x11, 204x112) si no existe
        has_thumb = any(l["type"] == 0x11 for l in active_layers)
        if not has_thumb:
            # Componer preview
            canvas = Image.new("RGBA", (320, 172), (0, 0, 0, 255))
            for l in active_layers:
                try:
                    img_b64 = l["image_data"].split(",", 1)[-1]
                    im = Image.open(io.BytesIO(base64.b64decode(img_b64)))
                    cnt = l.get("count", 1)
                    fw = im.width // cnt if cnt > 0 else im.width
                    fh = im.height
                    x, y = l["x"], l["y"]
                    if l["type"] in (2, 3, 5, 6, 7) and l["mode"] == 4:
                        f0 = im.crop((0, 0, fw, fh))
                        canvas.alpha_composite(f0, (int(x - fw), int(y - fh // 2)))
                        canvas.alpha_composite(f0, (int(x), int(y - fh // 2)))
                    else:
                        f0 = im.crop((0, 0, fw, fh))
                        canvas.alpha_composite(f0, (int(x - fw // 2), int(y - fh // 2)))
                except Exception:
                    pass

            thumb_im = canvas.resize((204, 112), Image.Resampling.LANCZOS)
            thumb_buf = io.BytesIO()
            thumb_im.save(thumb_buf, format="PNG")
            thumb_b64 = "data:image/png;base64," + base64.b64encode(thumb_buf.getvalue()).decode("ascii")
            active_layers.append({
                "id": "auto_thumb",
                "name": "Miniatura Automática",
                "type": 0x11,
                "mode": 2,
                "count": 1,
                "fmt": 1,
                "x": 160,
                "y": 86,
                "has_alpha": False,
                "image_data": thumb_b64,
                "extra": [0] * 8,
            })

        # Codificación y deduplicación
        encoded_cache = {}
        unique_chunks = []
        layer_offsets = []

        first_data_offset = 52 + len(active_layers) * 20
        curr_offset = first_data_offset

        for l in active_layers:
            img_b64 = l["image_data"].split(",", 1)[-1]
            im = Image.open(io.BytesIO(base64.b64decode(img_b64)))
            has_alpha = l.get("has_alpha", True)

            # Clave única
            raw_key = hashlib.md5(im.tobytes() + bytes([1 if has_alpha else 0])).hexdigest()
            if raw_key in encoded_cache:
                off = encoded_cache[raw_key]
            else:
                chunk = tool.encode_image_to_pa(im, has_alpha=has_alpha)
                off = curr_offset
                encoded_cache[raw_key] = off
                unique_chunks.append(chunk)
                curr_offset += len(chunk)

            layer_offsets.append(off)

        total_size = curr_offset

        # Cabecera WF
        hdr = bytearray(52)
        hdr[0:4] = b"WF\x02\x04"
        hdr[8] = 7
        hdr[9] = 9
        struct.pack_into("<HHHI", hdr, 42, 320, 172, len(active_layers), total_size)

        # Descriptores de 20 bytes
        descs = bytearray()
        for l, off in zip(active_layers, layer_offsets):
            d = bytearray(20)
            struct.pack_into("<IBBBBHH", d, 0, off, int(l.get("mode", 2)), int(l.get("type", 0x10)), int(l.get("count", 1)), 1, int(l.get("x", 160)), int(l.get("y", 86)))
            raw_extra = l.get("extra") or []
            clean_extra = [int(b) if isinstance(b, (int, float)) and not math.isnan(b) else 0 for b in raw_extra]
            clean_extra = (clean_extra + [0] * 8)[:8]
            # En capas de tipo 0x13 (animación en bucle), extra[2] especifica la velocidad / intervalo de cuadros en hardware (1 tick = 10ms)
            if int(l.get("type", 0x10)) == 0x13:
                f_dur = float(l.get("frame_duration_ms", 0))
                if f_dur <= 0:
                    tot_dur = float(l.get("total_duration_ms", 1000))
                    cnt = max(1, int(l.get("count", 1)))
                    f_dur = tot_dur / cnt
                # 1 tick = 10ms. Mínimo 5 (50ms), máximo 50 (500ms)
                clean_extra[2] = max(5, min(50, int(round(f_dur / 10.0))))
            for i_ex, ex_val in enumerate(clean_extra):
                d[12 + i_ex] = ex_val & 0xFF
            assert len(d) == 20, f"Error: descriptor must be 20 bytes, got {len(d)}"
            descs.extend(d)

        bin_data = bytes(hdr) + bytes(descs) + b"".join(unique_chunks)

        stats = {
            "total_size": len(bin_data),
            "max_size": MAX_THEME_SIZE,
            "within_limit": len(bin_data) <= MAX_THEME_SIZE,
            "percent_used": round((len(bin_data) / MAX_THEME_SIZE) * 100, 1),
            "num_layers": len(active_layers),
            "unique_chunks": len(unique_chunks),
        }
        return bin_data, stats


# Instancias globales
device_mgr = A9ProDeviceManager()
studio_engine = ThemeStudioEngine()


# ==============================================================================
# 3. SERVIDOR HTTP Y API REST
# ==============================================================================
class StudioHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silenciar logs para consola limpia

    def _send_json(self, data: Any, code: int = 200) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _serve_file(self, file_path: Path, mime: str) -> None:
        if not file_path.exists():
            self.send_error(404, "Archivo no encontrado")
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            html = get_studio_ui_html()
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        # Rutas de assets
        if path.startswith("/web_assets/"):
            fname = path.split("/")[-1]
            fpath = WEB_ASSETS_DIR / fname
            mime = "image/gif" if fname.endswith(".gif") else "image/png"
            self._serve_file(fpath, mime)
            return

        if path.startswith("/official_themes/"):
            fname = path.split("/")[-1]
            fpath = OFFICIAL_DIR / fname
            mime = "image/png" if fname.endswith(".png") else "application/octet-stream"
            self._serve_file(fpath, mime)
            return

        # API
        if path == "/api/status":
            self._send_json(device_mgr.state)
        elif path == "/api/connect":
            ok = device_mgr.connect()
            self._send_json({"success": ok, "state": device_mgr.state})
        elif path == "/api/disconnect":
            device_mgr.disconnect()
            self._send_json({"success": True})
        elif path == "/api/query":
            ok = device_mgr.query_all()
            self._send_json({"success": ok, "state": device_mgr.state})
        elif path == "/api/upload_status":
            self._send_json(device_mgr.upload_state)
        elif path == "/api/fonts":
            font_list = [{"id": k, "name": v["name"]} for k, v in AVAILABLE_FONTS.items()]
            self._send_json({"success": True, "fonts": font_list})
        elif path == "/api/themes/current_project":
            proj_file = BASE_DIR / "current_theme.json"
            if proj_file.exists():
                try:
                    data = json.loads(proj_file.read_text("utf-8"))
                    self._send_json({"success": True, "project": data, "has_custom": True})
                    return
                except Exception:
                    pass
            self._send_json({"success": False, "has_custom": False})
        elif path == "/api/official_themes":
            # Listar temas oficiales
            res = []
            for bp in sorted(OFFICIAL_DIR.glob("*.bin")):
                stem = bp.stem
                png_path = bp.with_suffix(".png")
                thumb_url = f"/official_themes/{png_path.name}" if png_path.exists() else ""
                res.append({
                    "id": stem,
                    "filename": bp.name,
                    "size_bytes": bp.stat().st_size,
                    "thumbnail_url": thumb_url
                })
            self._send_json(res)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        req = json.loads(body.decode("utf-8")) if body else {}

        if path == "/api/control/noise":
            mode = req.get("mode", 1)
            ok = device_mgr.set_noise_mode(mode)
            self._send_json({"success": ok, "mode": mode})
        elif path == "/api/control/work":
            mode = req.get("mode", 0)
            ok = device_mgr.set_work_mode(mode)
            self._send_json({"success": ok, "mode": mode})
        elif path == "/api/control/eq":
            preset = req.get("preset", 0)
            bands = req.get("bands", [0]*10)
            ok = device_mgr.set_eq(preset, bands)
            self._send_json({"success": ok})
        elif path == "/api/control/find":
            left = req.get("left", False)
            right = req.get("right", False)
            ok = device_mgr.find_earbuds(left, right)
            self._send_json({"success": ok})
        elif path == "/api/control/sync_time":
            ok = device_mgr.sync_time()
            self._send_json({"success": ok})
        elif path == "/api/themes/render_strip":
            strip_type = req.get("strip_type", "digits")
            font_size = req.get("font_size", 50)
            color_hex = req.get("color_hex", "#FFFFFF")
            stroke_w = req.get("stroke_w", 2)
            stroke_hex = req.get("stroke_hex", "#000000")
            lang = req.get("lang", "es")
            font_family = req.get("font_family", "opensans")
            if strip_type == "digits":
                b64 = studio_engine.render_digit_strip(font_size, color_hex, stroke_w, stroke_hex, font_family=font_family)
            else:
                b64 = studio_engine.render_weekday_strip(font_size, color_hex, stroke_w, stroke_hex, lang=lang, font_family=font_family)
            self._send_json({"success": True, "image_data": b64})
        elif path == "/api/fonts/upload":
            font_name = req.get("name", "custom_font.ttf")
            font_b64 = req.get("data", "").split(",", 1)[-1]
            try:
                raw_bytes = base64.b64decode(font_b64)
                safe_name = "".join(c for c in font_name if c.isalnum() or c in "._- ")
                dest_file = CUSTOM_FONTS_DIR / safe_name
                dest_file.write_bytes(raw_bytes)
                key = safe_name.lower().replace(" ", "_").replace(".ttf", "")
                AVAILABLE_FONTS[key] = {"name": f"Personalizada: {safe_name}", "path": str(dest_file)}
                self._send_json({"success": True, "font_id": key, "name": f"Personalizada: {safe_name}"})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, code=400)
        elif path == "/api/themes/convert_gif":
            gif_b64 = req.get("gif_data", "").split(",", 1)[-1]
            max_frames = int(req.get("max_frames", 12))
            fit_screen = bool(req.get("fit_screen", False))
            target_w = req.get("target_w")
            target_h = req.get("target_h")
            max_colors = int(req.get("max_colors", 64))
            try:
                gif_bytes = base64.b64decode(gif_b64)
                res_pct = int(req.get("res_pct", 100))
                strip_b64, num_frames, duration, total_frames, total_duration, orig_w, orig_h = studio_engine.convert_gif_to_strip(
                    gif_bytes, max_frames=max_frames, fit_screen=fit_screen,
                    target_w=int(target_w) if target_w else None,
                    target_h=int(target_h) if target_h else None,
                    max_colors=max_colors,
                    res_pct=res_pct
                )
                self._send_json({
                    "success": True,
                    "strip_data": strip_b64,
                    "count": num_frames,
                    "duration": duration,
                    "total_frames": total_frames,
                    "total_duration": total_duration,
                    "orig_w": orig_w,
                    "orig_h": orig_h
                })
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, code=400)
        elif path == "/api/themes/optimize_image":
            img_b64 = req.get("image_data", "").split(",", 1)[-1]
            target_w = req.get("target_w")
            target_h = req.get("target_h")
            mode = req.get("mode", "fit_exact")
            max_colors = int(req.get("max_colors", 64))
            res_pct = int(req.get("res_pct", 100))
            try:
                img_bytes = base64.b64decode(img_b64)
                res_b64 = studio_engine.optimize_image(
                    img_bytes,
                    target_w=int(target_w) if target_w else None,
                    target_h=int(target_h) if target_h else None,
                    mode=mode,
                    max_colors=max_colors,
                    res_pct=res_pct
                )
                self._send_json({"success": True, "image_data": res_b64})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, code=400)
        elif path == "/api/themes/load_official":
            theme_id = req.get("theme_id")
            bp = OFFICIAL_DIR / f"{theme_id}.bin"
            if not bp.exists():
                self._send_json({"success": False, "error": "No existe"}, code=404)
                return
            project = studio_engine.decompile_bin(bp.read_bytes())
            self._send_json({"success": True, "project": project})
        elif path == "/api/themes/save_current":
            project_data = req.get("project")
            if project_data:
                proj_file = BASE_DIR / "current_theme.json"
                clean_layers = []
                for l in project_data.get("layers", []):
                    cl = {k: v for k, v in l.items() if not k.startswith("_")}
                    clean_layers.append(cl)
                save_data = {
                    "width": project_data.get("width", 320),
                    "height": project_data.get("height", 172),
                    "layers": clean_layers
                }
                proj_file.write_text(json.dumps(save_data), encoding="utf-8")
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "No project data"}, code=400)
        elif path == "/api/themes/reset_default":
            proj_file = BASE_DIR / "current_theme.json"
            if proj_file.exists():
                proj_file.unlink()
            default_bp = OFFICIAL_DIR / "385_deb0daf817f94b5d8e1fb4a0ff4bd7f3.bin"
            if default_bp.exists():
                project = studio_engine.decompile_bin(default_bp.read_bytes())
                self._send_json({"success": True, "project": project})
            else:
                self._send_json({"success": True, "project": {"width": 320, "height": 172, "layers": []}})
        elif path == "/api/themes/compile":
            layers = req.get("layers", [])
            bin_data, stats = studio_engine.compile_project(layers)
            # Guardar temporalmente
            temp_path = BASE_DIR / "custom_compiled.bin"
            temp_path.write_bytes(bin_data)
            self._send_json({"success": True, "stats": stats})
        elif path == "/api/themes/upload_compiled":
            temp_path = BASE_DIR / "custom_compiled.bin"
            if not temp_path.exists():
                self._send_json({"success": False, "error": "No hay tema compilado"}, code=400)
                return
            bin_data = temp_path.read_bytes()
            device_mgr.upload_theme_async(bin_data)
            self._send_json({"success": True})
        elif path == "/api/themes/upload_official":
            theme_id = req.get("theme_id")
            bp = OFFICIAL_DIR / f"{theme_id}.bin"
            if not bp.exists():
                self._send_json({"success": False, "error": "No existe el tema"}, code=404)
                return
            device_mgr.upload_theme_async(bp.read_bytes())
            self._send_json({"success": True})
        else:
            self.send_error(404)


# ==============================================================================
# 4. INTERFAZ DE USUARIO INTEGRADA (SPA Web HTML5 / Canvas / Tailwind)
# ==============================================================================
def get_studio_ui_html() -> str:
    return """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>A9 Pro Studio - Control & Personalizador</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background-color: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    .screen-bezel { background: #000; border: 4px solid #1f2937; border-radius: 12px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.7); }
    .slider-vertical { writing-mode: bt-lr; -webkit-appearance: slider-vertical; width: 8px; height: 110px; }
    canvas {
      image-rendering: -moz-crisp-edges;
      image-rendering: -webkit-crisp-edges;
      image-rendering: pixelated;
      image-rendering: crisp-edges;
    }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- Header Superior -->
  <header class="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center font-bold text-white shadow-lg">A9</div>
      <div>
        <h1 class="text-base font-bold leading-tight flex items-center gap-2">
          A9 Pro Box & Mate Studio
          <span id="badge-conn" class="text-xs px-2 py-0.5 rounded-full bg-red-900/60 text-red-400 border border-red-700/50">Desconectado</span>
        </h1>
        <p class="text-xs text-gray-400">Bluetooth LE L2CAP (Linux) | Pantalla 320×172 px</p>
      </div>
    </div>

    <!-- Navegación de Pestañas -->
    <nav class="flex space-x-1 bg-gray-800/80 p-1 rounded-xl border border-gray-700/50">
      <button onclick="setTab('device')" id="btn-tab-device" class="px-4 py-1.5 rounded-lg text-sm font-medium transition bg-blue-600 text-white shadow">📱 Mi Dispositivo</button>
      <button onclick="setTab('studio')" id="btn-tab-studio" class="px-4 py-1.5 rounded-lg text-sm font-medium transition text-gray-300 hover:text-white">🎨 Personalizador de Temas</button>
      <button onclick="setTab('gallery')" id="btn-tab-gallery" class="px-4 py-1.5 rounded-lg text-sm font-medium transition text-gray-300 hover:text-white">📚 Galería (80 Oficiales)</button>
    </nav>

    <!-- Conexión Rápida -->
    <div class="flex items-center space-x-2">
      <button onclick="toggleConnect()" id="btn-conn" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow transition flex items-center gap-1.5">
        <span class="w-2 h-2 rounded-full bg-white animate-pulse"></span>
        <span id="btn-conn-text">Conectar Caja</span>
      </button>
    </div>
  </header>

  <!-- Contenido Principal -->
  <main class="flex-1 p-6 max-w-7xl mx-auto w-full">

    <!-- ==================== PESTAÑA 1: DISPOSITIVO ORIGINAL ==================== -->
    <section id="tab-device" class="space-y-6">
      
      <!-- Fila Superior: Renders del Dispositivo y Batería -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        <!-- Audífono Izquierdo (L) -->
        <div id="card-earbud-l" class="bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300">
          <div class="flex items-center justify-between w-full">
            <span class="text-xs uppercase font-bold tracking-wider text-gray-400">Audífono Izquierdo (L)</span>
            <span id="badge-mode-l" class="text-[10px] px-2 py-0.5 rounded-full font-semibold bg-gray-800 text-gray-400 border border-gray-700">En uso</span>
          </div>
          <div class="relative my-3">
            <img id="img-earbud-l" src="/web_assets/left_earphone.png" class="h-36 object-contain drop-shadow-xl transition-transform duration-300" alt="Left Earbud">
            <div id="glow-charge-l" class="absolute inset-0 rounded-full bg-emerald-500/10 blur-xl opacity-0 transition-opacity duration-500 pointer-events-none"></div>
          </div>
          <div class="w-full space-y-1.5">
            <div class="flex justify-between items-center text-sm font-semibold">
              <span id="txt-bat-l">0%</span>
              <span id="icon-charge-l" class="text-xs font-bold text-emerald-400 flex items-center gap-1 hidden">
                <span class="animate-bounce">⚡</span> Cargando en caja
              </span>
            </div>
            <div class="w-full bg-gray-800 rounded-full h-2.5 overflow-hidden">
              <div id="bar-bat-l" class="bg-blue-500 h-2.5 rounded-full transition-all duration-500" style="width: 0%"></div>
            </div>
          </div>
        </div>

        <!-- Estuche Inteligente A9 Pro Box -->
        <div id="card-box" class="bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden shadow-2xl transition-all duration-300">
          <div class="flex items-center justify-between w-full">
            <span class="text-xs uppercase font-bold tracking-wider text-gray-400">Caja Inteligente A9 Pro Box</span>
            <span id="txt-firmware" class="text-xs text-gray-500 font-mono">v3.4.0.0</span>
          </div>
          
          <div class="relative my-2 inline-block">
            <img src="/web_assets/connected.png" class="h-44 w-auto object-contain block" alt="A9 Pro Box">
            <!-- Mockup de la pantalla en vivo dentro del estuche encajada y ligeramente comprimida verticalmente -->
            <div class="absolute overflow-hidden pointer-events-none" style="top: 43.2%; left: 18.2%; width: 63.6%; height: 34.2%; border-radius: 7px;">
              <!-- Canvas que renderiza el tema actual a 320x172 -->
              <canvas id="box-screen-canvas" width="320" height="172" class="w-full h-full object-fill block"></canvas>
              <!-- Overlay de status bar transparente sobre el tema -->
              <div class="absolute inset-x-0 top-0 text-[8px] bg-gradient-to-b from-black/80 to-transparent text-gray-200 flex justify-between w-full px-1.5 pt-0.5 pointer-events-none drop-shadow">
                <span>L <b id="screen-l">100%</b> R <b id="screen-r">100%</b></span>
                <span id="screen-box">🔋 100%</span>
              </div>
            </div>
          </div>

          <div class="w-full space-y-1.5">
            <div class="flex justify-between items-center text-sm font-semibold">
              <span id="txt-bat-box">0%</span>
              <span id="icon-charge-box" class="text-xs font-bold text-amber-400 flex items-center gap-1 hidden">
                <span class="animate-pulse">⚡</span> Conectado a corriente
              </span>
            </div>
            <div class="w-full bg-gray-800 rounded-full h-2.5 overflow-hidden">
              <div id="bar-bat-box" class="bg-emerald-500 h-2.5 rounded-full transition-all duration-500" style="width: 0%"></div>
            </div>
          </div>
        </div>

        <!-- Audífono Derecho (R) -->
        <div id="card-earbud-r" class="bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300">
          <div class="flex items-center justify-between w-full">
            <span class="text-xs uppercase font-bold tracking-wider text-gray-400">Audífono Derecho (R)</span>
            <span id="badge-mode-r" class="text-[10px] px-2 py-0.5 rounded-full font-semibold bg-gray-800 text-gray-400 border border-gray-700">En uso</span>
          </div>
          <div class="relative my-3">
            <img id="img-earbud-r" src="/web_assets/right_earphone.png" class="h-36 object-contain drop-shadow-xl transition-transform duration-300" alt="Right Earbud">
            <div id="glow-charge-r" class="absolute inset-0 rounded-full bg-emerald-500/10 blur-xl opacity-0 transition-opacity duration-500 pointer-events-none"></div>
          </div>
          <div class="w-full space-y-1.5">
            <div class="flex justify-between items-center text-sm font-semibold">
              <span id="txt-bat-r">0%</span>
              <span id="icon-charge-r" class="text-xs font-bold text-emerald-400 flex items-center gap-1 hidden">
                <span class="animate-bounce">⚡</span> Cargando en caja
              </span>
            </div>
            <div class="w-full bg-gray-800 rounded-full h-2.5 overflow-hidden">
              <div id="bar-bat-r" class="bg-blue-500 h-2.5 rounded-full transition-all duration-500" style="width: 0%"></div>
            </div>
          </div>
        </div>

      </div>

      <!-- Notificación flotante Toast en tiempo real -->
      <div id="toast-notify" class="fixed bottom-6 right-6 z-50 bg-gray-900/95 border border-emerald-500 text-white px-4 py-3 rounded-xl shadow-2xl flex items-center space-x-3 transition-all duration-500 transform translate-y-24 opacity-0 pointer-events-none">
        <span id="toast-icon" class="text-xl">⚡</span>
        <div>
          <h4 id="toast-title" class="text-xs font-bold text-emerald-400">Estado de Carga</h4>
          <p id="toast-msg" class="text-xs text-gray-300">Auricular insertado en el estuche.</p>
        </div>
      </div>

      <!-- Barra de Acciones Rápidas -->
      <div class="bg-gray-900/80 border border-gray-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center space-x-3">
          <button onclick="requestBattery()" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium text-sm rounded-lg shadow transition flex items-center gap-2">
            🔄 Solicitar Batería e Info Ahora
          </button>
          <button onclick="syncTime()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 font-medium text-sm rounded-lg border border-gray-700 transition flex items-center gap-2">
            🕒 Sincronizar Reloj de la Caja
          </button>
        </div>
        <div class="flex items-center space-x-2 text-xs text-gray-400">
          <span>MAC: <b id="txt-mac" class="font-mono text-gray-200">41:42:6C:C2:DD:3C</b></span>
          <span>|</span>
          <span>Resolución: <b class="text-gray-200">320×172 px</b></span>
          <span>|</span>
          <span>Límite Esfera: <b class="text-gray-200">150 KB</b></span>
        </div>
      </div>

      <!-- Fila de Controles Oficiales (ANC, Modos, Ecualizador) -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

        <!-- Control de Ruido (ANC) -->
        <div class="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
          <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400">Control de Ruido</h2>
          <div class="grid grid-cols-3 gap-2">
            <button onclick="setNoiseMode(0)" id="btn-anc-0" class="flex flex-col items-center justify-center p-3 rounded-xl border border-gray-800 bg-gray-800/50 hover:border-blue-500 transition">
              <img src="/web_assets/immerse_blue.png" class="w-8 h-8 mb-2 object-contain" alt="ANC">
              <span class="text-xs font-semibold">Cancelación</span>
            </button>
            <button onclick="setNoiseMode(1)" id="btn-anc-1" class="flex flex-col items-center justify-center p-3 rounded-xl border border-blue-500 bg-blue-950/40 transition">
              <img src="/web_assets/mild_blue.png" class="w-8 h-8 mb-2 object-contain" alt="Normal">
              <span class="text-xs font-semibold">Normal</span>
            </button>
            <button onclick="setNoiseMode(2)" id="btn-anc-2" class="flex flex-col items-center justify-center p-3 rounded-xl border border-gray-800 bg-gray-800/50 hover:border-blue-500 transition">
              <img src="/web_assets/transparent_blue.png" class="w-8 h-8 mb-2 object-contain" alt="Transparente">
              <span class="text-xs font-semibold">Ambiente</span>
            </button>
          </div>

          <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 pt-2">Modo de Sonido</h2>
          <div class="grid grid-cols-3 gap-2">
            <button onclick="setWorkMode(0)" id="btn-work-0" class="flex flex-col items-center p-2.5 rounded-xl border border-blue-500 bg-blue-950/40 transition">
              <img src="/web_assets/music_mode.png" class="w-7 h-7 mb-1 object-contain" alt="Música">
              <span class="text-xs font-medium">Música</span>
            </button>
            <button onclick="setWorkMode(1)" id="btn-work-1" class="flex flex-col items-center p-2.5 rounded-xl border border-gray-800 bg-gray-800/50 hover:border-blue-500 transition">
              <img src="/web_assets/game_mode.png" class="w-7 h-7 mb-1 object-contain" alt="Juego">
              <span class="text-xs font-medium">Baja Latencia</span>
            </button>
            <button onclick="setWorkMode(2)" id="btn-work-2" class="flex flex-col items-center p-2.5 rounded-xl border border-gray-800 bg-gray-800/50 hover:border-blue-500 transition">
              <img src="/web_assets/movie_mode.png" class="w-7 h-7 mb-1 object-contain" alt="Película">
              <span class="text-xs font-medium">Película</span>
            </button>
          </div>
        </div>

        <!-- Ecualizador de 10 Bandas -->
        <div class="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 flex items-center gap-2">
              <img src="/web_assets/main_eq_sign.png" class="w-4 h-4 object-contain" alt="EQ">
              Ecualizador 10 Bandas
            </h2>
            <select id="sel-eq-preset" onchange="applyEqPreset(this.value)" class="text-xs bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-gray-200">
              <option value="0">Estándar</option>
              <option value="1">Pop</option>
              <option value="2">Rock</option>
              <option value="3">Jazz</option>
              <option value="4">Clásica</option>
              <option value="5">Vocal</option>
            </select>
          </div>

          <!-- Sliders de 10 bandas -->
          <div class="flex justify-between items-center px-1 pt-2">
            <!-- Bandas generadas dinámicamente -->
            <div id="eq-sliders-container" class="flex justify-between w-full text-center"></div>
          </div>
          <button onclick="applyCustomEq()" class="w-full py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-xs font-semibold text-gray-200 transition">
            Aplicar Ecualización a la Caja
          </button>
        </div>

        <!-- Buscar Audífonos & Ajustes -->
        <div class="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
          <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 flex items-center gap-2">
            <img src="/web_assets/main_find.png" class="w-4 h-4 object-contain" alt="Find">
            Buscar Mis Audífonos (Beep)
          </h2>
          <div class="grid grid-cols-3 gap-2">
            <button onclick="findEarbuds(true, false)" class="p-3 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-xs font-semibold transition">
              Izquierdo (L)
            </button>
            <button onclick="findEarbuds(false, true)" class="p-3 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-xl text-xs font-semibold transition">
              Derecho (R)
            </button>
            <button onclick="findEarbuds(true, true)" class="p-3 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-semibold transition">
              Ambos 🔊
            </button>
          </div>

          <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 pt-3 flex items-center gap-2">
            <img src="/web_assets/main_gesture_control.png" class="w-4 h-4 object-contain" alt="Gestures">
            Control Táctil Rápido
          </h2>
          <div class="text-xs text-gray-400 space-y-2">
            <div class="flex justify-between items-center bg-gray-800/40 p-2 rounded-lg">
              <span>1 Toque (L/R)</span>
              <span class="text-gray-200 font-semibold">Pausar / Reproducir</span>
            </div>
            <div class="flex justify-between items-center bg-gray-800/40 p-2 rounded-lg">
              <span>2 Toques (L/R)</span>
              <span class="text-gray-200 font-semibold">Siguiente Canción</span>
            </div>
            <div class="flex justify-between items-center bg-gray-800/40 p-2 rounded-lg">
              <span>Pulsación Larga</span>
              <span class="text-gray-200 font-semibold">Cambiar ANC / Siri</span>
            </div>
          </div>
        </div>

      </div>

    </section>


    <!-- ==================== PESTAÑA 2: PERSONALIZADOR DE TEMAS ==================== -->
    <section id="tab-studio" class="space-y-6 hidden">
      
      <!-- Controles de cabecera del estudio -->
      <div class="flex flex-wrap items-center justify-between gap-4 bg-gray-900 border border-gray-800 p-4 rounded-2xl">
        <div class="flex items-center space-x-2">
          <span class="text-sm font-bold">Lienzo 320×172</span>
          <span class="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400 font-mono">Zoom 2.5×</span>
        </div>

        <!-- Indicador de Peso vs Límite 153.6 KB -->
        <div class="flex items-center space-x-3">
          <div class="text-right">
            <div class="text-xs text-gray-400">Peso Estimado: <b id="txt-theme-size" class="text-white font-mono">0 KB</b> / 150 KB</div>
            <div class="w-36 bg-gray-800 h-2 rounded-full overflow-hidden mt-1">
              <div id="bar-theme-size" class="bg-blue-500 h-2 rounded-full transition-all" style="width: 0%"></div>
            </div>
          </div>
          <button onclick="resetDefaultTheme()" class="px-3.5 py-2 bg-gray-800 hover:bg-gray-700 text-sm font-semibold rounded-xl border border-gray-700 text-gray-300 hover:text-white transition flex items-center gap-1.5" title="Restablecer tema al predeterminado oficial (Cyberpunk 385)">
            🔄 Predeterminado
          </button>
          <button onclick="compileTheme()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm font-semibold rounded-xl border border-gray-700 transition">
            ⚙️ Compilar
          </button>
          <button onclick="uploadCurrentTheme()" class="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold rounded-xl shadow-lg transition flex items-center gap-2">
            🚀 Subir a la Caja
          </button>
        </div>
      </div>

      <!-- Área de Trabajo Principal: Lienzo + Inspector de Capas -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        <!-- Lienzo Central (320x172 escalado a 800x430) -->
        <div class="lg:col-span-8 flex flex-col items-center justify-center p-6 bg-gray-900 border border-gray-800 rounded-2xl space-y-4">
          <div class="relative screen-bezel p-1">
            <canvas id="theme-canvas" width="320" height="172" class="w-[800px] h-[430px] rounded cursor-crosshair bg-black block"></canvas>
            
            <!-- Barra de estado simulada superior -->
            <div id="canvas-overlay-status" class="absolute top-2 left-3 right-3 flex justify-between items-center text-[11px] text-white/90 font-semibold px-2 pointer-events-none drop-shadow">
              <span>L 100%  R 100%</span>
              <span>🔋 100%</span>
            </div>
          </div>

          <!-- Barra de Configuración Tipográfica Global para Números/Fechas -->
          <div class="w-full bg-gray-950/60 border border-gray-800 rounded-xl p-3 flex flex-wrap items-center justify-between gap-3 text-xs">
            <div class="flex items-center gap-2">
              <span class="text-gray-400 font-semibold">🔤 Fuente:</span>
              <select id="sel-font-family" onchange="onFontSettingsChange()" class="bg-gray-800 border border-gray-700 text-white rounded px-2 py-1 text-xs">
                <option value="orbitron">Orbitron Sci-Fi (Futurista)</option>
                <option value="tech">Share Tech Mono (Digital LED)</option>
                <option value="bebas">Bebas Neue (Condensada Alta)</option>
                <option value="pixel">Press Start 2P (Retro 8-Bit)</option>
                <option value="opensans" selected>Open Sans ExtraBold</option>
                <option value="fira">Fira Sans Bold</option>
                <option value="hack">Hack Monospace</option>
                <option value="dejavu">DejaVu Sans Bold</option>
              </select>
              <label class="px-2 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded text-[11px] cursor-pointer flex items-center gap-1">
                📁 Subir .TTF
                <input type="file" accept=".ttf,.otf" class="hidden" onchange="handleCustomFontUpload(event)">
              </label>
            </div>

            <div class="flex items-center gap-3">
              <div class="flex items-center gap-1.5">
                <span class="text-gray-400">Color:</span>
                <input type="color" id="inp-font-color" value="#FFFFFF" onchange="onFontSettingsChange()" class="w-6 h-6 rounded bg-transparent cursor-pointer border border-gray-700">
              </div>
              <div class="flex items-center gap-1.5">
                <span class="text-gray-400">Borde:</span>
                <input type="color" id="inp-font-stroke" value="#000000" onchange="onFontSettingsChange()" class="w-6 h-6 rounded bg-transparent cursor-pointer border border-gray-700">
              </div>
              <button onclick="applyFontToAllWidgets()" class="px-2.5 py-1 bg-blue-600/80 hover:bg-blue-600 text-white rounded font-semibold text-[11px] transition">
                ⚡ Aplicar a Todo
              </button>
            </div>
          </div>

          <!-- Barra de Herramientas para Añadir Elementos -->
          <div class="flex flex-wrap items-center justify-center gap-2 pt-1">
            <label class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs font-semibold cursor-pointer border border-gray-700 transition flex items-center gap-1.5">
              🖼️ Fondo (320×172)
              <input type="file" accept="image/*" class="hidden" onchange="handleImageUpload(event, 'background')">
            </label>
            <label class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs font-semibold cursor-pointer border border-gray-700 transition flex items-center gap-1.5">
              🦸 Personaje PNG (Profundidad)
              <input type="file" accept="image/*" class="hidden" onchange="handleImageUpload(event, 'overlay')">
            </label>
            <button onclick="addClockWidget()" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs font-semibold border border-gray-700 transition">
              🕒 Reloj Digital (09:28)
            </button>
            <button onclick="addDateWidget()" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs font-semibold border border-gray-700 transition">
              📅 Fecha & Día (DOM 10/24)
            </button>
            <label class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs font-semibold cursor-pointer border border-gray-700 transition flex items-center gap-1.5">
              ✨ Subir Tu GIF
              <input type="file" accept="image/gif" class="hidden" onchange="handleGifUpload(event)">
            </label>
            <button onclick="loadSampleReactorGif()" class="px-3 py-1.5 bg-cyan-900/60 hover:bg-cyan-800/80 text-cyan-300 rounded-lg text-xs font-semibold border border-cyan-700/60 transition flex items-center gap-1.5">
              ⚡ Probar Reactor GIF
            </button>
          </div>
        </div>

        <!-- Panel Lateral: Capas & Inspector de Propiedades -->
        <div class="lg:col-span-4 space-y-4">
          
          <!-- Lista de Capas (Orden Z) con Botones de Acción en la Parte Superior -->
          <div class="bg-gray-900 border border-gray-800 rounded-2xl p-4 space-y-3">
            <div class="flex justify-between items-center">
              <h3 class="text-xs font-bold uppercase tracking-wider text-gray-400">Capas del Tema (Orden Z)</h3>
              <span id="txt-layer-count" class="text-xs text-gray-500">0 capas</span>
            </div>

            <!-- Botones de Control de Capa: Subir, Bajar y Eliminar arriba de la lista -->
            <div class="flex items-center gap-1.5 bg-gray-950/50 p-1.5 rounded-xl border border-gray-800/80">
              <button onclick="moveLayerUp()" class="flex-1 py-1.5 px-2 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1" title="Subir nivel de capa (traer adelante)">
                ⬆️ Subir
              </button>
              <button onclick="moveLayerDown()" class="flex-1 py-1.5 px-2 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1" title="Bajar nivel de capa (enviar atrás)">
                ⬇️ Bajar
              </button>
              <button onclick="deleteLayer()" class="py-1.5 px-3 bg-red-900/40 hover:bg-red-800/60 text-red-400 border border-red-800/50 rounded-lg text-xs font-semibold transition flex items-center justify-center gap-1" title="Eliminar capa seleccionada">
                🗑️ Eliminar
              </button>
            </div>

            <div id="layers-list" class="space-y-2 max-h-[220px] overflow-y-auto pr-1">
              <!-- Elementos de capa generados dinámicamente -->
            </div>
          </div>

          <!-- Inspector del Elemento Seleccionado -->
          <div id="inspector-panel" class="bg-gray-900 border border-gray-800 rounded-2xl p-4 space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-gray-400">Propiedades de la Capa</h3>
            
            <div class="grid grid-cols-2 gap-3 text-xs">
              <div>
                <label class="text-gray-400">Posición X (Centro)</label>
                <input type="number" id="inp-x" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 mt-1 text-white font-mono" onchange="updateSelectedCoord('x', this.value)">
              </div>
              <div>
                <label class="text-gray-400">Posición Y (Centro)</label>
                <input type="number" id="inp-y" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 mt-1 text-white font-mono" onchange="updateSelectedCoord('y', this.value)">
              </div>
            </div>

            <!-- Ajuste Manual de Tamaño (Ancho × Alto) y Escala -->
            <div class="space-y-1.5 pt-1">
              <div class="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <label class="text-gray-400">Ancho (px)</label>
                  <input type="number" id="inp-w" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 mt-1 text-white font-mono" onchange="updateSelectedSize('w', this.value)">
                </div>
                <div>
                  <label class="text-gray-400">Alto (px)</label>
                  <input type="number" id="inp-h" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 mt-1 text-white font-mono" onchange="updateSelectedSize('h', this.value)">
                </div>
              </div>
              
              <!-- Botones de Escala Rápida -->
              <div class="flex items-center justify-between gap-1 pt-1">
                <span class="text-[10px] text-gray-500 uppercase">Escala:</span>
                <div class="flex items-center gap-1">
                  <button onclick="scaleSelectedLayer(0.8)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded text-[11px] font-semibold text-gray-300 transition" title="Reducir tamaño un 20%">
                    🔍 -20%
                  </button>
                  <button onclick="scaleSelectedLayer(0.9)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded text-[11px] font-semibold text-gray-300 transition" title="Reducir tamaño un 10%">
                    -10%
                  </button>
                  <button onclick="scaleSelectedLayer(1.1)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded text-[11px] font-semibold text-gray-300 transition" title="Aumentar tamaño un 10%">
                    +10%
                  </button>
                  <button onclick="scaleSelectedLayer(1.2)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded text-[11px] font-semibold text-gray-300 transition" title="Aumentar tamaño un 20%">
                    🔍 +20%
                  </button>
                </div>
              </div>
            </div>

            <!-- Botones de alineación rápida -->
            <div class="space-y-1 pt-1">
              <span class="text-[10px] text-gray-500 uppercase">Alineación Rápida</span>
              <div class="grid grid-cols-3 gap-1 text-[11px]">
                <button onclick="alignSelected('top-left')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Arr-Izq</button>
                <button onclick="alignSelected('top-center')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Arr-Cen</button>
                <button onclick="alignSelected('top-right')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Arr-Der</button>
                <button onclick="alignSelected('mid-left')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Med-Izq</button>
                <button onclick="alignSelected('center')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Centro</button>
                <button onclick="alignSelected('mid-right')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Med-Der</button>
                <button onclick="alignSelected('bottom-left')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Abj-Izq</button>
                <button onclick="alignSelected('bottom-center')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Abj-Cen</button>
                <button onclick="alignSelected('bottom-right')" class="p-1 bg-gray-800 rounded hover:bg-gray-700">Abj-Der</button>
              </div>
            </div>

            <!-- Herramientas de Compresión y Calidad de Imagen -->
            <div id="inspector-compress-box" class="space-y-2 pt-2 border-t border-gray-800">
              <div class="flex justify-between items-center text-[11px]">
                <span class="text-gray-400 font-semibold">Dimensiones</span>
                <span id="txt-layer-dims" class="text-gray-300 font-mono">0×0 px</span>
              </div>
              
              <!-- Ajuste de Escala a Pantalla Completa (Toggle) -->
              <button id="btn-toggle-fullscreen" onclick="toggleLayerFullScreen()" class="w-full p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition" title="Activa o desactiva el ajuste automático a pantalla completa (320×172)">
                <span>📐 Pantalla Completa (320×172):</span>
                <span id="txt-fullscreen-status" class="font-bold text-gray-400">OFF</span>
              </button>

              <!-- Reductor de Resolución (Reduce peso en KB manteniendo la escala en pantalla) -->
              <div class="space-y-1">
                <div class="flex justify-between items-center text-[11px]">
                  <span class="text-gray-400 font-semibold" title="Reduce la resolución real de la imagen/GIF para bajar drásticamente el peso del archivo .bin conservando su tamaño en pantalla">📉 Reducir Resolución (Ahorro KB):</span>
                  <span id="txt-res-pct-info" class="text-cyan-400 font-mono text-[10px]">100%</span>
                </div>
                <div class="grid grid-cols-5 gap-1 text-[11px]">
                  <button id="btn-res-100" onclick="compressInternalResolution(100)" class="p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded font-semibold text-gray-200 transition" title="100%: Resolución nativa completa">
                    100%
                  </button>
                  <button id="btn-res-75" onclick="compressInternalResolution(75)" class="p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded font-semibold text-gray-200 transition" title="75%: Alta resolución (~35% ahorro KB)">
                    75%
                  </button>
                  <button id="btn-res-50" onclick="compressInternalResolution(50)" class="p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded font-semibold text-gray-200 transition" title="50%: Resolución balanceada (~60% ahorro KB)">
                    50%
                  </button>
                  <button id="btn-res-35" onclick="compressInternalResolution(35)" class="p-1.5 bg-amber-950/60 hover:bg-amber-900 border border-amber-700/60 rounded font-semibold text-amber-200 transition" title="35%: Compresión fuerte (~78% ahorro KB)">
                    35%
                  </button>
                  <button id="btn-res-25" onclick="compressInternalResolution(25)" class="p-1.5 bg-red-950/60 hover:bg-red-900 border border-red-700/60 rounded font-semibold text-red-200 transition" title="25%: Compresión extrema (~88% ahorro KB)">
                    25%
                  </button>
                </div>
              </div>

              <!-- Compresor de Calidad (Entropía / Profundidad de Color) -->
              <div class="space-y-1">
                <div class="flex justify-between items-center text-[11px]">
                  <span class="text-gray-400 font-semibold">🎨 Comprimir Colores (KB):</span>
                </div>
                <div class="grid grid-cols-5 gap-1 text-[11px]">
                  <button onclick="compressLayerQuality(128)" class="p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded font-semibold text-gray-200 transition" title="Calidad Alta: 128 colores (reduce ~30% KB)">
                    128c
                  </button>
                  <button onclick="compressLayerQuality(64)" class="p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded font-semibold text-gray-300 transition" title="Calidad Media: 64 colores (reduce ~50% KB)">
                    64c
                  </button>
                  <button onclick="compressLayerQuality(32)" class="p-1.5 bg-amber-950/60 hover:bg-amber-900 border border-amber-700/60 rounded font-semibold text-amber-200 transition" title="Compresión Fuerte: 32 colores (reduce ~70% KB)">
                    32c
                  </button>
                  <button onclick="compressLayerQuality(16)" class="p-1.5 bg-rose-950/60 hover:bg-rose-900 border border-rose-700/60 rounded font-semibold text-rose-200 transition" title="Compresión Ultra: 16 colores (reduce ~85% KB)">
                    16c
                  </button>
                  <button onclick="compressLayerQuality(8)" class="p-1.5 bg-red-950/80 hover:bg-red-900 border border-red-700/70 rounded font-semibold text-red-200 transition" title="Compresión Extrema / Retro: 8 colores (reduce hasta 90% KB)">
                    8c
                  </button>
                </div>
              </div>

              <!-- Opciones exclusivas para GIF (Muestreo Configurable del Video Completo) -->
              <div id="gif-compress-options" class="hidden pt-2 space-y-2 border-t border-gray-800/60">
                <div class="flex items-center justify-between text-[11px]">
                  <span class="text-cyan-400 font-semibold">🎞️ Frames GIF (Video Completo):</span>
                  <span id="txt-gif-orig-info" class="text-gray-400 text-[10px] font-mono">12f orig</span>
                </div>

                <!-- 1. Dividir frames del video (Configurable) -->
                <div class="bg-gray-950/60 p-2 rounded-lg border border-gray-800/80 space-y-1.5">
                  <div class="flex items-center justify-between text-[11px] text-gray-300">
                    <span title="Toma el total de frames del video original y lo divide uniformemente">Dividir video entre:</span>
                    <div class="flex items-center gap-1">
                      <button id="btn-subsample-2" onclick="subsampleGifFrames(2)" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs font-semibold border border-transparent transition" title="Dividir entre 2 (1 de cada 2 frames)">÷2</button>
                      <button id="btn-subsample-3" onclick="subsampleGifFrames(3)" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs font-semibold border border-transparent transition" title="Dividir entre 3 (ej. 12f ÷ 3 = 4 frames completos)">÷3</button>
                      <button id="btn-subsample-4" onclick="subsampleGifFrames(4)" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs font-semibold border border-transparent transition" title="Dividir entre 4">÷4</button>
                    </div>
                  </div>
                  <div class="flex items-center gap-2 text-xs">
                    <span class="text-gray-400 text-[10px]">Divisor personalizado:</span>
                    <input type="number" id="inp-gif-divisor" min="1" max="20" placeholder="Ej: 3" class="w-14 bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-center text-white font-mono text-xs">
                    <button onclick="applyCustomGifDivisor()" class="px-2 py-0.5 bg-cyan-700 hover:bg-cyan-600 text-white rounded text-[11px] font-semibold transition">
                      Aplicar
                    </button>
                  </div>
                </div>

                <!-- 2. Fijar número exacto de frames muestreados -->
                <div class="flex items-center justify-between text-[11px] text-gray-300 pt-0.5">
                  <span>Frames exactos:</span>
                  <div class="flex items-center gap-1.5">
                    <button id="btn-exact-4" onclick="reduceGifFrames(4)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs border border-transparent transition">4f</button>
                    <button id="btn-exact-6" onclick="reduceGifFrames(6)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs border border-transparent transition">6f</button>
                    <button id="btn-exact-8" onclick="reduceGifFrames(8)" class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs border border-transparent transition">8f</button>
                    <input type="number" id="inp-gif-exact-frames" min="2" max="24" placeholder="Cant." class="w-12 bg-gray-800 border border-gray-700 rounded px-1 py-0.5 text-center text-white font-mono text-[10px]" onchange="applyCustomGifExactFrames()">
                  </div>
                </div>

                <!-- 3. Velocidad sincronizada automáticamente (Hardware extra[2]) -->
                <div class="bg-gray-950/60 p-2 rounded-lg border border-gray-800/80 flex items-center justify-between text-[11px]">
                  <span class="text-gray-300" title="Ajuste 100% automático de velocidad para conservar la duración exacta del video según la cantidad de fotogramas">⏱️ Velocidad Sincronizada:</span>
                  <span id="txt-hw-speed-info" class="text-cyan-400 font-mono text-[11px] font-bold">Auto</span>
                </div>
              </div>
            </div>
          </div>

        </div>

      </div>

    </section>


    <!-- ==================== PESTAÑA 3: GALERÍA DE 80 TEMAS ==================== -->
    <section id="tab-gallery" class="space-y-6 hidden">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-base font-bold">Catálogo Completo de 80 Esferas Oficiales (320×172)</h2>
          <p class="text-xs text-gray-400">Descargadas directamente del servidor de A9 Pro. Puedes abrirlas en el Personalizador o subirlas a la caja con 1 clic.</p>
        </div>
      </div>

      <!-- Cuadrícula de Temas -->
      <div id="gallery-grid" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
        <!-- Render dinámico de temas -->
      </div>
    </section>

  </main>

  <!-- Modal de Progreso de Subida BLE -->
  <div id="modal-upload" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center hidden">
    <div class="bg-gray-900 border border-gray-800 rounded-2xl p-6 max-w-md w-full mx-4 space-y-4 text-center">
      <h3 class="text-lg font-bold">Subiendo Esfera a la A9 Pro Box</h3>
      <p class="text-xs text-gray-400">Transmitiendo por Bluetooth LE L2CAP (paquetes de 128 bytes)...</p>
      
      <div class="w-full bg-gray-800 rounded-full h-4 overflow-hidden">
        <div id="upload-bar" class="bg-blue-600 h-4 rounded-full transition-all duration-300" style="width: 0%"></div>
      </div>

      <div class="flex justify-between items-center text-xs text-gray-400">
        <span id="upload-pct">0%</span>
        <span id="upload-packets">0 / 0 paquetes</span>
        <span id="upload-speed">0 KB/s</span>
      </div>

      <div id="upload-msg" class="text-xs font-semibold text-blue-400">Preparando transmisión...</div>
      
      <button id="btn-close-modal" onclick="closeUploadModal()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs font-semibold hidden">
        Cerrar
      </button>
    </div>
  </div>

  <!-- Script de la Aplicación -->
  <script>
    // Variables de Estado
    let currentTab = 'device';
    let deviceState = {};
    let project = { width: 320, height: 172, layers: [] };
    let selectedLayerId = null;
    let isDragging = false;
    let dragStartX = 0, dragStartY = 0;
    let initialLayerX = 0, initialLayerY = 0;

    // Inicialización de Bandas EQ
    const freqLabels = ['31', '62', '125', '250', '500', '1k', '2k', '4k', '8k', '16k'];
    const eqContainer = document.getElementById('eq-sliders-container');
    freqLabels.forEach((label, idx) => {
      const col = document.createElement('div');
      col.className = 'flex flex-col items-center space-y-1';
      col.innerHTML = `
        <span class="text-[10px] text-gray-400">${label}</span>
        <input type="range" min="-12" max="12" value="0" class="slider-vertical" id="eq-slider-${idx}">
        <span class="text-[10px] font-mono text-gray-500" id="eq-val-${idx}">0</span>
      `;
      eqContainer.appendChild(col);
      const sl = col.querySelector('input');
      sl.addEventListener('input', (e) => {
        col.querySelector(`#eq-val-${idx}`).innerText = e.target.value;
      });
    });

    // Pestañas
    function setTab(tab) {
      currentTab = tab;
      ['device', 'studio', 'gallery'].forEach(t => {
        document.getElementById(`tab-${t}`).classList.toggle('hidden', t !== tab);
        const btn = document.getElementById(`btn-tab-${t}`);
        if (t === tab) {
          btn.className = 'px-4 py-1.5 rounded-lg text-sm font-medium transition bg-blue-600 text-white shadow';
        } else {
          btn.className = 'px-4 py-1.5 rounded-lg text-sm font-medium transition text-gray-300 hover:text-white';
        }
      });
      if (tab === 'gallery') loadOfficialGallery();
      if (tab === 'studio') drawCanvas();
    }

    // Estado previo para detectar transiciones de carga
    let prevBatteryState = {
      left_charging: null,
      right_charging: null,
      box_charging: null,
      battery_left: null,
      battery_right: null,
      battery_box: null
    };

    let toastTimer = null;
    function showToast(title, msg, icon = '⚡', isGreen = true) {
      const toast = document.getElementById('toast-notify');
      if (!toast) return;
      document.getElementById('toast-title').innerText = title;
      document.getElementById('toast-title').className = isGreen ? 'text-xs font-bold text-emerald-400' : 'text-xs font-bold text-amber-400';
      document.getElementById('toast-msg').innerText = msg;
      document.getElementById('toast-icon').innerText = icon;
      toast.classList.remove('translate-y-24', 'opacity-0', 'pointer-events-none');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        toast.classList.add('translate-y-24', 'opacity-0', 'pointer-events-none');
      }, 4000);
    }

    // Actualización de Estado BLE
    async function updateStatus() {
      try {
        const res = await fetch('/api/status');
        const st = await res.json();
        deviceState = st;

        const badge = document.getElementById('badge-conn');
        const btnConn = document.getElementById('btn-conn-text');
        if (st.connected) {
          badge.className = 'text-xs px-2 py-0.5 rounded-full bg-emerald-900/60 text-emerald-400 border border-emerald-700/50';
          badge.innerText = 'Conectado';
          btnConn.innerText = 'Desconectar';
        } else {
          badge.className = 'text-xs px-2 py-0.5 rounded-full bg-red-900/60 text-red-400 border border-red-700/50';
          badge.innerText = 'Desconectado';
          btnConn.innerText = 'Conectar Caja';
        }

        // Detección de cambios de carga en tiempo real (notificaciones toast)
        if (prevBatteryState.left_charging !== null) {
          if (!prevBatteryState.left_charging && st.left_charging) {
            showToast("Auricular Izquierdo (L)", "Insertado en estuche: cargando ahora ⚡", "🎧", true);
          } else if (prevBatteryState.left_charging && !st.left_charging) {
            showToast("Auricular Izquierdo (L)", "Extraído del estuche: en uso", "🎧", false);
          }
          if (!prevBatteryState.right_charging && st.right_charging) {
            showToast("Auricular Derecho (R)", "Insertado en estuche: cargando ahora ⚡", "🎧", true);
          } else if (prevBatteryState.right_charging && !st.right_charging) {
            showToast("Auricular Derecho (R)", "Extraído del estuche: en uso", "🎧", false);
          }
          if (!prevBatteryState.box_charging && st.box_charging) {
            showToast("Estuche Inteligente", "Conectado al cargador USB ⚡", "🔌", true);
          } else if (prevBatteryState.box_charging && !st.box_charging) {
            showToast("Estuche Inteligente", "Desconectado de la corriente", "🔋", false);
          }
        }
        prevBatteryState = { ...st };

        // Tarjeta Auricular Izquierdo (L)
        document.getElementById('txt-bat-l').innerText = `${st.battery_left}%`;
        document.getElementById('bar-bat-l').style.width = `${st.battery_left}%`;
        const iconChargeL = document.getElementById('icon-charge-l');
        const badgeModeL = document.getElementById('badge-mode-l');
        const cardL = document.getElementById('card-earbud-l');
        const glowL = document.getElementById('glow-charge-l');
        if (st.left_charging) {
          iconChargeL.classList.remove('hidden');
          badgeModeL.innerText = '⚡ Cargando en caja';
          badgeModeL.className = 'text-[10px] px-2 py-0.5 rounded-full font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-500/80 animate-pulse';
          cardL.className = 'bg-gray-900 border-2 border-emerald-500/80 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300 shadow-[0_0_25px_rgba(16,185,129,0.2)]';
          if (glowL) glowL.classList.remove('opacity-0');
        } else {
          iconChargeL.classList.add('hidden');
          badgeModeL.innerText = 'En uso / Activo';
          badgeModeL.className = 'text-[10px] px-2 py-0.5 rounded-full font-semibold bg-gray-800 text-gray-400 border border-gray-700';
          cardL.className = 'bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
          if (glowL) glowL.classList.add('opacity-0');
        }

        // Tarjeta Auricular Derecho (R)
        document.getElementById('txt-bat-r').innerText = `${st.battery_right}%`;
        document.getElementById('bar-bat-r').style.width = `${st.battery_right}%`;
        const iconChargeR = document.getElementById('icon-charge-r');
        const badgeModeR = document.getElementById('badge-mode-r');
        const cardR = document.getElementById('card-earbud-r');
        const glowR = document.getElementById('glow-charge-r');
        if (st.right_charging) {
          iconChargeR.classList.remove('hidden');
          badgeModeR.innerText = '⚡ Cargando en caja';
          badgeModeR.className = 'text-[10px] px-2 py-0.5 rounded-full font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-500/80 animate-pulse';
          cardR.className = 'bg-gray-900 border-2 border-emerald-500/80 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300 shadow-[0_0_25px_rgba(16,185,129,0.2)]';
          if (glowR) glowR.classList.remove('opacity-0');
        } else {
          iconChargeR.classList.add('hidden');
          badgeModeR.innerText = 'En uso / Activo';
          badgeModeR.className = 'text-[10px] px-2 py-0.5 rounded-full font-semibold bg-gray-800 text-gray-400 border border-gray-700';
          cardR.className = 'bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
          if (glowR) glowR.classList.add('opacity-0');
        }

        // Tarjeta Caja Inteligente
        document.getElementById('txt-bat-box').innerText = `${st.battery_box}%`;
        document.getElementById('bar-bat-box').style.width = `${st.battery_box}%`;
        const iconChargeBox = document.getElementById('icon-charge-box');
        const cardBox = document.getElementById('card-box');
        if (st.box_charging) {
          iconChargeBox.classList.remove('hidden');
          cardBox.className = 'bg-gray-900 border-2 border-amber-500/80 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden shadow-[0_0_25px_rgba(245,158,11,0.2)] transition-all duration-300';
        } else {
          iconChargeBox.classList.add('hidden');
          cardBox.className = 'bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col items-center justify-between text-center relative overflow-hidden shadow-2xl transition-all duration-300';
        }

        // Pantalla dentro del mockup de la caja
        const lText = st.left_charging ? `⚡${st.battery_left}%` : `${st.battery_left}%`;
        const rText = st.right_charging ? `⚡${st.battery_right}%` : `${st.battery_right}%`;
        document.getElementById('screen-l').innerText = lText;
        document.getElementById('screen-r').innerText = rText;
        document.getElementById('screen-box').innerText = st.box_charging ? `⚡ ${st.battery_box}%` : `🔋 ${st.battery_box}%`;

        // Modos
        [0, 1, 2].forEach(m => {
          const btn = document.getElementById(`btn-anc-${m}`);
          if (btn) btn.className = (st.noise_mode === m)
            ? 'flex flex-col items-center justify-center p-3 rounded-xl border border-blue-500 bg-blue-950/40 transition'
            : 'flex flex-col items-center justify-center p-3 rounded-xl border border-gray-800 bg-gray-800/50 hover:border-blue-500 transition';
        });

        [0, 1, 2].forEach(m => {
          const btn = document.getElementById(`btn-work-${m}`);
          if (btn) btn.className = (st.work_mode === m)
            ? 'flex flex-col items-center p-2.5 rounded-xl border border-blue-500 bg-blue-950/40 transition'
            : 'flex flex-col items-center p-2.5 rounded-xl border border-gray-800 bg-gray-800/50 hover:border-blue-500 transition';
        });

      } catch (e) {
        console.error("Error consultando estado", e);
      }
    }

    async function toggleConnect() {
      if (deviceState.connected) {
        await fetch('/api/disconnect');
      } else {
        await fetch('/api/connect');
      }
      updateStatus();
    }

    async function requestBattery() {
      await fetch('/api/query');
      updateStatus();
    }

    async function syncTime() {
      await fetch('/api/control/sync_time', { method: 'POST' });
      alert("¡Hora y zona horaria sincronizadas con la A9 Pro Box!");
    }

    async function setNoiseMode(m) {
      await fetch('/api/control/noise', { method: 'POST', body: JSON.stringify({ mode: m }) });
      updateStatus();
    }

    async function setWorkMode(m) {
      await fetch('/api/control/work', { method: 'POST', body: JSON.stringify({ mode: m }) });
      updateStatus();
    }

    async function findEarbuds(l, r) {
      await fetch('/api/control/find', { method: 'POST', body: JSON.stringify({ left: l, right: r }) });
    }

    function applyEqPreset(p) {
      const presets = [
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],       // Estándar
        [2, 3, 4, 2, -1, -1, 1, 3, 4, 4],     // Pop
        [5, 4, 3, 1, -1, 0, 2, 4, 5, 5],      // Rock
        [3, 2, 1, 2, -2, -2, 0, 2, 3, 4],     // Jazz
        [4, 3, 2, 1, -2, -2, 0, 2, 4, 5],     // Clásica
        [-1, -2, -1, 2, 5, 5, 3, 1, 0, -1]    // Vocal
      ];
      const bands = presets[p] || presets[0];
      bands.forEach((b, idx) => {
        const sl = document.getElementById(`eq-slider-${idx}`);
        if (sl) sl.value = b;
        const valSpan = document.getElementById(`eq-val-${idx}`);
        if (valSpan) valSpan.innerText = b;
      });
      applyCustomEq();
    }

    async function applyCustomEq() {
      const bands = [];
      for (let i = 0; i < 10; i++) {
        bands.push(parseInt(document.getElementById(`eq-slider-${i}`).value || '0'));
      }
      const preset = parseInt(document.getElementById('sel-eq-preset').value || '0');
      await fetch('/api/control/eq', { method: 'POST', body: JSON.stringify({ preset, bands }) });
    }

    // ==================== PERSONALIZADOR DE TEMAS ====================
    const canvas = document.getElementById('theme-canvas');
    const ctx = canvas.getContext('2d');
    let animFrameIndex = 0;

    function drawCanvas() {
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, 320, 172);
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, 320, 172);

      project.layers.forEach(layer => {
        if (!layer.visible) return;
        const img = layer._imgObj;
        if (!img || !img.complete) return;

        const cnt = layer.count || 1;
        const fw = Math.floor(imWidth(layer) / cnt);
        const fh = imHeight(layer);
        const dw = layer.fit_screen ? 320 : fw;
        const dh = layer.fit_screen ? 172 : fh;
        const x = layer.fit_screen ? 160 : layer.x;
        const y = layer.fit_screen ? 86 : layer.y;

        // Horas y Minutos (type 5 y 6, mode 4) -> 2 dígitos
        if ((layer.type === 5 || layer.type === 6 || layer.type === 2 || layer.type === 3) && layer.mode === 4) {
          const d1 = (layer.type === 5) ? 0 : 2;
          const d2 = (layer.type === 5) ? 9 : 8;
          ctx.drawImage(img, d1 * fw, 0, fw, fh, Math.floor(x - dw), Math.floor(y - dh / 2), dw, dh);
          ctx.drawImage(img, d2 * fw, 0, fw, fh, Math.floor(x), Math.floor(y - dh / 2), dw, dh);
        } else if (layer.type === 0x13 && cnt > 1) {
          // Animación GIF en bucle
          const fDur = layer.frame_duration_ms || 120;
          const nowMs = Date.now();
          const frameIdx = Math.floor(nowMs / fDur) % cnt;
          ctx.drawImage(img, frameIdx * fw, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
        } else {
          ctx.drawImage(img, 0, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
        }

        // Borde de selección
        if (layer.id === selectedLayerId) {
          ctx.strokeStyle = "#3b82f6";
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.strokeRect(Math.floor(x - dw / 2) - 1, Math.floor(y - dh / 2) - 1, dw + 2, dh + 2);
          ctx.setLineDash([]);
        }
      });

      // Reflejar también el tema en la pantalla de la caja del menú principal ("Mi Dispositivo")
      const boxCanvas = document.getElementById('box-screen-canvas');
      if (boxCanvas) {
        const bCtx = boxCanvas.getContext('2d');
        bCtx.imageSmoothingEnabled = false;
        bCtx.clearRect(0, 0, 320, 172);
        bCtx.fillStyle = "#000000";
        bCtx.fillRect(0, 0, 320, 172);

        project.layers.forEach(layer => {
          if (!layer.visible) return;
          const img = layer._imgObj;
          if (!img || !img.complete) return;

          const cnt = layer.count || 1;
          const fw = Math.floor(imWidth(layer) / cnt);
          const fh = imHeight(layer);
          const dw = layer.fit_screen ? 320 : fw;
          const dh = layer.fit_screen ? 172 : fh;
          const x = layer.fit_screen ? 160 : layer.x;
          const y = layer.fit_screen ? 86 : layer.y;

          if ((layer.type === 5 || layer.type === 6 || layer.type === 2 || layer.type === 3) && layer.mode === 4) {
            const d1 = (layer.type === 5) ? 0 : 2;
            const d2 = (layer.type === 5) ? 9 : 8;
            bCtx.drawImage(img, d1 * fw, 0, fw, fh, Math.floor(x - dw), Math.floor(y - dh / 2), dw, dh);
            bCtx.drawImage(img, d2 * fw, 0, fw, fh, Math.floor(x), Math.floor(y - dh / 2), dw, dh);
          } else if (layer.type === 0x13 && cnt > 1) {
            const fDur = layer.frame_duration_ms || 120;
            const nowMs = Date.now();
            const frameIdx = Math.floor(nowMs / fDur) % cnt;
            bCtx.drawImage(img, frameIdx * fw, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
          } else {
            bCtx.drawImage(img, 0, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
          }
        });
      }
    }

    // Bucle de renderizado para animaciones en vivo sincronizado a 30 FPS
    setInterval(() => {
      const hasAnim = project.layers.some(l => l.type === 0x13 && l.count > 1);
      if (hasAnim) {
        drawCanvas();
      }
    }, 33);

    function imWidth(l) { return l._imgObj ? l._imgObj.width : (l.width || 320); }
    function imHeight(l) { return l._imgObj ? l._imgObj.height : (l.height || 172); }

    function preloadLayerImages() {
      let pending = project.layers.length;
      if (pending === 0) { drawCanvas(); return; }
      project.layers.forEach(l => {
        if (!l._imgObj) {
          const im = new Image();
          im.onload = () => { drawCanvas(); };
          im.src = l.image_data;
          l._imgObj = im;
        }
        if (l.raw_gif_b64 && !l._rawGifB64) l._rawGifB64 = l.raw_gif_b64;
        if (l.orig_frames_count && !l._origFramesCount) l._origFramesCount = l.orig_frames_count;
        if (l.orig_video_w && !l._origVideoW) l._origVideoW = l.orig_video_w;
        if (l.orig_video_h && !l._origVideoH) l._origVideoH = l.orig_video_h;
      });
    }

    function updateLayersListUI() {
      const list = document.getElementById('layers-list');
      list.innerHTML = '';
      document.getElementById('txt-layer-count').innerText = `${project.layers.length} capas`;

      project.layers.slice().reverse().forEach(l => {
        const item = document.createElement('div');
        const isSel = l.id === selectedLayerId;
        item.className = `p-2 rounded-lg border text-xs flex items-center justify-between cursor-pointer transition ${isSel ? 'border-blue-500 bg-blue-950/40 text-white' : 'border-gray-800 bg-gray-800/40 text-gray-300 hover:border-gray-700'}`;
        item.onclick = () => selectLayer(l.id);
        item.innerHTML = `
          <div class="flex items-center space-x-2 truncate">
            <span class="w-2 h-2 rounded-full ${l.visible ? 'bg-emerald-400' : 'bg-gray-600'}"></span>
            <span class="font-semibold truncate">${l.name}</span>
          </div>
          <span class="text-[10px] text-gray-500 font-mono">(${l.x}, ${l.y})</span>
        `;
        list.appendChild(item);
      });
    }

    function selectLayer(id) {
      selectedLayerId = id;
      const l = project.layers.find(x => x.id === id);
      if (l) {
        document.getElementById('inp-x').value = l.x;
        document.getElementById('inp-y').value = l.y;
        const cnt = l.count || 1;
        const fw = Math.floor(imWidth(l) / cnt);
        const fh = imHeight(l);
        const dw = l.target_w || fw;
        const dh = l.target_h || fh;
        const inpW = document.getElementById('inp-w');
        const inpH = document.getElementById('inp-h');
        if (inpW) inpW.value = fw;
        if (inpH) inpH.value = fh;

        const dimsEl = document.getElementById('txt-layer-dims');
        if (dimsEl) {
          if (l.fit_screen) {
            dimsEl.innerText = (cnt > 1) ? `320×172 (${fw}×${fh} res, ${cnt}f)` : `320×172 (${fw}×${fh} res)`;
          } else {
            dimsEl.innerText = (cnt > 1) ? `${fw}×${fh} (${cnt}f)` : `${fw}×${fh} px`;
          }
        }
        const gifOpt = document.getElementById('gif-compress-options');
        if (gifOpt) {
          if (l.type === 0x13) {
            gifOpt.classList.remove('hidden');
            const totalOrig = l._origFramesCount || cnt || 12;
            const durSec = l.total_duration_ms ? ` (${(l.total_duration_ms / 1000).toFixed(1)}s)` : '';
            const origInfo = document.getElementById('txt-gif-orig-info');
            if (origInfo) origInfo.innerText = `${totalOrig}f orig → ${cnt}f${durSec}`;
            const inpExact = document.getElementById('inp-gif-exact-frames');
            if (inpExact) inpExact.value = cnt;
            const inpDiv = document.getElementById('inp-gif-divisor');
            if (inpDiv && l.active_divisor) inpDiv.value = l.active_divisor;
          } else {
            gifOpt.classList.add('hidden');
          }
        }
        updateGifButtonsUI(l);
      }
      updateLayersListUI();
      drawCanvas();
    }

    function updateGifButtonsUI(l) {
      if (!l) return;
      if (l.type === 0x13) {
        const divisorBtns = [
          { id: 'btn-subsample-2', div: 2 },
          { id: 'btn-subsample-3', div: 3 },
          { id: 'btn-subsample-4', div: 4 }
        ];
        divisorBtns.forEach(b => {
          const el = document.getElementById(b.id);
          if (!el) return;
          const isActive = Number(l.active_divisor) === Number(b.div);
          if (isActive) {
            el.className = 'px-2.5 py-1 bg-cyan-600 hover:bg-cyan-500 text-white rounded text-xs font-bold border border-cyan-400 shadow-md shadow-cyan-900/50 transition';
          } else {
            el.className = 'px-2.5 py-1 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs font-semibold border border-transparent transition';
          }
        });

        const exactBtns = [
          { id: 'btn-exact-4', cnt: 4 },
          { id: 'btn-exact-6', cnt: 6 },
          { id: 'btn-exact-8', cnt: 8 }
        ];
        exactBtns.forEach(b => {
          const el = document.getElementById(b.id);
          if (!el) return;
          const isActive = !l.active_divisor && (Number(l.count) === Number(b.cnt));
          if (isActive) {
            el.className = 'px-2 py-0.5 bg-cyan-600 hover:bg-cyan-500 text-white rounded text-xs font-bold border border-cyan-400 shadow-md shadow-cyan-900/50 transition';
          } else {
            el.className = 'px-2 py-0.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs border border-transparent transition';
          }
        });

        // Indicador de velocidad de hardware en el reloj sincronizada automáticamente
        const fDur = l.frame_duration_ms || 100;
        const hwTicks = (l.extra && l.extra[2]) ? l.extra[2] : Math.max(5, Math.min(50, Math.round(fDur / 10)));
        const speedInfo = document.getElementById('txt-hw-speed-info');
        if (speedInfo) {
          speedInfo.innerText = `${fDur}ms/f (${hwTicks} ticks)`;
        }
      }

      // Indicador de resolución real de imagen
      const curRes = l.res_pct || 100;
      const resBtns = [
        { id: 'btn-res-100', pct: 100 },
        { id: 'btn-res-75', pct: 75 },
        { id: 'btn-res-50', pct: 50 },
        { id: 'btn-res-35', pct: 35 },
        { id: 'btn-res-25', pct: 25 }
      ];
      resBtns.forEach(b => {
        const el = document.getElementById(b.id);
        if (!el) return;
        const isActive = (curRes === b.pct);
        if (isActive) {
          el.className = 'p-1.5 bg-cyan-600 hover:bg-cyan-500 text-white rounded text-xs font-bold border border-cyan-400 shadow-md shadow-cyan-900/50 transition';
        } else {
          el.className = 'p-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded text-xs border border-transparent transition';
        }
      });
      // Indicador y botón de Pantalla Completa (320×172)
      const fsBtn = document.getElementById('btn-toggle-fullscreen');
      const fsTxt = document.getElementById('txt-fullscreen-status');
      if (fsBtn && fsTxt) {
        if (l.fit_screen) {
          fsBtn.className = 'w-full p-1.5 bg-blue-600 hover:bg-blue-500 border border-blue-400 text-white rounded-lg font-bold text-xs flex items-center justify-center gap-1.5 shadow-md shadow-blue-900/50 transition';
          fsTxt.innerText = 'ON (320×172)';
          fsTxt.className = 'font-extrabold text-blue-100';
        } else {
          fsBtn.className = 'w-full p-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition';
          fsTxt.innerText = 'OFF';
          fsTxt.className = 'font-bold text-gray-400';
        }
      }

      const resInfo = document.getElementById('txt-res-pct-info');
      if (resInfo) {
        const cnt = l.count || 1;
        const fw = Math.floor(imWidth(l) / cnt);
        const fh = imHeight(l);
        resInfo.innerText = `${curRes}% (${fw}×${fh} px)`;
      }
    }

    function updateSelectedCoord(axis, val) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (l) {
        l[axis] = parseInt(val) || 0;
        drawCanvas();
        updateLayersListUI();
      }
    }

    function alignSelected(pos) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return;
      const cnt = l.count || 1;
      const fw = Math.floor(imWidth(l) / cnt);
      const fh = imHeight(l);

      if (pos === 'center') { l.x = 160; l.y = 86; }
      else if (pos === 'top-left') { l.x = Math.floor(fw / 2) + 6; l.y = Math.floor(fh / 2) + 6; }
      else if (pos === 'top-center') { l.x = 160; l.y = Math.floor(fh / 2) + 6; }
      else if (pos === 'top-right') { l.x = 320 - Math.floor(fw / 2) - 6; l.y = Math.floor(fh / 2) + 6; }
      else if (pos === 'mid-left') { l.x = Math.floor(fw / 2) + 6; l.y = 86; }
      else if (pos === 'mid-right') { l.x = 320 - Math.floor(fw / 2) - 6; l.y = 86; }
      else if (pos === 'bottom-left') { l.x = Math.floor(fw / 2) + 6; l.y = 172 - Math.floor(fh / 2) - 6; }
      else if (pos === 'bottom-center') { l.x = 160; l.y = 172 - Math.floor(fh / 2) - 6; }
      else if (pos === 'bottom-right') { l.x = 320 - Math.floor(fw / 2) - 6; l.y = 172 - Math.floor(fh / 2) - 6; }

      document.getElementById('inp-x').value = l.x;
      document.getElementById('inp-y').value = l.y;
      drawCanvas();
      updateLayersListUI();
    }

    function moveLayerUp() {
      const idx = project.layers.findIndex(l => l.id === selectedLayerId);
      if (idx !== -1 && idx < project.layers.length - 1) {
        const tmp = project.layers[idx];
        project.layers[idx] = project.layers[idx + 1];
        project.layers[idx + 1] = tmp;
        updateLayersListUI();
        drawCanvas();
      }
    }

    function moveLayerDown() {
      const idx = project.layers.findIndex(l => l.id === selectedLayerId);
      if (idx > 0) {
        const tmp = project.layers[idx];
        project.layers[idx] = project.layers[idx - 1];
        project.layers[idx - 1] = tmp;
        updateLayersListUI();
        drawCanvas();
      }
    }

    function deleteLayer() {
      project.layers = project.layers.filter(l => l.id !== selectedLayerId);
      selectedLayerId = project.layers.length ? project.layers[project.layers.length - 1].id : null;
      updateLayersListUI();
      drawCanvas();
    }

    // --- PIPELINE DE PROCESAMIENTO ACUMULATIVO (Resolución, Calidad, Escala y Tamaño) ---

    // Función central: Aplica todos los parámetros activos de la capa acumulativamente sobre su imagen original
    async function applyLayerProcessing(l) {
      if (!l) return;
      // Inicializar propiedades si no existen
      if (l.res_pct === undefined) l.res_pct = 100;
      if (l.max_colors === undefined) l.max_colors = 256;
      if (l.max_frames === undefined) l.max_frames = l.count || 8;

      const cnt = l.count || 1;
      if (l.fit_screen) {
        l.target_w = 320;
        l.target_h = 172;
        l.x = 160;
        l.y = 86;
      }
      const targetW = l.target_w || (Math.floor(l.width / cnt));
      const targetH = l.target_h || l.height;

      if (l.type === 0x13 && (l.raw_gif_b64 || l._rawGifB64)) {
        const rawGif = l.raw_gif_b64 || l._rawGifB64;
        try {
          const res = await fetch('/api/themes/convert_gif', {
            method: 'POST',
            body: JSON.stringify({
              gif_data: rawGif,
              max_frames: l.max_frames,
              fit_screen: !!l.fit_screen,
              target_w: targetW,
              target_h: targetH,
              res_pct: l.res_pct,
              max_colors: l.max_colors
            })
          });
          const data = await res.json();
          if (!data.success) throw new Error(data.error);

          const im = new Image();
          await new Promise(r => { im.onload = r; im.src = data.strip_data; });
          l.image_data = data.strip_data;
          l._imgObj = im;
          l.width = im.width;
          l.height = im.height;
          l.count = data.count;
          if (data.total_frames) {
            l.orig_frames_count = data.total_frames;
            l._origFramesCount = data.total_frames;
          }
          if (data.orig_w && data.orig_h) {
            l.orig_video_w = data.orig_w;
            l.orig_video_h = data.orig_h;
            l._origVideoW = data.orig_w;
            l._origVideoH = data.orig_h;
          }
          if (data.duration) l.frame_duration_ms = data.duration;
          if (data.total_duration) l.total_duration_ms = data.total_duration;

          // Sincronizar automáticamente el intervalo de frames en hardware (extra[2]):
          // En el firmware del reloj, extra[2] es el retraso entre frames en centésimas / ticks de timer (10ms por tick).
          // Se calcula 100% automáticamente para mantener la duración exacta del video con la cantidad de frames activa.
          if (!l.extra) l.extra = [0, 0, 15, 0, 0, 0, 0, 0];
          const calculatedTicks = Math.max(5, Math.min(50, Math.round((l.frame_duration_ms || 100) / 10)));
          l.extra[2] = calculatedTicks;

          selectLayer(l.id);
          drawCanvas();
        } catch (e) {
          alert('Error aplicando procesamiento a la animación: ' + e.message);
        }
      } else if (l._rawImgB64 || l.image_data) {
        const rawSrc = l._rawImgB64 || l.image_data;
        try {
          const res = await fetch('/api/themes/optimize_image', {
            method: 'POST',
            body: JSON.stringify({
              image_data: rawSrc,
              target_w: targetW,
              target_h: targetH,
              mode: l.fit_screen ? 'cover' : 'fit_exact',
              res_pct: l.res_pct,
              max_colors: l.max_colors
            })
          });
          const data = await res.json();
          if (!data.success) throw new Error(data.error);

          const im = new Image();
          await new Promise(r => { im.onload = r; im.src = data.image_data; });
          l.image_data = data.image_data;
          l._imgObj = im;
          l.width = im.width;
          l.height = im.height;
          selectLayer(l.id);
          drawCanvas();
        } catch (e) {
          alert('Error aplicando procesamiento a la imagen: ' + e.message);
        }
      }
    }

    // 1. Alternar ajuste a pantalla completa (320x172) persistente
    async function toggleLayerFullScreen() {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return alert('Selecciona una imagen o animación primero.');
      l.fit_screen = !l.fit_screen;
      if (l.fit_screen) {
        l.target_w = 320;
        l.target_h = 172;
        l.x = 160;
        l.y = 86;
      } else {
        const cnt = l.count || 1;
        l.target_w = l.orig_video_w || l._origVideoW || Math.floor(imWidth(l) / cnt);
        l.target_h = l.orig_video_h || l._origVideoH || imHeight(l);
      }
      await applyLayerProcessing(l);
    }

    // 2. Modificar tamaño manual (ancho o alto) conservando resolución y compresión activas
    async function updateSelectedSize(dimension, val) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return;
      l.fit_screen = false;
      const numVal = parseInt(val) || 16;
      const cnt = l.count || 1;
      const curW = l.target_w || Math.floor(imWidth(l) / cnt);
      const curH = l.target_h || imHeight(l);

      if (dimension === 'w') l.target_w = numVal;
      if (dimension === 'h') l.target_h = numVal;
      await applyLayerProcessing(l);
    }

    // 3. Escalar capa seleccionada (+/- %) conservando resolución y compresión activas
    async function scaleSelectedLayer(factor) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return alert('Selecciona una capa primero.');
      l.fit_screen = false;
      const cnt = l.count || 1;
      const curW = l.target_w || Math.floor(imWidth(l) / cnt);
      const curH = l.target_h || imHeight(l);

      l.target_w = Math.max(16, Math.round(curW * factor));
      l.target_h = Math.max(16, Math.round(curH * factor));
      await applyLayerProcessing(l);
    }

    // 4. Bajar la resolución en porcentaje (Pixel Binning) acumulativo con colores y tamaño
    async function compressInternalResolution(resPct = 60) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return alert('Selecciona una imagen o animación primero.');
      l.res_pct = resPct;
      await applyLayerProcessing(l);
    }

    // 5. Comprimir colores/entropía acumulativo con resolución y tamaño
    async function compressLayerQuality(colors = 64) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return alert('Selecciona una imagen o animación primero.');
      l.max_colors = colors;
      await applyLayerProcessing(l);
    }

    // 6. Reducir cantidad de frames de GIF conservando el video completo (muestreo a lo largo de toda la duración)
    // 6. Reducir cantidad de frames de GIF conservando el video completo (muestreo a lo largo de toda la duración)
    async function reduceGifFrames(maxF) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l || l.type !== 0x13 || (!l.raw_gif_b64 && !l._rawGifB64)) return alert('Esta opción solo aplica a animaciones GIF.');
      l.active_divisor = null;
      l.max_frames = maxF;
      updateGifButtonsUI(l);
      await applyLayerProcessing(l);
    }

    // 7. Muestrear dividiendo los fotogramas del video (ej. 12 frames / 3 = 4 frames que cubren todo el video)
    async function subsampleGifFrames(divisor) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l || l.type !== 0x13 || (!l.raw_gif_b64 && !l._rawGifB64)) return alert('Esta opción solo aplica a animaciones GIF.');
      const baseFrames = l.orig_frames_count || l._origFramesCount || l.count || 12;
      const targetFrames = Math.max(2, Math.round(baseFrames / divisor));
      l.active_divisor = divisor;
      l.max_frames = targetFrames;
      updateGifButtonsUI(l);
      await applyLayerProcessing(l);
    }

    // 8. Aplicar divisor personalizado introducido por el usuario en el input
    async function applyCustomGifDivisor() {
      const inp = document.getElementById('inp-gif-divisor');
      const val = parseFloat(inp ? inp.value : 0);
      if (!val || val <= 0) return alert('Por favor introduce un divisor válido mayor a 0 (ej: 2, 3, 4).');
      await subsampleGifFrames(val);
    }

    // 9. Aplicar cantidad exacta de frames muestreados introducida en el input
    async function applyCustomGifExactFrames() {
      const inp = document.getElementById('inp-gif-exact-frames');
      const val = parseInt(inp ? inp.value : 0);
      if (!val || val < 2) return alert('Por favor introduce al menos 2 frames para la animación.');
      await reduceGifFrames(val);
    }

    // Drag & Drop en Canvas
    canvas.addEventListener('mousedown', (e) => {
      const rect = canvas.getBoundingClientRect();
      const scaleX = 320 / rect.width;
      const scaleY = 172 / rect.height;
      const mx = (e.clientX - rect.left) * scaleX;
      const my = (e.clientY - rect.top) * scaleY;

      // Buscar capa de arriba hacia abajo
      for (let i = project.layers.length - 1; i >= 0; i--) {
        const l = project.layers[i];
        if (!l.visible) continue;
        const cnt = l.count || 1;
        const fw = Math.floor(imWidth(l) / cnt);
        const fh = imHeight(l);
        const dw = l.fit_screen ? 320 : fw;
        const dh = l.fit_screen ? 172 : fh;
        const lx = l.fit_screen ? 160 : l.x;
        const ly = l.fit_screen ? 86 : l.y;
        if (mx >= lx - dw / 2 && mx <= lx + dw / 2 && my >= ly - dh / 2 && my <= ly + dh / 2) {
          selectLayer(l.id);
          isDragging = true;
          dragStartX = mx;
          dragStartY = my;
          initialLayerX = l.x;
          initialLayerY = l.y;
          return;
        }
      }
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging || !selectedLayerId) return;
      const rect = canvas.getBoundingClientRect();
      const scaleX = 320 / rect.width;
      const scaleY = 172 / rect.height;
      const mx = (e.clientX - rect.left) * scaleX;
      const my = (e.clientY - rect.top) * scaleY;

      const l = project.layers.find(x => x.id === selectedLayerId);
      if (l) {
        l.x = Math.round(initialLayerX + (mx - dragStartX));
        l.y = Math.round(initialLayerY + (my - dragStartY));
        document.getElementById('inp-x').value = l.x;
        document.getElementById('inp-y').value = l.y;
        drawCanvas();
      }
    });

    window.addEventListener('mouseup', () => { isDragging = false; });

    // Carga de Fondo y Personaje PNG
    function handleImageUpload(e, kind) {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (evt) => {
        const b64 = evt.target.result;
        const im = new Image();
        im.onload = () => {
          if (kind === 'background') {
            // Fondo reemplaza o agrega al inicio
            const l = {
              id: 'layer_bg_' + Date.now(),
              name: 'Fondo Pantalla (320×172)',
              type: 0x10, mode: 2, count: 1, fmt: 1,
              x: 160, y: 86, has_alpha: false,
              width: im.width, height: im.height,
              target_w: im.width, target_h: im.height,
              image_data: b64, _rawImgB64: b64,
              res_pct: 100, max_colors: 256,
              extra: [0, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: im
            };
            project.layers.unshift(l);
            selectLayer(l.id);
          } else {
            // Personaje superpuesto (Efecto profundidad)
            const l = {
              id: 'layer_overlay_' + Date.now(),
              name: 'Personaje PNG (Profundidad)',
              type: 0x10, mode: 2, count: 1, fmt: 1,
              x: 160, y: 86, has_alpha: true,
              width: im.width, height: im.height,
              target_w: im.width, target_h: im.height,
              image_data: b64, _rawImgB64: b64,
              res_pct: 100, max_colors: 256,
              extra: [0, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: im
            };
            project.layers.push(l);
            selectLayer(l.id);
          }
          updateLayersListUI();
          drawCanvas();
        };
        im.src = b64;
      };
      reader.readAsDataURL(file);
    }

    // Carga y conversión de Animación GIF (type=0x13)
    async function handleGifUpload(e) {
      const file = e.target.files[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = async (evt) => {
        const gifB64 = evt.target.result;
        try {
          // Solicitar al backend la extracción de fotogramas del GIF conservando todos sus fotogramas y velocidad original
          const res = await fetch('/api/themes/convert_gif', {
            method: 'POST',
            body: JSON.stringify({ gif_data: gifB64, max_frames: 0 })
          });
          const data = await res.json();
          if (!data.success) {
            alert('Error procesando GIF: ' + (data.error || 'Formato no soportado'));
            return;
          }

          const im = new Image();
          im.onload = () => {
            const frameW = Math.floor(im.width / data.count);
            const totalOrig = data.total_frames || data.count;
            const l = {
              id: 'layer_anim_' + Date.now(),
              name: `Animación GIF (${data.count} frames)`,
              type: 0x13,
              mode: 2,
              count: data.count,
              fmt: 1,
              x: 160,
              y: 86,
              has_alpha: true,
              width: im.width,
              height: im.height,
              target_w: frameW,
              target_h: im.height,
              res_pct: 100,
              max_colors: 256,
              max_frames: data.count,
              orig_frames_count: totalOrig,
              _origFramesCount: totalOrig,
              orig_video_w: data.orig_w || frameW,
              _origVideoW: data.orig_w || frameW,
              orig_video_h: data.orig_h || im.height,
              _origVideoH: data.orig_h || im.height,
              frame_duration_ms: data.duration || 100,
              total_duration_ms: data.total_duration || (totalOrig * 100),
              image_data: data.strip_data,
              raw_gif_b64: gifB64,
              _rawGifB64: gifB64,
              extra: [0, 0, Math.max(5, Math.min(50, Math.round((data.duration || 100) / 10))), 0, 0, 0, 0, 0],
              visible: true,
              _imgObj: im
            };
            project.layers.push(l);
            selectLayer(l.id);
            updateLayersListUI();
            drawCanvas();
            const durSec = ((data.total_duration || (totalOrig * 100)) / 1000).toFixed(1);
            alert(`¡Animación cargada con éxito!\n- Video completo: ${totalOrig} fotogramas originales (${durSec}s de duración).\n- Muestreo activo: ${data.count} frames (${durSec}s mantenidos).\n- Tamaño por frame: ${frameW}x${im.height} px.`);
          };
          im.src = data.strip_data;
        } catch (err) {
          alert('Error al convertir el GIF: ' + err.message);
        }
      };
      reader.readAsDataURL(file);
    }

    // Cargar directamente el GIF de prueba 'Reactor Cyberpunk'
    async function loadSampleReactorGif() {
      try {
        const resp = await fetch('/web_assets/reactor_pulse.gif');
        const blob = await resp.blob();
        const reader = new FileReader();
        reader.onload = async (e) => {
          const gifB64 = e.target.result;
          const res = await fetch('/api/themes/convert_gif', {
            method: 'POST',
            body: JSON.stringify({ gif_data: gifB64, max_frames: 12 })
          });
          const data = await res.json();
          if (!data.success) return alert('Error cargando demo: ' + data.error);

          const im = new Image();
          im.onload = () => {
            const frameW = Math.floor(im.width / data.count);
            const l = {
              id: 'layer_anim_reactor_' + Date.now(),
              name: 'Reactor Pulse GIF (8 frames)',
              type: 0x13,
              mode: 2,
              count: data.count,
              fmt: 1,
              x: 160,
              y: 86,
              has_alpha: true,
              width: im.width,
              height: im.height,
              target_w: frameW,
              target_h: im.height,
              res_pct: 100,
              max_colors: 256,
              max_frames: data.count,
              orig_frames_count: data.total_frames || data.count,
              _origFramesCount: data.total_frames || data.count,
              orig_video_w: data.orig_w || frameW,
              _origVideoW: data.orig_w || frameW,
              orig_video_h: data.orig_h || im.height,
              _origVideoH: data.orig_h || im.height,
              frame_duration_ms: data.duration || 100,
              total_duration_ms: data.total_duration || (data.count * 100),
              image_data: data.strip_data,
              raw_gif_b64: gifB64,
              _rawGifB64: gifB64,
              extra: [0, 0, Math.max(5, Math.min(50, Math.round((data.duration || 100) / 10))), 0, 0, 0, 0, 0],
              visible: true,
              _imgObj: im
            };
            project.layers.push(l);
            selectLayer(l.id);
            updateLayersListUI();
            drawCanvas();
          };
          im.src = data.strip_data;
        };
        reader.readAsDataURL(blob);
      } catch (err) {
        alert('Error: ' + err.message);
      }
    }

    // Manejador de cambios de fuente
    function getSelectedFontConfig() {
      const fam = document.getElementById('sel-font-family')?.value || 'opensans';
      const col = document.getElementById('inp-font-color')?.value || '#FFFFFF';
      const str = document.getElementById('inp-font-stroke')?.value || '#000000';
      return { font_family: fam, color_hex: col, stroke_hex: str, stroke_w: 2 };
    }

    function onFontSettingsChange() {
      // Si hay capas de reloj o fecha, podemos actualizar o permitir aplicar
    }

    // Subir fuente TTF personalizada
    async function handleCustomFontUpload(e) {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async (evt) => {
        const b64 = evt.target.result;
        try {
          const res = await fetch('/api/fonts/upload', {
            method: 'POST',
            body: JSON.stringify({ name: file.name, data: b64 })
          });
          const data = await res.json();
          if (!data.success) throw new Error(data.error);

          const sel = document.getElementById('sel-font-family');
          const opt = document.createElement('option');
          opt.value = data.font_id;
          opt.textContent = data.name;
          opt.selected = true;
          sel.prepend(opt);
          alert(`¡Fuente "${file.name}" cargada con éxito!`);
          await applyFontToAllWidgets();
        } catch (err) {
          alert('Error al subir la fuente TTF: ' + err.message);
        }
      };
      reader.readAsDataURL(file);
    }

    // Aplicar la tipografía seleccionada a todos los widgets de dígitos/fecha existentes
    async function applyFontToAllWidgets() {
      const cfg = getSelectedFontConfig();
      let modified = false;

      // Actualizar capas de reloj (Horas 0x05, Minutos 0x06, Segundos 0x07)
      const clockLayers = project.layers.filter(l => [5, 6, 7].includes(l.type));
      if (clockLayers.length > 0) {
        const res = await fetch('/api/themes/render_strip', {
          method: 'POST',
          body: JSON.stringify({
            strip_type: 'digits',
            font_size: 52,
            color_hex: cfg.color_hex,
            stroke_w: 2,
            stroke_hex: cfg.stroke_hex,
            font_family: cfg.font_family
          })
        });
        const dClock = await res.json();
        if (dClock.success) {
          const im = new Image();
          await new Promise((resolve) => {
            im.onload = resolve;
            im.src = dClock.image_data;
          });
          clockLayers.forEach(l => {
            l.image_data = dClock.image_data;
            l._imgObj = im;
            l.width = im.width;
            l.height = im.height;
          });
          modified = true;
        }
      }

      // Actualizar capas de fecha numérica (Mes 0x02, Día 0x03, Año 0x01)
      const dateNumLayers = project.layers.filter(l => [1, 2, 3].includes(l.type));
      if (dateNumLayers.length > 0) {
        const res = await fetch('/api/themes/render_strip', {
          method: 'POST',
          body: JSON.stringify({
            strip_type: 'digits',
            font_size: 16,
            color_hex: cfg.color_hex,
            stroke_w: 1,
            stroke_hex: cfg.stroke_hex,
            font_family: cfg.font_family
          })
        });
        const dDate = await res.json();
        if (dDate.success) {
          const im = new Image();
          await new Promise((resolve) => {
            im.onload = resolve;
            im.src = dDate.image_data;
          });
          dateNumLayers.forEach(l => {
            l.image_data = dDate.image_data;
            l._imgObj = im;
            l.width = im.width;
            l.height = im.height;
          });
          modified = true;
        }
      }

      // Actualizar capas de día de semana (0x04)
      const weekLayers = project.layers.filter(l => l.type === 4);
      if (weekLayers.length > 0) {
        const res = await fetch('/api/themes/render_strip', {
          method: 'POST',
          body: JSON.stringify({
            strip_type: 'weekdays',
            font_size: 14,
            color_hex: cfg.color_hex,
            stroke_w: 1,
            stroke_hex: cfg.stroke_hex,
            lang: 'es',
            font_family: cfg.font_family
          })
        });
        const dWeek = await res.json();
        if (dWeek.success) {
          const im = new Image();
          await new Promise((resolve) => {
            im.onload = resolve;
            im.src = dWeek.image_data;
          });
          weekLayers.forEach(l => {
            l.image_data = dWeek.image_data;
            l._imgObj = im;
            l.width = im.width;
            l.height = im.height;
          });
          modified = true;
        }
      }

      if (modified) {
        updateLayersListUI();
        drawCanvas();
      } else {
        alert('Añade un Reloj Digital o Fecha primero usando los botones de abajo.');
      }
    }

    // Agregar Reloj Digital
    async function addClockWidget() {
      const cfg = getSelectedFontConfig();
      const res = await fetch('/api/themes/render_strip', {
        method: 'POST',
        body: JSON.stringify({
          strip_type: 'digits',
          font_size: 52,
          color_hex: cfg.color_hex,
          stroke_w: 2,
          stroke_hex: cfg.stroke_hex,
          font_family: cfg.font_family
        })
      });
      const data = await res.json();
      const b64 = data.image_data;
      const im = new Image();
      im.onload = () => {
        // Horas
        const lh = {
          id: 'layer_hours_' + Date.now(),
          name: 'Horas (HH)',
          type: 0x05, mode: 4, count: 10, fmt: 1,
          x: 64, y: 115, has_alpha: true,
          width: im.width, height: im.height,
          image_data: b64, extra: [1, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: im
        };
        // Minutos
        const lm = {
          id: 'layer_mins_' + Date.now(),
          name: 'Minutos (MM)',
          type: 0x06, mode: 4, count: 10, fmt: 1,
          x: 258, y: 115, has_alpha: true,
          width: im.width, height: im.height,
          image_data: b64, extra: [1, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: im
        };
        project.layers.push(lh, lm);
        selectLayer(lm.id);
        updateLayersListUI();
        drawCanvas();
      };
      im.src = b64;
    }

    // Agregar Fecha & Día
    async function addDateWidget() {
      const cfg = getSelectedFontConfig();
      // Tira dígitos fecha
      const resD = await fetch('/api/themes/render_strip', {
        method: 'POST',
        body: JSON.stringify({
          strip_type: 'digits',
          font_size: 16,
          color_hex: cfg.color_hex,
          stroke_w: 1,
          stroke_hex: cfg.stroke_hex,
          font_family: cfg.font_family
        })
      });
      const dataD = await resD.json();

      // Tira días semana
      const resW = await fetch('/api/themes/render_strip', {
        method: 'POST',
        body: JSON.stringify({
          strip_type: 'weekdays',
          font_size: 14,
          color_hex: cfg.color_hex,
          stroke_w: 1,
          stroke_hex: cfg.stroke_hex,
          lang: 'es',
          font_family: cfg.font_family
        })
      });
      const dataW = await resW.json();

      const imD = new Image();
      imD.onload = () => {
        const lMonth = {
          id: 'layer_month_' + Date.now(),
          name: 'Mes (MM)',
          type: 0x02, mode: 4, count: 10, fmt: 1,
          x: 49, y: 31, has_alpha: true,
          width: imD.width, height: imD.height,
          image_data: dataD.image_data, extra: [1, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: imD
        };
        const lDay = {
          id: 'layer_day_' + Date.now(),
          name: 'Día (DD)',
          type: 0x03, mode: 4, count: 10, fmt: 1,
          x: 98, y: 31, has_alpha: true,
          width: imD.width, height: imD.height,
          image_data: dataD.image_data, extra: [1, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: imD
        };
        project.layers.push(lMonth, lDay);

        const imW = new Image();
        imW.onload = () => {
          const lWeek = {
            id: 'layer_week_' + Date.now(),
            name: 'Día Semana (DOM..SAB)',
            type: 0x04, mode: 4, count: 7, fmt: 1,
            x: 250, y: 31, has_alpha: true,
            width: imW.width, height: imW.height,
            image_data: dataW.image_data, extra: [0, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: imW
          };
          project.layers.push(lWeek);
          selectLayer(lWeek.id);
          updateLayersListUI();
          drawCanvas();
        };
        imW.src = dataW.image_data;
      };
      imD.src = dataD.image_data;
    }

    // Compilar Tema
    async function compileTheme() {
      const res = await fetch('/api/themes/compile', {
        method: 'POST',
        body: JSON.stringify({ layers: project.layers })
      });
      const ret = await res.json();
      if (ret.success) {
        const st = ret.stats;
        document.getElementById('txt-theme-size').innerText = `${Math.round(st.total_size / 1024)} KB`;
        document.getElementById('bar-theme-size').style.width = `${Math.min(100, st.percent_used)}%`;
        if (st.within_limit) {
          alert(`¡Esfera compilada con éxito!\nTamaño: ${st.total_size} bytes (${Math.round(st.total_size/1024)} KB)\nLímite: 153,600 bytes\nCapas: ${st.num_layers}`);
        } else {
          alert(`⚠️ ATENCIÓN: El tema pesa ${st.total_size} bytes y excede el límite de 153,600 bytes. Reduce la resolución o número de capas.`);
        }
      }
    }

    // Guardar el tema actual localmente y en el backend para persistencia
    async function saveCurrentProject() {
      try {
        const cleanLayers = project.layers.map(l => {
          const cl = { ...l };
          delete cl._imgObj;
          return cl;
        });
        const projData = {
          width: project.width || 320,
          height: project.height || 172,
          layers: cleanLayers
        };
        // 1. Guardar en backend (persistencia de servidor)
        await fetch('/api/themes/save_current', {
          method: 'POST',
          body: JSON.stringify({ project: projData })
        });
        // 2. Guardar en localStorage del navegador como respaldo
        try {
          localStorage.setItem('a9pro_current_theme', JSON.stringify(projData));
        } catch (e) {
          // Si la cuota de localStorage se supera por imágenes pesadas, el backend lo mantiene
        }
      } catch (err) {
        console.warn("Error guardando tema persistente:", err);
      }
    }

    // Restablecer al tema por defecto oficial
    async function resetDefaultTheme() {
      if (!confirm('¿Restablecer el personalizador a la esfera predeterminada oficial (Cyberpunk 385)?')) return;
      try {
        localStorage.removeItem('a9pro_current_theme');
        const res = await fetch('/api/themes/reset_default', { method: 'POST' });
        const data = await res.json();
        if (data.success && data.project) {
          project = data.project;
          preloadLayerImages();
          updateLayersListUI();
          drawCanvas();
        }
      } catch (e) {
        console.error("Error al restablecer tema predeterminado:", e);
      }
    }

    // Subir Tema a la Caja
    async function uploadCurrentTheme() {
      await compileTheme();
      // Guardar tema actual para que sea persistente en la interfaz tras recargar
      await saveCurrentProject();
      openUploadModal();
      await fetch('/api/themes/upload_compiled', { method: 'POST' });
    }

    function openUploadModal() {
      document.getElementById('modal-upload').classList.remove('hidden');
      document.getElementById('upload-bar').style.width = '0%';
      document.getElementById('upload-pct').innerText = '0%';
      document.getElementById('upload-msg').innerText = 'Iniciando transmisión Bluetooth LE...';
      document.getElementById('btn-close-modal').classList.add('hidden');
      pollUploadProgress();
    }

    function closeUploadModal() {
      document.getElementById('modal-upload').classList.add('hidden');
    }

    let uploadPollTimer = null;
    function pollUploadProgress() {
      if (uploadPollTimer) clearTimeout(uploadPollTimer);
      uploadPollTimer = setTimeout(async () => {
        try {
          const res = await fetch('/api/upload_status');
          const st = await res.json();
          document.getElementById('upload-bar').style.width = `${st.percent}%`;
          document.getElementById('upload-pct').innerText = `${st.percent}%`;
          document.getElementById('upload-packets').innerText = `${st.current_packet} / ${st.total_packets}`;
          document.getElementById('upload-speed').innerText = `${st.speed_kbps} KB/s`;

          if (st.status === 'uploading') {
            document.getElementById('upload-msg').innerText = `Transmitiendo paquete ${st.current_packet} de ${st.total_packets}...`;
            pollUploadProgress();
          } else if (st.status === 'completed') {
            document.getElementById('upload-msg').innerText = '🎉 ¡Tema subido e instalado con éxito en la A9 Pro Box!';
            document.getElementById('btn-close-modal').classList.remove('hidden');
          } else if (st.status === 'error') {
            document.getElementById('upload-msg').innerText = `❌ Error: ${st.error || 'Fallo de transmisión'}`;
            document.getElementById('btn-close-modal').classList.remove('hidden');
          }
        } catch (e) {
          pollUploadProgress();
        }
      }, 500);
    }

    // ==================== GALERÍA OFICIAL ====================
    async function loadOfficialGallery() {
      const grid = document.getElementById('gallery-grid');
      grid.innerHTML = '<div class="col-span-4 text-center py-8 text-gray-500">Cargando catálogo oficial...</div>';
      const res = await fetch('/api/official_themes');
      const list = await res.json();
      grid.innerHTML = '';

      list.forEach(item => {
        const card = document.createElement('div');
        card.className = 'bg-gray-900 border border-gray-800 rounded-xl p-3 flex flex-col items-center space-y-2 hover:border-blue-500/50 transition';
        card.innerHTML = `
          <div class="w-full h-24 bg-black rounded-lg overflow-hidden flex items-center justify-center">
            ${item.thumbnail_url ? `<img src="${item.thumbnail_url}" class="w-full h-full object-cover">` : '<span class="text-xs text-gray-600">Sin vista</span>'}
          </div>
          <div class="w-full flex justify-between items-center text-[10px] text-gray-400">
            <span class="truncate font-mono">${item.id.split('_')[0]}</span>
            <span>${Math.round(item.size_bytes / 1024)} KB</span>
          </div>
          <div class="grid grid-cols-2 gap-1.5 w-full pt-1">
            <button onclick="loadOfficialToStudio('${item.id}')" class="py-1 px-2 bg-gray-800 hover:bg-gray-700 rounded text-[11px] font-semibold text-gray-200">
              ✏️ Editar
            </button>
            <button onclick="uploadOfficialDirect('${item.id}')" class="py-1 px-2 bg-blue-600/80 hover:bg-blue-600 rounded text-[11px] font-semibold text-white">
              📤 Subir
            </button>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    async function loadOfficialToStudio(themeId, switchTab = true) {
      const res = await fetch('/api/themes/load_official', {
        method: 'POST',
        body: JSON.stringify({ theme_id: themeId })
      });
      const data = await res.json();
      if (data.success) {
        project = data.project;
        preloadLayerImages();
        updateLayersListUI();
        if (switchTab) {
          setTab('studio');
        } else {
          setTab('device');
        }
      }
    }

    async function uploadOfficialDirect(themeId) {
      // También cargar este tema para que se visualice de inmediato en la caja del menú principal
      loadOfficialToStudio(themeId, false);
      await saveCurrentProject();
      openUploadModal();
      await fetch('/api/themes/upload_official', {
        method: 'POST',
        body: JSON.stringify({ theme_id: themeId })
      });
    }

    // Bucle de actualización periódica de batería en tiempo real
    setInterval(updateStatus, 1500);
    updateStatus();

    // Iniciar siempre en la pestaña principal "Mi Dispositivo"
    setTab('device');

    // Inicialización del tema: Si hay un tema subido/guardado persistente, cargarlo. Si no, cargar 385 por defecto.
    async function initThemeOnStartup() {
      try {
        // Intentar consultar proyecto persistente al servidor
        const res = await fetch('/api/themes/current_project');
        const data = await res.json();
        if (data.success && data.has_custom && data.project && data.project.layers && data.project.layers.length > 0) {
          project = data.project;
          preloadLayerImages();
          updateLayersListUI();
          return;
        }

        // Respaldo en localStorage si existiera
        const local = localStorage.getItem('a9pro_current_theme');
        if (local) {
          const parsed = JSON.parse(local);
          if (parsed && parsed.layers && parsed.layers.length > 0) {
            project = parsed;
            preloadLayerImages();
            updateLayersListUI();
            return;
          }
        }
      } catch (err) {
        console.warn("Fallo cargando tema guardado:", err);
      }

      // Si no hay tema previo persistido, cargar la esfera oficial por defecto
      loadOfficialToStudio('385_deb0daf817f94b5d8e1fb4a0ff4bd7f3', false);
    }

    setTimeout(initThemeOnStartup, 150);

  </script>
</body>
</html>
"""


def main():
    server = HTTPServer(("0.0.0.0", PORT), StudioHTTPHandler)
    url = f"http://localhost:{PORT}"
    print(f"\n========================================================")
    print(f"  A9 Pro Studio Server activo en: {url}")
    print(f"  - Interfaz Original de la App (Batería L/R/Box en Vivo)")
    print(f"  - Personalizador de Temas Mejorado (320x172 Multicapa)")
    print(f"  - Galería de 80 Esferas Oficiales")
    print(f"========================================================\n")
    
    # Intentar abrir el navegador automáticamente si hay entorno gráfico
    if "DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nApagando servidor...")
        device_mgr.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
