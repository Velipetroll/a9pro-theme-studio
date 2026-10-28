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
import datetime
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
        self.lock = threading.RLock()
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
                now = time.time()
                # Sondeo de estado cada 5 seg para no saturar el MCU
                if now - last_poll_time >= 5.0:
                    last_poll_time = now
                    query_tags = [1, 2, 8, 12, 13, 14]
                    payload = b"".join(bytes([t, 0]) for t in query_tags)
                    with self.lock:
                        if self.client and self.connected:
                            try:
                                self.client.write_cmd(self.client.build_cmd(0x27, payload))
                            except Exception:
                                pass

                raw = None
                with self.lock:
                    if self.client and self.connected:
                        raw = self.client.recv_notification(timeout=0.1)

                if raw and len(raw) >= 5:
                    cmd = raw[1]
                    if cmd in (0x27, 0x28):
                        self._parse_device_tlv(raw)
            except Exception:
                pass
            time.sleep(0.05)

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

    def send_raw_command(self, cmd: int, payload: bytes, use_req: bool = False) -> bool:
        with self.lock:
            if not self.connected or not self.client:
                if not self.connect():
                    return False
            try:
                pkt = self.client.build_cmd(cmd, payload)
                if use_req:
                    return self.client.write_req(pkt)
                else:
                    self.client.write_cmd(pkt)
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
                                     resample_filter: int = Image.Resampling.NEAREST) -> Image.Image:
        """
        Reduce la resolución real de la imagen/fotograma en porcentaje,
        disminuyendo drásticamente el tamaño del archivo binario y de la tira de sprites.
        """
        if res_pct >= 100 or res_pct <= 5:
            return img
        curr_w, curr_h = img.size
        new_w = max(16, int(round(curr_w * res_pct / 100.0)))
        new_h = max(16, int(round(curr_h * res_pct / 100.0)))
        return img.resize((new_w, new_h), resample_filter)

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

        # Si el ancho original supera los 320 px de la pantalla, auto-reescalar proporcionalmente a 320 px
        if not fit_screen and base_w > 320:
            target_w = 320
            target_h = max(1, int(round(base_h * 320.0 / base_w)))

        frames = []
        for frame in raw_frames:
            if fit_screen:
                # Ajuste proporcional exacto a pantalla completa (320x172) sin aplastar ni deformar (crop centrado con NEAREST)
                from PIL import ImageOps
                frame = ImageOps.fit(frame, (320, 172), method=Image.Resampling.NEAREST, centering=(0.5, 0.5))
            elif target_w and target_h:
                if target_w > 320:
                    target_h = max(1, int(round(target_h * 320.0 / target_w)))
                    target_w = 320
                frame = frame.resize((target_w, target_h), Image.Resampling.NEAREST)
            elif frame.width > 320:
                nh = max(1, int(round(frame.height * 320.0 / frame.width)))
                frame = frame.resize((320, nh), Image.Resampling.NEAREST)

            # Reducir resolución real en porcentaje (mantiene la escala en pantalla pero reduce drásticamente el tamaño .bin)
            # Para GIFs se usa NEAREST para no perder nitidez en pixel art y bordes
            if res_pct < 100:
                frame = ThemeStudioEngine._resample_internal_resolution(frame, res_pct=res_pct, resample_filter=Image.Resampling.NEAREST)

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
        return b64, len(frames), adjusted_frame_duration_ms, total_available, total_video_duration_ms, fw, fh

    @staticmethod
    def optimize_image(img_bytes: bytes, target_w: Optional[int] = None, target_h: Optional[int] = None,
                       mode: str = "fit_exact", max_colors: int = 64, res_pct: int = 100) -> str:
        """Comprime la información de la imagen ajustando resolución interna en porcentaje y aplicando filtrado de alta calidad (Lanczos)."""
        im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        base_w, base_h = im.size

        # Si el ancho original supera los 320 px de la pantalla, auto-reescalar proporcionalmente a 320 px
        if base_w > 320 and mode != "cover":
            new_w = 320
            new_h = max(1, int(round(base_h * 320.0 / base_w)))
            im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
            if target_w and target_w > 320:
                target_w = 320
                target_h = new_h

        if target_w and target_h:
            if target_w > 320 and mode != "cover":
                target_h = max(1, int(round(target_h * 320.0 / target_w)))
                target_w = 320
            if mode == "cover":
                from PIL import ImageOps
                im = ImageOps.fit(im, (target_w, target_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            elif mode == "fit_exact":
                im = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
            elif mode == "contain":
                resized = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                im.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
                rx = (target_w - im.width) // 2
                ry = (target_h - im.height) // 2
                resized.paste(im, (rx, ry))
                im = resized
        elif im.width > 320:
            new_h = max(1, int(round(im.height * 320.0 / im.width)))
            im = im.resize((320, new_h), Image.Resampling.LANCZOS)

        # Reducir resolución real en porcentaje con filtrado suave Lanczos
        if res_pct < 100:
            im = ThemeStudioEngine._resample_internal_resolution(im, res_pct=res_pct, resample_filter=Image.Resampling.LANCZOS)

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

                orig_fw = im.width // cnt if cnt > 0 else im.width
                orig_fh = im.height
                is_gif = (l.get("type") == 0x13)
                filt = Image.Resampling.NEAREST if is_gif else Image.Resampling.LANCZOS

                # Si la capa tiene activada la opción de pantalla completa (fit_screen):
                if l.get("fit_screen"):
                    if (orig_fw, orig_fh) != (320, 172):
                        scaled_strip = Image.new("RGBA", (320 * cnt, 172), (0, 0, 0, 0))
                        for i in range(cnt):
                            frame = im.crop((i * orig_fw, 0, (i + 1) * orig_fw, orig_fh))
                            frame_scaled = frame.resize((320, 172), filt)
                            scaled_strip.paste(frame_scaled, (i * 320, 0))
                        im = scaled_strip
                    l_copy["x"] = 160
                    l_copy["y"] = 86
                    l_copy["target_w"] = 320
                    l_copy["target_h"] = 172
                elif orig_fw > 320:
                    # Auto-reescalar si el ancho supera los 320px de la pantalla
                    new_fw = 320
                    new_fh = max(1, int(round(orig_fh * 320.0 / orig_fw)))
                    scaled_strip = Image.new("RGBA", (new_fw * cnt, new_fh), (0, 0, 0, 0))
                    for i in range(cnt):
                        frame = im.crop((i * orig_fw, 0, (i + 1) * orig_fw, orig_fh))
                        frame_scaled = frame.resize((new_fw, new_fh), filt)
                        scaled_strip.paste(frame_scaled, (i * new_fw, 0))
                    im = scaled_strip
                    l_copy["target_w"] = new_fw
                    l_copy["target_h"] = new_fh

                buf = io.BytesIO()
                im.save(buf, format="PNG")
                l_copy["image_data"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                l_copy["x"] = int(round(float(l.get("x", 160))))
                l_copy["y"] = int(round(float(l.get("y", 86))))
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

            # Auto-detección rigurosa de canal y píxeles de transparencia:
            # Si la capa contiene píxeles con transparencia o semitransparencia (alpha < 250),
            # DEBE codificarse obligatoriamente con has_alpha=True para que el hardware
            # del reloj no renderice un fondo negro sólido alrededor de la imagen.
            # Si la imagen es 100% opaca, se codifica con has_alpha=False para ahorrar espacio y CPU.
            has_alpha = bool(l.get("has_alpha", True))
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im_rgba = im.convert("RGBA")
                alpha_ch = im_rgba.split()[-1]
                min_a, _ = alpha_ch.getextrema()
                if min_a < 250:
                    has_alpha = True
                    # Limpiar ruido de borde casi invisible de remuestreo Lanczos (alpha < 6)
                    if min_a < 6:
                        r, g, b, a = im_rgba.split()
                        a = a.point(lambda p: 0 if p < 6 else p)
                        im = Image.merge("RGBA", (r, g, b, a))
                else:
                    has_alpha = False
            elif not has_alpha:
                has_alpha = False

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
            lx = int(round(float(l.get("x", 160))))
            ly = int(round(float(l.get("y", 86))))
            struct.pack_into("<IBBBBhh", d, 0, off, int(l.get("mode", 2)), int(l.get("type", 0x10)), int(l.get("count", 1)), 1, lx, ly)
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
  <title>A9 Pro Studio — Hardware Console & Visual Customizer</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script id="tailwind-config">
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            chassis: {
              950: '#07080b',
              900: '#0c0e13',
              850: '#11131a',
              800: '#161922',
              700: '#212532',
              600: '#2d3345',
              500: '#434b62',
            },
            azure: {
              500: '#1f8fff',
              600: '#127ae6',
              700: '#095db8',
              800: '#094b94',
            }
          },
          fontFamily: {
            mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace']
          }
        }
      }
    }
  </script>
  <style>
    :root {
      --chassis-bg: #090b0f;
      --panel-bg: #11141b;
      --panel-sub: #161922;
      --border-line: #212633;
      --border-bright: #313749;
      --accent-azure: #1f8fff;
      --led-active: #10b981;
      --led-warn: #f59e0b;
      --led-alert: #ef4444;
      --text-main: #f0f2f5;
      --text-dim: #7f889b;
      --text-ghost: #454d5e;
    }
    body {
      background-color: var(--chassis-bg);
      background-image: 
        linear-gradient(rgba(33, 38, 51, 0.35) 1px, transparent 1px),
        linear-gradient(90deg, rgba(33, 38, 51, 0.35) 1px, transparent 1px);
      background-size: 28px 28px;
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .panel-chassis {
      background: var(--panel-bg);
      border: 1px solid var(--border-line);
      border-radius: 6px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }
    .screen-chassis {
      background: #000000;
      border: 2px solid #282f40;
      border-radius: 6px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.9), inset 0 0 16px rgba(0, 0, 0, 0.95);
    }
    .hardware-bay {
      background: radial-gradient(circle at 50% 50%, #151924 0%, #0d0f14 75%);
      border: 1px solid #1c212c;
      border-radius: 4px;
    }
    .slider-vertical {
      writing-mode: bt-lr;
      -webkit-appearance: slider-vertical;
      width: 4px;
      height: 105px;
      accent-color: #1f8fff;
    }
    canvas {
      image-rendering: -moz-crisp-edges;
      image-rendering: -webkit-crisp-edges;
      image-rendering: pixelated;
      image-rendering: crisp-edges;
    }
    .img-eq-sign {
      filter: brightness(0) invert(1);
      opacity: 0.85;
    }
    .btn-tactile {
      background: #161922;
      border: 1px solid #262c3b;
      border-radius: 4px;
      color: #b5bcc9;
      font-family: ui-monospace, SFMono-Regular, "Roboto Mono", Menlo, monospace;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.04em;
      transition: all 0.08s ease;
      box-shadow: 0 1px 2px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05);
    }
    .btn-tactile:hover {
      background: #1c212d;
      border-color: #353d52;
      color: #ffffff;
    }
    .btn-tactile:active {
      transform: translateY(1px);
      box-shadow: inset 0 1px 2px rgba(0,0,0,0.6);
    }
    .btn-action-azure {
      background: #1f8fff;
      border: 1px solid #4da7ff;
      border-radius: 4px;
      color: #ffffff;
      font-family: ui-monospace, SFMono-Regular, "Roboto Mono", Menlo, monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.05em;
      box-shadow: 0 2px 8px rgba(31, 143, 255, 0.4), inset 0 1px 0 rgba(255,255,255,0.3);
      transition: all 0.08s ease;
    }
    .btn-action-azure:hover {
      background: #369cff;
      border-color: #70b8ff;
    }
    .btn-action-azure:active {
      transform: translateY(1px);
    }
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: #090b0f; }
    ::-webkit-scrollbar-thumb { background: #1f2533; border-radius: 2px; }
    ::-webkit-scrollbar-thumb:hover { background: #2f374c; }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased">

  <!-- Header Superior Técnico: Consola de Control -->
  <header class="bg-[#0e1117] border-b border-[#212633] px-6 py-2.5 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center space-x-3">
      <div class="w-7 h-7 rounded bg-[#161a24] border border-[#2c3345] flex items-center justify-center font-mono font-bold text-xs text-[#1f8fff]">
        A9
      </div>
      <div>
        <div class="flex items-center gap-2">
          <span class="text-xs font-mono font-bold tracking-wider text-white uppercase">A9 Pro // Studio Console</span>
          <span id="badge-conn" class="text-[9px] font-mono px-2 py-0.5 rounded uppercase font-semibold bg-[#1a1114] text-[#ef4444] border border-[#ef4444]/40 flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-[#ef4444]"></span>
            <span>Disconnected</span>
          </span>
        </div>
        <p class="text-[10px] font-mono text-[#616b7f]">PROTOCOL: BLE L2CAP // NATIVE DISP 320x172px</p>
      </div>
    </div>

    <!-- Navegación de Modos de Consola (Segmented Switch) -->
    <nav class="flex bg-[#08090d] p-1 rounded border border-[#212633]">
      <button onclick="setTab('device')" id="btn-tab-device" class="px-3.5 py-1 rounded text-xs font-mono font-semibold transition bg-[#1f8fff] text-white shadow-sm flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
        DEVICE DECK
      </button>
      <button onclick="setTab('studio')" id="btn-tab-studio" class="px-3.5 py-1 rounded text-xs font-mono font-medium transition text-[#7f889b] hover:text-white flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        THEME STUDIO
      </button>
      <button onclick="setTab('gallery')" id="btn-tab-gallery" class="px-3.5 py-1 rounded text-xs font-mono font-medium transition text-[#7f889b] hover:text-white flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
        OFFICIAL REPO (80)
      </button>
    </nav>

    <!-- Conexión de Hardware -->
    <div class="flex items-center space-x-2">
      <button onclick="toggleConnect()" id="btn-conn" class="px-3 py-1.5 text-xs font-mono font-semibold rounded btn-action-azure flex items-center gap-1.5">
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 7l10 10-5 5V2l5 5L7 17"/></svg>
        <span id="btn-conn-text">LINK DEVICE</span>
      </button>
    </div>
  </header>

  <!-- Contenido Principal -->
  <main class="flex-1 p-5 max-w-7xl mx-auto w-full">

    <!-- ==================== PESTAÑA 1: DISPOSITIVO ORIGINAL ==================== -->
    <section id="tab-device" class="space-y-5">
      
      <!-- Fila Superior: Bahías de Monitoreo de Hardware (L, Estuche, R) -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
        
        <!-- Audífono Izquierdo (L) -->
        <div id="card-earbud-l" class="panel-chassis p-4 flex flex-col justify-between relative transition-all duration-200">
          <div class="flex items-center justify-between border-b border-[#212633] pb-2">
            <span class="text-[10px] font-mono font-bold tracking-wider text-[#7f889b] uppercase">CH-L // LEFT TRANSDUCER</span>
            <span id="badge-mode-l" class="text-[9px] font-mono px-2 py-0.5 rounded font-semibold bg-[#181c26] text-[#7f889b] border border-[#262c3b]">STANDBY</span>
          </div>
          
          <div class="hardware-bay my-3 p-3 flex items-center justify-center relative min-h-[160px]">
            <img id="img-earbud-l" src="/web_assets/left_earphone.png" class="h-32 object-contain drop-shadow-md" alt="Left Earbud">
            <div id="glow-charge-l" class="absolute inset-0 rounded bg-emerald-500/10 opacity-0 transition-opacity duration-300 pointer-events-none"></div>
          </div>

          <div class="border-t border-[#212633] pt-2 space-y-1.5">
            <div class="flex justify-between items-center text-xs font-mono">
              <span class="text-[#7f889b]">PWR LEVEL</span>
              <div class="flex items-center gap-1.5">
                <span id="icon-charge-l" class="text-[10px] text-emerald-400 font-mono hidden">CHARGING</span>
                <span id="txt-bat-l" class="font-bold text-white">0%</span>
              </div>
            </div>
            <div class="w-full bg-[#08090d] rounded-sm h-1.5 overflow-hidden border border-[#212633]">
              <div id="bar-bat-l" class="bg-[#1f8fff] h-1.5 transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>
        </div>

        <!-- Estuche Inteligente A9 Pro Box -->
        <div id="card-box" class="panel-chassis p-4 flex flex-col justify-between relative transition-all duration-200">
          <div class="flex items-center justify-between border-b border-[#212633] pb-2">
            <span class="text-[10px] font-mono font-bold tracking-wider text-[#7f889b] uppercase">DOCK // A9 PRO SMART CASE</span>
            <span id="txt-firmware" class="text-[9px] font-mono text-[#4da7ff]">REV 3.4.0</span>
          </div>
          
          <div class="hardware-bay my-3 p-3 flex items-center justify-center relative min-h-[160px]">
            <div class="relative inline-block">
              <img src="/web_assets/connected.png" class="h-36 w-auto object-contain block drop-shadow-md" alt="A9 Pro Box">
              <!-- Mockup de la pantalla en vivo dentro del estuche encajada -->
              <div class="absolute overflow-hidden pointer-events-none" style="top: 43.2%; left: 18.2%; width: 63.6%; height: 34.2%; border-radius: 4px;">
                <canvas id="box-screen-canvas" width="320" height="172" class="w-full h-full object-fill block"></canvas>
                <div class="absolute inset-x-0 top-0 text-[7px] font-mono bg-black/80 text-[#9ba4b5] flex justify-between w-full px-1 pt-0.5 pointer-events-none">
                  <span>L:<b id="screen-l" class="text-white">100%</b> R:<b id="screen-r" class="text-white">100%</b></span>
                  <span id="screen-box" class="text-white">DOCK 100%</span>
                </div>
              </div>
            </div>
          </div>

          <div class="border-t border-[#212633] pt-2 space-y-1.5">
            <div class="flex justify-between items-center text-xs font-mono">
              <span class="text-[#7f889b]">DOCK BATTERY</span>
              <div class="flex items-center gap-1.5">
                <span id="icon-charge-box" class="text-[10px] text-amber-400 font-mono hidden">AC ACTIVE</span>
                <span id="txt-bat-box" class="font-bold text-white">0%</span>
              </div>
            </div>
            <div class="w-full bg-[#08090d] rounded-sm h-1.5 overflow-hidden border border-[#212633]">
              <div id="bar-bat-box" class="bg-emerald-500 h-1.5 transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>
        </div>

        <!-- Audífono Derecho (R) -->
        <div id="card-earbud-r" class="panel-chassis p-4 flex flex-col justify-between relative transition-all duration-200">
          <div class="flex items-center justify-between border-b border-[#212633] pb-2">
            <span class="text-[10px] font-mono font-bold tracking-wider text-[#7f889b] uppercase">CH-R // RIGHT TRANSDUCER</span>
            <span id="badge-mode-r" class="text-[9px] font-mono px-2 py-0.5 rounded font-semibold bg-[#181c26] text-[#7f889b] border border-[#262c3b]">STANDBY</span>
          </div>
          
          <div class="hardware-bay my-3 p-3 flex items-center justify-center relative min-h-[160px]">
            <img id="img-earbud-r" src="/web_assets/right_earphone.png" class="h-32 object-contain drop-shadow-md" alt="Right Earbud">
            <div id="glow-charge-r" class="absolute inset-0 rounded bg-emerald-500/10 opacity-0 transition-opacity duration-300 pointer-events-none"></div>
          </div>

          <div class="border-t border-[#212633] pt-2 space-y-1.5">
            <div class="flex justify-between items-center text-xs font-mono">
              <span class="text-[#7f889b]">PWR LEVEL</span>
              <div class="flex items-center gap-1.5">
                <span id="icon-charge-r" class="text-[10px] text-emerald-400 font-mono hidden">CHARGING</span>
                <span id="txt-bat-r" class="font-bold text-white">0%</span>
              </div>
            </div>
            <div class="w-full bg-[#08090d] rounded-sm h-1.5 overflow-hidden border border-[#212633]">
              <div id="bar-bat-r" class="bg-[#1f8fff] h-1.5 transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>
        </div>

      </div>

      <!-- Notificación Toast Industrial -->
      <div id="toast-notify" class="fixed bottom-5 right-5 z-50 bg-[#0e1117] border border-[#1f8fff] text-white px-4 py-2.5 rounded shadow-2xl flex items-center space-x-3 transition-all duration-300 transform translate-y-24 opacity-0 pointer-events-none">
        <span id="toast-icon" class="text-sm font-mono text-[#1f8fff]">INFO</span>
        <div>
          <h4 id="toast-title" class="text-xs font-mono font-bold text-[#1f8fff]">TELEMETRY</h4>
          <p id="toast-msg" class="text-[11px] font-mono text-[#9ba4b5]">Status updated.</p>
        </div>
      </div>

      <!-- Barra de Acciones y Telemetría -->
      <div class="panel-chassis px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
        <div class="flex items-center gap-2">
          <button onclick="requestBattery()" class="px-3 py-1 btn-tactile flex items-center gap-1.5">
            <svg class="w-3 h-3 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            POLL TELEMETRY
          </button>
        </div>
        <div class="flex items-center space-x-4 text-[11px] text-[#7f889b]">
          <span>MAC: <b id="txt-mac" class="text-white">41:42:6C:C2:DD:3C</b></span>
          <span>|</span>
          <span>DISP: <b class="text-white">320×172 px</b></span>
          <span>|</span>
          <span>SRAM BUFFER: <b class="text-white">153.6 KB</b></span>
        </div>
      </div>

      <!-- Módulos de Control Técnico (ANC, Modos, Ecualizador, Búsqueda) -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">

        <!-- Módulo 1: Control de Ruido y Modo de Sonido -->
        <div class="panel-chassis p-4 space-y-4">
          <div class="border-b border-[#212633] pb-1.5 flex justify-between items-center">
            <h2 class="text-[10px] font-mono font-bold uppercase tracking-wider text-[#7f889b]">DSP // NOISE CANCELLATION</h2>
            <span class="text-[9px] font-mono text-[#525a6c]">ANC 0x14</span>
          </div>

          <div class="grid grid-cols-3 gap-2">
            <button onclick="setNoiseMode(0)" id="btn-anc-0" class="flex flex-col items-center justify-center p-2.5 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition">
              <img src="/web_assets/immerse_blue.png" class="w-6 h-6 mb-1 object-contain" alt="ANC">
              <span class="text-[10px] font-mono font-semibold">CANCEL</span>
            </button>
            <button onclick="setNoiseMode(1)" id="btn-anc-1" class="flex flex-col items-center justify-center p-2.5 rounded border border-[#1f8fff] bg-[#1f8fff]/15 transition">
              <img src="/web_assets/mild_blue.png" class="w-6 h-6 mb-1 object-contain" alt="Normal">
              <span class="text-[10px] font-mono font-semibold text-white">NORMAL</span>
            </button>
            <button onclick="setNoiseMode(2)" id="btn-anc-2" class="flex flex-col items-center justify-center p-2.5 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition">
              <img src="/web_assets/transparent_blue.png" class="w-6 h-6 mb-1 object-contain" alt="Transparente">
              <span class="text-[10px] font-mono font-semibold">AMBIENT</span>
            </button>
          </div>

          <div class="border-b border-[#212633] pb-1.5 pt-1 flex justify-between items-center">
            <h2 class="text-[10px] font-mono font-bold uppercase tracking-wider text-[#7f889b]">DSP // SOUND PROFILE</h2>
            <span class="text-[9px] font-mono text-[#525a6c]">PROFILE 0x07</span>
          </div>

          <div class="grid grid-cols-3 gap-2">
            <button onclick="setWorkMode(0)" id="btn-work-0" class="flex flex-col items-center p-2 rounded border border-[#1f8fff] bg-[#1f8fff]/15 transition">
              <img src="/web_assets/music_mode.png" class="w-5 h-5 mb-1 object-contain" alt="Música">
              <span class="text-[10px] font-mono font-medium text-white">MUSIC</span>
            </button>
            <button onclick="setWorkMode(1)" id="btn-work-1" class="flex flex-col items-center p-2 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition">
              <img src="/web_assets/game_mode.png" class="w-5 h-5 mb-1 object-contain" alt="Juego">
              <span class="text-[10px] font-mono font-medium text-[#7f889b]">LOW-LAT</span>
            </button>
            <button onclick="setWorkMode(2)" id="btn-work-2" class="flex flex-col items-center p-2 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition">
              <img src="/web_assets/movie_mode.png" class="w-5 h-5 mb-1 object-contain" alt="Película">
              <span class="text-[10px] font-mono font-medium text-[#7f889b]">CINEMA</span>
            </button>
          </div>
        </div>

        <!-- Módulo 2: Ecualizador de 10 Bandas -->
        <div class="panel-chassis p-4 space-y-3">
          <div class="flex items-center justify-between border-b border-[#212633] pb-1.5">
            <h2 class="text-[10px] font-mono font-bold uppercase tracking-wider text-[#7f889b] flex items-center gap-1.5">
              <img src="/web_assets/main_eq_sign.png" class="w-3.5 h-3.5 object-contain img-eq-sign" alt="EQ">
              GRAPHIC EQUALIZER // 10-BAND
            </h2>
            <select id="sel-eq-preset" onchange="applyEqPreset(this.value)" class="text-[10px] font-mono bg-[#08090d] border border-[#262c3b] rounded px-2 py-0.5 text-white focus:border-[#1f8fff] focus:outline-none">
              <option value="0">PRESET: STANDARD</option>
              <option value="1">PRESET: POP</option>
              <option value="2">PRESET: ROCK</option>
              <option value="3">PRESET: JAZZ</option>
              <option value="4">PRESET: CLASSIC</option>
              <option value="5">PRESET: VOCAL</option>
            </select>
          </div>

          <div class="bg-[#08090d] border border-[#1b1f2b] p-2 rounded flex justify-between items-center">
            <div id="eq-sliders-container" class="flex justify-between w-full text-center"></div>
          </div>

          <button onclick="applyCustomEq()" class="w-full py-1.5 btn-tactile text-xs flex items-center justify-center gap-1.5">
            <svg class="w-3 h-3 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
            COMMIT EQ REGISTERS
          </button>
        </div>

        <!-- Módulo 3: Buscar Audífonos & Mapeo Táctil -->
        <div class="panel-chassis p-4 space-y-3">
          <div class="border-b border-[#212633] pb-1.5 flex justify-between items-center">
            <h2 class="text-[10px] font-mono font-bold uppercase tracking-wider text-[#7f889b] flex items-center gap-1.5">
              <img src="/web_assets/main_find.png" class="w-3.5 h-3.5 object-contain" alt="Find">
              ACOUSTIC LOCALIZER // PING
            </h2>
            <span class="text-[9px] font-mono text-[#525a6c]">0x17 BEACON</span>
          </div>

          <div class="grid grid-cols-3 gap-2">
            <button onclick="findEarbuds(true, false)" class="p-2 btn-tactile text-center">
              PING L
            </button>
            <button onclick="findEarbuds(false, true)" class="p-2 btn-tactile text-center">
              PING R
            </button>
            <button onclick="findEarbuds(true, true)" class="p-2 btn-action-azure text-center">
              PING BOTH
            </button>
          </div>

          <div class="border-b border-[#212633] pb-1.5 pt-1">
            <h2 class="text-[10px] font-mono font-bold uppercase tracking-wider text-[#7f889b] flex items-center gap-1.5">
              <img src="/web_assets/main_gesture_control.png" class="w-3.5 h-3.5 object-contain" alt="Gestures">
              TOUCH SENSOR SCHEMATIC
            </h2>
          </div>

          <div class="text-[10px] font-mono space-y-1.5">
            <div class="flex justify-between items-center bg-[#08090d] border border-[#1c212d] p-1.5 rounded">
              <span class="text-[#7f889b]">1x TAP (L/R)</span>
              <span class="text-white font-semibold">PLAY / PAUSE</span>
            </div>
            <div class="flex justify-between items-center bg-[#08090d] border border-[#1c212d] p-1.5 rounded">
              <span class="text-[#7f889b]">2x TAP (L/R)</span>
              <span class="text-white font-semibold">NEXT TRACK</span>
            </div>
            <div class="flex justify-between items-center bg-[#08090d] border border-[#1c212d] p-1.5 rounded">
              <span class="text-[#7f889b]">LONG PRESS</span>
              <span class="text-white font-semibold">TOGGLE ANC / VOICE</span>
            </div>
          </div>
        </div>

      </div>

    </section>


    <!-- ==================== PESTAÑA 2: PERSONALIZADOR DE TEMAS ==================== -->
    <section id="tab-studio" class="space-y-5 hidden">
      
      <!-- Barra Superior de Parámetros de Compilación -->
      <div class="panel-chassis px-4 py-3 flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center space-x-3 text-xs font-mono">
          <span class="text-white font-bold tracking-wider">CANVAS // 320x172 px</span>
          <span class="text-[10px] px-2 py-0.5 rounded bg-[#08090d] text-[#7f889b] border border-[#212633]">SCALE 2.5X</span>
        </div>

        <!-- Indicador de Peso vs Límite 153.6 KB -->
        <div class="flex items-center space-x-3">
          <div class="text-right font-mono">
            <div class="text-[11px] text-[#7f889b]">BUFFER: <b id="txt-theme-size" class="text-white">0 KB</b> / 150 KB</div>
            <div class="w-32 bg-[#08090d] h-1.5 rounded-sm overflow-hidden border border-[#212633] mt-1">
              <div id="bar-theme-size" class="bg-[#1f8fff] h-1.5 transition-all" style="width: 0%"></div>
            </div>
          </div>
          <button onclick="resetDefaultTheme()" class="px-3 py-1.5 btn-tactile flex items-center gap-1.5" title="Restablecer tema al predeterminado oficial (Cyberpunk 385)">
            <svg class="w-3.5 h-3.5 text-[#7f889b]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            RESET
          </button>
          <button onclick="compileTheme()" class="px-3.5 py-1.5 btn-tactile flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5 text-[#7f889b]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
            BUILD .BIN
          </button>
          <button onclick="uploadCurrentTheme()" class="px-4 py-1.5 btn-action-azure flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            FLASH TO CASE
          </button>
        </div>
      </div>

      <!-- Área de Trabajo Principal: Lienzo 320x172 + Inspector de Capas -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">

        <!-- Lienzo Central (320x172 escalado a 800x430) -->
        <div class="lg:col-span-8 flex flex-col items-center justify-center p-5 panel-chassis space-y-3">
          <div class="relative screen-chassis p-1">
            <canvas id="theme-canvas" width="320" height="172" class="w-[800px] h-[430px] rounded-[3px] cursor-crosshair bg-black block"></canvas>
            
            <!-- Barra de estado simulada superior -->
            <div id="canvas-overlay-status" class="absolute top-2 left-3 right-3 flex justify-between items-center text-[10px] font-mono text-white/80 px-1 pointer-events-none drop-shadow">
              <span>L 100%  R 100%</span>
              <span>DOCK 100%</span>
            </div>
          </div>

          <!-- Barra Tipográfica Global -->
          <div class="w-full bg-[#090b10] border border-[#212633] rounded px-3 py-2 flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
            <div class="flex items-center gap-2">
              <span class="text-[#7f889b]">FONT:</span>
              <select id="sel-font-family" onchange="onFontSettingsChange()" class="bg-[#121620] border border-[#282f40] text-white rounded px-2 py-0.5 text-xs focus:border-[#1f8fff] focus:outline-none">
                <option value="orbitron">Orbitron (Futurista)</option>
                <option value="tech">Share Tech Mono (Digital LED)</option>
                <option value="bebas">Bebas Neue (Condensada)</option>
                <option value="pixel">Press Start 2P (Retro 8-Bit)</option>
                <option value="opensans" selected>Open Sans ExtraBold</option>
                <option value="fira">Fira Sans Bold</option>
                <option value="hack">Hack Monospace</option>
                <option value="dejavu">DejaVu Sans Bold</option>
              </select>
              <label class="px-2 py-0.5 btn-tactile cursor-pointer flex items-center gap-1">
                IMPORT .TTF
                <input type="file" accept=".ttf,.otf" class="hidden" onchange="handleCustomFontUpload(event)">
              </label>
            </div>

            <div class="flex items-center gap-3">
              <div class="flex items-center gap-1.5">
                <span class="text-[#7f889b]">COLOR:</span>
                <input type="color" id="inp-font-color" value="#FFFFFF" onchange="onFontSettingsChange()" class="w-5 h-5 rounded bg-transparent cursor-pointer border border-[#282f40]">
              </div>
              <div class="flex items-center gap-1.5">
                <span class="text-[#7f889b]">BORDER:</span>
                <input type="color" id="inp-font-stroke" value="#000000" onchange="onFontSettingsChange()" class="w-5 h-5 rounded bg-transparent cursor-pointer border border-[#282f40]">
              </div>
              <button onclick="applyFontToAllWidgets()" class="px-2.5 py-0.5 btn-action-azure text-[10px]">
                APPLY ALL
              </button>
            </div>
          </div>

          <!-- Barra de Herramientas para Añadir Elementos -->
          <div class="flex flex-wrap items-center justify-center gap-2 pt-1 font-mono text-xs">
            <label class="px-3 py-1.5 btn-tactile cursor-pointer flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
              + BACKGROUND (320x172)
              <input type="file" accept="image/*" class="hidden" onchange="handleImageUpload(event, 'background')">
            </label>
            <label class="px-3 py-1.5 btn-tactile cursor-pointer flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5z"/></svg>
              + OVERLAY PNG
              <input type="file" accept="image/*" class="hidden" onchange="handleImageUpload(event, 'overlay')">
            </label>
            <button id="btn-add-clock" onclick="addClockWidget()" class="px-3 py-1.5 btn-tactile flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              + CLOCK (09:28)
            </button>
            <button id="btn-add-date" onclick="addDateWidget()" class="px-3 py-1.5 btn-tactile flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
              + DATE (DOM 10/24)
            </button>
            <label class="px-3 py-1.5 btn-tactile cursor-pointer flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5 text-[#1f8fff]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              + IMPORT GIF
              <input type="file" accept="image/gif" class="hidden" onchange="handleGifUpload(event)">
            </label>
            <button onclick="loadSampleReactorGif()" class="px-3 py-1.5 bg-[#0f1f33] hover:bg-[#152e4d] text-[#6bb5ff] border border-[#1f8fff]/40 rounded font-mono text-xs font-semibold transition flex items-center gap-1.5">
              SAMPLE REACTOR
            </button>
          </div>
        </div>

        <!-- Panel Lateral: Capas & Inspector de Propiedades -->
        <div class="lg:col-span-4 space-y-4">
          
          <!-- Lista de Capas (Orden Z) -->
          <div class="panel-chassis p-4 space-y-2.5">
            <div class="flex justify-between items-center border-b border-[#212633] pb-1.5">
              <h3 class="text-[10px] font-mono font-bold uppercase tracking-wider text-[#7f889b]">LAYER HIERARCHY (Z-STACK)</h3>
              <span id="txt-layer-count" class="text-[10px] font-mono text-[#525a6c]">0 LAYERS</span>
            </div>

            <!-- Botones de Control de Capa -->
            <div class="flex items-center gap-1.5 bg-[#08090d] p-1 rounded border border-[#1c212c]">
              <button onclick="moveLayerUp()" class="flex-1 py-1 px-2 btn-tactile text-[10px] flex items-center justify-center gap-1" title="Subir nivel de capa">
                ▲ UP
              </button>
              <button onclick="moveLayerDown()" class="flex-1 py-1 px-2 btn-tactile text-[10px] flex items-center justify-center gap-1" title="Bajar nivel de capa">
                ▼ DOWN
              </button>
              <button onclick="deleteLayer()" class="py-1 px-3 bg-[#1f1013] hover:bg-[#2e151a] text-[#ef4444] border border-[#ef4444]/30 rounded text-[10px] font-mono font-semibold transition flex items-center justify-center gap-1" title="Eliminar capa">
                ✕ REMOVE
              </button>
            </div>

            <div id="layers-list" class="space-y-1.5 max-h-[220px] overflow-y-auto pr-1">
              <!-- Elementos de capa generados dinámicamente -->
            </div>
          </div>

          <!-- Inspector del Elemento Seleccionado -->
          <div id="inspector-panel" class="panel-chassis p-4 space-y-3 font-mono">
            <div class="border-b border-[#212633] pb-1.5">
              <h3 class="text-[10px] font-bold uppercase tracking-wider text-[#7f889b]">LAYER PARAMETERS</h3>
            </div>
            
            <div class="grid grid-cols-2 gap-2 text-xs">
              <div>
                <label class="text-[10px] text-[#7f889b]">POS X (CENTER)</label>
                <input type="number" id="inp-x" class="w-full bg-[#08090d] border border-[#212633] rounded px-2 py-0.5 mt-0.5 text-white font-mono text-xs focus:border-[#1f8fff] focus:outline-none" onchange="updateSelectedCoord('x', this.value)">
              </div>
              <div>
                <label class="text-[10px] text-[#7f889b]">POS Y (CENTER)</label>
                <input type="number" id="inp-y" class="w-full bg-[#08090d] border border-[#212633] rounded px-2 py-0.5 mt-0.5 text-white font-mono text-xs focus:border-[#1f8fff] focus:outline-none" onchange="updateSelectedCoord('y', this.value)">
              </div>
            </div>

            <!-- Ajuste Manual de Tamaño (Ancho × Alto) -->
            <div class="grid grid-cols-2 gap-2 text-xs pt-0.5">
              <div>
                <label class="text-[10px] text-[#7f889b]">WIDTH (PX)</label>
                <input type="number" id="inp-w" class="w-full bg-[#08090d] border border-[#212633] rounded px-2 py-0.5 mt-0.5 text-white font-mono text-xs focus:border-[#1f8fff] focus:outline-none" onchange="updateSelectedSize('w', this.value)">
              </div>
              <div>
                <label class="text-[10px] text-[#7f889b]">HEIGHT (PX)</label>
                <input type="number" id="inp-h" class="w-full bg-[#08090d] border border-[#212633] rounded px-2 py-0.5 mt-0.5 text-white font-mono text-xs focus:border-[#1f8fff] focus:outline-none" onchange="updateSelectedSize('h', this.value)">
              </div>
            </div>

            <!-- Botones de alineación rápida -->
            <div class="space-y-1 pt-1">
              <span class="text-[9px] text-[#525a6c] uppercase">QUICK ANCHOR</span>
              <div class="grid grid-cols-3 gap-1 text-[10px]">
                <button onclick="alignSelected('top-left')" class="p-1 btn-tactile">TL</button>
                <button onclick="alignSelected('top-center')" class="p-1 btn-tactile">TC</button>
                <button onclick="alignSelected('top-right')" class="p-1 btn-tactile">TR</button>
                <button onclick="alignSelected('mid-left')" class="p-1 btn-tactile">ML</button>
                <button onclick="alignSelected('center')" class="p-1 btn-tactile">MID</button>
                <button onclick="alignSelected('mid-right')" class="p-1 btn-tactile">MR</button>
                <button onclick="alignSelected('bottom-left')" class="p-1 btn-tactile">BL</button>
                <button onclick="alignSelected('bottom-center')" class="p-1 btn-tactile">BC</button>
                <button onclick="alignSelected('bottom-right')" class="p-1 btn-tactile">BR</button>
              </div>
            </div>

            <!-- Herramientas de Compresión y Calidad de Imagen -->
            <div id="inspector-compress-box" class="space-y-2 pt-2 border-t border-[#212633]">
              <div class="flex justify-between items-center text-[10px]">
                <span class="text-[#7f889b]">DIMENSIONS:</span>
                <span id="txt-layer-dims" class="text-white font-mono">0x0 px</span>
              </div>
              
              <!-- Ajuste de Escala a Pantalla Completa (Toggle) -->
              <button id="btn-toggle-fullscreen" onclick="toggleLayerFullScreen()" class="w-full p-1.5 btn-tactile flex items-center justify-between px-2 text-[10px]" title="Activa o desactiva el ajuste automático a pantalla completa (320x172)">
                <span>FULLSCREEN 320x172:</span>
                <span id="txt-fullscreen-status" class="font-bold text-[#7f889b]">OFF</span>
              </button>

              <!-- Reductor de Resolución (Slider interactivo) -->
              <div class="space-y-1">
                <div class="flex justify-between items-center text-[10px]">
                  <span class="text-[#7f889b]">SUBSAMPLE RESOLUTION:</span>
                  <span id="txt-res-pct-info" class="text-[#1f8fff] font-bold">100%</span>
                </div>
                <div class="flex items-center gap-2 pt-0.5">
                  <span class="text-[9px] text-[#525a6c]">10%</span>
                  <input type="range" id="sl-res-pct" min="10" max="100" step="5" value="100"
                         class="w-full h-1 bg-[#212633] rounded appearance-none cursor-pointer accent-[#1f8fff]"
                         oninput="onResolutionSliderInput(this.value)"
                         onchange="onResolutionSliderChange(this.value)">
                  <span class="text-[9px] text-[#525a6c]">100%</span>
                </div>
              </div>

              <!-- Compresor de Calidad -->
              <div class="space-y-1">
                <div class="flex justify-between items-center text-[10px]">
                  <span class="text-[#7f889b]">PALETTE QUANTIZATION:</span>
                </div>
                <div class="grid grid-cols-5 gap-1 text-[10px]">
                  <button onclick="compressLayerQuality(128)" class="p-1 btn-tactile" title="128 colores">128c</button>
                  <button onclick="compressLayerQuality(64)" class="p-1 btn-tactile" title="64 colores">64c</button>
                  <button onclick="compressLayerQuality(32)" class="p-1 btn-tactile text-amber-300" title="32 colores">32c</button>
                  <button onclick="compressLayerQuality(16)" class="p-1 btn-tactile text-rose-300" title="16 colores">16c</button>
                  <button onclick="compressLayerQuality(8)" class="p-1 btn-tactile text-red-400" title="8 colores">8c</button>
                </div>
              </div>

              <!-- Opciones exclusivas para GIF -->
              <div id="gif-compress-options" class="hidden pt-2 space-y-2 border-t border-[#212633]">
                <div class="flex items-center justify-between text-[10px]">
                  <span class="text-[#1f8fff]">GIF FRAMES // TIMELINE:</span>
                  <span id="txt-gif-orig-info" class="text-[#7f889b]">12f orig</span>
                </div>

                <!-- 1. Dividir frames del video -->
                <div class="bg-[#08090d] p-2 rounded border border-[#1c212d] space-y-1.5">
                  <div class="flex items-center justify-between text-[10px] text-[#7f889b]">
                    <span>FRAME DECIMATION:</span>
                    <div class="flex items-center gap-1">
                      <button id="btn-subsample-2" onclick="subsampleGifFrames(2)" class="px-2 py-0.5 btn-tactile text-[10px]">/2</button>
                      <button id="btn-subsample-3" onclick="subsampleGifFrames(3)" class="px-2 py-0.5 btn-tactile text-[10px]">/3</button>
                      <button id="btn-subsample-4" onclick="subsampleGifFrames(4)" class="px-2 py-0.5 btn-tactile text-[10px]">/4</button>
                    </div>
                  </div>
                  <div class="flex items-center gap-2 text-[10px]">
                    <span class="text-[#525a6c]">CUSTOM DIV:</span>
                    <input type="number" id="inp-gif-divisor" min="1" max="20" placeholder="Ej: 3" class="w-12 bg-[#121620] border border-[#212633] rounded px-1 text-center text-white text-[10px] focus:border-[#1f8fff] focus:outline-none">
                    <button onclick="applyCustomGifDivisor()" class="px-2 py-0.5 btn-action-azure text-[10px]">SET</button>
                  </div>
                </div>

                <!-- 2. Fijar número exacto de frames muestreados -->
                <div class="flex items-center justify-between text-[10px] text-[#7f889b] pt-0.5">
                  <span>EXACT COUNT:</span>
                  <div class="flex items-center gap-1">
                    <button id="btn-exact-4" onclick="reduceGifFrames(4)" class="px-1.5 py-0.5 btn-tactile text-[10px]">4f</button>
                    <button id="btn-exact-6" onclick="reduceGifFrames(6)" class="px-1.5 py-0.5 btn-tactile text-[10px]">6f</button>
                    <button id="btn-exact-8" onclick="reduceGifFrames(8)" class="px-1.5 py-0.5 btn-tactile text-[10px]">8f</button>
                    <input type="number" id="inp-gif-exact-frames" min="2" max="24" placeholder="Qty" class="w-10 bg-[#121620] border border-[#212633] rounded px-1 text-center text-white text-[10px] focus:border-[#1f8fff] focus:outline-none" onchange="applyCustomGifExactFrames()">
                  </div>
                </div>

                <!-- 3. Velocidad sincronizada -->
                <div class="bg-[#08090d] p-1.5 rounded border border-[#1c212d] flex items-center justify-between text-[10px]">
                  <span class="text-[#7f889b]">HW PLAYBACK RATE:</span>
                  <span id="txt-hw-speed-info" class="text-[#1f8fff] font-bold">AUTO SYNC</span>
                </div>
              </div>
            </div>
          </div>

        </div>

      </div>

    </section>


    <!-- ==================== PESTAÑA 3: GALERÍA DE 80 TEMAS ==================== -->
    <section id="tab-gallery" class="space-y-4 hidden">
      <div class="border-b border-[#212633] pb-2 flex items-center justify-between">
        <div>
          <h2 class="text-xs font-mono font-bold uppercase tracking-wider text-white">OFFICIAL FIRMWARE REPOSITORY // 80 PRESETS (320x172)</h2>
          <p class="text-[10px] font-mono text-[#7f889b]">Direct binary images extracted from A9 Pro ROM. One-click load or direct Bluetooth flashing.</p>
        </div>
      </div>

      <!-- Cuadrícula de Temas -->
      <div id="gallery-grid" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        <!-- Render dinámico de temas -->
      </div>
    </section>

  </main>

  <!-- Modal de Transmisión BLE Industrial -->
  <div id="modal-upload" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center hidden font-mono">
    <div class="panel-chassis p-5 max-w-md w-full mx-4 space-y-3 text-center bg-[#0d0f15] border border-[#1f8fff]/50 shadow-2xl">
      <div class="flex items-center justify-between border-b border-[#212633] pb-2">
        <h3 class="text-xs font-bold uppercase tracking-wider text-white">FIRMWARE UPLOAD // BLE L2CAP</h3>
        <span class="text-[9px] text-[#1f8fff]">CHANNEL 0x22</span>
      </div>
      
      <p class="text-[10px] text-[#7f889b]">Transmitting payload in 128-byte framed packets...</p>
      
      <div class="w-full bg-[#08090d] rounded-sm h-2 overflow-hidden border border-[#212633]">
        <div id="upload-bar" class="bg-[#1f8fff] h-2 transition-all duration-200" style="width: 0%"></div>
      </div>

      <div class="flex justify-between items-center text-[10px] text-[#7f889b]">
        <span id="upload-pct" class="text-white font-bold">0%</span>
        <span id="upload-packets">0 / 0 PKTS</span>
        <span id="upload-speed">0 KB/s</span>
      </div>

      <div id="upload-msg" class="text-xs font-semibold text-[#1f8fff] bg-[#08090d] p-1.5 rounded border border-[#1c212c]">INITIALIZING TRANSMISSION...</div>
      
      <button id="btn-close-modal" onclick="closeUploadModal()" class="px-4 py-1.5 btn-tactile text-xs hidden w-full">
        DISMISS
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
          btn.className = 'px-3.5 py-1 rounded text-xs font-mono font-semibold transition bg-[#1f8fff] text-white shadow-sm flex items-center gap-1.5';
        } else {
          btn.className = 'px-3.5 py-1 rounded text-xs font-mono font-medium transition text-[#7f889b] hover:text-white flex items-center gap-1.5';
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
    function showToast(title, msg, icon = 'OK', isGreen = true) {
      const toast = document.getElementById('toast-notify');
      if (!toast) return;
      document.getElementById('toast-title').innerText = title;
      document.getElementById('toast-title').className = isGreen ? 'text-xs font-mono font-bold text-emerald-400' : 'text-xs font-mono font-bold text-[#1f8fff]';
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
          badge.className = 'text-[9px] font-mono px-2 py-0.5 rounded uppercase font-semibold bg-[#0d1f18] text-[#10b981] border border-[#10b981]/40 flex items-center gap-1.5';
          badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-[#10b981]"></span><span>CONNECTED</span>';
          btnConn.innerText = 'UNLINK DEVICE';
        } else {
          badge.className = 'text-[9px] font-mono px-2 py-0.5 rounded uppercase font-semibold bg-[#1a1114] text-[#ef4444] border border-[#ef4444]/40 flex items-center gap-1.5';
          badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-[#ef4444]"></span><span>DISCONNECTED</span>';
          btnConn.innerText = 'LINK DEVICE';
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
          badgeModeL.className = 'text-[10px] px-2.5 py-0.5 rounded-full font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-700/60 animate-pulse';
          cardL.className = 'card-panel border-emerald-500/80 shadow-[0_0_25px_rgba(16,185,129,0.2)] p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
          if (glowL) glowL.classList.remove('opacity-0');
        } else {
          iconChargeL.classList.add('hidden');
          badgeModeL.innerText = 'En uso / Activo';
          badgeModeL.className = 'text-[10px] px-2.5 py-0.5 rounded-full font-semibold bg-slate-900 text-slate-400 border border-white/5';
          cardL.className = 'card-panel p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
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
          badgeModeR.className = 'text-[10px] px-2.5 py-0.5 rounded-full font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-700/60 animate-pulse';
          cardR.className = 'card-panel border-emerald-500/80 shadow-[0_0_25px_rgba(16,185,129,0.2)] p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
          if (glowR) glowR.classList.remove('opacity-0');
        } else {
          iconChargeR.classList.add('hidden');
          badgeModeR.innerText = 'En uso / Activo';
          badgeModeR.className = 'text-[10px] px-2.5 py-0.5 rounded-full font-semibold bg-slate-900 text-slate-400 border border-white/5';
          cardR.className = 'card-panel p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
          if (glowR) glowR.classList.add('opacity-0');
        }

        // Tarjeta Caja Inteligente
        document.getElementById('txt-bat-box').innerText = `${st.battery_box}%`;
        document.getElementById('bar-bat-box').style.width = `${st.battery_box}%`;
        const iconChargeBox = document.getElementById('icon-charge-box');
        const cardBox = document.getElementById('card-box');
        if (st.box_charging) {
          iconChargeBox.classList.remove('hidden');
          cardBox.className = 'card-panel border-amber-500/80 shadow-[0_0_25px_rgba(245,158,11,0.2)] p-5 flex flex-col items-center justify-between text-center relative overflow-hidden transition-all duration-300';
        } else {
          iconChargeBox.classList.add('hidden');
          cardBox.className = 'card-panel p-5 flex flex-col items-center justify-between text-center relative overflow-hidden shadow-2xl transition-all duration-300';
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
            ? 'flex flex-col items-center justify-center p-2.5 rounded border border-[#1f8fff] bg-[#1f8fff]/15 transition'
            : 'flex flex-col items-center justify-center p-2.5 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition';
        });

        [0, 1, 2].forEach(m => {
          const btn = document.getElementById(`btn-work-${m}`);
          if (btn) btn.className = (st.work_mode === m)
            ? 'flex flex-col items-center p-2 rounded border border-[#1f8fff] bg-[#1f8fff]/15 transition'
            : 'flex flex-col items-center p-2 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition';
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

        // Filtrado suave para imágenes estáticas, nearest-neighbor para animaciones GIF
        ctx.imageSmoothingEnabled = (layer.type !== 0x13);

        const cnt = layer.count || 1;
        const fw = Math.floor(imWidth(layer) / cnt);
        const fh = imHeight(layer);
        const dw = layer.fit_screen ? 320 : fw;
        const dh = layer.fit_screen ? 172 : fh;
        const x = layer.fit_screen ? 160 : layer.x;
        const y = layer.fit_screen ? 86 : layer.y;

        // Horas, Minutos, Mes, Día y Día de la semana dinámicos en tiempo real
        const now = new Date();
        const curH = now.getHours();
        const curM = now.getMinutes();
        const curMonth = now.getMonth() + 1; // 1..12
        const curDay = now.getDate();        // 1..31
        const curDayOfWeek = now.getDay();   // 0=DOM..6=SAB

        if (layer.mode === 4) {
          if (layer.type === 5 || layer.type === 6 || layer.type === 2 || layer.type === 3) {
            let val = 0;
            if (layer.type === 5) val = curH;
            else if (layer.type === 6) val = curM;
            else if (layer.type === 2) val = curMonth;
            else if (layer.type === 3) val = curDay;

            const d1 = Math.floor(val / 10) % 10;
            const d2 = val % 10;
            ctx.drawImage(img, d1 * fw, 0, fw, fh, Math.floor(x - dw), Math.floor(y - dh / 2), dw, dh);
            ctx.drawImage(img, d2 * fw, 0, fw, fh, Math.floor(x), Math.floor(y - dh / 2), dw, dh);
          } else if (layer.type === 4) {
            // Día de semana (0=DOM, 1=LUN ... 6=SAB)
            const dIdx = curDayOfWeek % cnt;
            ctx.drawImage(img, dIdx * fw, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
          } else {
            ctx.drawImage(img, 0, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
          }
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
        bCtx.clearRect(0, 0, 320, 172);
        bCtx.fillStyle = "#000000";
        bCtx.fillRect(0, 0, 320, 172);

        const now = new Date();
        const curH = now.getHours();
        const curM = now.getMinutes();
        const curMonth = now.getMonth() + 1;
        const curDay = now.getDate();
        const curDayOfWeek = now.getDay();

        project.layers.forEach(layer => {
          if (!layer.visible) return;
          const img = layer._imgObj;
          if (!img || !img.complete) return;

          // Filtrado suave para imágenes estáticas, nearest-neighbor para animaciones GIF
          bCtx.imageSmoothingEnabled = (layer.type !== 0x13);

          const cnt = layer.count || 1;
          const fw = Math.floor(imWidth(layer) / cnt);
          const fh = imHeight(layer);
          const dw = layer.fit_screen ? 320 : fw;
          const dh = layer.fit_screen ? 172 : fh;
          const x = layer.fit_screen ? 160 : layer.x;
          const y = layer.fit_screen ? 86 : layer.y;

          if (layer.mode === 4) {
            if (layer.type === 5 || layer.type === 6 || layer.type === 2 || layer.type === 3) {
              let val = 0;
              if (layer.type === 5) val = curH;
              else if (layer.type === 6) val = curM;
              else if (layer.type === 2) val = curMonth;
              else if (layer.type === 3) val = curDay;

              const d1 = Math.floor(val / 10) % 10;
              const d2 = val % 10;
              bCtx.drawImage(img, d1 * fw, 0, fw, fh, Math.floor(x - dw), Math.floor(y - dh / 2), dw, dh);
              bCtx.drawImage(img, d2 * fw, 0, fw, fh, Math.floor(x), Math.floor(y - dh / 2), dw, dh);
            } else if (layer.type === 4) {
              const dIdx = curDayOfWeek % cnt;
              bCtx.drawImage(img, dIdx * fw, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
            } else {
              bCtx.drawImage(img, 0, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
            }
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

    // Actualizar botones de añadir reloj y fecha en tiempo real (reloj en vivo)
    function updateWidgetButtonsClock() {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, '0');
      const mm = String(now.getMinutes()).padStart(2, '0');
      const month = String(now.getMonth() + 1).padStart(2, '0');
      const day = String(now.getDate()).padStart(2, '0');
      const days = ["DOM", "LUN", "MAR", "MIE", "JUE", "VIE", "SAB"];
      const dayName = days[now.getDay()];

      const bClock = document.getElementById('btn-add-clock');
      if (bClock) bClock.innerHTML = `<svg class="w-3.5 h-3.5 text-[#1f8fff] inline-block mr-1.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>DIGITAL CLOCK (${hh}:${mm})`;

      const bDate = document.getElementById('btn-add-date');
      if (bDate) bDate.innerHTML = `<svg class="w-3.5 h-3.5 text-[#1f8fff] inline-block mr-1.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>DATE & DAY (${dayName} ${month}/${day})`;
    }

    // Bucle de renderizado para animaciones en vivo sincronizado a 30 FPS
    setInterval(() => {
      const hasAnim = project.layers.some(l => l.type === 0x13 && l.count > 1);
      if (hasAnim) {
        drawCanvas();
      }
    }, 33);

    // Bucle para actualizar reloj y fecha cada segundo
    setInterval(() => {
      updateWidgetButtonsClock();
      const hasClockOrDate = project.layers.some(l => l.mode === 4 || [1, 2, 3, 4, 5, 6, 7].includes(l.type));
      if (hasClockOrDate) {
        drawCanvas();
      }
    }, 1000);
    updateWidgetButtonsClock();

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
        if (!l.raw_image_b64 && l.image_data) l.raw_image_b64 = l.image_data;
        if (!l._rawImgB64 && l.raw_image_b64) l._rawImgB64 = l.raw_image_b64;
        if (!l.orig_w && l.width) l.orig_w = l.width;
        if (!l.orig_h && l.height) l.orig_h = l.height;
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
        item.className = `p-2 rounded border text-xs font-mono flex items-center justify-between cursor-pointer transition ${isSel ? 'border-[#1f8fff] bg-[#1f8fff]/15 text-white' : 'border-[#212633] bg-[#090b10] text-[#7f889b] hover:border-[#333a4d] hover:text-white'}`;
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
            el.className = 'px-2 py-0.5 bg-[#1f8fff] text-white rounded text-[10px] font-mono font-bold border border-[#4da7ff]';
          } else {
            el.className = 'px-2 py-0.5 bg-[#1f8fff] text-white rounded text-[10px] font-mono font-bold border border-[#4da7ff]';
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
            el.className = 'px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs border border-transparent transition';
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

      // Slider de resolución interna de imagen
      const curRes = l.res_pct || 100;
      const slRes = document.getElementById('sl-res-pct');
      if (slRes) {
        slRes.value = curRes;
      }
      // Indicador y botón de Pantalla Completa (320×172)
      const fsBtn = document.getElementById('btn-toggle-fullscreen');
      const fsTxt = document.getElementById('txt-fullscreen-status');
      if (fsBtn && fsTxt) {
        if (l.fit_screen) {
          fsBtn.className = 'w-full p-1.5 bg-blue-600 hover:bg-blue-500 border border-blue-400 text-white rounded-lg font-bold text-xs flex items-center justify-center gap-1.5 shadow-md shadow-blue-900/50 transition';
          fsTxt.innerText = 'ON (320×172)';
          fsTxt.className = 'font-extrabold text-blue-100';
        } else {
          fsBtn.className = 'w-full p-1.5 bg-slate-800 hover:bg-slate-700 border border-white/10 text-slate-300 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition';
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
        if (l.fit_screen) {
          l.fit_screen = false;
          const fsTxt = document.getElementById('txt-fullscreen-status');
          if (fsTxt) { fsTxt.innerText = 'OFF'; fsTxt.className = 'font-bold text-gray-400'; }
          const fsBtn = document.getElementById('btn-toggle-fullscreen');
          if (fsBtn) { fsBtn.className = 'w-full p-1.5 bg-slate-800 hover:bg-slate-700 border border-white/10 text-slate-300 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition'; }
        }
        l[axis] = parseInt(val) || 0;
        drawCanvas();
        updateLayersListUI();
      }
    }

    function alignSelected(pos) {
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (!l) return;
      if (l.fit_screen) {
        l.fit_screen = false;
        const fsTxt = document.getElementById('txt-fullscreen-status');
        if (fsTxt) { fsTxt.innerText = 'OFF'; fsTxt.className = 'font-bold text-gray-400'; }
        const fsBtn = document.getElementById('btn-toggle-fullscreen');
        if (fsBtn) { fsBtn.className = 'w-full p-1.5 bg-slate-800 hover:bg-slate-700 border border-white/10 text-slate-300 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition'; }
      }
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

      // Preservar siempre las dimensiones originales nativas para reescalado bidireccional perfecto
      if (!l.orig_w) {
        l.orig_w = l.orig_video_w || l.target_w || (l.width ? Math.floor(l.width / cnt) : 320);
      }
      if (!l.orig_h) {
        l.orig_h = l.orig_video_h || l.target_h || (l.height || 172);
      }
      if (!l.fit_screen && l.orig_w > 320) {
        l.orig_h = Math.max(1, Math.round(l.orig_h * (320 / l.orig_w)));
        l.orig_w = 320;
      }

      if (l.fit_screen) {
        l.target_w = 320;
        l.target_h = 172;
        l.x = 160;
        l.y = 86;
      } else {
        l.target_w = l.orig_w;
        l.target_h = l.orig_h;
      }
      let targetW = l.target_w;
      let targetH = l.target_h;

      if (l.type === 0x13 && (l.raw_gif_b64 || l._rawGifB64)) {
        if (!l.raw_gif_b64) l.raw_gif_b64 = l._rawGifB64;
        l._rawGifB64 = l.raw_gif_b64;
        const rawGif = l.raw_gif_b64;
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
            l.orig_w = data.orig_w;
            l.orig_h = data.orig_h;
          }
          if (data.duration) l.frame_duration_ms = data.duration;
          if (data.total_duration) l.total_duration_ms = data.total_duration;

          // Sincronizar automáticamente el intervalo de frames en hardware (extra[2]):
          if (!l.extra) l.extra = [0, 0, 15, 0, 0, 0, 0, 0];
          const calculatedTicks = Math.max(5, Math.min(50, Math.round((l.frame_duration_ms || 100) / 10)));
          l.extra[2] = calculatedTicks;

          selectLayer(l.id);
          drawCanvas();
        } catch (e) {
          alert('Error aplicando procesamiento a la animación: ' + e.message);
        }
      } else if (l.raw_image_b64 || l._rawImgB64 || l.image_data) {
        // Asegurar que siempre se mantenga la imagen original en alta definición sin degradación
        if (!l.raw_image_b64) {
          l.raw_image_b64 = l._rawImgB64 || l.image_data;
        }
        l._rawImgB64 = l.raw_image_b64;
        const rawSrc = l.raw_image_b64;
        try {
          const res = await fetch('/api/themes/optimize_image', {
            method: 'POST',
            body: JSON.stringify({
              image_data: rawSrc,
              target_w: targetW,
              target_h: targetH,
              mode: l.fit_screen ? 'cover' : 'fit_exact',
              res_pct: l.res_pct || 100,
              max_colors: l.max_colors || 256
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
        l.target_w = l.orig_w || l.orig_video_w || l._origVideoW || Math.floor(imWidth(l) / cnt);
        l.target_h = l.orig_h || l.orig_video_h || l._origVideoH || imHeight(l);
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

    // Eventos del slider interactivo de resolución
    function onResolutionSliderInput(val) {
      const resInfo = document.getElementById('txt-res-pct-info');
      const l = project.layers.find(x => x.id === selectedLayerId);
      if (resInfo && l) {
        const cnt = l.count || 1;
        const baseW = l.fit_screen ? 320 : (l.orig_w || l.orig_video_w || l.target_w || Math.floor(imWidth(l) / cnt));
        const baseH = l.fit_screen ? 172 : (l.orig_h || l.orig_video_h || l.target_h || imHeight(l));
        const estW = Math.max(16, Math.round(baseW * parseInt(val) / 100));
        const estH = Math.max(16, Math.round(baseH * parseInt(val) / 100));
        resInfo.innerText = `${val}% (~${estW}×${estH} px)`;
      } else if (resInfo) {
        resInfo.innerText = `${val}%`;
      }
    }

    async function onResolutionSliderChange(val) {
      const pct = parseInt(val);
      if (!isNaN(pct)) {
        await compressInternalResolution(pct);
      }
    }

    // 3. Bajar la resolución en porcentaje (Pixel Binning) acumulativo con colores y tamaño
    async function compressInternalResolution(resPct = 100) {
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
        if (l.fit_screen) {
          l.fit_screen = false;
          const fsTxt = document.getElementById('txt-fullscreen-status');
          if (fsTxt) { fsTxt.innerText = 'OFF'; fsTxt.className = 'font-bold text-gray-400'; }
          const fsBtn = document.getElementById('btn-toggle-fullscreen');
          if (fsBtn) { fsBtn.className = 'w-full p-1.5 bg-slate-800 hover:bg-slate-700 border border-white/10 text-slate-300 rounded-lg font-semibold text-xs flex items-center justify-center gap-1.5 transition'; }
        }
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
      reader.onload = async (evt) => {
        const rawB64 = evt.target.result;
        try {
          // Optimizar y reescalar en backend si el ancho supera 320 px (usando filtrado Lanczos suave)
          const res = await fetch('/api/themes/optimize_image', {
            method: 'POST',
            body: JSON.stringify({
              image_data: rawB64,
              max_colors: 256,
              res_pct: 100
            })
          });
          const data = await res.json();
          const finalB64 = data.success ? data.image_data : rawB64;

          const im = new Image();
          im.onload = () => {
            // Analizar si la imagen contiene píxeles con transparencia real
            let imgHasAlpha = false;
            try {
              const testCanvas = document.createElement('canvas');
              testCanvas.width = Math.min(im.width, 320);
              testCanvas.height = Math.min(im.height, 172);
              const tCtx = testCanvas.getContext('2d');
              tCtx.drawImage(im, 0, 0, testCanvas.width, testCanvas.height);
              const imgData = tCtx.getImageData(0, 0, testCanvas.width, testCanvas.height);
              for (let idx = 3; idx < imgData.data.length; idx += 4) {
                if (imgData.data[idx] < 250) {
                  imgHasAlpha = true;
                  break;
                }
              }
            } catch (e) {
              imgHasAlpha = (kind !== 'background');
            }

            if (kind === 'background') {
              // Si la imagen tiene transparencia, no forzar modo fondo opaco
              const l = {
                id: 'layer_bg_' + Date.now(),
                name: imgHasAlpha ? 'Imagen PNG Transparente' : 'Fondo Pantalla (320×172)',
                type: 0x10, mode: 2, count: 1, fmt: 1,
                x: 160, y: 86, has_alpha: imgHasAlpha,
                fit_screen: !imgHasAlpha,
                width: im.width, height: im.height,
                orig_w: imgHasAlpha ? im.width : 320,
                orig_h: imgHasAlpha ? im.height : 172,
                target_w: imgHasAlpha ? im.width : 320,
                target_h: imgHasAlpha ? im.height : 172,
                image_data: finalB64,
                raw_image_b64: finalB64,
                _rawImgB64: finalB64,
                res_pct: 100, max_colors: 256,
                extra: [0, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: im
              };
              project.layers.unshift(l);
              selectLayer(l.id);
            } else {
              // Personaje superpuesto (Efecto profundidad, conserva sus dimensiones y posición)
              const l = {
                id: 'layer_overlay_' + Date.now(),
                name: 'Personaje PNG (Profundidad)',
                type: 0x10, mode: 2, count: 1, fmt: 1,
                x: 160, y: 86, has_alpha: imgHasAlpha,
                fit_screen: false,
                width: im.width, height: im.height,
                orig_w: im.width,
                orig_h: im.height,
                target_w: im.width,
                target_h: im.height,
                image_data: finalB64,
                raw_image_b64: finalB64,
                _rawImgB64: finalB64,
                res_pct: 100, max_colors: 256,
                extra: [0, 0, 0, 0, 0, 0, 0, 0], visible: true, _imgObj: im
              };
              project.layers.push(l);
              selectLayer(l.id);
            }
            updateLayersListUI();
            drawCanvas();
          };
          im.src = finalB64;
        } catch (err) {
          alert('Error cargando imagen: ' + err.message);
        }
      };
      reader.readAsDataURL(file);
      e.target.value = '';
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
        card.className = 'panel-chassis p-3 flex flex-col justify-between space-y-2 hover:border-[#1f8fff]/60 transition font-mono';
        card.innerHTML = `
          <div class="w-full h-24 bg-black screen-chassis rounded overflow-hidden flex items-center justify-center">
            ${item.thumbnail_url ? `<img src="${item.thumbnail_url}" class="w-full h-full object-cover">` : '<span class="text-[10px] text-[#525a6c]">NO PREVIEW</span>'}
          </div>
          <div class="w-full flex justify-between items-center text-[10px] text-[#7f889b] pt-1 border-t border-[#1e2330]">
            <span class="truncate font-mono">${item.id.split('_')[0]}</span>
            <span class="font-mono">${Math.round(item.size_bytes / 1024)} KB</span>
          </div>
          <div class="grid grid-cols-2 gap-1.5 w-full pt-0.5">
            <button onclick="loadOfficialToStudio('${item.id}')" class="py-1 px-1.5 btn-tactile text-[10px]">
              EDIT
            </button>
            <button onclick="uploadOfficialDirect('${item.id}')" class="py-1 px-1.5 btn-action-azure text-[10px]">
              FLASH
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
