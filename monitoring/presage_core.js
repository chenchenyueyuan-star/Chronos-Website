"use strict";
// SDK logic extracted from the user's New folder (2)/server.js.
// Account delivery and input selection are adapted; processing/decoding follows the supplied sample.
function createPresageCore(api, apiKey, deliver, requestedMetrics = null, inputMode = 'browser') {
const {SmartSpectraSDK, PixelFormat, ProcessingStatus, breathingMetrics, cardioMetrics, faceMetrics, decodeMetrics} = api;
const maxMetricSamples = 60;
const state = {
  running: false,
  processingStatus: 1,
  stopInProgress: false,
  validation: null,
  latestMetrics: null,
  metricHistory: { pulse: [], breathing: [], expressions: [] },
  pythonDelivery: { status: "waiting", error: null },
  error: null,
};

function appendMeasurements(history, measurements, timestampUs) {
  if (!Array.isArray(measurements)) return;
  const knownTimestamps = new Set(history.map((sample) => sample.timestampUs));
  for (const measurement of measurements) {
    const value = Number(measurement?.value);
    if (!Number.isFinite(value)) continue;
    const sampleTimestamp = String(measurement.timestamp ?? timestampUs);
    if (knownTimestamps.has(sampleTimestamp)) continue;
    knownTimestamps.add(sampleTimestamp);
    history.push({
      value,
      confidence: Number.isFinite(Number(measurement.confidence))
        ? Number(measurement.confidence)
        : null,
      stable: Boolean(measurement.stable),
      timestampUs: sampleTimestamp,
    });
  }
  if (history.length > 60) history.splice(0, history.length - 60);
}

function trimMetricSamples(value) {
  if (Array.isArray(value)) {
    if (value.length > maxMetricSamples) {
      value.splice(0, value.length - maxMetricSamples);
    }
    for (const sample of value) trimMetricSamples(sample);
  } else if (value && typeof value === "object") {
    for (const field of Object.values(value)) trimMetricSamples(field);
  }
}

const sdk = new SmartSpectraSDK({
  apiKey: apiKey,
  requestedMetrics: requestedMetrics ?? [...breathingMetrics, ...cardioMetrics, ...faceMetrics],
  enableTelemetry: false,
  enableAccumulatedOutput: true,
  logLevel: api.SmartSpectraLogLevel?.kInfo,
});

function selectInput() {
  if (inputMode === 'native') {
    const options = {width: 640, height: 480, fps: 30};
    if (api.CameraSelection?.default) sdk.useCamera(api.CameraSelection.default, options);
    else sdk.useCamera(options);
  } else sdk.useCustomInput();
}

let stopPromise = null;

function stopMeasurement() {
  if (stopPromise) return stopPromise;

  stopPromise = (async () => {
    state.stopInProgress = true;
    try {
      let status = sdk.processingStatus;
      if (status === ProcessingStatus.kError) {
        sdk.reset();
        selectInput();
      } else if (
        status === ProcessingStatus.kStarting ||
        status === ProcessingStatus.kRunning ||
        status === ProcessingStatus.kStopping
      ) {
        await sdk.stopAsync();
      }

      const deadline = Date.now() + 10000;
      status = sdk.processingStatus;
      while (
        status !== ProcessingStatus.kUninitialized &&
        status !== ProcessingStatus.kIdle &&
        status !== ProcessingStatus.kError &&
        Date.now() < deadline
      ) {
        await new Promise((resolve) => setTimeout(resolve, 50));
        status = sdk.processingStatus;
      }

      if (status === ProcessingStatus.kError) {
        sdk.reset();
        selectInput();
        status = sdk.processingStatus;
      }
      if (
        status !== ProcessingStatus.kUninitialized &&
        status !== ProcessingStatus.kIdle
      ) {
        throw new Error(`SmartSpectra did not stop; current state is ${status}.`);
      }

      state.processingStatus = status;
      state.running = false;
      state.validation = null;
      state.error = null;
    } finally {
      state.stopInProgress = false;
    }
  })().finally(() => {
    stopPromise = null;
  });

  return stopPromise;
}

selectInput();
sdk.on("processingStatus", (status) => {
  state.processingStatus = status;
  state.running = status === ProcessingStatus.kRunning;
});
sdk.on("validationStatus", (code, timestampUs, hint) => {
  state.validation = { code, timestampUs, hint };
});
function handleMetrics(buffer, timestampUs) {
  let metrics;
  try {
    const decoded = decodeMetrics(buffer);
    // The app uses these three streams. Avoid repeatedly serializing face
    // landmarks and unrelated waveforms on the camera processing thread.
    metrics = {
      cardio: {pulseRate: decoded.cardio?.pulseRate ?? []},
      breathing: {rate: decoded.breathing?.rate ?? []},
      face: {expression: decoded.face?.expression ?? []},
    };
    trimMetricSamples(metrics);
    metrics = JSON.parse(JSON.stringify(metrics));
  } catch (error) {
    state.error = `Unable to decode SmartSpectra metrics: ${error.message}`;
    console.error(state.error);
    return;
  }

  const payload = { type: "metrics", timestampUs, metrics };
  state.latestMetrics = { ...payload, receivedAt: new Date().toISOString() };
  appendMeasurements(
    state.metricHistory.pulse,
    metrics.cardio?.pulseRate,
    timestampUs,
  );
  appendMeasurements(
    state.metricHistory.breathing,
    metrics.breathing?.rate,
    timestampUs,
  );
  if (Array.isArray(metrics.face?.expression)) {
    const knownTimestamps = new Set(
      state.metricHistory.expressions.map((sample) => sample.timestampUs),
    );
    for (const expression of metrics.face.expression) {
      if (!Array.isArray(expression?.scores)) continue;
      const sampleTimestamp = String(expression.timestamp ?? timestampUs);
      if (knownTimestamps.has(sampleTimestamp)) continue;
      knownTimestamps.add(sampleTimestamp);
      state.metricHistory.expressions.push({
        timestampUs: sampleTimestamp,
        stable: Boolean(expression.stable),
        scores: expression.scores
          .filter((score) => Number.isFinite(Number(score.confidence)))
          .map((score) => ({
            type: score.type,
            confidence: Number(score.confidence),
          })),
      });
    }
    if (state.metricHistory.expressions.length > 60) {
      state.metricHistory.expressions.splice(
        0,
        state.metricHistory.expressions.length - 60,
      );
    }
  }
  void forwardMetrics(payload);
}
sdk.on("metrics", handleMetrics);
sdk.on("accumulatedMetrics", handleMetrics);
sdk.on("error", (code, message, retryable) => {
  state.error = `SmartSpectra error ${code}: ${message}`;
  state.running = false;
  console.error(`${state.error} (retryable: ${retryable})`);
});

async function forwardMetrics(payload) {
  state.pythonDelivery = {status: "sending", error: null};
  try { await deliver(payload); state.pythonDelivery = {status: "delivered", error: null}; }
  catch { state.pythonDelivery = {status: "failed", error: "Measurement delivery unavailable."}; }
}
function startMeasurement() {
  if (state.stopInProgress) throw new Error("Wait for the current measurement to stop before starting again.");
  const status = sdk.processingStatus;
  if (status === ProcessingStatus.kError) {
    sdk.reset();
    selectInput();
    state.processingStatus = sdk.processingStatus;
    state.validation = null;
  } else if (status !== ProcessingStatus.kUninitialized && status !== ProcessingStatus.kIdle) {
    throw new Error(`Cannot start while SmartSpectra is in state ${status}. Stop the current measurement and try again.`);
  }
  sdk.start();
  state.metricHistory = {pulse: [], breathing: [], expressions: []};
  state.latestMetrics = null;
  state.validation = null;
  state.error = null;
}
function sendFrame(frame, width, height, timestampUs) {
  if (state.stopInProgress || (sdk.processingStatus !== ProcessingStatus.kStarting && sdk.processingStatus !== ProcessingStatus.kRunning)) return false;
  return sdk.sendFrame(frame, width, height, width * 4, PixelFormat.kRGBA, timestampUs);
}
return {sdk, state, startMeasurement, stopMeasurement, sendFrame};
}
module.exports = {createPresageCore};
