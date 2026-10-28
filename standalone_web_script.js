// standalone_web_script.js - Motor Completo Client-Side de A9 Pro Studio
// 100% Funcional en Navegador (GitHub Pages) con Web Bluetooth, omggif, Canvas, Fuentes y Compilador .bin

// ==================== ESTADO DEL DISPOSITIVO Y VARIABLES GLOBALES ====================
let deviceState = {
  connected: false,
  battery_left: 100,
  left_charging: false,
  battery_right: 100,
  right_charging: false,
  battery_box: 100,
  box_charging: false,
  noise_mode: 1,
  work_mode: 0,
  eq_preset: 0,
  eq_bands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  mac: "41:42:6C:C2:DD:3C"
};

const SERVICE_UUID = "0000fdb3-0000-1000-8000-00805f9b34fb";
const CHAR_RX_NOTIFY = "0000ff18-0000-1000-8000-00805f9b34fb";
const CHAR_TX_WRITE  = "0000ff17-0000-1000-8000-00805f9b34fb";

let bleDevice = null;
let bleServer = null;
let rxChar = null;
let txChar = null;
let packetSeq = 0;

let currentTab = 'device';
let project = {
  name: "Mi Esfera A9 Pro",
  layers: []
};
let selectedLayerId = null;

// ==================== NAVEGACIÓN DE PESTAÑAS ====================
function setTab(tab) {
  currentTab = tab;
  ['device', 'studio', 'gallery'].forEach(t => {
    const el = document.getElementById(`tab-${t}`);
    const btn = document.getElementById(`btn-tab-${t}`);
    if (el) el.classList.toggle('hidden', t !== tab);
    if (btn) {
      btn.className = (t === tab)
        ? 'px-3.5 py-1 rounded text-xs font-mono font-semibold transition bg-[#1f8fff] text-white shadow-sm flex items-center gap-1.5'
        : 'px-3.5 py-1 rounded text-xs font-mono font-medium transition text-[#7f889b] hover:text-white flex items-center gap-1.5';
    }
  });
  if (tab === 'studio') {
    drawCanvas();
    updateLayersListUI();
  }
  if (tab === 'gallery') {
    loadOfficialGallery();
  }
}

// ==================== NOTIFICACIONES TOAST ====================
function showToast(title, msg, icon = 'OK', isGreen = true) {
  const toast = document.getElementById('toast-notify');
  const tIcon = document.getElementById('toast-icon');
  const tTitle = document.getElementById('toast-title');
  const tMsg = document.getElementById('toast-msg');
  if (!toast) return;

  tIcon.innerText = icon;
  tTitle.innerText = title;
  tTitle.className = isGreen ? 'text-xs font-mono font-bold text-emerald-400' : 'text-xs font-mono font-bold text-[#1f8fff]';
  tMsg.innerText = msg;
  toast.className = 'fixed bottom-5 right-5 z-50 bg-[#0e1117] border border-[#1f8fff] text-white px-4 py-2.5 rounded shadow-2xl flex items-center space-x-3 transition-all duration-300 transform translate-y-0 opacity-100 pointer-events-auto font-mono';

  setTimeout(() => {
    toast.className = 'fixed bottom-5 right-5 z-50 bg-[#0e1117] border border-[#1f8fff] text-white px-4 py-2.5 rounded shadow-2xl flex items-center space-x-3 transition-all duration-300 transform translate-y-24 opacity-0 pointer-events-none font-mono';
  }, 4500);
}

// ==================== TELEMETRÍA Y ACTUALIZACIÓN VISUAL ====================
function updateStatusUI() {
  const st = deviceState;
  
  // Badge de conexión
  const badge = document.getElementById('badge-conn');
  const btnConn = document.getElementById('btn-conn-text');
  if (badge) {
    if (st.connected) {
      badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-[#10b981]"></span><span>CONNECTED</span>';
      badge.className = "text-[9px] font-mono px-2 py-0.5 rounded uppercase font-semibold bg-[#0d1f18] text-[#10b981] border border-[#10b981]/40 flex items-center gap-1.5";
      if (btnConn) btnConn.innerText = "UNLINK DEVICE";
    } else {
      badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-[#ef4444]"></span><span>DISCONNECTED</span>';
      badge.className = "text-[9px] font-mono px-2 py-0.5 rounded uppercase font-semibold bg-[#1a1114] text-[#ef4444] border border-[#ef4444]/40 flex items-center gap-1.5";
      if (btnConn) btnConn.innerText = "LINK DEVICE";
    }
  }

  // Audífono Izquierdo
  const txtL = document.getElementById('txt-bat-l');
  const barL = document.getElementById('bar-bat-l');
  const iconChargeL = document.getElementById('icon-charge-l');
  const glowL = document.getElementById('glow-charge-l');
  if (txtL) txtL.innerText = `${st.battery_left}%`;
  if (barL) barL.style.width = `${st.battery_left}%`;
  if (iconChargeL) iconChargeL.classList.toggle('hidden', !st.left_charging);
  if (glowL) glowL.style.opacity = st.left_charging ? '1' : '0';

  // Audífono Derecho
  const txtR = document.getElementById('txt-bat-r');
  const barR = document.getElementById('bar-bat-r');
  const iconChargeR = document.getElementById('icon-charge-r');
  const glowR = document.getElementById('glow-charge-r');
  if (txtR) txtR.innerText = `${st.battery_right}%`;
  if (barR) barR.style.width = `${st.battery_right}%`;
  if (iconChargeR) iconChargeR.classList.toggle('hidden', !st.right_charging);
  if (glowR) glowR.style.opacity = st.right_charging ? '1' : '0';

  // Caja A9 Pro
  const txtBox = document.getElementById('txt-bat-box');
  const barBox = document.getElementById('bar-bat-box');
  const iconChargeBox = document.getElementById('icon-charge-box');
  if (txtBox) txtBox.innerText = `${st.battery_box}%`;
  if (barBox) barBox.style.width = `${st.battery_box}%`;
  if (iconChargeBox) iconChargeBox.classList.toggle('hidden', !st.box_charging);

  // Pantalla Mockup en el estuche
  const screenL = document.getElementById('screen-l');
  const screenR = document.getElementById('screen-r');
  const screenBox = document.getElementById('screen-box');
  if (screenL) screenL.innerText = st.left_charging ? `⚡${st.battery_left}%` : `${st.battery_left}%`;
  if (screenR) screenR.innerText = st.right_charging ? `⚡${st.battery_right}%` : `${st.battery_right}%`;
  if (screenBox) screenBox.innerText = st.box_charging ? `⚡ ${st.battery_box}%` : `🔋 ${st.battery_box}%`;

  // Modos de Ruido (ANC)
  [0, 1, 2].forEach(m => {
    const btn = document.getElementById(`btn-anc-${m}`);
    if (btn) {
      btn.className = (st.noise_mode === m)
        ? 'flex flex-col items-center justify-center p-2.5 rounded border border-[#1f8fff] bg-[#1f8fff]/15 transition'
        : 'flex flex-col items-center justify-center p-2.5 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition';
    }
  });

  // Modo de Sonido (Work mode)
  [0, 1, 2].forEach(m => {
    const btn = document.getElementById(`btn-work-${m}`);
    if (btn) {
      btn.className = (st.work_mode === m)
        ? 'flex flex-col items-center p-2 rounded border border-[#1f8fff] bg-[#1f8fff]/15 transition'
        : 'flex flex-col items-center p-2 rounded border border-[#212633] bg-[#0c0e14] hover:border-[#333a4d] transition';
    }
  });
}

async function updateStatus() {
  if (bleDevice && bleDevice.gatt.connected) {
    updateStatusUI();
  } else {
    try {
      const res = await fetch('/api/status');
      if (res.ok) {
        const data = await res.json();
        Object.assign(deviceState, data);
      }
    } catch (e) {}
    updateStatusUI();
  }
}

// ==================== WEB BLUETOOTH NATIVO ====================
function buildBlePacket(cmd, payload) {
  packetSeq = (packetSeq + 1) & 0xFF;
  const pkt = new Uint8Array(5 + payload.length);
  pkt[0] = packetSeq;
  pkt[1] = cmd & 0xFF;
  pkt[2] = 0x01;
  pkt[3] = 0x00;
  pkt[4] = payload.length & 0xFF;
  pkt.set(payload, 5);
  return pkt;
}

async function sendBleCommand(cmd, payload, useReq = false) {
  if (!txChar || !deviceState.connected) {
    // Si no hay Web Bluetooth, intentar backend local si existe
    try {
      if (cmd === 0x93) {
        const sel = document.getElementById('sel-time-offset');
        const offset = sel ? parseFloat(sel.value) : -13.0;
        const res = await fetch('/api/control/sync_time', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ offset_hours: offset })
        });
        const d = await res.json();
        return d.success;
      }
    } catch (e) {}
    return false;
  }
  const pkt = buildBlePacket(cmd, payload);
  try {
    if (useReq && txChar.writeValueWithResponse) {
      await txChar.writeValueWithResponse(pkt);
    } else {
      await txChar.writeValueWithoutResponse(pkt);
    }
    return true;
  } catch (e) {
    console.error("Error enviando comando BLE:", e);
    return false;
  }
}

function parseIncomingNotification(event) {
  const val = new Uint8Array(event.target.value.buffer);
  if (val.length < 5) return;
  const cmd = val[1];
  if (cmd === 0x27 || cmd === 0x28) {
    let p = 5;
    while (p + 2 <= val.length) {
      const tag = val[p];
      const len = val[p + 1];
      p += 2;
      if (p + len > val.length) break;
      const tagData = val.subarray(p, p + len);
      p += len;

      if (tag === 0x01 && tagData.length >= 2) {
        deviceState.battery_left = tagData[0] & 0x7F;
        deviceState.left_charging = bool((tagData[0] & 0x80) || (tagData[1] === 1));
      } else if (tag === 0x02 && tagData.length >= 2) {
        deviceState.battery_right = tagData[0] & 0x7F;
        deviceState.right_charging = bool((tagData[0] & 0x80) || (tagData[1] === 1));
      } else if (tag === 0x03 && tagData.length >= 2) {
        deviceState.battery_box = tagData[0] & 0x7F;
        deviceState.box_charging = bool((tagData[0] & 0x80) || (tagData[1] === 1));
      } else if (tag === 0x0C && tagData.length >= 1) {
        deviceState.noise_mode = tagData[0];
      } else if (tag === 0x08 && tagData.length >= 1) {
        deviceState.eq_preset = tagData[0];
      }
    }
    updateStatusUI();
  }
}

function bool(x) { return !!x; }

function showBleHelpModal() {
  let m = document.getElementById('modal-ble-help');
  if (m) {
    m.classList.remove('hidden');
    return;
  }
  m = document.createElement('div');
  m.id = 'modal-ble-help';
  m.className = 'fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4';
  m.innerHTML = `
    <div class="bg-gray-900 border border-gray-700 rounded-2xl p-6 max-w-lg w-full space-y-4 shadow-2xl text-left">
      <div class="flex items-center justify-between pb-2 border-b border-gray-800">
        <div class="flex items-center space-x-2">
          <span class="text-2xl">📡</span>
          <h3 class="text-base font-bold text-white">Navegador sin soporte de Web Bluetooth</h3>
        </div>
        <button onclick="document.getElementById('modal-ble-help').classList.add('hidden')" class="text-gray-400 hover:text-white text-xl font-bold">&times;</button>
      </div>
      
      <p class="text-xs text-gray-300 leading-relaxed">
        Estás usando un navegador (como <b class="text-amber-400">Mozilla Firefox</b> o Safari) que no implementa la tecnología <b>Web Bluetooth API</b> por políticas de su motor web.
      </p>

      <div class="bg-gray-800/80 rounded-xl p-4 border border-gray-700/60 space-y-3 text-xs">
        <p class="font-bold text-blue-400 text-sm">¿Cómo conectar la caja A9 Pro?</p>
        
        <div class="space-y-1">
          <p class="font-semibold text-white">1. Si estás en esta computadora (Linux):</p>
          <p class="text-gray-300 text-[11px]">Tienes el servidor local ya ejecutándose en segundo plano. Ábrelo en Firefox o en cualquier navegador:</p>
          <a href="http://localhost:8765" target="_blank" class="inline-block mt-1 bg-blue-600/30 hover:bg-blue-600/50 text-blue-300 px-3 py-1.5 rounded-lg font-mono text-xs border border-blue-500/40">
            👉 Abrir http://localhost:8765
          </a>
          <p class="text-gray-400 text-[10px] mt-0.5">(En el servidor local, Python maneja el Bluetooth directamente por BlueZ sin depender del navegador).</p>
        </div>

        <div class="space-y-1 pt-2 border-t border-gray-700/40">
          <p class="font-semibold text-white">2. Para usar Web Bluetooth directo desde GitHub Pages:</p>
          <p class="text-gray-300 text-[11px]">Abre esta página en <b class="text-white">Google Chrome</b> (ya está instalado en tu sistema):</p>
          <pre class="bg-black/60 p-2 rounded mt-1 font-mono text-[10px] text-green-400 select-all overflow-x-auto">google-chrome-stable https://velipetroll.github.io/a9pro-theme-studio/</pre>
          <p class="text-gray-400 text-[10px]">En Linux, recuerda habilitar la bandera experimental: <code class="text-amber-300">chrome://flags/#enable-experimental-web-platform-features</code> y reiniciar Chrome.</p>
        </div>

        <div class="space-y-1 pt-2 border-t border-gray-700/40">
          <p class="font-semibold text-white">3. Desde tu teléfono celular:</p>
          <p class="text-gray-300 text-[11px]">Abre la página en <b class="text-white">Google Chrome para Android</b> con Bluetooth activado.</p>
        </div>
      </div>

      <div class="bg-emerald-950/40 border border-emerald-800/50 rounded-xl p-3 text-[11px] text-emerald-300">
        💡 <b>Recuerda:</b> En este navegador puedes seguir diseñando esferas completas, subiendo GIFs, personalizando fuentes tipográficas y pulsando <b>"Descargar Archivo .bin"</b> sin ninguna limitación.
      </div>

      <div class="flex justify-end">
        <button onclick="document.getElementById('modal-ble-help').classList.add('hidden')" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-xs font-bold text-white rounded-lg transition shadow">
          Entendido
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(m);
}

async function toggleConnect() {
  if (deviceState.connected) {
    if (bleDevice && bleDevice.gatt.connected) {
      bleDevice.gatt.disconnect();
    }
    deviceState.connected = false;
    updateStatusUI();
    showToast("Bluetooth", "Desconectado de la caja", "🔌", false);
  } else {
    if (!navigator.bluetooth) {
      // Intentar fallback al servidor local si estamos en localhost
      try {
        const res = await fetch('/api/connect');
        const data = await res.json();
        if (data.success) {
          deviceState.connected = true;
          updateStatus();
          showToast("Conexión Local", "Conectado a la caja vía servidor local", "⚡", true);
          return;
        }
      } catch (e) {}
      showBleHelpModal();
      return;
    }

    try {
      document.getElementById('btn-conn-text').innerText = 'Emparejando...';
      bleDevice = await navigator.bluetooth.requestDevice({
        filters: [{ namePrefix: "A9" }, { services: [SERVICE_UUID] }],
        optionalServices: [SERVICE_UUID]
      });

      bleDevice.addEventListener('gattserverdisconnected', () => {
        deviceState.connected = false;
        updateStatusUI();
        showToast("Bluetooth", "Caja desconectada", "⚠️", false);
      });

      bleServer = await bleDevice.gatt.connect();
      const service = await bleServer.getPrimaryService(SERVICE_UUID);
      rxChar = await service.getCharacteristic(CHAR_RX_NOTIFY);
      txChar = await service.getCharacteristic(CHAR_TX_WRITE);

      await rxChar.startNotifications();
      rxChar.addEventListener('characteristicvaluechanged', parseIncomingNotification);

      deviceState.connected = true;
      updateStatusUI();
      showToast("¡Conectado!", "A9 Pro Box conectada por Web Bluetooth", "⚡", true);

      // Solicitar estado de batería inmediatamente
      await requestBattery();
    } catch (err) {
      document.getElementById('btn-conn-text').innerText = 'Conectar Caja';
      if (err.name !== 'NotFoundError') {
        alert("Error de conexión Bluetooth: " + err.message);
      }
    }
  }
}

async function requestBattery() {
  if (deviceState.connected && txChar) {
    const queryTags = [1, 2, 3, 8, 12, 13, 14, 21, 254, 128, 255];
    const payload = new Uint8Array(queryTags.length * 2);
    for (let i = 0; i < queryTags.length; i++) {
      payload[i * 2] = queryTags[i];
      payload[i * 2 + 1] = 0;
    }
    await sendBleCommand(0x27, payload);
    showToast("Batería", "Solicitando telemetría en vivo...", "🔄", true);
  } else {
    try { await fetch('/api/query'); } catch (e) {}
  }
  updateStatus();
}

async function syncTime() {
  const sel = document.getElementById('sel-time-offset');
  const offset = sel ? parseFloat(sel.value) : -13.0;
  const targetTs = Math.floor(Date.now() / 1000) + Math.round(offset * 3600);
  const buf = new Uint8Array(4);
  buf[0] = targetTs & 0xFF;
  buf[1] = (targetTs >> 8) & 0xFF;
  buf[2] = (targetTs >> 16) & 0xFF;
  buf[3] = (targetTs >> 24) & 0xFF;

  let ok = false;
  if (deviceState.connected && txChar) {
    ok = await sendBleCommand(0x93, buf, true);
  } else {
    try {
      const res = await fetch('/api/control/sync_time', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ offset_hours: offset })
      });
      const data = await res.json();
      ok = data.success;
    } catch (e) {}
  }

  if (ok) {
    showToast("Reloj Sincronizado", `Hora local enviada (ajuste: ${offset}h). Si la pantalla no cambia al instante, apágala y préndela para refrescar dígitos. 🕒`, "🕒", true);
  } else {
    if (!navigator.bluetooth && !deviceState.connected) {
      showBleHelpModal();
    } else {
      alert("No se pudo sincronizar el reloj. Asegúrate de conectar el Bluetooth y encender la pantalla de la caja.");
    }
  }
}

async function setNoiseMode(m) {
  deviceState.noise_mode = m;
  updateStatusUI();
  if (deviceState.connected && txChar) {
    await sendBleCommand(0x2C, new Uint8Array([m & 0xFF]));
  } else {
    try { await fetch('/api/control/noise', { method: 'POST', body: JSON.stringify({ mode: m }) }); } catch(e) {}
  }
}

async function setWorkMode(m) {
  deviceState.work_mode = m;
  updateStatusUI();
  if (deviceState.connected && txChar) {
    await sendBleCommand(0x25, new Uint8Array([m & 0xFF]));
  } else {
    try { await fetch('/api/control/work', { method: 'POST', body: JSON.stringify({ mode: m }) }); } catch(e) {}
  }
}

async function findEarbuds(l, r) {
  const mask = (l ? 1 : 0) | (r ? 2 : 0);
  if (deviceState.connected && txChar) {
    await sendBleCommand(0x37, new Uint8Array([mask]));
    showToast("Búsqueda", "Emmitiendo pitido en auriculares 🔊", "🔊", true);
  } else {
    try { await fetch('/api/control/find', { method: 'POST', body: JSON.stringify({ left: l, right: r }) }); } catch(e) {}
  }
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
    const sl = document.getElementById(`eq-slider-${i}`);
    bands.push(sl ? parseInt(sl.value) : 0);
  }
  const presetSel = document.getElementById('sel-eq-preset');
  const preset = presetSel ? parseInt(presetSel.value) : 0;
  deviceState.eq_preset = preset;
  deviceState.eq_bands = bands;

  if (deviceState.connected && txChar) {
    const payload = new Uint8Array(12);
    payload[0] = 10;
    payload[1] = preset;
    for (let i = 0; i < 10; i++) payload[2 + i] = bands[i] & 0xFF;
    await sendBleCommand(0x20, payload);
    showToast("Ecualizador", "Perfil de audio aplicado a la caja", "🎚️", true);
  } else {
    try { await fetch('/api/control/eq', { method: 'POST', body: JSON.stringify({ preset, bands }) }); } catch(e) {}
  }
}

// Inicializar Sliders del Ecualizador
const eqLabels = ["32Hz", "64Hz", "125Hz", "250Hz", "500Hz", "1kHz", "2kHz", "4kHz", "8kHz", "16kHz"];
const eqContainer = document.getElementById('eq-sliders-container');
if (eqContainer) {
  eqContainer.innerHTML = '';
  eqLabels.forEach((lbl, idx) => {
    const col = document.createElement('div');
    col.className = 'flex flex-col items-center space-y-1';
    col.innerHTML = `
      <span class="text-[9px] text-gray-400 font-mono">${lbl}</span>
      <input type="range" min="-12" max="12" value="0" class="slider-vertical" id="eq-slider-${idx}">
      <span class="text-[10px] font-mono text-gray-500" id="eq-val-${idx}">0</span>
    `;
    eqContainer.appendChild(col);
    const sl = col.querySelector('input');
    sl.addEventListener('input', (e) => {
      col.querySelector(`#eq-val-${idx}`).innerText = e.target.value;
    });
  });
}

// ==================== MOTOR DEL LIENZO (CANVAS Y CAPAS) ====================
const canvas = document.getElementById('theme-canvas');
const ctx = canvas.getContext('2d');
const boxCanvas = document.getElementById('box-screen-canvas');
const boxCtx = boxCanvas ? boxCanvas.getContext('2d') : null;

let isDragging = false;
let dragStartX = 0;
let dragStartY = 0;
let layerOrigX = 0;
let layerOrigY = 0;

canvas.addEventListener('mousedown', (e) => {
  const rect = canvas.getBoundingClientRect();
  const scaleX = 320 / rect.width;
  const scaleY = 172 / rect.height;
  const mx = (e.clientX - rect.left) * scaleX;
  const my = (e.clientY - rect.top) * scaleY;

  for (let i = project.layers.length - 1; i >= 0; i--) {
    const l = project.layers[i];
    if (!l.visible) continue;
    const cnt = l.count || 1;
    const fw = Math.floor(imWidth(l) / cnt);
    const fh = imHeight(l);
    const dw = l.target_w || (l.fit_screen ? 320 : fw);
    const dh = l.target_h || (l.fit_screen ? 172 : fh);
    const lx = l.fit_screen ? 160 : l.x;
    const ly = l.fit_screen ? 86 : l.y;

    if (mx >= lx - dw / 2 && mx <= lx + dw / 2 && my >= ly - dh / 2 && my <= ly + dh / 2) {
      selectLayer(l.id);
      isDragging = true;
      dragStartX = mx;
      dragStartY = my;
      layerOrigX = l.x;
      layerOrigY = l.y;
      break;
    }
  }
});

window.addEventListener('mousemove', (e) => {
  if (!isDragging || !selectedLayerId) return;
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l || l.fit_screen) return;
  const rect = canvas.getBoundingClientRect();
  const scaleX = 320 / rect.width;
  const scaleY = 172 / rect.height;
  const mx = (e.clientX - rect.left) * scaleX;
  const my = (e.clientY - rect.top) * scaleY;

  l.x = Math.round(layerOrigX + (mx - dragStartX));
  l.y = Math.round(layerOrigY + (my - dragStartY));
  const inpX = document.getElementById('inp-x');
  const inpY = document.getElementById('inp-y');
  if (inpX) inpX.value = l.x;
  if (inpY) inpY.value = l.y;
  drawCanvas();
});

window.addEventListener('mouseup', () => { isDragging = false; });

function imWidth(l) { return l._imgObj ? l._imgObj.width : (l.width || 320); }
function imHeight(l) { return l._imgObj ? l._imgObj.height : (l.height || 172); }

function drawCanvas() {
  ctx.clearRect(0, 0, 320, 172);
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, 320, 172);

  const now = new Date();
  const curH = now.getHours();
  const curM = now.getMinutes();
  const curMonth = now.getMonth() + 1;
  const curDay = now.getDate();
  const curDayOfWeek = now.getDay();

  project.layers.forEach(layer => {
    if (!layer.visible || !layer._imgObj) return;
    const img = layer._imgObj;
    const cnt = layer.count || 1;
    const fw = Math.floor(imWidth(layer) / cnt);
    const fh = imHeight(layer);
    const dw = layer.fit_screen ? 320 : (layer.target_w || fw);
    const dh = layer.fit_screen ? 172 : (layer.target_h || fh);
    const x = layer.fit_screen ? 160 : layer.x;
    const y = layer.fit_screen ? 86 : layer.y;

    if (layer.mode === 4) {
      if (layer.type === 5 || layer.type === 6 || layer.type === 2 || layer.type === 3) {
        let val = (layer.type === 5) ? curH : (layer.type === 6 ? curM : (layer.type === 2 ? curMonth : curDay));
        const d1 = Math.floor(val / 10) % 10;
        const d2 = val % 10;
        ctx.drawImage(img, d1 * fw, 0, fw, fh, Math.floor(x - dw), Math.floor(y - dh / 2), dw, dh);
        ctx.drawImage(img, d2 * fw, 0, fw, fh, Math.floor(x), Math.floor(y - dh / 2), dw, dh);
      } else if (layer.type === 4) {
        const dIdx = curDayOfWeek % cnt;
        ctx.drawImage(img, dIdx * fw, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
      } else {
        ctx.drawImage(img, 0, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
      }
    } else if (layer.type === 0x13 && cnt > 1) {
      const fDur = layer.frame_duration_ms || 120;
      const frameIdx = Math.floor(Date.now() / fDur) % cnt;
      ctx.drawImage(img, frameIdx * fw, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
    } else {
      ctx.drawImage(img, 0, 0, fw, fh, Math.floor(x - dw / 2), Math.floor(y - dh / 2), dw, dh);
    }

    // Borde de selección
    if (layer.id === selectedLayerId && currentTab === 'studio') {
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 1;
      ctx.strokeRect(Math.floor(x - dw / 2) - 1, Math.floor(y - dh / 2) - 1, dw + 2, dh + 2);
    }
  });

  // Replicar sobre la pantalla en vivo de la caja
  if (boxCtx) {
    boxCtx.clearRect(0, 0, 320, 172);
    boxCtx.drawImage(canvas, 0, 0);
  }
}

// Bucle a 30 FPS para animaciones GIF fluidas
setInterval(() => {
  const hasAnim = project.layers.some(l => l.type === 0x13 && l.count > 1);
  if (hasAnim) drawCanvas();
}, 33);

// Actualizar reloj y fecha en vivo cada segundo
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
setInterval(updateWidgetButtonsClock, 1000);
updateWidgetButtonsClock();

// ==================== GESTIÓN DE CAPAS ====================
function updateLayersListUI() {
  const list = document.getElementById('layers-list');
  if (!list) return;
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
  updateThemeSizeUI();
}

function selectLayer(id) {
  selectedLayerId = id;
  const l = project.layers.find(x => x.id === id);
  if (l) {
    const inpX = document.getElementById('inp-x');
    const inpY = document.getElementById('inp-y');
    if (inpX) inpX.value = l.x;
    if (inpY) inpY.value = l.y;
    const cnt = l.count || 1;
    const fw = Math.floor(imWidth(l) / cnt);
    const fh = imHeight(l);
    const inpW = document.getElementById('inp-w');
    const inpH = document.getElementById('inp-h');
    if (inpW) inpW.value = fw;
    if (inpH) inpH.value = fh;

    const dimsEl = document.getElementById('txt-layer-dims');
    if (dimsEl) {
      dimsEl.innerText = (cnt > 1) ? `${fw}×${fh} (${cnt}f)` : `${fw}×${fh} px`;
    }

    const gifOpt = document.getElementById('gif-compress-options');
    if (gifOpt) {
      if (l.type === 0x13) {
        gifOpt.classList.remove('hidden');
        const totalOrig = l.orig_frames_count || cnt || 12;
        const origInfo = document.getElementById('txt-gif-orig-info');
        if (origInfo) origInfo.innerText = `${totalOrig}f orig → ${cnt}f`;
      } else {
        gifOpt.classList.add('hidden');
      }
    }
  }
  updateLayersListUI();
  drawCanvas();
}

function updateSelectedCoord(axis, val) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (l) {
    l[axis] = parseInt(val) || 0;
    drawCanvas();
    updateLayersListUI();
  }
}

function updateSelectedSize(dimension, val) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l) return;
  const num = parseInt(val);
  if (isNaN(num) || num <= 0) return;
  const cnt = l.count || 1;
  if (dimension === 'w') {
    l.target_w = num;
  } else if (dimension === 'h') {
    l.target_h = num;
  }
  applyLayerProcessing(l);
}

function scaleSelectedLayer(factor) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l) return;
  const cnt = l.count || 1;
  const fw = l.target_w || Math.floor(imWidth(l) / cnt);
  const fh = l.target_h || imHeight(l);
  l.target_w = Math.max(10, Math.round(fw * factor));
  l.target_h = Math.max(10, Math.round(fh * factor));
  applyLayerProcessing(l);
}

function alignSelected(pos) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l) return;
  const cnt = l.count || 1;
  const fw = l.target_w || Math.floor(imWidth(l) / cnt);
  const fh = l.target_h || imHeight(l);

  const coords = {
    'top-left': [Math.round(fw / 2), Math.round(fh / 2)],
    'top-center': [160, Math.round(fh / 2)],
    'top-right': [320 - Math.round(fw / 2), Math.round(fh / 2)],
    'mid-left': [Math.round(fw / 2), 86],
    'center': [160, 86],
    'mid-right': [320 - Math.round(fw / 2), 86],
    'bottom-left': [Math.round(fw / 2), 172 - Math.round(fh / 2)],
    'bottom-center': [160, 172 - Math.round(fh / 2)],
    'bottom-right': [320 - Math.round(fw / 2), 172 - Math.round(fh / 2)],
  };
  const c = coords[pos] || [160, 86];
  l.x = c[0];
  l.y = c[1];
  selectLayer(l.id);
  drawCanvas();
}

function moveLayerUp() {
  const i = project.layers.findIndex(x => x.id === selectedLayerId);
  if (i !== -1 && i < project.layers.length - 1) {
    const tmp = project.layers[i];
    project.layers[i] = project.layers[i + 1];
    project.layers[i + 1] = tmp;
    updateLayersListUI();
    drawCanvas();
  }
}

function moveLayerDown() {
  const i = project.layers.findIndex(x => x.id === selectedLayerId);
  if (i > 0) {
    const tmp = project.layers[i];
    project.layers[i] = project.layers[i - 1];
    project.layers[i - 1] = tmp;
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

function toggleLayerFullScreen() {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l) return;
  l.fit_screen = !l.fit_screen;
  if (l.fit_screen) {
    l.target_w = 320;
    l.target_h = 172;
    l.x = 160;
    l.y = 86;
  }
  applyLayerProcessing(l);
}

function compressInternalResolution(resPct) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l) return;
  l.res_pct = resPct;
  applyLayerProcessing(l);
}

function compressLayerQuality(colors) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l) return;
  l.max_colors = colors;
  applyLayerProcessing(l);
}

function reduceGifFrames(maxF) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l || l.type !== 0x13) return;
  l.max_frames = maxF;
  l.active_divisor = null;
  applyLayerProcessing(l);
}

function subsampleGifFrames(divisor) {
  const l = project.layers.find(x => x.id === selectedLayerId);
  if (!l || l.type !== 0x13) return;
  const orig = l.orig_frames_count || l.count || 12;
  l.max_frames = Math.max(3, Math.round(orig / divisor));
  l.active_divisor = divisor;
  applyLayerProcessing(l);
}

function applyCustomGifDivisor() {
  const inp = document.getElementById('inp-gif-divisor');
  if (inp) subsampleGifFrames(parseFloat(inp.value) || 2);
}

function applyCustomGifExactFrames() {
  const inp = document.getElementById('inp-gif-exact-frames');
  if (inp) reduceGifFrames(parseInt(inp.value) || 8);
}

// ==================== PROCESAMIENTO CLIENT-SIDE DE GIFS E IMÁGENES ====================
async function parseGifToStrip(arrayBuffer, maxFrames, resPct = 100, targetW = null, targetH = null, fitScreen = false) {
  if (typeof GifReader === 'undefined') {
    throw new Error("omggif no está cargado");
  }
  const gr = new GifReader(new Uint8Array(arrayBuffer));
  const totalFrames = gr.numFrames();
  const origW = gr.width;
  const origH = gr.height;

  const count = Math.max(1, Math.min(maxFrames || totalFrames, totalFrames));
  const step = totalFrames / count;

  let totalDur = 0;
  for (let i = 0; i < totalFrames; i++) {
    totalDur += (gr.frameInfo(i).delay || 10) * 10;
  }
  const avgFrameDur = count > 0 ? Math.round(totalDur / count) : 100;

  let fw = fitScreen ? 320 : (targetW || origW);
  let fh = fitScreen ? 172 : (targetH || origH);
  if (resPct && resPct < 100) {
    fw = Math.max(16, Math.round(fw * resPct / 100));
    fh = Math.max(16, Math.round(fh * resPct / 100));
  }

  const stripCanvas = document.createElement('canvas');
  stripCanvas.width = fw * count;
  stripCanvas.height = fh;
  const sCtx = stripCanvas.getContext('2d');
  sCtx.imageSmoothingEnabled = false;

  const frameCanvas = document.createElement('canvas');
  frameCanvas.width = origW;
  frameCanvas.height = origH;
  const fCtx = frameCanvas.getContext('2d');
  const imgData = fCtx.createImageData(origW, origH);

  for (let i = 0; i < count; i++) {
    const srcFrameIdx = Math.min(totalFrames - 1, Math.floor(i * step));
    gr.decodeAndBlitFrameRGBA(srcFrameIdx, imgData.data);
    fCtx.putImageData(imgData, 0, 0);
    sCtx.drawImage(frameCanvas, 0, 0, origW, origH, i * fw, 0, fw, fh);
  }

  return {
    stripData: stripCanvas.toDataURL('image/png'),
    count: count,
    totalFrames: totalFrames,
    duration: avgFrameDur,
    totalDuration: totalDur,
    origW: origW,
    origH: origH,
    canvas: stripCanvas
  };
}

async function optimizeImageClient(dataUrl, targetW, targetH, fitScreen = false, resPct = 100) {
  return new Promise((resolve) => {
    const im = new Image();
    im.onload = () => {
      let w = fitScreen ? 320 : (targetW || im.width);
      let h = fitScreen ? 172 : (targetH || im.height);
      if (resPct && resPct < 100) {
        w = Math.max(16, Math.round(w * resPct / 100));
        h = Math.max(16, Math.round(h * resPct / 100));
      }
      const c = document.createElement('canvas');
      c.width = w;
      c.height = h;
      const cctx = c.getContext('2d');
      cctx.imageSmoothingEnabled = (resPct === 100);
      cctx.drawImage(im, 0, 0, w, h);
      resolve({
        imageData: c.toDataURL('image/png'),
        width: w,
        height: h,
        canvas: c
      });
    };
    im.src = dataUrl;
  });
}

async function applyLayerProcessing(l) {
  if (!l) return;
  if (l.res_pct === undefined) l.res_pct = 100;
  if (l.max_colors === undefined) l.max_colors = 256;
  if (l.max_frames === undefined) l.max_frames = l.count || 8;

  const cnt = l.count || 1;
  const targetW = l.target_w || (Math.floor(imWidth(l) / cnt));
  const targetH = l.target_h || imHeight(l);

  if (l.type === 0x13 && (l._rawGifArrayBuffer || l.raw_gif_b64)) {
    try {
      let buf = l._rawGifArrayBuffer;
      if (!buf && l.raw_gif_b64) {
        const binStr = atob(l.raw_gif_b64.split(',')[1]);
        buf = new Uint8Array(binStr.length);
        for (let i = 0; i < binStr.length; i++) buf[i] = binStr.charCodeAt(i);
        l._rawGifArrayBuffer = buf.buffer;
      }

      const res = await parseGifToStrip(buf, l.max_frames, l.res_pct, targetW, targetH, l.fit_screen);
      const im = new Image();
      await new Promise(r => { im.onload = r; im.src = res.stripData; });
      l.image_data = res.stripData;
      l._imgObj = im;
      l._canvas = res.canvas;
      l.width = im.width;
      l.height = im.height;
      l.count = res.count;
      l.orig_frames_count = res.totalFrames;
      l.frame_duration_ms = res.duration;
      l.total_duration_ms = res.totalDuration;

      // Sincronizar ticks de hardware extra[2]
      if (!l.extra) l.extra = [0, 0, 15, 0, 0, 0, 0, 0];
      l.extra[2] = Math.max(5, Math.min(50, Math.round(res.duration / 10)));

      selectLayer(l.id);
      drawCanvas();
    } catch (e) {
      alert("Error procesando animación: " + e.message);
    }
  } else if (l._rawImgB64 || l.image_data) {
    try {
      const rawSrc = l._rawImgB64 || l.image_data;
      const res = await optimizeImageClient(rawSrc, targetW, targetH, l.fit_screen, l.res_pct);
      const im = new Image();
      await new Promise(r => { im.onload = r; im.src = res.imageData; });
      l.image_data = res.imageData;
      l._imgObj = im;
      l._canvas = res.canvas;
      l.width = im.width;
      l.height = im.height;
      selectLayer(l.id);
      drawCanvas();
    } catch (e) {
      alert("Error procesando imagen: " + e.message);
    }
  }
}

// ==================== CARGA DE FONDOS, PERSONAJES Y GIFS ====================
function handleImageUpload(e, kind) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (evt) => {
    const rawSrc = evt.target.result;
    const im = new Image();
    im.onload = () => {
      const isBg = (kind === 'background');
      const l = {
        id: 'layer_' + Date.now(),
        name: isBg ? 'Fondo (320×172)' : 'Personaje PNG',
        type: 0x10,
        mode: 2,
        count: 1,
        fmt: 1,
        x: 160,
        y: 86,
        width: im.width,
        height: im.height,
        target_w: isBg ? 320 : im.width,
        target_h: isBg ? 172 : im.height,
        fit_screen: isBg,
        has_alpha: !isBg,
        image_data: rawSrc,
        _rawImgB64: rawSrc,
        extra: [0, 0, 0, 0, 0, 0, 0, 0],
        visible: true,
        _imgObj: im
      };
      if (isBg) project.layers.unshift(l);
      else project.layers.push(l);
      selectLayer(l.id);
      drawCanvas();
    };
    im.src = rawSrc;
  };
  reader.readAsDataURL(file);
}

async function handleGifUpload(e) {
  const file = e.target.files[0];
  if (!file) return;

  const abReader = new FileReader();
  abReader.onload = async (evt) => {
    const arrayBuffer = evt.target.result;
    try {
      const res = await parseGifToStrip(arrayBuffer, 12, 100);
      const im = new Image();
      await new Promise(r => { im.onload = r; im.src = res.stripData; });
      const frameW = Math.floor(im.width / res.count);

      const l = {
        id: 'layer_anim_' + Date.now(),
        name: `Animación GIF (${res.count}f)`,
        type: 0x13,
        mode: 2,
        count: res.count,
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
        max_frames: res.count,
        orig_frames_count: res.totalFrames,
        frame_duration_ms: res.duration,
        total_duration_ms: res.totalDuration,
        image_data: res.stripData,
        _rawGifArrayBuffer: arrayBuffer,
        _canvas: res.canvas,
        extra: [0, 0, Math.max(5, Math.round(res.duration / 10)), 0, 0, 0, 0, 0],
        visible: true,
        _imgObj: im
      };
      project.layers.push(l);
      selectLayer(l.id);
      drawCanvas();
      showToast("GIF Cargado", `${res.totalFrames} fotogramas adaptados a ${res.count} frames`, "🎬", true);
    } catch (err) {
      alert("Error procesando GIF: " + err.message);
    }
  };
  abReader.readAsArrayBuffer(file);
}

async function loadSampleReactorGif() {
  try {
    const resp = await fetch('assets/reactor_pulse.gif');
    const arrayBuffer = await resp.arrayBuffer();
    const res = await parseGifToStrip(arrayBuffer, 8, 100);
    const im = new Image();
    await new Promise(r => { im.onload = r; im.src = res.stripData; });
    const frameW = Math.floor(im.width / res.count);

    const l = {
      id: 'layer_sample_reactor',
      name: 'Reactor Pulse GIF',
      type: 0x13,
      mode: 2,
      count: res.count,
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
      max_frames: res.count,
      orig_frames_count: res.totalFrames,
      frame_duration_ms: res.duration,
      total_duration_ms: res.totalDuration,
      image_data: res.stripData,
      _rawGifArrayBuffer: arrayBuffer,
      _canvas: res.canvas,
      extra: [0, 0, Math.max(5, Math.round(res.duration / 10)), 0, 0, 0, 0, 0],
      visible: true,
      _imgObj: im
    };
    project.layers.push(l);
    selectLayer(l.id);
    drawCanvas();
    showToast("Reactor Pulse", "Animación cargada con éxito", "⚡", true);
  } catch (e) {
    alert("Error cargando Reactor GIF: " + e.message);
  }
}

// ==================== TIPOGRAFÍAS Y GENERACIÓN DE STRIPS ====================
function getSelectedFontConfig() {
  const sel = document.getElementById('sel-font-family');
  const inpColor = document.getElementById('inp-font-color');
  const inpStroke = document.getElementById('inp-font-stroke');
  return {
    fontFamily: sel ? sel.value : 'opensans',
    colorHex: inpColor ? inpColor.value : '#FFFFFF',
    strokeHex: inpStroke ? inpStroke.value : '#000000',
    strokeW: 2
  };
}

function renderDigitStripClient(fontSize, colorHex, strokeW, strokeHex, fontFamily) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const fontStr = `bold ${fontSize}px "${fontFamily}", sans-serif`;
  ctx.font = fontStr;

  let maxW = 0;
  for (let d = 0; d <= 9; d++) {
    const m = ctx.measureText(String(d));
    if (m.width > maxW) maxW = m.width;
  }
  const dw = Math.ceil(maxW + strokeW * 2 + 6);
  const dh = Math.ceil(fontSize * 1.25 + strokeW * 2);

  c.width = dw * 10;
  c.height = dh;
  ctx.font = fontStr;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';

  for (let d = 0; d <= 9; d++) {
    const cx = d * dw + dw / 2;
    const cy = dh / 2;
    if (strokeW > 0) {
      ctx.strokeStyle = strokeHex;
      ctx.lineWidth = strokeW * 2;
      ctx.strokeText(String(d), cx, cy);
    }
    ctx.fillStyle = colorHex;
    ctx.fillText(String(d), cx, cy);
  }
  return { dataUrl: c.toDataURL('image/png'), width: c.width, height: c.height, canvas: c };
}

function renderWeekdayStripClient(fontSize, colorHex, strokeW, strokeHex, fontFamily) {
  const days = ["DOM", "LUN", "MAR", "MIE", "JUE", "VIE", "SAB"];
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const fontStr = `bold ${fontSize}px "${fontFamily}", sans-serif`;
  ctx.font = fontStr;

  let maxW = 0;
  days.forEach(d => {
    const m = ctx.measureText(d);
    if (m.width > maxW) maxW = m.width;
  });
  const dw = Math.ceil(maxW + strokeW * 2 + 8);
  const dh = Math.ceil(fontSize * 1.3 + strokeW * 2);

  c.width = dw * 7;
  c.height = dh;
  ctx.font = fontStr;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';

  days.forEach((d, idx) => {
    const cx = idx * dw + dw / 2;
    const cy = dh / 2;
    if (strokeW > 0) {
      ctx.strokeStyle = strokeHex;
      ctx.lineWidth = strokeW * 2;
      ctx.strokeText(d, cx, cy);
    }
    ctx.fillStyle = colorHex;
    ctx.fillText(d, cx, cy);
  });
  return { dataUrl: c.toDataURL('image/png'), width: c.width, height: c.height, canvas: c };
}

function onFontSettingsChange() {
  applyFontToAllWidgets();
}

async function handleCustomFontUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  const name = file.name.replace(/\.[^/.]+$/, "");
  const reader = new FileReader();
  reader.onload = async (evt) => {
    try {
      const font = new FontFace(name, evt.target.result);
      await font.load();
      document.fonts.add(font);

      const sel = document.getElementById('sel-font-family');
      const opt = document.createElement('option');
      opt.value = name;
      opt.innerText = `Personalizada: ${name}`;
      opt.selected = true;
      sel.appendChild(opt);

      onFontSettingsChange();
      showToast("Fuente TTF", `Tipografía "${name}" cargada con éxito`, "🔤", true);
    } catch (err) {
      alert("Error cargando tipografía: " + err.message);
    }
  };
  reader.readAsArrayBuffer(file);
}

async function applyFontToAllWidgets() {
  const cfg = getSelectedFontConfig();
  for (let l of project.layers) {
    if (l.mode === 4) {
      let res;
      if (l.type === 4) {
        res = renderWeekdayStripClient(14, cfg.colorHex, cfg.strokeW, cfg.strokeHex, cfg.fontFamily);
      } else {
        res = renderDigitStripClient(40, cfg.colorHex, cfg.strokeW, cfg.strokeHex, cfg.fontFamily);
      }
      const im = new Image();
      await new Promise(r => { im.onload = r; im.src = res.dataUrl; });
      l.image_data = res.dataUrl;
      l._imgObj = im;
      l._canvas = res.canvas;
      l.width = im.width;
      l.height = im.height;
    }
  }
  drawCanvas();
}

async function addClockWidget() {
  const cfg = getSelectedFontConfig();
  const resD = renderDigitStripClient(44, cfg.colorHex, cfg.strokeW, cfg.strokeHex, cfg.fontFamily);
  const im = new Image();
  await new Promise(r => { im.onload = r; im.src = resD.dataUrl; });

  const fw = Math.floor(im.width / 10);
  const lH = {
    id: 'layer_clock_h_' + Date.now(),
    name: 'Reloj Horas (Dígitos)',
    type: 5,
    mode: 4,
    count: 10,
    fmt: 1,
    x: 100,
    y: 86,
    has_alpha: true,
    width: im.width,
    height: im.height,
    target_w: fw,
    target_h: im.height,
    image_data: resD.dataUrl,
    _canvas: resD.canvas,
    extra: [0] * 8,
    visible: true,
    _imgObj: im
  };
  const lM = {
    id: 'layer_clock_m_' + Date.now(),
    name: 'Reloj Minutos (Dígitos)',
    type: 6,
    mode: 4,
    count: 10,
    fmt: 1,
    x: 220,
    y: 86,
    has_alpha: true,
    width: im.width,
    height: im.height,
    target_w: fw,
    target_h: im.height,
    image_data: resD.dataUrl,
    _canvas: resD.canvas,
    extra: [0] * 8,
    visible: true,
    _imgObj: im
  };

  project.layers.push(lH, lM);
  selectLayer(lM.id);
  drawCanvas();
  showToast("Reloj Añadido", "Dígitos de hora y minutos integrados", "🕒", true);
}

async function addDateWidget() {
  const cfg = getSelectedFontConfig();
  const resW = renderWeekdayStripClient(14, cfg.colorHex, cfg.strokeW, cfg.strokeHex, cfg.fontFamily);
  const im = new Image();
  await new Promise(r => { im.onload = r; im.src = resW.dataUrl; });

  const fw = Math.floor(im.width / 7);
  const l = {
    id: 'layer_date_' + Date.now(),
    name: 'Fecha (Día Semana)',
    type: 4,
    mode: 4,
    count: 7,
    fmt: 1,
    x: 160,
    y: 35,
    has_alpha: true,
    width: im.width,
    height: im.height,
    target_w: fw,
    target_h: im.height,
    image_data: resW.dataUrl,
    _canvas: resW.canvas,
    extra: [0] * 8,
    visible: true,
    _imgObj: im
  };
  project.layers.push(l);
  selectLayer(l.id);
  drawCanvas();
  showToast("Fecha Añadida", "Día de la semana integrado", "📅", true);
}

// ==================== COMPILACIÓN .BIN Y TRANSMISIÓN BLE ====================
async function updateThemeSizeUI() {
  let est = 0;
  for (let l of project.layers) {
    if (!l.visible) continue;
    const cnt = l.count || 1;
    est += Math.round((imWidth(l) * imHeight(l) * 2 * 0.45)); // Factor de compresión RLE
  }
  const kb = (est / 1024).toFixed(1);
  const pct = Math.min(100, Math.round((est / 153600) * 100));
  const txtSize = document.getElementById('txt-theme-size');
  const barSize = document.getElementById('bar-theme-size');
  if (txtSize) txtSize.innerText = `${kb} KB`;
  if (barSize) {
    barSize.style.width = `${pct}%`;
    barSize.className = (pct > 95) ? 'bg-red-500 h-2 rounded-full transition-all' : (pct > 75 ? 'bg-amber-500 h-2 rounded-full transition-all' : 'bg-gradient-to-r from-brand-600 to-brand-400 h-2 rounded-full transition-all');
  }
}

async function compileTheme() {
  try {
    const binData = await compileProjectToBin(project.layers);
    const kb = (binData.length / 1024).toFixed(1);

    const txtSize = document.getElementById('txt-theme-size');
    const barSize = document.getElementById('bar-theme-size');
    if (txtSize) txtSize.innerText = `${kb} KB`;
    if (barSize) barSize.style.width = `${Math.min(100, Math.round((binData.length / 153600) * 100))}%`;

    // Descargar binario al navegador
    const blob = new Blob([binData], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${project.name || 'custom_theme'}.bin`;
    a.click();
    URL.revokeObjectURL(url);
    showToast("Compilado", `Esfera descargada con éxito (${kb} KB)`, "💾", true);
  } catch (err) {
    alert("Error compilando esfera: " + err.message);
  }
}

async function uploadCurrentTheme() {
  try {
    const binData = await compileProjectToBin(project.layers);
    if (binData.length > 153600) {
      alert(`El tema pesa ${(binData.length / 1024).toFixed(1)} KB y supera el límite de 150 KB de la caja A9 Pro. Reduce resolución o frames en el panel derecho.`);
      return;
    }
    await uploadBinDataDirect(binData);
  } catch (err) {
    alert("Error preparando tema: " + err.message);
  }
}

async function uploadBinDataDirect(binData) {
  if (!txChar || !deviceState.connected) {
    // Si no está conectado Web Bluetooth, intentar subir mediante API local si existe
    try {
      const b64 = btoa(String.fromCharCode.apply(null, binData));
      const res = await fetch('/api/themes/compile_and_upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layers: project.layers })
      });
      const data = await res.json();
      if (data.success) {
        openUploadModal();
        pollUploadProgress();
        return;
      }
    } catch (e) {}
    if (!navigator.bluetooth) {
      showBleHelpModal();
      return;
    }
    alert("Primero conecta la caja con el botón 'Conectar Caja' vía Bluetooth.");
    return;
  }

  openUploadModal();
  const chunkSize = 112;
  const totalPackages = Math.ceil(binData.length / chunkSize);
  const startTime = Date.now();

  try {
    // Comando 0x22 (Inicio de sesión de tema)
    const startPkt = new Uint8Array(15);
    startPkt[0] = 0x00;
    const view = new DataView(startPkt.buffer);
    view.setUint32(1, binData.length, true);
    await sendBleCommand(0x22, startPkt);

    for (let p = 0; p < totalPackages; p++) {
      const start = p * chunkSize;
      const end = Math.min(start + chunkSize, binData.length);
      const chunk = binData.subarray(start, end);

      const pkt = new Uint8Array(4 + chunk.length);
      pkt[0] = 0x01; // Block
      pkt[1] = 0x00;
      const pView = new DataView(pkt.buffer);
      pView.setUint16(2, p, true);
      pkt.set(chunk, 4);

      await sendBleCommand(0x22, pkt);

      const pct = Math.round(((p + 1) / totalPackages) * 100);
      const elapsed = (Date.now() - startTime) / 1000;
      const speed = elapsed > 0 ? (end / 1024 / elapsed).toFixed(1) : 0;

      document.getElementById('upload-pct').innerText = `${pct}%`;
      document.getElementById('upload-bar').style.width = `${pct}%`;
      document.getElementById('upload-packet-info').innerText = `${p + 1} / ${totalPackages} paquetes`;
      document.getElementById('upload-speed').innerText = `${speed} KB/s`;

      // Ceder el hilo para fluidez en UI
      if (p % 8 === 0) await new Promise(r => setTimeout(r, 10));
    }

    // Fin de transmisión (0x02)
    const endPkt = new Uint8Array([0x02]);
    await sendBleCommand(0x22, endPkt);

    document.getElementById('upload-msg').innerText = "¡Esfera instalada con éxito en la caja! 🕒";
    showToast("¡Tema Instalado!", "La nueva esfera ya está activa en tu A9 Pro", "🎨", true);
    setTimeout(closeUploadModal, 2500);
  } catch (err) {
    document.getElementById('upload-msg').innerText = "Error en subida: " + err.message;
    alert("Error transmitiendo tema: " + err.message);
  }
}

function openUploadModal() {
  document.getElementById('modal-upload').classList.remove('hidden');
  document.getElementById('upload-bar').style.width = '0%';
  document.getElementById('upload-pct').innerText = '0%';
  document.getElementById('upload-msg').innerText = 'Iniciando transferencia Bluetooth...';
}

function closeUploadModal() {
  document.getElementById('modal-upload').classList.add('hidden');
}

function pollUploadProgress() {
  const timer = setInterval(async () => {
    try {
      const res = await fetch('/api/upload_status');
      const st = await res.json();
      if (st.active) {
        document.getElementById('upload-pct').innerText = `${st.percent}%`;
        document.getElementById('upload-bar').style.width = `${st.percent}%`;
        document.getElementById('upload-packet-info').innerText = `${st.current_packet} / ${st.total_packets}`;
        document.getElementById('upload-speed').innerText = `${st.speed_kbps} KB/s`;
      } else {
        clearInterval(timer);
        if (st.status === 'success') {
          document.getElementById('upload-msg').innerText = '¡Tema instalado con éxito!';
          setTimeout(closeUploadModal, 2000);
        }
      }
    } catch (e) {
      clearInterval(timer);
    }
  }, 500);
}

// ==================== GALERÍA OFICIAL DE 80 TEMAS ====================
let galleryLoaded = false;
async function loadOfficialGallery() {
  if (galleryLoaded) return;
  const grid = document.getElementById('gallery-grid');
  if (!grid) return;

  let themes = [];
  if (typeof OFFICIAL_THEMES_CATALOG !== 'undefined') {
    themes = OFFICIAL_THEMES_CATALOG;
  } else {
    try {
      const res = await fetch('/api/official_themes');
      themes = await res.json();
    } catch (e) {}
  }

  grid.innerHTML = '';
  themes.forEach(t => {
    const card = document.createElement('div');
    card.className = "panel-chassis p-3 flex flex-col justify-between space-y-2 hover:border-[#1f8fff]/60 transition font-mono";
    card.innerHTML = `
      <div class="relative rounded overflow-hidden screen-chassis bg-black aspect-[320/172] flex items-center justify-center">
        <img src="${t.thumb}" class="w-full h-full object-cover" alt="${t.name}">
      </div>
      <div class="border-t border-[#1e2330] pt-1">
        <h4 class="text-xs font-mono font-bold truncate text-white">${t.name}</h4>
        <span class="text-[10px] text-[#7f889b] font-mono">${(t.size / 1024).toFixed(1)} KB</span>
      </div>
      <div class="grid grid-cols-2 gap-1.5 pt-0.5">
        <button onclick="loadOfficialToStudio('${t.id}')" class="py-1 px-1.5 btn-tactile text-[10px]">
          EDIT
        </button>
        <button onclick="uploadOfficialDirect('${t.id}')" class="py-1 px-1.5 btn-action-azure text-[10px]">
          FLASH
        </button>
      </div>
    `;
    grid.appendChild(card);
  });
  galleryLoaded = true;
}

async function loadOfficialToStudio(themeId) {
  try {
    const res = await fetch(`official_bins/${themeId}.bin`);
    const buf = await res.arrayBuffer();
    const u8 = new Uint8Array(buf);
    decompileBinToLayers(u8);
    setTab('studio');
    showToast("Tema Abierto", "Esfera oficial cargada en el personalizador", "🎨", true);
  } catch (err) {
    alert("Error cargando esfera oficial: " + err.message);
  }
}

async function uploadOfficialDirect(themeId) {
  try {
    const res = await fetch(`official_bins/${themeId}.bin`);
    const buf = await res.arrayBuffer();
    const binData = new Uint8Array(buf);
    await uploadBinDataDirect(binData);
  } catch (err) {
    alert("Error subiendo tema oficial: " + err.message);
  }
}

function decompileBinToLayers(u8) {
  const view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const numItems = view.getUint16(46, true);
  project.layers = [];

  const items = [];
  for (let i = 0; i < numItems; i++) {
    const pos = 52 + i * 20;
    const off = view.getUint32(pos, true);
    const mode = u8[pos + 4];
    const typ = u8[pos + 5];
    const count = u8[pos + 6];
    const fmt = u8[pos + 7];
    const x = view.getUint16(pos + 8, true);
    const y = view.getUint16(pos + 10, true);
    const extra = Array.from(u8.subarray(pos + 12, pos + 20));
    items.push({ index: i, off, mode, typ, count, fmt, x, y, extra });
  }

  const allOffs = Array.from(new Set(items.map(it => it.off).concat([u8.byteLength]))).sort((a,b) => a-b);
  const offToSz = {};
  for (let k = 0; k < allOffs.length - 1; k++) {
    offToSz[allOffs[k]] = allOffs[k+1] - allOffs[k];
  }

  items.forEach(it => {
    // Omitir thumbnail en el lienzo
    if (it.typ === 0x11) return;
    const sz = offToSz[it.off] || 0;
    const chunk = u8.subarray(it.off, it.off + sz);
    if (chunk[0] === 0x50 && chunk[1] === 0x41) {
      const offCanvas = document.createElement('canvas');
      const octx = offCanvas.getContext('2d');
      const imgData = decodePAtoImageData(chunk, octx);
      if (imgData) {
        offCanvas.width = imgData.width;
        offCanvas.height = imgData.height;
        octx.putImageData(imgData, 0, 0);

        const im = new Image();
        im.src = offCanvas.toDataURL();
        const l = {
          id: 'layer_' + it.index,
          name: (it.typ === 0x10) ? 'Fondo Oficial' : (it.typ === 0x13 ? `Animación (${it.count}f)` : `Elemento 0x${it.typ.toString(16).toUpperCase()}`),
          type: it.typ,
          mode: it.mode,
          count: it.count,
          fmt: it.fmt,
          x: it.x,
          y: it.y,
          width: imgData.width,
          height: imgData.height,
          target_w: Math.floor(imgData.width / it.count),
          target_h: imgData.height,
          image_data: offCanvas.toDataURL(),
          _canvas: offCanvas,
          extra: it.extra,
          visible: true,
          _imgObj: im
        };
        project.layers.push(l);
      }
    }
  });

  selectedLayerId = project.layers.length ? project.layers[0].id : null;
  updateLayersListUI();
  drawCanvas();
}

async function resetDefaultTheme() {
  await loadOfficialToStudio("385_deb0daf817f94b5d8e1fb4a0ff4bd7f3");
  showToast("Restablecido", "Tema predeterminado Cyberpunk 385 cargado", "🔄", true);
}

// ==================== INICIALIZACIÓN AL ABRIR LA PÁGINA ====================
window.addEventListener('DOMContentLoaded', async () => {
  setTab('device');
  updateStatus();
  // Cargar por defecto el tema oficial 385 en el lienzo del personalizador
  setTimeout(() => {
    resetDefaultTheme();
  }, 300);
});
