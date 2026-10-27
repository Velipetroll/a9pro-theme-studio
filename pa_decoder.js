
// Decoder de Sprites PA en puro JavaScript (Canvas ImageData)
function decodePAtoImageData(u8, ctx) {
    if (u8[0] !== 0x50 || u8[1] !== 0x41) return null;
    const hasAlpha = u8[3] === 1;
    const view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const w = view.getUint16(4, true);
    const h = view.getUint16(6, true);
    const tileStep = u8[8];
    const offBytes = u8[9];
    const avgLen = view.getUint16(10, true);

    const nEntries = (tileStep === 0) ? h : Math.ceil(w / tileStep) * Math.ceil(h / tileStep);
    const tableOffset = 16;
    const rleBase = tableOffset + (nEntries + 1) * offBytes;

    function getResidual(i) {
        const pos = tableOffset + i * offBytes;
        return (offBytes === 1) ? view.getInt8(pos) : view.getInt16(pos, true);
    }

    // Buffer RGBA transpuesto (w columnas, h filas)
    const rawPx = new Uint8ClampedArray(w * h * 4);

    if (tileStep === 0) {
        for (let r = 0; r < h; r++) {
            const s = r * avgLen + getResidual(r);
            const e = (r + 1) * avgLen + getResidual(r + 1);
            const stream = u8.subarray(rleBase + s, rleBase + e);
            decodeStreamRow(stream, hasAlpha, rawPx, r * w * 4, w);
        }
    }

    // Destransponer: x_orig = r, y_orig = w - 1 - c
    const uprightImg = ctx.createImageData(h, w);
    const dst = uprightImg.data;
    for (let r = 0; r < h; r++) {
        for (let c = 0; c < w; c++) {
            const srcIdx = (r * w + c) * 4;
            const dstIdx = ((w - 1 - c) * h + r) * 4;
            dst[dstIdx] = rawPx[srcIdx];
            dst[dstIdx + 1] = rawPx[srcIdx + 1];
            dst[dstIdx + 2] = rawPx[srcIdx + 2];
            dst[dstIdx + 3] = rawPx[srcIdx + 3];
        }
    }
    return uprightImg;
}

function decodeStreamRow(stream, hasAlpha, outBuf, outOffset, maxPixels) {
    let i = 0, pIdx = 0;
    let prevA = 255, prevR = 0, prevG = 0, prevB = 0;

    function rgb565To888(c) {
        const r = (c >> 11) & 0x1F;
        const g = (c >> 5) & 0x3F;
        const b = c & 0x1F;
        return [Math.round((r * 255) / 31), Math.round((g * 255) / 63), Math.round((b * 255) / 31)];
    }

    while (i < stream.length && pIdx < maxPixels) {
        const op = stream[i++];
        if ((op & 0x80) === 0) {
            // Delta pixel
            const dR = ((op >> 5) & 3) - 2;
            const dG = ((op >> 2) & 7) - 4;
            const dB = (op & 3) - 2;
            let r5 = Math.max(0, Math.min(31, Math.round((prevR * 31) / 255) + dR));
            let g6 = Math.max(0, Math.min(63, Math.round((prevG * 63) / 255) + dG));
            let b5 = Math.max(0, Math.min(31, Math.round((prevB * 31) / 255) + dB));
            prevR = Math.round((r5 * 255) / 31);
            prevG = Math.round((g6 * 255) / 63);
            prevB = Math.round((b5 * 255) / 31);

            const o = outOffset + pIdx * 4;
            outBuf[o] = prevR; outBuf[o+1] = prevG; outBuf[o+2] = prevB; outBuf[o+3] = prevA;
            pIdx++;
        } else if ((op & 0xC0) === 0x80) {
            // Run length
            const cnt = op & 0x3F;
            let a = 255;
            if (hasAlpha) a = stream[i++];
            const cLo = stream[i++];
            const cHi = stream[i++];
            const c565 = cLo | (cHi << 8);
            const [r, g, b] = rgb565To888(c565);
            prevA = a; prevR = r; prevG = g; prevB = b;

            for (let k = 0; k < cnt && pIdx < maxPixels; k++) {
                const o = outOffset + pIdx * 4;
                outBuf[o] = r; outBuf[o+1] = g; outBuf[o+2] = b; outBuf[o+3] = a;
                pIdx++;
            }
        } else if ((op & 0xC0) === 0xC0) {
            // Literal sequence
            const cnt = op & 0x3F;
            for (let k = 0; k < cnt && pIdx < maxPixels; k++) {
                const cLo = stream[i++];
                const cHi = stream[i++];
                const c565 = cLo | (cHi << 8);
                const [r, g, b] = rgb565To888(c565);
                prevR = r; prevG = g; prevB = b;
                const o = outOffset + pIdx * 4;
                outBuf[o] = r; outBuf[o+1] = g; outBuf[o+2] = b; outBuf[o+3] = prevA;
                pIdx++;
            }
        }
    }
}
