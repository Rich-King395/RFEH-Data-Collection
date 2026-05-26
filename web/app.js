const state = {
  samples: [],
  maxSamples: 150000,
  connected: false,
  status: null,
  socket: null,
  reconnectTimer: null,
};

const els = {
  canvas: document.getElementById("chartCanvas"),
  message: document.getElementById("chartMessage"),
  connectionDot: document.getElementById("connectionDot"),
  connectionText: document.getElementById("connectionText"),
  portText: document.getElementById("portText"),
  voltageValue: document.getElementById("voltageValue"),
  adcValue: document.getElementById("adcValue"),
  rateValue: document.getElementById("rateValue"),
  sampleCount: document.getElementById("sampleCount"),
  errorText: document.getElementById("errorText"),
  rawToggle: document.getElementById("rawToggle"),
  smoothToggle: document.getElementById("smoothToggle"),
  windowSelect: document.getElementById("windowSelect"),
  smoothWindow: document.getElementById("smoothWindow"),
};

const ctx = els.canvas.getContext("2d");

function connect() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  state.socket = socket;

  socket.addEventListener("open", () => {
    setConnection("Connected", "warn");
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    state.status = payload.status;

    if (Array.isArray(payload.samples) && payload.samples.length > 0) {
      state.samples.push(...payload.samples);
      if (state.samples.length > state.maxSamples) {
        state.samples.splice(0, state.samples.length - state.maxSamples);
      }
    }

    updateMetrics();
  });

  socket.addEventListener("close", () => {
    setConnection("Disconnected", "off");
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    setConnection("Disconnected", "off");
    socket.close();
  });
}

function scheduleReconnect() {
  if (state.reconnectTimer) {
    return;
  }

  state.reconnectTimer = window.setTimeout(() => {
    state.reconnectTimer = null;
    connect();
  }, 1500);
}

function setConnection(label, mode) {
  els.connectionText.textContent = label;
  els.connectionDot.className = `dot dot-${mode}`;
}

function updateMetrics() {
  const status = state.status;
  if (!status) {
    return;
  }

  if (status.connected) {
    setConnection("Connected", "on");
  } else {
    setConnection("Disconnected", "off");
  }

  els.portText.textContent = `${status.port} @ ${status.baudRate}`;
  els.sampleCount.textContent = formatInteger(status.totalSamples);
  els.rateValue.textContent = `${status.sampleRate.toFixed(0)} Hz`;
  els.errorText.textContent = status.latestError || "";

  if (status.latest) {
    els.voltageValue.textContent = `${status.latest.voltage.toFixed(3)} V`;
    els.adcValue.textContent = String(status.latest.adc);
  }
}

function formatInteger(value) {
  return new Intl.NumberFormat("en-US").format(value || 0);
}

function getWindowSeconds() {
  return Number.parseFloat(els.windowSelect.value) || 60;
}

function getSmoothWindow() {
  const value = Math.max(1, Number.parseInt(els.smoothWindow.value, 10) || 1);
  return value % 2 === 0 ? value + 1 : value;
}

function visibleSamples() {
  const windowMs = getWindowSeconds() * 1000;
  const latest = state.samples.at(-1);
  if (!latest) {
    return [];
  }

  const cutoff = latest.pcTimeMs - windowMs;
  return state.samples.filter((sample) => sample.pcTimeMs >= cutoff);
}

function movingAverage(values, windowSize) {
  if (windowSize <= 1 || values.length < windowSize) {
    return values.slice();
  }

  const radius = Math.floor(windowSize / 2);
  const prefix = [0];
  for (const value of values) {
    prefix.push(prefix.at(-1) + value);
  }

  const output = [];

  for (let i = 0; i < values.length; i += 1) {
    const start = Math.max(0, i - radius);
    const end = Math.min(values.length - 1, i + radius);
    const total = prefix[end + 1] - prefix[start];
    output.push(total / (end - start + 1));
  }

  return output;
}

function resizeCanvas() {
  const rect = els.canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));

  if (els.canvas.width !== width || els.canvas.height !== height) {
    els.canvas.width = width;
    els.canvas.height = height;
  }

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return rect;
}

function draw() {
  const rect = resizeCanvas();
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);

  const samples = visibleSamples();
  const showRaw = els.rawToggle.checked;
  const showSmooth = els.smoothToggle.checked;

  if (!showRaw && !showSmooth) {
    els.message.textContent = "Enable Raw or Smooth";
    requestAnimationFrame(draw);
    return;
  }

  if (samples.length < 2) {
    els.message.textContent = "Waiting for samples";
    drawFrame(width, height, 0, 1, 0, getWindowSeconds());
    requestAnimationFrame(draw);
    return;
  }

  els.message.textContent = "";

  const padding = { left: 62, right: 20, top: 24, bottom: 44 };
  const plot = {
    x: padding.left,
    y: padding.top,
    width: width - padding.left - padding.right,
    height: height - padding.top - padding.bottom,
  };

  const latestMs = samples.at(-1).pcTimeMs;
  const windowMs = getWindowSeconds() * 1000;
  const startMs = latestMs - windowMs;
  const times = samples.map((sample) => (sample.pcTimeMs - startMs) / 1000);
  const voltages = samples.map((sample) => sample.voltage);
  const smoothVoltages = showSmooth ? movingAverage(voltages, getSmoothWindow()) : [];

  const plottedValues = showRaw ? voltages.slice() : [];
  if (showSmooth) {
    plottedValues.push(...smoothVoltages);
  }

  const yRange = paddedRange(plottedValues);
  drawFrame(width, height, yRange.min, yRange.max, 0, getWindowSeconds(), plot);

  if (showRaw) {
    drawLine(times, voltages, yRange, plot, "#2563eb", showSmooth ? 0.55 : 0.95, showSmooth ? 1.2 : 1.8);
  }

  if (showSmooth) {
    drawLine(times, smoothVoltages, yRange, plot, "#dc2626", 1, 2.2);
  }

  drawLegend(showRaw, showSmooth, plot);
  requestAnimationFrame(draw);
}

function paddedRange(values) {
  let min = Math.min(...values);
  let max = Math.max(...values);

  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    min = 0;
    max = 1;
  }

  if (min === max) {
    min -= 0.01;
    max += 0.01;
  }

  const pad = (max - min) * 0.12;
  return { min: Math.max(0, min - pad), max: max + pad };
}

function drawFrame(width, height, yMin, yMax, xMin, xMax, plotOverride) {
  const plot =
    plotOverride ||
    {
      x: 62,
      y: 24,
      width: width - 82,
      height: height - 68,
    };

  ctx.save();
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "#e8edf4";
  ctx.lineWidth = 1;
  ctx.font = "12px system-ui, sans-serif";
  ctx.fillStyle = "#697386";

  const yTicks = 5;
  for (let i = 0; i <= yTicks; i += 1) {
    const t = i / yTicks;
    const y = plot.y + plot.height * t;
    const value = yMax - (yMax - yMin) * t;

    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.width, y);
    ctx.stroke();
    ctx.fillText(`${value.toFixed(3)} V`, 12, y + 4);
  }

  const xTicks = 6;
  for (let i = 0; i <= xTicks; i += 1) {
    const t = i / xTicks;
    const x = plot.x + plot.width * t;
    const value = xMin + (xMax - xMin) * t;

    ctx.beginPath();
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.height);
    ctx.stroke();
    ctx.fillText(`${value.toFixed(0)}s`, x - 10, plot.y + plot.height + 24);
  }

  ctx.strokeStyle = "#d8dee9";
  ctx.strokeRect(plot.x, plot.y, plot.width, plot.height);
  ctx.restore();
}

function drawLine(times, values, yRange, plot, color, alpha, lineWidth) {
  if (times.length < 2 || values.length < 2) {
    return;
  }

  const xMax = getWindowSeconds();
  const ySpan = yRange.max - yRange.min;

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();

  for (let i = 0; i < times.length; i += 1) {
    const x = plot.x + (times[i] / xMax) * plot.width;
    const y = plot.y + plot.height - ((values[i] - yRange.min) / ySpan) * plot.height;

    if (i === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  }

  ctx.stroke();
  ctx.restore();
}

function drawLegend(showRaw, showSmooth, plot) {
  const items = [];
  if (showRaw) {
    items.push(["Raw", "#2563eb"]);
  }
  if (showSmooth) {
    items.push(["Smooth", "#dc2626"]);
  }

  ctx.save();
  ctx.font = "13px system-ui, sans-serif";
  let x = plot.x + 12;
  const y = plot.y + 18;

  for (const [label, color] of items) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 22, y);
    ctx.stroke();
    ctx.fillStyle = "#18202f";
    ctx.fillText(label, x + 30, y + 4);
    x += label.length * 8 + 64;
  }
  ctx.restore();
}

window.addEventListener("resize", resizeCanvas);
connect();
requestAnimationFrame(draw);
