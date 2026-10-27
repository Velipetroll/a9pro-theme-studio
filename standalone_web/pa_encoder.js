// pa_encoder.js - Compilador de temas A9 Pro Box (320x172 px) en puro JavaScript (Client-side)
// Implementa el algoritmo de compresión 'PA' RLE transpuesto y empaquetado binario 'WF\x02\x04'.

function rgb888_to_rgb565(r, g, b) {
  const r5 = (r >> 3) & 0x1F;
  const g6 = (g >> 2) & 0x3F;
  const b5 = (b >> 3) & 0x1F;
  return (r5 << 11) | (g6 << 5) | b5;
}

function encodePARow(pixelsRGBA, hasAlpha) {
  const conv = [];
  for (let i = 0; i < pixelsRGBA.length; i += 4) {
    const r = pixelsRGBA[i];
    const g = pixelsRGBA[i + 1];
    const b = pixelsRGBA[i + 2];
    const a = pixelsRGBA[i + 3];
    if (hasAlpha && a === 0) {
      conv.push([0, 0]);
    } else {
      const c565 = rgb888_to_rgb565(r, g, b);
      conv.push([hasAlpha ? a : 255, c565]);
    }
  }

  const out = [];
  const n = conv.length;
  let i = 0;
  let prev_a = -1;
  let prev_c = -1;

  while (i < n) {
    const cur_a = conv[i][0];
    const cur_c = conv[i][1];
    let run_len = 1;
    while (i + run_len < n && conv[i + run_len][0] === cur_a && conv[i + run_len][1] === cur_c && run_len < 63) {
      run_len++;
    }

    if (run_len >= 2) {
      out.push(0x80 | run_len);
      if (hasAlpha) out.push(cur_a & 0xFF);
      out.push(cur_c & 0xFF);
      out.push((cur_c >> 8) & 0xFF);
      prev_a = cur_a;
      prev_c = cur_c;
      i += run_len;
      continue;
    }

    if (cur_a === prev_a && prev_c >= 0) {
      const pr5 = (prev_c >> 11) & 0x1F, pg6 = (prev_c >> 5) & 0x3F, pb5 = prev_c & 0x1F;
      const cr5 = (cur_c >> 11) & 0x1F, cg6 = (cur_c >> 5) & 0x3F, cb5 = cur_c & 0x1F;
      const dr = cr5 - pr5, dg = cg6 - pg6, db = cb5 - pb5;
      if (dr >= -2 && dr <= 1 && dg >= -4 && dg <= 3 && db >= -2 && db <= 1) {
        const op = ((dr + 2) << 5) | ((dg + 4) << 2) | (db + 2);
        out.push(op);
        prev_c = cur_c;
        i++;
        continue;
      }
    }

    // Look-ahead for literal sequence
    let lit_len = 1;
    while (
      i + lit_len < n && lit_len < 63 &&
      (!hasAlpha || conv[i + lit_len][0] === cur_a) &&
      (i + lit_len + 1 >= n || conv[i + lit_len][0] !== conv[i + lit_len + 1][0] || conv[i + lit_len][1] !== conv[i + lit_len + 1][1])
    ) {
      lit_len++;
    }

    if (lit_len >= 2) {
      if (hasAlpha && cur_a !== prev_a) {
        out.push(0x80 | 1);
        out.push(cur_a & 0xFF);
        out.push(cur_c & 0xFF);
        out.push((cur_c >> 8) & 0xFF);
        prev_a = cur_a;
        prev_c = cur_c;
        i++;
        lit_len--;
      }
      if (lit_len > 0) {
        out.push(0xC0 | lit_len);
        for (let k = 0; k < lit_len; k++) {
          const c = conv[i + k][1];
          out.push(c & 0xFF);
          out.push((c >> 8) & 0xFF);
        }
        prev_c = conv[i + lit_len - 1][1];
        i += lit_len;
      }
      continue;
    }

    // Fallback single pixel run
    out.push(0x80 | 1);
    if (hasAlpha) out.push(cur_a & 0xFF);
    out.push(cur_c & 0xFF);
    out.push((cur_c >> 8) & 0xFF);
    prev_a = cur_a;
    prev_c = cur_c;
    i++;
  }

  return new Uint8Array(out);
}

function encodeImageToPA(canvas, hasAlpha = true, isTranspose = true) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const imgData = ctx.getImageData(0, 0, w, h);
  const data = imgData.data;

  const rowsData = [];
  let pa_w, pa_h;

  if (isTranspose) {
    pa_w = h;
    pa_h = w;
    for (let x = 0; x < w; x++) {
      const rowPx = new Uint8ClampedArray(h * 4);
      let pIdx = 0;
      for (let y = h - 1; y >= 0; y--) {
        const srcIdx = (y * w + x) * 4;
        rowPx[pIdx] = data[srcIdx];
        rowPx[pIdx + 1] = data[srcIdx + 1];
        rowPx[pIdx + 2] = data[srcIdx + 2];
        rowPx[pIdx + 3] = data[srcIdx + 3];
        pIdx += 4;
      }
      rowsData.push(encodePARow(rowPx, hasAlpha));
    }
  } else {
    pa_w = w;
    pa_h = h;
    for (let y = h - 1; y >= 0; y--) {
      const rowPx = new Uint8ClampedArray(w * 4);
      let pIdx = 0;
      for (let x = 0; x < w; x++) {
        const srcIdx = (y * w + x) * 4;
        rowPx[pIdx] = data[srcIdx];
        rowPx[pIdx + 1] = data[srcIdx + 1];
        rowPx[pIdx + 2] = data[srcIdx + 2];
        rowPx[pIdx + 3] = data[srcIdx + 3];
        pIdx += 4;
      }
      rowsData.push(encodePARow(rowPx, hasAlpha));
    }
  }

  let totalRle = 0;
  for (let r of rowsData) totalRle += r.length;
  const avgLen = pa_h > 0 ? Math.round(totalRle / pa_h) : 0;

  const offsets = [0];
  let acc = 0;
  for (let r of rowsData) {
    acc += r.length;
    offsets.push(acc);
  }

  const residuals = [];
  let allFitInt8 = true;
  for (let i = 0; i <= pa_h; i++) {
    const res = offsets[i] - i * avgLen;
    residuals.push(res);
    if (res < -128 || res > 127) allFitInt8 = false;
  }

  const offBytes = allFitInt8 ? 1 : 2;
  const tableBuffer = new ArrayBuffer(residuals.length * offBytes);
  const tableDV = new DataView(tableBuffer);
  for (let i = 0; i < residuals.length; i++) {
    if (offBytes === 1) {
      tableDV.setInt8(i, residuals[i]);
    } else {
      tableDV.setInt16(i * 2, residuals[i], true);
    }
  }

  const hdr = new Uint8Array(16);
  hdr[0] = 0x50; // 'P'
  hdr[1] = 0x41; // 'A'
  hdr[2] = 0;
  hdr[3] = hasAlpha ? 1 : 0;
  const hdrDV = new DataView(hdr.buffer);
  hdrDV.setUint16(4, pa_w, true);
  hdrDV.setUint16(6, pa_h, true);
  hdr[8] = 0;
  hdr[9] = offBytes;
  hdrDV.setUint16(10, avgLen, true);

  // Combinar: hdr (16) + table + rowsData
  const totalLength = 16 + tableBuffer.byteLength + totalRle;
  const result = new Uint8Array(totalLength);
  result.set(hdr, 0);
  result.set(new Uint8Array(tableBuffer), 16);
  let pos = 16 + tableBuffer.byteLength;
  for (let r of rowsData) {
    result.set(r, pos);
    pos += r.length;
  }

  return result;
}

async function compileProjectToBin(layers) {
  const activeLayers = layers.filter(l => l.visible !== false);
  if (!activeLayers.length) throw new Error("No hay capas visibles para compilar.");

  // Generar thumbnail automático (layer type 0x11, 204x112) si no existe
  let hasThumb = activeLayers.some(l => l.type === 0x11);
  if (!hasThumb) {
    const thumbCanvas = document.createElement('canvas');
    thumbCanvas.width = 204;
    thumbCanvas.height = 112;
    const tCtx = thumbCanvas.getContext('2d');
    tCtx.fillStyle = '#000';
    tCtx.fillRect(0, 0, 204, 112);
    const prevCanvas = document.getElementById('theme-canvas');
    if (prevCanvas) {
      tCtx.drawImage(prevCanvas, 0, 0, 320, 172, 0, 0, 204, 112);
    }
    activeLayers.push({
      id: 'auto_thumb',
      name: 'Miniatura Automática',
      type: 0x11,
      mode: 2,
      count: 1,
      x: 160,
      y: 86,
      has_alpha: false,
      _canvas: thumbCanvas
    });
  }

  const encodedChunks = [];
  const layerOffsets = [];
  const firstDataOffset = 52 + activeLayers.length * 20;
  let currOffset = firstDataOffset;

  for (let l of activeLayers) {
    let canvas = l._canvas;
    if (!canvas) {
      canvas = document.createElement('canvas');
      const img = l._imgObj;
      canvas.width = img.width;
      canvas.height = img.height;
      const cctx = canvas.getContext('2d');
      cctx.drawImage(img, 0, 0);
    }
    const chunk = encodeImageToPA(canvas, l.has_alpha !== false, true);
    encodedChunks.push(chunk);
    layerOffsets.push(currOffset);
    currOffset += chunk.length;
  }

  const totalSize = currOffset;
  const bin = new Uint8Array(totalSize);

  // Cabecera WF (52 bytes)
  bin[0] = 0x57; // 'W'
  bin[1] = 0x46; // 'F'
  bin[2] = 0x02;
  bin[3] = 0x04;
  bin[8] = 7;
  bin[9] = 9;
  const binDV = new DataView(bin.buffer);
  binDV.setUint16(42, 320, true);
  binDV.setUint16(44, 172, true);
  binDV.setUint16(46, activeLayers.length, true);
  binDV.setUint32(48, totalSize, true);

  // Descriptores (20 bytes c/u)
  for (let idx = 0; idx < activeLayers.length; idx++) {
    const l = activeLayers[idx];
    const off = layerOffsets[idx];
    const dPos = 52 + idx * 20;

    binDV.setUint32(dPos, off, true);
    bin[dPos + 4] = l.mode || 2;
    bin[dPos + 5] = l.type !== undefined ? l.type : 0x10;
    bin[dPos + 6] = l.count || 1;
    bin[dPos + 7] = 1; // PA format
    binDV.setUint16(dPos + 8, Math.round(l.x !== undefined ? l.x : 160), true);
    binDV.setUint16(dPos + 10, Math.round(l.y !== undefined ? l.y : 86), true);

    const extra = l.extra || [0, 0, 0, 0, 0, 0, 0, 0];
    for (let k = 0; k < 8; k++) {
      bin[dPos + 12 + k] = extra[k] || 0;
    }
    if (l.type === 0x13) {
      const fDur = l.frame_duration_ms || 100;
      const ticks = Math.max(5, Math.min(50, Math.round(fDur / 10)));
      bin[dPos + 14] = ticks; // extra[2]
    }
  }

  // Chunks de imagen
  for (let idx = 0; idx < encodedChunks.length; idx++) {
    const off = layerOffsets[idx];
    bin.set(encodedChunks[idx], off);
  }

  return bin;
}
