const apiUrl = (path) => `${rootPath}${path}`;
const slopeEl = document.getElementById("slope");
const forceMaxPowerBelowTargetEl = document.getElementById("force-max-power-below-target");
const forceMaxPowerMarginCEl = document.getElementById("force-max-power-margin-c");
const minSupplyTempCEl = document.getElementById("min-supply-temp-c");
const maxSupplyTempCEl = document.getElementById("max-supply-temp-c");
const previewEl = document.getElementById("heating-curve-preview");
const chartCtx = document.getElementById("heating-curve-chart").getContext("2d");

let chart;

function getFormData() {
    const slope = Number(slopeEl.value);
    const forceMaxPowerMarginC = Number(forceMaxPowerMarginCEl.value);
    const minSupplyTempC = Number(minSupplyTempCEl.value);
    const maxSupplyTempC = Number(maxSupplyTempCEl.value);
    return {
        slope: Number.isFinite(slope) ? slope : 1.2,
        force_max_power_below_target: Boolean(forceMaxPowerBelowTargetEl.checked),
        force_max_power_margin_c: Number.isFinite(forceMaxPowerMarginC) ? forceMaxPowerMarginC : 5.0,
        min_supply_temp_c: Number.isFinite(minSupplyTempC) ? minSupplyTempC : 25.0,
        max_supply_temp_c: Number.isFinite(maxSupplyTempC) ? maxSupplyTempC : 60.0,
    };
}

function applyFormData(data) {
    slopeEl.value = data.slope;
    forceMaxPowerBelowTargetEl.checked = Boolean(data.force_max_power_below_target);
    forceMaxPowerMarginCEl.value = data.force_max_power_margin_c;
    minSupplyTempCEl.value = data.min_supply_temp_c;
    maxSupplyTempCEl.value = data.max_supply_temp_c;
    renderPreview();
}

function computeCurvePoint(outdoorTempC, data) {
    const unclamped = data.min_supply_temp_c + (20 - outdoorTempC) * data.slope;
    return Math.min(data.max_supply_temp_c, Math.max(data.min_supply_temp_c, unclamped));
}

function renderPreview() {
    const data = getFormData();
    previewEl.textContent = JSON.stringify(data, null, 2);

    const points = [];
    for (let outdoorTempC = -30; outdoorTempC <= 20; outdoorTempC += 1) {
        points.push({
            x: outdoorTempC,
            y: computeCurvePoint(outdoorTempC, data),
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
            ],
        },
        options: {
            responsive: true,
            scales: {
                x: {
                    type: "linear",
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
    const res = await fetch(apiUrl("/api/heating-curve"));
    const payload = await res.json();
    applyFormData(payload.data || {});
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
forceMaxPowerBelowTargetEl.addEventListener("change", renderPreview);
forceMaxPowerMarginCEl.addEventListener("input", renderPreview);
minSupplyTempCEl.addEventListener("input", renderPreview);
maxSupplyTempCEl.addEventListener("input", renderPreview);

document.getElementById("reload").addEventListener("click", loadHeatingCurve);
document.getElementById("save").addEventListener("click", saveHeatingCurve);

loadHeatingCurve();
