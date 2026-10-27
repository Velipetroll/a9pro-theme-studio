#!/usr/bin/env python3
"""
Herramienta de conexión BLE (L2CAP ATT directo), decodificador/codificador RLE 'PA' (con canal alfa)
y creador de temas personalizados (.bin) para A9 Pro Box.
Ingeniería inversa realizada sobre com.zxl.mate (v3.0.18) y los 80 temas oficiales del servidor.

Formato de Sprite Comprimido 'PA' (descubierto al 100%):
  Cabecera de 16 bytes:
    - [0..1]:  'PA' (0x50, 0x41)
    - [2]:     0x00
    - [3]:     has_alpha (0 = opaco RGB565, 1 = con transparencia ARGB8565)
    - [4..5]:  w (uint16 LE) - En modo transpuesto, corresponde al alto visual de la imagen
    - [6..7]:  h (uint16 LE) - En modo transpuesto, corresponde al ancho visual (multiplicado por n_frames)
    - [8]:     tile_step (0 = compresión por filas h de ancho w; 30 = compresión por bloques 31x31 para agujas)
    - [9]:     off_bytes (1 = tabla de offsets int8; 2 = tabla de offsets int16 LE)
    - [10..11]: avg_len (uint16 LE) - Longitud media por fila/bloque para calcular: offset[i] = i * avg_len + table[i]
    - [12..15]: 0x00000000

  Opcodes del flujo RLE por cada fila/bloque:
    - 0x00..0x7F ((op & 0x80) == 0):
        1 píxel con diferencia (delta) respecto al píxel anterior (manteniendo el mismo alfa):
          dR5 = ((op >> 5) & 0x03) - 2   (rango -2..+1)
          dG6 = ((op >> 2) & 0x07) - 4   (rango -4..+3)
          dB5 = (op & 0x03) - 2          (rango -2..+1)
        Nota: 0x52 (0b0_10_100_10) equivale a (dR=0, dG=0, dB=0), es decir, repetir el píxel anterior.
    - 0x80..0xBF ((op & 0xC0) == 0x80):
        Ráfaga (run) de `count = op & 0x3F` (1..63) píxeles idénticos:
          - Si has_alpha == 1: seguido de 3 bytes: [alpha (uint8), color565_lo, color565_hi]
          - Si has_alpha == 0: seguido de 2 bytes: [color565_lo, color565_hi]
    - 0xC0..0xFF ((op & 0xC0) == 0xC0):
        Secuencia literal de `count = op & 0x3F` (1..63) píxeles sin comprimir (manteniendo alfa anterior o 0xFF):
          - Seguido de `count * 2` bytes (valores uint16 LE RGB565).
"""

import argparse
import asyncio
import json
import math
import socket
import struct
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None

DEFAULT_MAC = "41:42:6C:C2:DD:3C"
SERVICE_UUID = "0000fdb3-0000-1000-8000-00805f9b34fb"
COMPANY_ID = 0x0642

HANDLE_RX_NOTIFY = 0x0003
HANDLE_RX_CCCD = 0x0004
HANDLE_TX_RESP = 0x0006
HANDLE_TX = 0x0008

ATT_OP_MTU_REQ = 0x02
ATT_OP_MTU_RSP = 0x03
ATT_OP_WRITE_REQ = 0x12
ATT_OP_WRITE_RSP = 0x13
ATT_OP_WRITE_CMD = 0x52
ATT_OP_HANDLE_NOTIFY = 0x1B

CMD_DEVICE_INFO = 0x27
CMD_FACE_SYNC = 0x87
CMD_TIMESTAMP = 0x93

ITEM_TYPE_NAMES = {
    0x01: "Año (YYYY)",
    0x02: "Mes (MM)",
    0x03: "Día (DD)",
    0x04: "Día de semana (Lun..Dom)",
    0x05: "Horas (HH / Aguja Hora)",
    0x06: "Minutos (MM / Aguja Min)",
    0x07: "Segundos (SS / Aguja Seg)",
    0x08: "Hora Decenas (H1)",
    0x09: "Hora Unidades (H2)",
    0x0A: "Minuto Decenas (M1)",
    0x0B: "Minuto Unidades (M2)",
    0x10: "Imagen / Capa Estática",
    0x11: "Miniatura (Thumbnail)",
    0x13: "Animación (Sprite en bucle)",
}


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    """Convierte RGB888 (8 bits por canal) a RGB565 (16 bits)."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | ((b & 0xF8) >> 3)


def rgb565_to_rgb888(val: int) -> Tuple[int, int, int]:
    """Convierte RGB565 (16 bits) a RGB888 (8 bits por canal)."""
    r5 = (val >> 11) & 0x1F
    g6 = (val >> 5) & 0x3F
    b5 = val & 0x1F
    r = (r5 << 3) | (r5 >> 2)
    g = (g6 << 2) | (g6 >> 4)
    b = (b5 << 3) | (b5 >> 2)
    return r, g, b


def hex_to_rgb565(hex_color: str) -> int:
    """Convierte una cadena '#RRGGBB' a entero uint16 RGB565."""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Color inválido '{hex_color}', usa formato #RRGGBB")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return rgb888_to_rgb565(r, g, b)


def _decode_pa_stream(row_bytes: bytes, has_alpha: int) -> List[Tuple[int, int, int, int]]:
    """Decodifica una fila o bloque RLE del formato PA a lista de píxeles (R, G, B, A)."""
    out: List[Tuple[int, int, int, int]] = []
    idx = 0
    r5, g6, b5 = 0, 0, 0
    alpha = 0 if has_alpha else 255

    while idx < len(row_bytes):
        op = row_bytes[idx]
        idx += 1
        if (op & 0x80) == 0:
            dr = ((op >> 5) & 0x03) - 2
            dg = ((op >> 2) & 0x07) - 4
            db = (op & 0x03) - 2
            r5 = (r5 + dr) & 0x1F
            g6 = (g6 + dg) & 0x3F
            b5 = (b5 + db) & 0x1F
            c565 = (r5 << 11) | (g6 << 5) | b5
            r, g, b = rgb565_to_rgb888(c565)
            out.append((r, g, b, alpha))
        elif (op & 0xC0) == 0x80:
            cnt = op & 0x3F
            if has_alpha:
                alpha = row_bytes[idx]
                c565 = row_bytes[idx + 1] | (row_bytes[idx + 2] << 8)
                idx += 3
            else:
                alpha = 255
                c565 = row_bytes[idx] | (row_bytes[idx + 1] << 8)
                idx += 2
            r5 = (c565 >> 11) & 0x1F
            g6 = (c565 >> 5) & 0x3F
            b5 = c565 & 0x1F
            r, g, b = rgb565_to_rgb888(c565)
            out.extend([(r, g, b, alpha)] * cnt)
        else:
            cnt = op & 0x3F
            for _ in range(cnt):
                c565 = row_bytes[idx] | (row_bytes[idx + 1] << 8)
                idx += 2
                r5 = (c565 >> 11) & 0x1F
                g6 = (c565 >> 5) & 0x3F
                b5 = c565 & 0x1F
                r, g, b = rgb565_to_rgb888(c565)
                out.append((r, g, b, alpha))
    return out


def decode_pa_to_image(chunk: bytes, untrack_transpose: bool = True) -> "Image.Image":
    """
    Decodifica un bloque 'PA' (tanto normal c8=0 como de agujas por bloques c8=30)
    y devuelve un objeto PIL.Image (RGBA).
    En A9 Pro Box (320x172), las imágenes PA vienen transpuestas (w=alto visual, h=ancho visual):
    si untrack_transpose=True, devuelve la imagen ya enderezada al derecho.
    """
    if Image is None:
        raise RuntimeError("Se requiere Pillow. Instálalo con: pip install Pillow")
    if chunk[:2] != b"PA":
        raise ValueError(f"Cabecera PA inválida: {chunk[:4]!r}")

    _f0, has_alpha = chunk[2], chunk[3]
    w, h = struct.unpack_from("<HH", chunk, 4)
    tile_step, off_bytes = chunk[8], chunk[9]
    avg_len = struct.unpack_from("<H", chunk, 10)[0]
    fmt = "<b" if off_bytes == 1 else "<h"

    if tile_step == 0:
        n_entries = h
        table = [struct.unpack_from(fmt, chunk, 16 + i * off_bytes)[0] for i in range(n_entries + 1)]
        rle = chunk[16 + (n_entries + 1) * off_bytes :]

        raw_img = Image.new("RGBA", (w, h))
        px_access = raw_img.load()
        for r in range(h):
            s = r * avg_len + table[r]
            e = (r + 1) * avg_len + table[r + 1]
            row_pixels = _decode_pa_stream(rle[s:e], has_alpha)
            for c, rgba in enumerate(row_pixels[:w]):
                px_access[c, r] = rgba
    else:
        # Modo de bloques (ej. tile_step = 30 -> bloques de 31x31 para agujas analógicas)
        tw = math.ceil(w / tile_step)
        th = math.ceil(h / tile_step)
        n_entries = tw * th
        tile_side = tile_step + 1
        table = [struct.unpack_from(fmt, chunk, 16 + i * off_bytes)[0] for i in range(n_entries + 1)]
        rle = chunk[16 + (n_entries + 1) * off_bytes :]

        raw_img = Image.new("RGBA", (w, h))
        px_access = raw_img.load()
        for idx_tile in range(n_entries):
            ty = idx_tile // tw
            tx = idx_tile % tw
            s = idx_tile * avg_len + table[idx_tile]
            e = (idx_tile + 1) * avg_len + table[idx_tile + 1]
            tile_pixels = _decode_pa_stream(rle[s:e], has_alpha)
            for p_idx, rgba in enumerate(tile_pixels):
                ly = p_idx // tile_side
                lx = p_idx % tile_side
                gx = tx * tile_step + lx
                gy = ty * tile_step + ly
                if gx < w and gy < h:
                    px_access[gx, gy] = rgba

    if not untrack_transpose:
        return raw_img

    # Deshacer la transposición (en PA: x_orig = h - 1 - r, y_orig = w - 1 - c)
    # Es decir, rotar 90 grados en sentido horario (o flip ambos ejes + transpose)
    upright = Image.new("RGBA", (h, w))
    src_px = raw_img.load()
    dst_px = upright.load()
    for r in range(h):
        for c in range(w):
            dst_px[r, w - 1 - c] = src_px[c, r]
    return upright


def _encode_pa_row(pixels_rgba: List[Tuple[int, int, int, int]], has_alpha: bool) -> bytes:
    """
    Codifica una fila de píxeles (R, G, B, A) en el flujo RLE del formato 'PA'.
    Usa ráfagas (0x80 | cnt), deltas de 1 byte (0x00..0x7F) y literales (0xC0 | cnt).
    """
    conv = []
    for r, g, b, a in pixels_rgba:
        if has_alpha and a == 0:
            conv.append((0, 0))
        else:
            c565 = rgb888_to_rgb565(r, g, b)
            conv.append((a if has_alpha else 255, c565))

    out = bytearray()
    n = len(conv)
    i = 0
    prev_a = -1
    prev_c = -1

    while i < n:
        cur_a, cur_c = conv[i]
        run_len = 1
        while i + run_len < n and conv[i + run_len] == (cur_a, cur_c) and run_len < 63:
            run_len += 1

        if run_len >= 2:
            out.append(0x80 | run_len)
            if has_alpha:
                out.append(cur_a & 0xFF)
            out.append(cur_c & 0xFF)
            out.append((cur_c >> 8) & 0xFF)
            prev_a, prev_c = cur_a, cur_c
            i += run_len
            continue

        if cur_a == prev_a and prev_c >= 0:
            pr5, pg6, pb5 = (prev_c >> 11) & 0x1F, (prev_c >> 5) & 0x3F, prev_c & 0x1F
            cr5, cg6, cb5 = (cur_c >> 11) & 0x1F, (cur_c >> 5) & 0x3F, cur_c & 0x1F
            dr, dg, db = cr5 - pr5, cg6 - pg6, cb5 - pb5
            if -2 <= dr <= 1 and -4 <= dg <= 3 and -2 <= db <= 1:
                op = ((dr + 2) << 5) | ((dg + 4) << 2) | (db + 2)
                out.append(op)
                prev_c = cur_c
                i += 1
                continue

        # Look-ahead for literal sequence of varying pixels
        lit_len = 1
        while (i + lit_len < n and lit_len < 63 and
               (not has_alpha or conv[i + lit_len][0] == cur_a) and
               (i + lit_len + 1 >= n or conv[i + lit_len] != conv[i + lit_len + 1])):
            lit_len += 1

        if lit_len >= 2:
            if has_alpha and cur_a != prev_a:
                out.append(0x80 | 1)
                out.append(cur_a & 0xFF)
                out.append(cur_c & 0xFF)
                out.append((cur_c >> 8) & 0xFF)
                prev_a, prev_c = cur_a, cur_c
                i += 1
                lit_len -= 1
            if lit_len > 0:
                out.append(0xC0 | lit_len)
                for k in range(lit_len):
                    c = conv[i + k][1]
                    out.append(c & 0xFF)
                    out.append((c >> 8) & 0xFF)
                prev_c = conv[i + lit_len - 1][1]
                i += lit_len
            continue

        # Fallback single pixel run
        out.append(0x80 | 1)
        if has_alpha:
            out.append(cur_a & 0xFF)
        out.append(cur_c & 0xFF)
        out.append((cur_c >> 8) & 0xFF)
        prev_a, prev_c = cur_a, cur_c
        i += 1

    return bytes(out)


def encode_image_to_pa(img: "Image.Image", has_alpha: bool = True, is_transpose: bool = True) -> bytes:
    """
    Comprime cualquier imagen PIL (con o sin transparencia) al formato nativo 'PA' de la A9 Pro Box.
    """
    rgba = img.convert("RGBA")
    orig_w, orig_h = rgba.size
    src_px = rgba.load()

    rows_data: List[bytes] = []
    if is_transpose:
        pa_w, pa_h = orig_h, orig_w
        for x in range(orig_w):
            row_pixels = [src_px[x, y] for y in range(orig_h - 1, -1, -1)]
            rows_data.append(_encode_pa_row(row_pixels, has_alpha=has_alpha))
    else:
        pa_w, pa_h = orig_w, orig_h
        for y in range(orig_h - 1, -1, -1):
            row_pixels = [src_px[x, y] for x in range(orig_w)]
            rows_data.append(_encode_pa_row(row_pixels, has_alpha=has_alpha))

    total_rle = sum(len(r) for r in rows_data)
    avg_len = round(total_rle / pa_h) if pa_h > 0 else 0

    offsets = [0]
    acc = 0
    for r in rows_data:
        acc += len(r)
        offsets.append(acc)

    residuals = [offsets[i] - i * avg_len for i in range(pa_h + 1)]
    if all(-128 <= res <= 127 for res in residuals):
        off_bytes = 1
        table_bytes = b"".join(struct.pack("<b", res) for res in residuals)
    else:
        off_bytes = 2
        table_bytes = b"".join(struct.pack("<h", res) for res in residuals)

    hdr = bytearray(16)
    hdr[0:2] = b"PA"
    hdr[2] = 0
    hdr[3] = 1 if has_alpha else 0
    struct.pack_into("<HH", hdr, 4, pa_w, pa_h)
    hdr[8] = 0
    hdr[9] = off_bytes
    struct.pack_into("<H", hdr, 10, avg_len)
    return bytes(hdr) + table_bytes + b"".join(rows_data)


def image_to_rgb565_bytes(img: "Image.Image", is_transpose: bool = True) -> Tuple[bytes, int, int]:
    """Convierte una imagen PIL a bytes RGB565 little-endian para BMP sin comprimir."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    pixels = rgb.load()
    out = bytearray()

    if not is_transpose:
        for y in range(h - 1, -1, -1):
            for x in range(w):
                r, g, b = pixels[x, y]
                val = rgb888_to_rgb565(r, g, b)
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out), w, h
    else:
        for x in range(w - 1, -1, -1):
            for y in range(h - 1, -1, -1):
                r, g, b = pixels[x, y]
                val = rgb888_to_rgb565(r, g, b)
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
        return bytes(out), h, w


def make_bmp_v3_rgb565(pixel_bytes: bytes, w: int, h: int) -> bytes:
    """Construye el encabezado BMP V3 de 70 bytes (0x46) con máscaras BI_BITFIELDS RGB565."""
    file_size = len(pixel_bytes) + 70
    header = bytearray(70)
    header[0:2] = b"BM"
    struct.pack_into("<I", header, 2, file_size)
    struct.pack_into("<I", header, 6, 0)
    struct.pack_into("<I", header, 10, 70)
    struct.pack_into("<I", header, 14, 56)
    struct.pack_into("<i", header, 18, w)
    struct.pack_into("<i", header, 22, h)
    struct.pack_into("<H", header, 26, 1)
    struct.pack_into("<H", header, 28, 16)
    struct.pack_into("<I", header, 30, 3)
    struct.pack_into("<I", header, 34, len(pixel_bytes))
    struct.pack_into("<I", header, 38, 0x00000B12)
    struct.pack_into("<I", header, 42, 0x00000B12)
    struct.pack_into("<I", header, 46, 0)
    struct.pack_into("<I", header, 50, 0)
    struct.pack_into("<I", header, 54, 0x0000F800)
    struct.pack_into("<I", header, 58, 0x000007E0)
    struct.pack_into("<I", header, 62, 0x0000001F)
    struct.pack_into("<I", header, 66, 0x00000000)
    return bytes(header) + pixel_bytes


def build_dial_header(
    width: int,
    height: int,
    align: int,
    text_color_565: int,
    total_size: int,
    num_items: int = 2,
    is_usr: bool = True,
) -> bytes:
    """Construye el encabezado principal de 52 bytes (0x34) del archivo .bin."""
    hdr = bytearray(52)
    hdr[0:8] = b"WF\x02\x04USR\x00" if is_usr else b"WF\x02\x04\x00\x00\x00\x00"
    hdr[8] = align & 0xFF
    struct.pack_into("<H", hdr, 9, text_color_565 & 0xFFFF)
    hdr[40] = 0x80
    hdr[41] = 0x00
    struct.pack_into("<H", hdr, 42, width & 0xFFFF)
    struct.pack_into("<H", hdr, 44, height & 0xFFFF)
    hdr[46] = num_items & 0xFF
    hdr[47] = 0
    struct.pack_into("<I", hdr, 48, total_size & 0xFFFFFFFF)
    return bytes(hdr)


def build_item_descriptor(
    item_type: int,
    offset: int,
    x: int,
    y: int,
    mode: int = 2,
    count: int = 1,
    fmt: int = 0,
    extra: bytes = b"\x00" * 8,
) -> bytes:
    """Construye un descriptor de ítem de 20 bytes (0x14)."""
    desc = bytearray(20)
    struct.pack_into("<I", desc, 0, offset & 0xFFFFFFFF)
    desc[4] = mode & 0xFF
    desc[5] = item_type & 0xFF
    desc[6] = count & 0xFF
    desc[7] = fmt & 0xFF
    struct.pack_into("<H", desc, 8, x & 0xFFFF)
    struct.pack_into("<H", desc, 10, y & 0xFFFF)
    desc[12:20] = extra[:8].ljust(8, b"\x00")
    return bytes(desc)


def build_custom_dial_bin(
    bg_path: Path,
    width: int = 320,
    height: int = 172,
    color_hex: str = "#FFFFFF",
    align: int = 2,
    thumb_path: Optional[Path] = None,
    thumb_max_side: int = 200,
    custom_x: Optional[int] = None,
    custom_y: Optional[int] = None,
    is_transpose: bool = True,
    no_clock: bool = False,
) -> bytes:
    """
    Genera un archivo de tema .bin ('WF\\x02\\x04USR\\x00' o 'WF\\x02\\x04\\x00\\x00\\x00\\x00' si no_clock=True).
    Si no_clock=True, empaqueta en formato 'PA' comprimido con cabecera nativa sin ningún widget de reloj,
    logrando un fondo de pantalla 100% limpio sin números encima.
    """
    if Image is None:
        raise RuntimeError("Se requiere Pillow. Instálalo con: pip install Pillow")

    bg_img = Image.open(bg_path).convert("RGBA")
    if bg_img.size != (width, height):
        bg_img = bg_img.resize((width, height), Image.Resampling.LANCZOS)

    if thumb_path is not None:
        thumb_img = Image.open(thumb_path).convert("RGBA")
    else:
        thumb_img = bg_img.copy()

    if width >= height:
        tw = thumb_max_side
        th = round(thumb_max_side * height / width)
    else:
        th = thumb_max_side
        tw = round(thumb_max_side * width / height)

    if thumb_img.size != (tw, th):
        thumb_img = thumb_img.resize((tw, th), Image.Resampling.LANCZOS)

    x1 = custom_x if custom_x is not None else (width // 2)
    y1 = custom_y if custom_y is not None else (height // 2)

    if no_clock:
        pa_bg = encode_image_to_pa(bg_img, has_alpha=False, is_transpose=is_transpose)
        pa_thumb = encode_image_to_pa(thumb_img, has_alpha=True, is_transpose=is_transpose)
        item1 = build_item_descriptor(item_type=0x10, offset=92, x=x1, y=y1, mode=2, count=1, fmt=1)
        item2 = build_item_descriptor(
            item_type=0x11, offset=92 + len(pa_bg), x=width // 2, y=height // 2, mode=2, count=1, fmt=1
        )
        total_size = 92 + len(pa_bg) + len(pa_thumb)
        header = build_dial_header(width, height, 7, 0x0000, total_size, num_items=2, is_usr=False)
        return header + item1 + item2 + pa_bg + pa_thumb

    img1_raw, w1, h1 = image_to_rgb565_bytes(bg_img, is_transpose=is_transpose)
    img2_raw, w2, h2 = image_to_rgb565_bytes(thumb_img, is_transpose=is_transpose)

    bmp1 = make_bmp_v3_rgb565(img1_raw, w1, h1)
    bmp2 = make_bmp_v3_rgb565(img2_raw, w2, h2)

    total_size_field = 92 + len(img1_raw) + len(img2_raw)
    text_color_565 = hex_to_rgb565(color_hex)

    header = build_dial_header(width, height, align, text_color_565, total_size_field, num_items=2, is_usr=True)
    item1 = build_item_descriptor(item_type=0x10, offset=92, x=x1, y=y1, mode=2, count=1, fmt=0)
    item2 = build_item_descriptor(item_type=0x11, offset=92 + len(bmp1), x=0, y=0, mode=2, count=1, fmt=0)

    return header + item1 + item2 + bmp1 + bmp2


def inspect_dial_bin(bin_path: Path, extract_dir: Optional[Path] = None) -> None:
    """
    Inspecciona cualquier archivo .bin de esfera y extrae TODAS sus capas (tanto BMP como PA con canal alfa)
    a imágenes PNG listas para ver o editar.
    """
    data = bin_path.read_bytes()
    print(f"=== Archivo: {bin_path} ({len(data)} bytes) ===")
    if len(data) < 52:
        print("Archivo demasiado corto para tener cabecera WF.")
        return

    magic = data[0:8]
    align = data[8]
    color565 = struct.unpack_from("<H", data, 9)[0]
    width, height = struct.unpack_from("<HH", data, 42)
    num_items = data[46]
    total_size = struct.unpack_from("<I", data, 48)[0]

    r, g, b = rgb565_to_rgb888(color565)

    print(f"Magic:          {magic!r}")
    print(f"Resolución:     {width}x{height}")
    print(f"Posición/Align: {align}")
    print(f"Color Reloj:    0x{color565:04X} (~ #{r:02X}{g:02X}{b:02X})")
    print(f"Núm. Ítems:     {num_items}")
    print(f"TotalSize Hdr:  {total_size}")

    items = []
    for i in range(num_items):
        pos = 52 + i * 20
        if pos + 20 > len(data):
            break
        offset = struct.unpack_from("<I", data, pos)[0]
        mode, item_type, count, fmt = data[pos + 4 : pos + 8]
        x, y = struct.unpack_from("<HH", data, pos + 8)
        extra = data[pos + 12 : pos + 20]
        items.append((i, item_type, offset, mode, count, fmt, x, y, extra))

    all_offsets = sorted(set([it[2] for it in items] + [len(data)]))
    offset_to_size = {all_offsets[k]: all_offsets[k + 1] - all_offsets[k] for k in range(len(all_offsets) - 1)}

    for i, item_type, offset, mode, count, fmt, x, y, extra in items:
        sz = offset_to_size.get(offset, 0)
        chunk = data[offset : offset + sz]
        t_name = ITEM_TYPE_NAMES.get(item_type, f"Desconocido({item_type})")
        c_magic = chunk[:2]
        info_str = ""
        if c_magic == b"PA" and len(chunk) >= 16:
            has_a = chunk[3]
            pw, ph = struct.unpack_from("<HH", chunk, 4)
            t_step = chunk[8]
            info_str = f"PA(w={pw}, h={ph}, alpha={has_a}, step={t_step})"
        elif c_magic == b"BM" and len(chunk) >= 26:
            bw, bh = struct.unpack_from("<ii", chunk, 18)
            info_str = f"BMP(w={bw}, h={bh})"

        print(
            f"  [Ítem {i:2d}] type=0x{item_type:02X} ({t_name:28s}) | mode={mode} count={count:2d} fmt={fmt} "
            f"| x={x:3d} y={y:3d} | offset={offset:6d} ({sz:6d} B) | {info_str}"
        )

        if extract_dir:
            extract_dir.mkdir(parents=True, exist_ok=True)
            if c_magic == b"BM":
                bmp_size = struct.unpack_from("<I", chunk, 2)[0]
                out_bmp = extract_dir / f"item_{i:02d}_type_{item_type:02x}.bmp"
                out_bmp.write_bytes(chunk[:bmp_size])
                print(f"            -> Extraído BMP en {out_bmp}")
            elif c_magic == b"PA" and Image is not None:
                img = decode_pa_to_image(chunk, untrack_transpose=True)
                out_png = extract_dir / f"item_{i:02d}_type_{item_type:02x}_x{x}_y{y}_count{count}.png"
                img.save(out_png)
                print(f"            -> Decodificado PA a PNG ({img.size[0]}x{img.size[1]} RGBA) en {out_png}")


class A9ProL2CAPClient:
    """
    Cliente BLE directo por socket L2CAP ATT (CID=4, BDADDR_LE_PUBLIC=1).
    Evita el bug de BlueZ donde bluetoothd intenta usar BR/EDR debido a AdvertisingFlags=0x02.
    """

    def __init__(self, address: Optional[str] = None):
        self.address = address or DEFAULT_MAC
        self.sock: Optional[socket.socket] = None
        self.seq = 0
        self.att_mtu = 188
        self.device_mtu = 185
        self.face_mtu = 128
        self.screen_width = 320
        self.screen_height = 172
        self.screen_type = 0
        self.max_size = 153600
        self.is_rle = 1

    def _next_seq_byte(self, first_byte: int = 0) -> int:
        val = (first_byte & 0xF0) | (self.seq & 0x0F)
        self.seq = (self.seq + 1) & 0x0F
        return val

    def build_cmd(self, cmd: int, payload: bytes, offset: int = 0, size: int = 0) -> bytes:
        pkt = bytearray(5 + len(payload))
        pkt[0] = self._next_seq_byte(0)
        pkt[1] = cmd & 0xFF
        pkt[2] = 0x01
        pkt[3] = ((size & 0x0F) << 4) | (offset & 0x0F)
        pkt[4] = len(payload) & 0xFF
        pkt[5:] = payload
        return bytes(pkt)

    def connect(self, timeout: float = 8.0) -> None:
        print(f"[BLE] Conectando directamente por L2CAP ATT a {self.address}...")
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        s.settimeout(timeout)
        s.bind((socket.BDADDR_ANY, 0, 4, 1))
        s.connect((self.address, 0, 4, 1))
        self.sock = s
        print(f"[BLE] ¡Conectado exitosamente a {self.address}!")

        s.settimeout(1.5)
        try:
            init_rx = s.recv(512)
            if init_rx and init_rx[0] == ATT_OP_MTU_REQ:
                peer_mtu = struct.unpack_from("<H", init_rx, 1)[0]
                s.send(struct.pack("<BH", ATT_OP_MTU_RSP, 512))
                print(f"[BLE] Caja solicitó ATT MTU={peer_mtu}, respondido 512.")
        except socket.timeout:
            pass

        s.send(struct.pack("<BH", ATT_OP_MTU_REQ, 512))
        try:
            mtu_rsp = s.recv(512)
            if mtu_rsp and mtu_rsp[0] == ATT_OP_MTU_RSP:
                self.att_mtu = struct.unpack_from("<H", mtu_rsp, 1)[0]
                print(f"[BLE] ATT MTU acordado: {self.att_mtu} bytes")
        except socket.timeout:
            pass

        s.send(struct.pack("<BHH", ATT_OP_WRITE_REQ, HANDLE_RX_CCCD, 0x0001))
        try:
            cccd_rsp = s.recv(512)
            if cccd_rsp and cccd_rsp[0] == ATT_OP_WRITE_RSP:
                print("[BLE] Notificaciones en 0xFF18 (handle 0x0003) activadas.")
        except socket.timeout:
            pass

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def write_cmd(self, payload: bytes, handle: int = HANDLE_TX) -> None:
        assert self.sock is not None
        att_pdu = struct.pack("<BH", ATT_OP_WRITE_CMD, handle) + payload
        self.sock.send(att_pdu)

    def write_req(self, payload: bytes, handle: int = HANDLE_TX, timeout: float = 2.0) -> bool:
        assert self.sock is not None
        att_pdu = struct.pack("<BH", ATT_OP_WRITE_REQ, handle) + payload
        self.sock.send(att_pdu)
        deadline = time.time() + timeout
        self.sock.settimeout(timeout)
        while time.time() < deadline:
            try:
                pdu = self.sock.recv(512)
            except (socket.timeout, OSError):
                return False
            if not pdu:
                return False
            if pdu[0] == ATT_OP_WRITE_RSP:
                return True
            if pdu[0] == 0x01:  # ATT_OP_ERROR_RSP
                print(f"[BLE] Error en write_req: {pdu.hex()}")
                return False
            if pdu[0] == ATT_OP_MTU_REQ:
                self.sock.send(struct.pack("<BH", ATT_OP_MTU_RSP, 512))
                continue
            if pdu[0] == ATT_OP_HANDLE_NOTIFY:
                continue
        return False

    def recv_notification(self, timeout: float = 3.0) -> Optional[bytes]:
        assert self.sock is not None
        self.sock.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pdu = self.sock.recv(512)
            except socket.timeout:
                return None
            if not pdu:
                return None
            if pdu[0] == ATT_OP_MTU_REQ:
                self.sock.send(struct.pack("<BH", ATT_OP_MTU_RSP, 512))
                continue
            if pdu[0] == ATT_OP_HANDLE_NOTIFY and len(pdu) >= 3:
                return pdu[3:]
        return None

    def _parse_device_info(self, raw: bytes) -> None:
        if len(raw) < 7 or raw[1] != CMD_DEVICE_INFO:
            return
        idx = 5
        while idx + 2 <= len(raw):
            t = raw[idx]
            length = raw[idx + 1]
            sub = raw[idx + 2 : idx + 2 + length]
            if t == 0xFF and len(sub) >= 1:
                self.device_mtu = sub[0] if len(sub) == 1 else ((sub[0] << 8) | sub[1])
                self.face_mtu = 240 if self.device_mtu > 240 else 128
                print(
                    f"[BLE] MTU de protocolo reportado: {self.device_mtu} "
                    f"(FaceModel block MTU = {self.face_mtu})"
                )
            elif t == 0x80 and len(sub) >= 10:
                self.screen_width = (sub[0] << 8) | sub[1]
                self.screen_height = (sub[2] << 8) | sub[3]
                self.screen_type = sub[4]
                self.max_size = struct.unpack_from("<I", sub, 5)[0]
                self.is_rle = sub[9]
                print(
                    f"[BLE] Parámetros de pantalla A9 Pro Box:\n"
                    f"      - Resolución:       {self.screen_width} x {self.screen_height} px\n"
                    f"      - Tipo de pantalla: {self.screen_type}\n"
                    f"      - Tamaño máx .bin:  {self.max_size} bytes ({self.max_size / 1024:.1f} KB)\n"
                    f"      - Modo Transpuesto: {self.is_rle} (isRle={bool(self.is_rle)})"
                )
            idx += 2 + length

    def query_info(self) -> None:
        self.connect()
        try:
            self.write_cmd(self.build_cmd(CMD_DEVICE_INFO, bytes([0xFF, 0x00])))
            time.sleep(0.1)
            self.write_cmd(self.build_cmd(CMD_DEVICE_INFO, bytes([0x80, 0x00])))
            for _ in range(4):
                notif = self.recv_notification(timeout=1.5)
                if not notif:
                    break
                self._parse_device_info(notif)
        finally:
            self.close()



    def upload_watchface(
        self,
        bin_data: bytes,
        cmd_type: int = CMD_FACE_SYNC,
        progress_callback: Optional[Any] = None,
    ) -> bool:
        self.connect()
        try:
            self.write_cmd(self.build_cmd(CMD_DEVICE_INFO, bytes([0xFF, 0x00])))
            time.sleep(0.1)
            self.write_cmd(self.build_cmd(CMD_DEVICE_INFO, bytes([0x80, 0x00])))
            for _ in range(4):
                notif = self.recv_notification(timeout=0.8)
                if not notif:
                    break
                self._parse_device_info(notif)

            if len(bin_data) > self.max_size > 0:
                print(
                    f"[!] Advertencia: El archivo ({len(bin_data)} B) supera maxSize ({self.max_size} B)."
                )

            chunk_size = self.face_mtu - 16
            total_packages = math.ceil(len(bin_data) / chunk_size)
            print(
                f"[BLE] Iniciando transferencia de {len(bin_data)} bytes en {total_packages} paquetes "
                f"(chunk={chunk_size} B, face_mtu={self.face_mtu})..."
            )

            start_pkt = bytearray(15)
            start_pkt[0] = self._next_seq_byte(0)
            start_pkt[1] = cmd_type & 0xFF
            start_pkt[2] = 0x01
            start_pkt[3] = 0x00
            start_pkt[4] = 0x0A
            start_pkt[5] = 0x01
            struct.pack_into("<H", start_pkt, 6, total_packages)
            struct.pack_into("<I", start_pkt, 8, len(bin_data))
            struct.pack_into("<H", start_pkt, 12, self.face_mtu)
            start_pkt[14] = 0x00

            self.write_cmd(bytes(start_pkt))

            msg = None
            deadline = time.time() + 5.0
            while time.time() < deadline:
                notif = self.recv_notification(timeout=2.0)
                if notif and len(notif) >= 7 and notif[1] == cmd_type and notif[5] == 1:
                    msg = notif
                    break

            if not msg:
                print("[!] La caja no respondió al paquete de inicio (timeout).")
                return False

            resp_code = msg[6]
            if resp_code != 0:
                codes = {1: "ParameterError", 2: "Busy", 3: "OverSize"}
                print(
                    f"[!] La caja rechazó la transferencia: código {resp_code} "
                    f"({codes.get(resp_code, 'Unknown')})"
                )
                return False

            print("[BLE] ¡Transferencia aceptada por la caja! Enviando bloques...")

            pkg_index = 0
            burst_count = 0
            t0 = time.time()
            while pkg_index < total_packages:
                offset = pkg_index * chunk_size
                chunk = bin_data[offset : offset + chunk_size]
                is_last = (pkg_index + 1) == total_packages

                if is_last:
                    control = 1
                elif burst_count == 9:
                    control = 0
                else:
                    control = 3

                progress = min(100, ((pkg_index + 1) * 100) // total_packages)
                pkt = bytearray(16 + len(chunk))
                pkt[0] = self._next_seq_byte(0)
                pkt[1] = cmd_type & 0xFF
                pkt[2] = 0x01
                pkt[3] = 0x00
                pkt[4] = (len(chunk) + 11) & 0xFF
                pkt[5] = 0x02
                struct.pack_into("<H", pkt, 6, pkg_index + 1)
                struct.pack_into("<I", pkt, 8, offset)
                pkt[12] = progress & 0xFF
                pkt[13] = control

                chk = sum(chunk) & 0xFFFF
                struct.pack_into("<H", pkt, 14, chk)
                pkt[16:] = chunk

                self.write_cmd(bytes(pkt))
                pkg_index += 1
                burst_count += 1

                if control in (0, 1):
                    burst_count = 0
                    elapsed = max(0.001, time.time() - t0)
                    speed = (offset + len(chunk)) / 1024 / elapsed
                    sys.stdout.write(
                        f"\r[BLE] Progreso: {progress:3d}% ({pkg_index}/{total_packages} paquetes, {speed:.1f} KB/s)"
                    )
                    sys.stdout.flush()
                    if progress_callback:
                        try:
                            progress_callback(pkg_index, total_packages, progress)
                        except Exception:
                            pass

                    ack = None
                    ack_deadline = time.time() + 5.0
                    while time.time() < ack_deadline:
                        notif = self.recv_notification(timeout=2.5)
                        if notif and len(notif) >= 10 and notif[1] == cmd_type and notif[5] == 2:
                            ack = notif
                            break
                    if not ack:
                        print(f"\n[!] Timeout esperando confirmación en paquete {pkg_index}.")
                        return False
                    ack_code = ack[9]
                    if ack_code != 0:
                        print(f"\n[!] Error reportado por la caja en paquete {pkg_index}: code={ack_code}")
                        return False
                else:
                    time.sleep(0.004)

            print(f"\n[BLE] ¡Tema sincronizado exitosamente en {time.time() - t0:.1f} segundos!")
            return True
        finally:
            self.close()


async def scan_for_mac() -> Optional[str]:
    from bleak import BleakScanner

    print("[BLE] Buscando dirección MAC de A9 Pro Box...")
    devices = await BleakScanner.discover(timeout=4.0, return_adv=True)
    for dev, adv in devices.values():
        has_uuid = any(SERVICE_UUID.lower() in u.lower() for u in (adv.service_uuids or []))
        has_mfg = COMPANY_ID in (adv.manufacturer_data or {})
        if has_uuid or has_mfg or (dev.name and "a9" in dev.name.lower()):
            print(f"[BLE] Detectada: {dev.name} ({dev.address}) RSSI={adv.rssi}")
            return dev.address
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Herramienta BLE, decodificador/codificador PA y generador de temas para A9 Pro Box"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Subcomando: info
    p_info = sub.add_parser("info", help="Conecta y consulta la resolución/parámetros de tu A9 Pro Box")
    p_info.add_argument("--mac", default=DEFAULT_MAC, help=f"Dirección MAC (por defecto {DEFAULT_MAC})")
    p_info.add_argument("--scan", action="store_true", help="Escanear antes de conectar")

    # Subcomando: build
    p_build = sub.add_parser("build", help="Construye un archivo de tema .bin personalizado sin límites de la app")
    p_build.add_argument("--bg", required=True, type=Path, help="Imagen de fondo (PNG/JPG)")
    p_build.add_argument("--thumb", type=Path, help="Imagen miniatura opcional distinta al fondo")
    p_build.add_argument("--width", type=int, default=320, help="Ancho de pantalla (por defecto 320)")
    p_build.add_argument("--height", type=int, default=172, help="Alto de pantalla (por defecto 172)")
    p_build.add_argument("--color", default="#FFFFFF", help="Color del reloj en hex (#RRGGBB)")
    p_build.add_argument(
        "--align",
        type=int,
        default=2,
        help="Posición del reloj (1=TopLeft, 2=TopRight, 3=BottomLeft, 4=BottomRight, 5=Center)",
    )
    p_build.add_argument(
        "--no-clock",
        action="store_true",
        help="Generar fondo 100%% limpio SIN RELOJ usando formato nativo PA comprimido",
    )
    p_build.add_argument("--x", type=int, help="Coordenada X personalizada en el descriptor")
    p_build.add_argument("--y", type=int, help="Coordenada Y personalizada en el descriptor")
    p_build.add_argument(
        "--no-transpose",
        action="store_true",
        help="Desactivar orientación transpuesta (por defecto activa para A9 Pro Box isRle=1)",
    )
    p_build.add_argument("-o", "--out", required=True, type=Path, help="Ruta del archivo .bin de salida")

    # Subcomando: inspect
    p_insp = sub.add_parser("inspect", help="Inspecciona un archivo .bin y extrae todas sus capas PA/BMP a PNG")
    p_insp.add_argument("file", type=Path, help="Archivo .bin a inspeccionar")
    p_insp.add_argument("--extract", type=Path, help="Carpeta donde extraer los PNGs/BMPs decodificados")

    # Subcomando: upload
    p_up = sub.add_parser("upload", help="Envía un archivo .bin directamente a la caja A9 Pro Box vía BLE")
    p_up.add_argument("file", type=Path, help="Archivo .bin a enviar")
    p_up.add_argument("--mac", default=DEFAULT_MAC, help=f"Dirección MAC (por defecto {DEFAULT_MAC})")

    args = parser.parse_args()

    if args.cmd == "info":
        mac = asyncio.run(scan_for_mac()) if args.scan else args.mac
        client = A9ProL2CAPClient(address=mac or DEFAULT_MAC)
        client.query_info()
    elif args.cmd == "build":
        bin_bytes = build_custom_dial_bin(
            bg_path=args.bg,
            width=args.width,
            height=args.height,
            color_hex=args.color,
            align=args.align,
            thumb_path=args.thumb,
            custom_x=args.x,
            custom_y=args.y,
            is_transpose=not args.no_transpose,
            no_clock=args.no_clock,
        )
        args.out.write_bytes(bin_bytes)
        print(f"[OK] Archivo .bin generado: {args.out} ({len(bin_bytes)} bytes)")
    elif args.cmd == "inspect":
        inspect_dial_bin(args.file, extract_dir=args.extract)
    elif args.cmd == "upload":
        data = args.file.read_bytes()
        client = A9ProL2CAPClient(address=args.mac)
        client.upload_watchface(data)


if __name__ == "__main__":
    main()
