const apiUrl = (path) => `${rootPath}${path}`;
const slopeEl = document.getElementById("slope");
const exponentEl = document.getElementById("exponent");
const targetRoomTempCEl = document.getElementById("target-room-temp-c");
const offsetEl = document.getElementById("offset");
const forceMaxPowerBelowTargetEl = document.getElementById("force-max-power-below-target");
const forceMaxPowerMarginCEl = document.getElementById("force-max-power-margin-c");
const minSupplyTempCEl = document.getElementById("min-supply-temp-c");
const maxSupplyTempCEl = document.getElementById("max-supply-temp-c");
const previewEl = document.getElementById("heating-curve-preview");
const chartCtx = document.getElementById("heating-curve-chart").getContext("2d");

let chart;
let targetRoomTempC = 22.0;

function getFormData() {
    const slope = Number(slopeEl.value);
    const exponent = Number(exponentEl.value);
    const offset = Number(offsetEl.value);
    const forceMaxPowerMarginC = Number(forceMaxPowerMarginCEl.value);
    const minSupplyTempC = Number(minSupplyTempCEl.value);
    const maxSupplyTempC = Number(maxSupplyTempCEl.value);
    return {
        slope: Number.isFinite(slope) ? slope : 6.0,
        exponent: Number.isFinite(exponent) ? exponent : 0.4,
        offset: Number.isFinite(offset) ? offset : 0.0,
        force_max_power_below_target: Boolean(forceMaxPowerBelowTargetEl.checked),
        force_max_power_margin_c: Number.isFinite(forceMaxPowerMarginC) ? forceMaxPowerMarginC : 5.0,
        min_supply_temp_c: Number.isFinite(minSupplyTempC) ? minSupplyTempC : 25.0,
        max_supply_temp_c: Number.isFinite(maxSupplyTempC) ? maxSupplyTempC : 60.0,
    };
}

function applyFormData(data) {
    slopeEl.value = data.slope;
    exponentEl.value = data.exponent;
    offsetEl.value = data.offset;
    forceMaxPowerBelowTargetEl.checked = Boolean(data.force_max_power_below_target);
    forceMaxPowerMarginCEl.value = data.force_max_power_margin_c;
    minSupplyTempCEl.value = data.min_supply_temp_c;
    maxSupplyTempCEl.value = data.max_supply_temp_c;
    renderPreview();
}

function computeCurvePoint(outdoorTempC, data) {
    const delta = targetRoomTempC - outdoorTempC;
    if (delta < 0) {
        return null;
    }
    const unclamped = (data.slope * (delta ** data.exponent)) + data.offset + targetRoomTempC;
    return Math.min(data.max_supply_temp_c, Math.max(data.min_supply_temp_c, unclamped));
}

function resolveTargetRoomTempC(parsedConfig) {
    if (!parsedConfig || typeof parsedConfig !== "object") {
        return 22.0;
    }
    const heatingMode = parsedConfig.heating_mode;
    if (!heatingMode || typeof heatingMode !== "object") {
        return 22.0;
    }
    const params = heatingMode.params;
    if (!params || typeof params !== "object") {
        return 22.0;
    }
    const value = Number(params.target_room_temp_c);
    return Number.isFinite(value) ? value : 22.0;
}

function renderPreview() {
    const data = getFormData();
    targetRoomTempCEl.value = String(targetRoomTempC);
    previewEl.textContent = JSON.stringify({
        target_room_temp_c: targetRoomTempC,
        formula: "Ft = S * (Tt - Ct)^exponent + O + Tt",
        heating_curve: data,
    }, null, 2);

    const points = [];
    const minSupplyPoints = [];
    const maxSupplyPoints = [];
    const maxOutdoorTempC = Math.floor(targetRoomTempC);
    for (let outdoorTempC = -30; outdoorTempC <= maxOutdoorTempC; outdoorTempC += 1) {
        const supplyTempC = computeCurvePoint(outdoorTempC, data);
        if (supplyTempC === null) {
            continue;
        }
        points.push({
            x: outdoorTempC,
            y: supplyTempC,
        });
        minSupplyPoints.push({
            x: outdoorTempC,
            y: data.min_supply_temp_c,
        });
        maxSupplyPoints.push({
            x: outdoorTempC,
            y: data.max_supply_temp_c,
        });
    }

    if (chart) chart.destroy();
    chart = new Chart(chartCtx, {
        type: "line",
        data: {
            datasets: [
                {
                    label: "Supply temp",
                    data: points,
                    borderColor: "#2563eb",
                    backgroundColor: "#2563eb22",
                    tension: 0.2,
                    fill: false,
                },
                {
                    label: "Min supply",
                    data: minSupplyPoints,
                    borderColor: "#16a34a",
                    backgroundColor: "#16a34a22",
                    borderDash: [6, 4],
                    pointRadius: 0,
                    tension: 0,
                    fill: false,
                },
                {
                    label: "Max supply",
                    data: maxSupplyPoints,
                    borderColor: "#dc2626",
                    backgroundColor: "#dc262622",
                    borderDash: [6, 4],
                    pointRadius: 0,
                    tension: 0,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            scales: {
                x: {
                    type: "linear",
                    reverse: true,
                    max: maxOutdoorTempC,
                    min: -30,
                    title: { display: true, text: "Outdoor temperature °C" },
                },
                y: {
                    title: { display: true, text: "Supply temperature °C" },
                },
            },
        },
    });
}

async function loadHeatingCurve() {
    previewEl.textContent = "Loading...";
    const [curveRes, configRes] = await Promise.all([
        fetch(apiUrl("/api/heating-curve")),
        fetch(apiUrl("/api/config")),
    ]);
    const curvePayload = await curveRes.json();
    const configPayload = await configRes.json();
    if (!curveRes.ok) {
        previewEl.textContent = "Error: " + (curvePayload.detail || "Failed to load");
        return;
    }
    targetRoomTempC = resolveTargetRoomTempC(configPayload.parsed || {});
    applyFormData(curvePayload.data || {});
}

async function saveHeatingCurve() {
    const res = await fetch(apiUrl("/api/heating-curve"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(getFormData()),
    });
    const payload = await res.json();
    applyFormData(payload.data || {});
}

slopeEl.addEventListener("input", renderPreview);
exponentEl.addEventListener("input", renderPreview);
offsetEl.addEventListener("input", renderPreview);
forceMaxPowerBelowTargetEl.addEventListener("change", renderPreview);
forceMaxPowerMarginCEl.addEventListener("input", renderPreview);
minSupplyTempCEl.addEventListener("input", renderPreview);
maxSupplyTempCEl.addEventListener("input", renderPreview);

document.getElementById("reload").addEventListener("click", loadHeatingCurve);
document.getElementById("save").addEventListener("click", saveHeatingCurve);

loadHeatingCurve();
