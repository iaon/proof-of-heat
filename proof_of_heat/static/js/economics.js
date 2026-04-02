const apiUrl = (path) => `${rootPath}${path}`;
const currentEl = document.getElementById("economics-current");
const currentMetaEl = document.getElementById("economics-current-meta");
const currentErrorsEl = document.getElementById("economics-current-errors");
const refreshCurrentBtn = document.getElementById("refresh-current");
const metricRowsEl = document.getElementById("metric-rows");
const addMetricBtn = document.getElementById("add-metric");
const startDateEl = document.getElementById("start-date");
const startHourEl = document.getElementById("start-hour");
const startMinuteEl = document.getElementById("start-minute");
const startSecondEl = document.getElementById("start-second");
const endDateEl = document.getElementById("end-date");
const endHourEl = document.getElementById("end-hour");
const endMinuteEl = document.getElementById("end-minute");
const endSecondEl = document.getElementById("end-second");
const rangeButtons = Array.from(document.querySelectorAll(".range-btn"));
const emptyEl = document.getElementById("empty");
const ctx = document.getElementById("chart").getContext("2d");
const presetButtons = Array.from(document.querySelectorAll(".preset-btn"));
const STORAGE_KEY = "proof_of_heat_economics_view_v1";
const palette = ["#2563eb", "#dc2626", "#16a34a", "#7c3aed", "#ea580c", "#0891b2", "#db2777", "#0f766e"];

let chart;
let metricRowSeq = 0;
let availableMetrics = [];
let catalogData = {
    enabled: true,
    currencies: {},
    current_metrics: [],
    labels: {},
    presets: {},
    stale_after_ms_by_metric: {},
};
const metricRows = [];

function prettifyMetricName(name) {
    return name.replaceAll("_", " ");
}

function describeEconomicsMetric(metricName) {
    if (!metricName) return "—";
    return catalogData.labels[metricName] || prettifyMetricName(metricName);
}

function getCurrentMetricsOrder() {
    const configured = Array.isArray(catalogData.current_metrics) ? catalogData.current_metrics : [];
    return configured.length ? configured : availableMetrics;
}

function getPresetMetrics(name) {
    const preset = catalogData.presets[name];
    return Array.isArray(preset && preset.metrics) ? preset.metrics : [];
}

function getMetricGapMs(metricName) {
    const gapMs = Number((catalogData.stale_after_ms_by_metric || {})[metricName]);
    return Number.isFinite(gapMs) && gapMs > 0 ? gapMs * 3 : null;
}

function renderPresetLabels() {
    presetButtons.forEach((button) => {
        const presetName = button.getAttribute("data-preset");
        const preset = catalogData.presets[presetName];
        if (preset && preset.label) button.textContent = preset.label;
    });
}

function formatCurrentMetricValue(metricName, value) {
    if (value === null || value === undefined) return "—";
    const num = Number(value);
    if (Number.isNaN(num)) return String(value);
    if (metricName === "network_hashrate_th_s") {
        return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(num);
    }
    if (Math.abs(num) >= 1000) {
        return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(num);
    }
    if (Math.abs(num) >= 1) {
        return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 6 }).format(num);
    }
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 12 }).format(num);
}

function formatDateTime(value) {
    return new Intl.DateTimeFormat("ru-RU", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    }).format(new Date(value));
}

function setRangeToLastHours(hours) {
    const rangeHours = Number(hours);
    if (!Number.isFinite(rangeHours) || rangeHours <= 0) return;
    const now = new Date();
    const start = new Date(now.getTime() - rangeHours * 60 * 60 * 1000);
    const pad = (num) => String(num).padStart(2, "0");
    const toDateValue = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    startDateEl.value = toDateValue(start);
    endDateEl.value = toDateValue(now);
    startHourEl.value = pad(start.getHours());
    startMinuteEl.value = pad(start.getMinutes());
    startSecondEl.value = pad(start.getSeconds());
    endHourEl.value = pad(now.getHours());
    endMinuteEl.value = pad(now.getMinutes());
    endSecondEl.value = pad(now.getSeconds());
}

function parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue) {
    if (!dateValue) return null;
    const [year, month, day] = dateValue.split("-").map(Number);
    const hour = Number(hourValue);
    const minute = Number(minuteValue);
    const second = Number(secondValue);
    if ([year, month, day, hour, minute, second].some((item) => Number.isNaN(item))) return null;
    return new Date(year, month - 1, day, hour, minute, second, 0);
}

function toIsoWithOffset(dateValue, hourValue, minuteValue, secondValue) {
    const date = parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue);
    if (!date) return "";
    const pad = (num) => String(num).padStart(2, "0");
    const tzOffset = -date.getTimezoneOffset();
    const sign = tzOffset >= 0 ? "+" : "-";
    const offsetHours = pad(Math.floor(Math.abs(tzOffset) / 60));
    const offsetMinutes = pad(Math.abs(tzOffset) % 60);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${offsetHours}:${offsetMinutes}`;
}

function parseLocalInputToMs(dateValue, hourValue, minuteValue, secondValue) {
    const date = parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue);
    return date ? date.getTime() : null;
}

function persistState() {
    const payload = {
        series: metricRows.map((row) => ({
            metric: row.metricValueEl.value || "",
        })),
    };
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (err) {
        // Ignore storage failures.
    }
}

function loadState() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") return null;
        return parsed;
    } catch (err) {
        return null;
    }
}

function closeAllMetricDropdowns(exceptRowId = null) {
    metricRows.forEach((row) => {
        if (row.id !== exceptRowId) row.dropdownEl.hidden = true;
    });
}

function updateRemoveButtons() {
    metricRows.forEach((row) => {
        row.removeBtn.hidden = metricRows.length <= 1;
    });
}

function renderRowDropdown(row, filterValue = "") {
    const query = filterValue.trim().toLowerCase();
    const filtered = row.options.filter((item) => {
        if (!query) return true;
        return item.value.toLowerCase().includes(query) || item.human.toLowerCase().includes(query);
    });
    if (!filtered.length) {
        row.dropdownEl.innerHTML = '<div class="metric-empty">No matching metrics</div>';
        return;
    }
    row.dropdownEl.innerHTML = filtered.map((item) => {
        const activeClass = item.value === row.metricValueEl.value ? " active" : "";
        return `<div class="metric-option${activeClass}" data-value="${item.value}">
                    <div class="metric-db-name">${item.value}</div>
                    <div class="metric-human-name">${item.human}</div>
                </div>`;
    }).join("");
}

function updateRowInfo(row) {
    const metricName = row.metricValueEl.value || "";
    row.fullNameEl.textContent = metricName || "—";
    row.humanEl.textContent = describeEconomicsMetric(metricName);
}

function selectMetricForRow(row, value) {
    const selected = row.options.find((item) => item.value === value);
    if (!selected) {
        row.metricValueEl.value = "";
        row.searchEl.value = "";
        updateRowInfo(row);
        renderRowDropdown(row, row.searchEl.value);
        return;
    }
    row.metricValueEl.value = selected.value;
    row.searchEl.value = selected.value;
    updateRowInfo(row);
    renderRowDropdown(row, row.searchEl.value);
}

function loadMetricsForRow(row) {
    const prevMetric = row.metricValueEl.value;
    row.options = availableMetrics.map((value) => ({
        value,
        human: describeEconomicsMetric(value),
    }));
    if (prevMetric && row.options.some((item) => item.value === prevMetric)) {
        selectMetricForRow(row, prevMetric);
    } else {
        row.metricValueEl.value = "";
        row.searchEl.value = "";
        updateRowInfo(row);
    }
    renderRowDropdown(row, row.searchEl.value);
}

function collectSelectedSeries() {
    return metricRows
        .map((row) => ({
            metric: row.metricValueEl.value,
            label: describeEconomicsMetric(row.metricValueEl.value),
        }))
        .filter((item) => Boolean(item.metric));
}

function createMetricRow(initialState = null) {
    metricRowSeq += 1;
    const rowId = metricRowSeq;
    const rowEl = document.createElement("div");
    rowEl.className = "metric-row";
    rowEl.innerHTML = `
        <div class="metric-row-head">
            <div class="metric-picker">
                <input id="metric-search-${rowId}" type="text" autocomplete="off" placeholder="Select or search economics metric..." />
                <input id="metric-value-${rowId}" type="hidden" />
                <div id="metric-dropdown-${rowId}" class="metric-dropdown" hidden></div>
            </div>
            <button id="metric-remove-${rowId}" type="button" class="metric-remove">-</button>
        </div>
        <div class="metric-meta">
            <div>Full DB metric name: <code id="metric-full-name-${rowId}">—</code></div>
            <div>Description: <span id="metric-human-${rowId}">—</span></div>
        </div>
    `;
    metricRowsEl.appendChild(rowEl);

    const row = {
        id: rowId,
        rowEl,
        searchEl: rowEl.querySelector(`#metric-search-${rowId}`),
        metricValueEl: rowEl.querySelector(`#metric-value-${rowId}`),
        dropdownEl: rowEl.querySelector(`#metric-dropdown-${rowId}`),
        removeBtn: rowEl.querySelector(`#metric-remove-${rowId}`),
        fullNameEl: rowEl.querySelector(`#metric-full-name-${rowId}`),
        humanEl: rowEl.querySelector(`#metric-human-${rowId}`),
        options: [],
        initialState,
    };
    metricRows.push(row);
    loadMetricsForRow(row);

    row.searchEl.addEventListener("focus", () => {
        closeAllMetricDropdowns(row.id);
        renderRowDropdown(row, row.searchEl.value);
        row.dropdownEl.hidden = false;
    });
    row.searchEl.addEventListener("input", () => {
        row.metricValueEl.value = "";
        updateRowInfo(row);
        closeAllMetricDropdowns(row.id);
        renderRowDropdown(row, row.searchEl.value);
        row.dropdownEl.hidden = false;
        persistState();
        loadChart();
    });
    row.dropdownEl.addEventListener("click", (event) => {
        const option = event.target.closest(".metric-option");
        if (!option) return;
        selectMetricForRow(row, option.getAttribute("data-value") || "");
        row.dropdownEl.hidden = true;
        persistState();
        loadChart();
    });
    row.removeBtn.addEventListener("click", () => {
        if (metricRows.length <= 1) return;
        const idx = metricRows.findIndex((item) => item.id === row.id);
        if (idx >= 0) {
            metricRows.splice(idx, 1);
            row.rowEl.remove();
            updateRemoveButtons();
            persistState();
            loadChart();
        }
    });

    updateRowInfo(row);
    renderRowDropdown(row, "");
    updateRemoveButtons();
    return row;
}

function clearMetricRows() {
    metricRows.splice(0, metricRows.length);
    metricRowsEl.innerHTML = "";
    updateRemoveButtons();
}

function renderCurrentValues(data, polledAt, errors) {
    const currencies = catalogData.currencies || {};
    const currenciesText = currencies.crypto && currencies.fiat
        ? `Currencies: ${currencies.crypto}/${currencies.fiat}`
        : "";
    const updateText = polledAt ? `Last update: ${polledAt}` : "";
    currentEl.innerHTML = "";
    currentMetaEl.textContent = [currenciesText, updateText].filter(Boolean).join(" · ");

    const warnings = [];
    if (catalogData.enabled === false) warnings.push("Economics is disabled in config.");
    if (Array.isArray(errors) && errors.length) warnings.push(`Warnings: ${errors.join(" | ")}`);
    currentErrorsEl.textContent = warnings.join(" ");

    if (!data || typeof data !== "object") {
        currentEl.innerHTML = '<p class="muted">No economics data yet.</p>';
        return;
    }

    const fields = getCurrentMetricsOrder().filter((key) => data[key] !== undefined && data[key] !== null);
    if (!fields.length) {
        currentEl.innerHTML = '<p class="muted">No economics values available yet.</p>';
        return;
    }

    fields.forEach((metricName) => {
        const item = document.createElement("div");
        item.className = "economics-stat";
        item.innerHTML = `
            <div class="economics-stat-label">${describeEconomicsMetric(metricName)}</div>
            <div class="economics-stat-value">${formatCurrentMetricValue(metricName, data[metricName])}</div>
        `;
        currentEl.appendChild(item);
    });
}

async function loadCurrentValues() {
    const res = await fetch(apiUrl("/api/economics/current"));
    const payload = await res.json();
    renderCurrentValues(payload.data, payload.polled_at, payload.errors);
}

async function loadChart() {
    const selectedSeries = collectSelectedSeries();
    if (!selectedSeries.length) {
        if (chart) {
            chart.destroy();
            chart = null;
        }
        emptyEl.textContent = "Select one or more series.";
        return;
    }

    const startDate = startDateEl.value;
    const startHour = startHourEl.value;
    const startMinute = startMinuteEl.value;
    const startSecond = startSecondEl.value;
    const endDate = endDateEl.value;
    const endHour = endHourEl.value;
    const endMinute = endMinuteEl.value;
    const endSecond = endSecondEl.value;
    const startMs = parseLocalInputToMs(startDate, startHour, startMinute, startSecond);
    const endMs = parseLocalInputToMs(endDate, endHour, endMinute, endSecond);

    const requests = selectedSeries.map(async (seriesItem) => {
        const params = new URLSearchParams({ metric: seriesItem.metric });
        if (startDate) params.set("start", toIsoWithOffset(startDate, startHour, startMinute, startSecond));
        if (endDate) params.set("end", toIsoWithOffset(endDate, endHour, endMinute, endSecond));
        const res = await fetch(apiUrl(`/api/economics/data?${params.toString()}`));
        const data = await res.json();
        return {
            label: seriesItem.label,
            points: data.points || [],
        };
    });
    const metricSeries = await Promise.all(requests);

    if (chart) chart.destroy();
    const datasets = metricSeries.map((seriesData, index) => {
        const gapMs = getMetricGapMs(selectedSeries[index] && selectedSeries[index].metric);
        const series = [];
        seriesData.points.forEach((point, pointIndex) => {
            const ts = point.ts;
            if (pointIndex > 0) {
                const prevTs = seriesData.points[pointIndex - 1].ts;
                if (gapMs !== null && ts - prevTs > gapMs) {
                    series.push({ x: prevTs + gapMs, y: null });
                }
            }
            series.push({ x: ts, y: point.value });
        });
        const color = palette[index % palette.length];
        return {
            label: seriesData.label,
            data: series,
            borderColor: color,
            backgroundColor: `${color}22`,
            tension: 0.25,
            fill: false,
            spanGaps: false,
        };
    });

    const hasPoints = metricSeries.some((seriesData) => seriesData.points.length > 0);
    emptyEl.textContent = hasPoints ? "" : "No data for the selected range.";
    chart = new Chart(ctx, {
        type: "line",
        data: { datasets },
        options: {
            responsive: true,
            scales: {
                x: {
                    type: "linear",
                    min: startMs ?? undefined,
                    max: endMs ?? undefined,
                    ticks: { callback: (value) => formatDateTime(value) },
                },
                y: { beginAtZero: false },
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        title: (items) => items.length ? formatDateTime(items[0].parsed.x) : "",
                    },
                },
            },
        },
    });
}

async function loadCatalog() {
    const res = await fetch(apiUrl("/api/economics/catalog"));
    const payload = await res.json();
    catalogData = payload && typeof payload === "object" ? payload : catalogData;
    const metrics = Array.isArray(catalogData.metrics) ? catalogData.metrics : [];
    availableMetrics = Array.from(new Set(metrics));
    renderPresetLabels();
    metricRows.forEach((row) => loadMetricsForRow(row));
}

async function applyPreset(name) {
    const metrics = getPresetMetrics(name);
    if (!metrics.length) return;
    clearMetricRows();
    metrics.forEach((metric) => createMetricRow({ metric }));
    await restoreRowsFromState();
    persistState();
    loadChart();
}

async function restoreRowsFromState() {
    for (const row of metricRows) {
        const initial = row.initialState || {};
        loadMetricsForRow(row);
        if (initial.metric && row.options.some((item) => item.value === initial.metric)) {
            selectMetricForRow(row, initial.metric);
        }
    }
}

document.addEventListener("click", (event) => {
    const clickedInsideAnyRow = metricRows.some((row) => row.rowEl.contains(event.target));
    if (!clickedInsideAnyRow) closeAllMetricDropdowns();
});

addMetricBtn.addEventListener("click", () => {
    createMetricRow();
    persistState();
});

document.getElementById("apply").addEventListener("click", () => {
    persistState();
    loadChart();
});

rangeButtons.forEach((button) => {
    button.addEventListener("click", () => {
        setRangeToLastHours(button.getAttribute("data-hours"));
        loadChart();
    });
});

refreshCurrentBtn.addEventListener("click", () => {
    loadCurrentValues();
});

presetButtons.forEach((button) => {
    button.addEventListener("click", () => {
        const presetName = button.getAttribute("data-preset");
        applyPreset(presetName);
    });
});

const savedState = loadState();
setRangeToLastHours(24);

const initialSeries = Array.isArray(savedState && savedState.series) && savedState.series.length
    ? savedState.series
    : [{}];
initialSeries.forEach((series) => createMetricRow(series));

Promise.all([loadCatalog(), loadCurrentValues()]).then(async () => {
    await restoreRowsFromState();
    persistState();
    loadChart();
});
