const state = {
  channels: {},
  primaryChannelId: null,
  activeChannelId: null,
  channelRenderKey: "",
  maxSamples: 150000,
  socket: null,
  reconnectTimer: null,
};

const CHANNEL_COLORS = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2", "#be185d", "#4f46e5"];

const els = {
  canvas: document.getElementById("chartCanvas"),
  fftCanvas: document.getElementById("fftCanvas"),
  message: document.getElementById("chartMessage"),
  fftMessage: document.getElementById("fftMessage"),
  fftWindowSelect: document.getElementById("fftWindowSelect"),
  fftRangeSelect: document.getElementById("fftRangeSelect"),
  fftRateText: document.getElementById("fftRateText"),
  channelSelect: document.getElementById("channelSelect"),
  displayChannelList: document.getElementById("displayChannelList"),
  channelList: document.getElementById("channelList"),
  connectionDot: document.getElementById("connectionDot"),
  connectionText: document.getElementById("connectionText"),
  portText: document.getElementById("portText"),
  voltageValue: document.getElementById("voltageValue"),
  adcValue: document.getElementById("adcValue"),
  rateValue: document.getElementById("rateValue"),
  errorText: document.getElementById("errorText"),
  rawToggle: document.getElementById("rawToggle"),
  smoothToggle: document.getElementById("smoothToggle"),
  windowSelect: document.getElementById("windowSelect"),
  smoothWindow: document.getElementById("smoothWindow"),
  recordName: document.getElementById("recordName"),
  recordDuration: document.getElementById("recordDuration"),
  recordFftRange: document.getElementById("recordFftRange"),
  startRecord: document.getElementById("startRecord"),
  stopRecord: document.getElementById("stopRecord"),
  recordState: document.getElementById("recordState"),
  recordElapsed: document.getElementById("recordElapsed"),
  recordSamples: document.getElementById("recordSamples"),
  recordOutput: document.getElementById("recordOutput"),
  recordMessage: document.getElementById("recordMessage"),
  recordPill: document.getElementById("recordPill"),
};

const ctx = els.canvas.getContext("2d");
const fftCtx = els.fftCanvas.getContext("2d");

function connect() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  state.socket = socket;

  socket.addEventListener("open", () => {
    setConnection("Connected", "warn");
    sendFftConfig();
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    applyChannelPayload(payload);

    updateMetrics();
    updateRecording();
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

function applyChannelPayload(payload) {
  if (!payload.channels) {
    const channelId = state.primaryChannelId || state.activeChannelId || "primary";
    state.primaryChannelId = state.primaryChannelId || channelId;
    state.activeChannelId = state.activeChannelId || channelId;
    upsertChannel(channelId, {
      id: channelId,
      port: payload.status?.port || channelId,
      baudRate: payload.status?.baudRate,
      status: payload.status,
      recording: payload.recording,
      fft: payload.fft,
      samples: payload.samples,
    });
    updateChannelControls();
    return;
  }

  state.primaryChannelId = payload.primaryChannelId || state.primaryChannelId || Object.keys(payload.channels)[0] || null;
  state.activeChannelId = state.activeChannelId || state.primaryChannelId;

  for (const [channelId, channelPayload] of Object.entries(payload.channels)) {
    upsertChannel(channelId, channelPayload);
  }

  if (!state.channels[state.activeChannelId]) {
    state.activeChannelId = state.primaryChannelId;
  }

  updateChannelControls();
}

function upsertChannel(channelId, channelPayload) {
  const existing = state.channels[channelId] || { samples: [] };
  state.channels[channelId] = {
    ...existing,
    id: channelPayload.id || channelId,
    port: channelPayload.port || channelPayload.status?.port || channelId,
    baudRate: channelPayload.baudRate || channelPayload.status?.baudRate,
    status: channelPayload.status,
    recording: channelPayload.recording,
    fft: channelPayload.fft,
  };
  appendSamples(state.channels[channelId].samples, channelPayload.samples);
}

function appendSamples(target, samples) {
  if (!Array.isArray(samples) || samples.length === 0) {
    return;
  }

  target.push(...samples);
  if (target.length > state.maxSamples) {
    target.splice(0, target.length - state.maxSamples);
  }
}

function getActiveChannelId() {
  return state.activeChannelId || state.primaryChannelId || Object.keys(state.channels)[0] || null;
}

function getActiveChannel() {
  const channelId = getActiveChannelId();
  return channelId ? state.channels[channelId] || null : null;
}

function setActiveChannel(channelId) {
  if (!state.channels[channelId]) {
    return;
  }
  state.activeChannelId = channelId;
  updateMetrics();
  updateRecording();
  sendFftConfig();
}

function getChannelIds() {
  return Object.keys(state.channels);
}

function updateChannelControls() {
  const channelIds = getChannelIds();
  if (channelIds.length === 0) {
    return;
  }

  const activeChannelId = getActiveChannelId();
  const renderKey = channelIds.map((channelId) => `${channelId}:${Boolean(state.channels[channelId]?.status?.connected)}`).join("|");
  if (renderKey === state.channelRenderKey) {
    els.channelSelect.value = activeChannelId;
    return;
  }

  state.channelRenderKey = renderKey;
  const existingActive = els.channelSelect.value;
  const selectedDisplayChannels = getSelectedDisplayChannelIds({ fallbackToActive: false });
  const selectedRecordChannels = getSelectedRecordChannelIds({ fallbackToActive: false });

  els.channelSelect.innerHTML = "";
  for (const channelId of channelIds) {
    const channel = state.channels[channelId];
    const option = document.createElement("option");
    option.value = channelId;
    option.textContent = `${channelId}${channel?.status?.connected ? "" : " (offline)"}`;
    els.channelSelect.appendChild(option);
  }
  els.channelSelect.value = state.channels[existingActive] ? existingActive : activeChannelId;

  els.displayChannelList.innerHTML = "";
  for (const channelId of channelIds) {
    const channel = state.channels[channelId];
    const label = document.createElement("label");
    label.className = "channel-choice";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = channelId;
    input.checked =
      selectedDisplayChannels.length > 0
        ? selectedDisplayChannels.includes(channelId)
        : Boolean(channel?.status?.connected) || channelId === activeChannelId;

    const swatch = document.createElement("i");
    swatch.style.background = getChannelColor(channelId);

    const text = document.createElement("span");
    text.textContent = `${channelId} ${channel?.status?.connected ? "Connected" : "Offline"}`;

    label.append(input, swatch, text);
    els.displayChannelList.appendChild(label);
  }

  els.channelList.innerHTML = "";
  for (const channelId of channelIds) {
    const channel = state.channels[channelId];
    const label = document.createElement("label");
    label.className = "channel-choice";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = channelId;
    input.checked = selectedRecordChannels.length > 0 ? selectedRecordChannels.includes(channelId) : channelId === activeChannelId;

    const text = document.createElement("span");
    text.textContent = `${channelId} ${channel?.status?.connected ? "Connected" : "Offline"}`;

    const swatch = document.createElement("i");
    swatch.style.background = getChannelColor(channelId);

    label.append(input, swatch, text);
    els.channelList.appendChild(label);
  }
}

function getSelectedDisplayChannelIds(options = {}) {
  const selected = Array.from(els.displayChannelList.querySelectorAll("input[type='checkbox']:checked")).map(
    (input) => input.value
  );
  if (selected.length > 0 || options.fallbackToActive === false) {
    return selected;
  }

  const connected = getChannelIds().filter((channelId) => state.channels[channelId]?.status?.connected);
  if (connected.length > 0) {
    return connected;
  }

  const activeChannelId = getActiveChannelId();
  return activeChannelId ? [activeChannelId] : [];
}

function getSelectedRecordChannelIds(options = {}) {
  const selected = Array.from(els.channelList.querySelectorAll("input[type='checkbox']:checked")).map((input) => input.value);
  if (selected.length > 0 || options.fallbackToActive === false) {
    return selected;
  }
  const activeChannelId = getActiveChannelId();
  return activeChannelId ? [activeChannelId] : [];
}

function sendFftConfig() {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    return;
  }

  state.socket.send(
    JSON.stringify({
      type: "fft_config_all",
      windowSeconds: Number(els.fftWindowSelect.value),
      maxFrequencyHz: Number(els.fftRangeSelect.value),
    })
  );
}

function getChannelColor(channelId) {
  const index = Math.max(0, getChannelIds().indexOf(channelId));
  return CHANNEL_COLORS[index % CHANNEL_COLORS.length];
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
  const channel = getActiveChannel();
  const status = channel?.status;
  if (!status) {
    return;
  }

  if (status.connected) {
    setConnection("Connected", "on");
  } else {
    setConnection("Disconnected", "off");
  }

  els.portText.textContent = `${status.port} @ ${status.baudRate}`;
  els.rateValue.textContent = `${status.sampleRate.toFixed(0)} Hz`;
  els.errorText.textContent = status.latestError || "";

  if (status.latest) {
    els.voltageValue.textContent = `${status.latest.voltage.toFixed(3)} V`;
    els.adcValue.textContent = String(status.latest.adc);
  }
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || `Request failed: ${response.status}`);
  }
  return data;
}

async function startRecording() {
  const folderName = els.recordName.value.trim();
  const durationSeconds = Number.parseFloat(els.recordDuration.value);
  const smooth = els.smoothToggle.checked;
  const showRaw = els.rawToggle.checked;
  const channelIds = getSelectedRecordChannelIds();

  setRecordMessage("");

  if (!folderName) {
    setRecordMessage("Enter an experiment folder name.", true);
    return;
  }

  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    setRecordMessage("Enter a valid recording duration.", true);
    return;
  }

  if (!smooth && !showRaw) {
    setRecordMessage("Enable Raw or Smooth before recording.", true);
    return;
  }

  if (channelIds.length === 0) {
    setRecordMessage("Select at least one recording channel.", true);
    return;
  }

  if (!channelIds.includes(getActiveChannelId())) {
    state.activeChannelId = channelIds[0];
    els.channelSelect.value = channelIds[0];
  }

  els.startRecord.disabled = true;
  setRecordMessage(channelIds.length > 1 ? "Starting synchronized recordings..." : "Starting recording...");
  try {
    const recording = await postJson("/api/recording/start", {
      channelId: getActiveChannelId(),
      channelIds,
      folderName,
      durationSeconds,
      smooth,
      showRaw,
      smoothWindow: getSmoothWindow(),
      offlineFftMaxFrequencyHz: getOfflineFftMaxFrequency(),
    });
    updateRecordingResponse(recording);
    updateRecording();
  } catch (error) {
    setRecordMessage(error.message, true);
    updateRecording();
  }
}

async function stopRecording() {
  els.stopRecord.disabled = true;
  setRecordMessage("Stopping and saving...");
  const channelIds = getSelectedRecordChannelIds();

  try {
    const recording = await postJson("/api/recording/stop", {
      channelId: getActiveChannelId(),
      channelIds,
    });
    updateRecordingResponse(recording);
    updateRecording();
  } catch (error) {
    setRecordMessage(error.message, true);
    updateRecording();
  }
}

function updateRecordingResponse(response) {
  if (response?.multi && response.channels) {
    for (const [channelId, recording] of Object.entries(response.channels)) {
      if (state.channels[channelId]) {
        state.channels[channelId].recording = recording;
      }
    }
    return;
  }

  if (response?.channelId && state.channels[response.channelId]) {
    state.channels[response.channelId].recording = response.recording;
    return;
  }

  updateActiveChannelRecording(response?.recording || response);
}

function updateActiveChannelRecording(recording) {
  const channel = getActiveChannel();
  if (channel) {
    channel.recording = recording;
  }
}

function setRecordMessage(message, isError = false) {
  els.recordMessage.textContent = message;
  els.recordMessage.classList.toggle("error", isError);
}

function updateRecording() {
  const channel = getActiveChannel();
  const recording = channel?.recording;
  if (!recording) {
    return;
  }

  const active = Boolean(recording.active);
  const saving = Boolean(recording.saving);
  const completed = Boolean(recording.completed);
  const busy = active || saving;
  const duration = Number(recording.durationSeconds || 0);
  const elapsed = Number(recording.elapsedSeconds || 0);

  els.recordState.textContent = saving ? "Saving" : active ? "Recording" : completed ? "Completed" : "Idle";
  els.recordPill.textContent = els.recordState.textContent;
  els.recordPill.classList.toggle("recording", active);
  els.recordPill.classList.toggle("saving", saving);
  els.recordElapsed.textContent = `${elapsed.toFixed(1)} / ${duration.toFixed(1)} s`;
  els.recordSamples.textContent = formatInteger(recording.sampleCount);
  els.recordOutput.textContent = recording.outputDir || "--";
  els.startRecord.disabled = busy;
  els.stopRecord.disabled = !active;

  els.recordName.disabled = busy;
  els.recordDuration.disabled = busy;
  els.recordFftRange.disabled = busy;
  els.channelSelect.disabled = busy;
  for (const input of els.channelList.querySelectorAll("input")) {
    input.disabled = busy;
  }
  for (const input of els.displayChannelList.querySelectorAll("input")) {
    input.disabled = false;
  }
  els.smoothWindow.disabled = busy;
  els.rawToggle.disabled = busy;
  els.smoothToggle.disabled = busy;

  if (recording.error) {
    setRecordMessage(recording.error, true);
  } else if (saving) {
    setRecordMessage("Saving CSV, plot, and offline FFT files...");
  } else if (active) {
    setRecordMessage(`Writing ${recording.csvPath || "raw CSV"}...`);
  } else if (completed && recording.result) {
    const outputs = [
      recording.result.csvPath,
      recording.result.smoothCsvPath,
      recording.result.pngPath,
      recording.result.fftCsvPath,
      recording.result.fftPngPath,
    ].filter(Boolean);
    setRecordMessage(`Saved: ${outputs.join(", ")}`);
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

function getOfflineFftMaxFrequency() {
  const value = els.recordFftRange.value;
  if (value === "full") {
    return "full";
  }
  return Number.parseFloat(value);
}

function visibleSamplesForChannel(channel, startMs) {
  const samples = channel?.samples || [];
  return samples.filter((sample) => sample.pcTimeMs >= startMs);
}

function visibleChannelSeries() {
  const channelIds = getSelectedDisplayChannelIds();
  const windowMs = getWindowSeconds() * 1000;
  const latestMs = Math.max(
    ...channelIds.map((channelId) => state.channels[channelId]?.samples?.at(-1)?.pcTimeMs || Number.NEGATIVE_INFINITY)
  );

  if (!Number.isFinite(latestMs)) {
    return [];
  }

  const startMs = latestMs - windowMs;
  return channelIds
    .map((channelId) => {
      const channel = state.channels[channelId];
      const samples = visibleSamplesForChannel(channel, startMs);
      return {
        id: channelId,
        channel,
        color: getChannelColor(channelId),
        samples,
        times: samples.map((sample) => (sample.pcTimeMs - startMs) / 1000),
        voltages: samples.map((sample) => sample.voltage),
      };
    })
    .filter((series) => series.samples.length >= 2);
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

function resizeFftCanvas() {
  const rect = els.fftCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));

  if (els.fftCanvas.width !== width || els.fftCanvas.height !== height) {
    els.fftCanvas.width = width;
    els.fftCanvas.height = height;
  }

  fftCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return rect;
}

function draw() {
  const rect = resizeCanvas();
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);

  const seriesList = visibleChannelSeries();
  const showRaw = els.rawToggle.checked;
  const showSmooth = els.smoothToggle.checked;

  if (!showRaw && !showSmooth) {
    els.message.textContent = "Enable Raw or Smooth";
    drawFft();
    requestAnimationFrame(draw);
    return;
  }

  if (seriesList.length === 0) {
    els.message.textContent = "Waiting for samples";
    drawFrame(width, height, 0, 1, 0, getWindowSeconds());
    drawFft();
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

  const plottedValues = [];
  for (const series of seriesList) {
    series.smoothVoltages = showSmooth ? movingAverage(series.voltages, getSmoothWindow()) : [];
    if (showRaw) {
      plottedValues.push(...series.voltages);
    }
    if (showSmooth) {
      plottedValues.push(...series.smoothVoltages);
    }
  }

  const yRange = paddedRange(plottedValues);
  drawFrame(width, height, yRange.min, yRange.max, 0, getWindowSeconds(), plot);

  const legendItems = [];
  for (const series of seriesList) {
    if (showRaw) {
      drawLine(series.times, series.voltages, yRange, plot, series.color, showSmooth ? 0.3 : 0.95, showSmooth ? 1.1 : 1.8);
      legendItems.push([`${series.id} Raw`, series.color, showSmooth ? 0.45 : 1]);
    }
    if (showSmooth) {
      drawLine(series.times, series.smoothVoltages, yRange, plot, series.color, 1, 2.2);
      legendItems.push([`${series.id} Smooth`, series.color, 1]);
    }
  }

  drawLegendItems(ctx, legendItems, plot);
  drawFft();
  requestAnimationFrame(draw);
}

function drawFft() {
  const rect = resizeFftCanvas();
  const width = rect.width;
  const height = rect.height;
  fftCtx.clearRect(0, 0, width, height);

  const fftSeries = getSelectedDisplayChannelIds()
    .map((channelId) => ({
      id: channelId,
      color: getChannelColor(channelId),
      fft: state.channels[channelId]?.fft,
    }))
    .filter((series) => series.fft);

  if (fftSeries.length === 0) {
    els.fftMessage.textContent = "Waiting for FFT data";
    drawSpectrumFrame(width, height, 0, 1, 0, 1);
    return;
  }

  const activeFft = getActiveChannel()?.fft || fftSeries[0].fft;
  els.fftRateText.textContent = activeFft.sampleRateHz
    ? `Sample Rate: ${Number(activeFft.sampleRateHz).toFixed(1)} Hz`
    : "Sample Rate: --";

  const readySeries = fftSeries.filter(
    (series) => series.fft.ready && Array.isArray(series.fft.frequencyHz) && series.fft.frequencyHz.length >= 2
  );

  if (readySeries.length === 0) {
    els.fftMessage.textContent = activeFft.message || "Waiting for FFT data";
    drawSpectrumFrame(width, height, 0, 1, 0, Number(activeFft.maxFrequencyHz || 1));
    return;
  }

  els.fftMessage.textContent = "";

  const maxFrequency = Math.max(
    ...readySeries.map((series) => Number(series.fft.maxFrequencyHz || series.fft.frequencyHz.at(-1) || 1))
  );
  const yRange = paddedRange(readySeries.flatMap((series) => series.fft.amplitudeV));
  const padding = { left: 72, right: 20, top: 18, bottom: 42 };
  const plot = {
    x: padding.left,
    y: padding.top,
    width: width - padding.left - padding.right,
    height: height - padding.top - padding.bottom,
  };

  drawSpectrumFrame(width, height, yRange.min, yRange.max, 0, maxFrequency, plot);
  const legendItems = [];
  for (const series of readySeries) {
    drawSpectrumLine(series.fft.frequencyHz, series.fft.amplitudeV, yRange, plot, maxFrequency, series.color);
    legendItems.push([`${series.id} FFT`, series.color, 1]);
  }
  drawLegendItems(fftCtx, legendItems, plot);
}

function drawSpectrumFrame(width, height, yMin, yMax, xMin, xMax, plotOverride) {
  const plot =
    plotOverride ||
    {
      x: 72,
      y: 18,
      width: width - 92,
      height: height - 60,
    };

  fftCtx.save();
  fftCtx.fillStyle = "#ffffff";
  fftCtx.fillRect(0, 0, width, height);

  fftCtx.strokeStyle = "#e8edf4";
  fftCtx.lineWidth = 1;
  fftCtx.font = "12px system-ui, sans-serif";
  fftCtx.fillStyle = "#697386";

  const yTicks = 4;
  for (let i = 0; i <= yTicks; i += 1) {
    const t = i / yTicks;
    const y = plot.y + plot.height * t;
    const value = yMax - (yMax - yMin) * t;

    fftCtx.beginPath();
    fftCtx.moveTo(plot.x, y);
    fftCtx.lineTo(plot.x + plot.width, y);
    fftCtx.stroke();
    fftCtx.fillText(`${value.toFixed(4)} V`, 10, y + 4);
  }

  const xTicks = 5;
  for (let i = 0; i <= xTicks; i += 1) {
    const t = i / xTicks;
    const x = plot.x + plot.width * t;
    const value = xMin + (xMax - xMin) * t;

    fftCtx.beginPath();
    fftCtx.moveTo(x, plot.y);
    fftCtx.lineTo(x, plot.y + plot.height);
    fftCtx.stroke();
    fftCtx.fillText(`${value.toFixed(2)}Hz`, x - 18, plot.y + plot.height + 24);
  }

  fftCtx.strokeStyle = "#d8dee9";
  fftCtx.strokeRect(plot.x, plot.y, plot.width, plot.height);
  fftCtx.restore();
}

function drawSpectrumLine(frequencies, amplitudes, yRange, plot, maxFrequency, color = "#7c3aed") {
  if (frequencies.length < 2 || amplitudes.length < 2) {
    return;
  }

  const ySpan = yRange.max - yRange.min;

  fftCtx.save();
  fftCtx.strokeStyle = color;
  fftCtx.lineWidth = 1.6;
  fftCtx.lineJoin = "round";
  fftCtx.lineCap = "round";
  fftCtx.beginPath();

  for (let i = 0; i < frequencies.length; i += 1) {
    const x = plot.x + (frequencies[i] / maxFrequency) * plot.width;
    const y = plot.y + plot.height - ((amplitudes[i] - yRange.min) / ySpan) * plot.height;

    if (i === 0) {
      fftCtx.moveTo(x, y);
    } else {
      fftCtx.lineTo(x, y);
    }
  }

  fftCtx.stroke();
  fftCtx.restore();
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

function drawLegendItems(canvasCtx, items, plot) {
  if (!items.length) {
    return;
  }

  canvasCtx.save();
  canvasCtx.font = "13px system-ui, sans-serif";
  let x = plot.x + 12;
  const y = plot.y + 18;

  for (const [label, color, alpha = 1] of items.slice(0, 10)) {
    canvasCtx.globalAlpha = alpha;
    canvasCtx.strokeStyle = color;
    canvasCtx.lineWidth = 3;
    canvasCtx.beginPath();
    canvasCtx.moveTo(x, y);
    canvasCtx.lineTo(x + 22, y);
    canvasCtx.stroke();
    canvasCtx.globalAlpha = 1;
    canvasCtx.fillStyle = "#18202f";
    canvasCtx.fillText(label, x + 30, y + 4);
    x += Math.max(76, label.length * 7 + 58);
    if (x > plot.x + plot.width - 120) {
      break;
    }
  }
  canvasCtx.restore();
}

window.addEventListener("resize", () => {
  resizeCanvas();
  resizeFftCanvas();
});
els.startRecord.addEventListener("click", startRecording);
els.stopRecord.addEventListener("click", stopRecording);
els.channelSelect.addEventListener("change", () => setActiveChannel(els.channelSelect.value));
els.fftWindowSelect.addEventListener("change", sendFftConfig);
els.fftRangeSelect.addEventListener("change", sendFftConfig);
connect();
requestAnimationFrame(draw);
