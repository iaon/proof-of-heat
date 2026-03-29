const apiUrl = (path) => `${rootPath}${path}`;
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
const resetRangeBtn = document.getElementById("reset-range");
const emptyEl = document.getElementById("empty");
const ctx = document.getElementById("chart").getContext("2d");
let chart;
let metricRowSeq = 0;
let deviceTypes = [];
const metricRows = [];
const STORAGE_KEY = "proof_of_heat_metrics_view_v1";
const palette = ["#2563eb", "#dc2626", "#16a34a", "#7c3aed", "#ea580c", "#0891b2", "#db2777", "#0f766e"];

function setOptions(select, options) {
    select.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "—";
    select.appendChild(placeholder);
    options.forEach((item) => {
        const opt = document.createElement("option");
        opt.value = item;
        opt.textContent = item;
        select.appendChild(opt);
    });
}

function toDateInputValue(date) {
    const pad = (num) => String(num).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function toTimeParts(date) {
    const pad = (num) => String(num).padStart(2, "0");
    return { hour: pad(date.getHours()), minute: pad(date.getMinutes()), second: pad(date.getSeconds()) };
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

function parseLocalInputToMs(dateValue, hourValue, minuteValue, secondValue) {
    const date = parseDateTimeInput(dateValue, hourValue, minuteValue, secondValue);
    return date ? date.getTime() : null;
}

function applyLast24HoursRange() {
    const now = new Date();
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    startDateEl.value = toDateInputValue(yesterday);
    endDateEl.value = toDateInputValue(now);
    const startParts = toTimeParts(yesterday);
    const endParts = toTimeParts(now);
    startHourEl.value = startParts.hour;
    startMinuteEl.value = startParts.minute;
    startSecondEl.value = startParts.second;
    endHourEl.value = endParts.hour;
    endMinuteEl.value = endParts.minute;
    endSecondEl.value = endParts.second;
}

function persistState() {
    const payload = {
        series: metricRows.map((row) => ({
            device_type: row.deviceTypeEl.value || "",
            device_id: row.deviceIdEl.value || "",
            metric: row.metricValueEl.value || "",
        })),
    };
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (err) {
        // Ignore storage failures (private mode/quota).
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

function prettifyMetricName(name) {
    return name.replaceAll("_", " ");
}

function describeZontMetric(metricName) {
    let match = metricName.match(/^io_thermometers_state_([a-zA-Z0-9]+)_last_value$/);
    if (match) return `Thermometer ${match[1].slice(0, 8)}: temperature`;
    match = metricName.match(/^io_thermometers_state_([a-zA-Z0-9]+)_last_value_time$/);
    if (match) return `Thermometer ${match[1].slice(0, 8)}: last value time (epoch seconds)`;
    match = metricName.match(/^io_last_boiler_state_(.+)$/);
    if (match) return `Boiler state: ${prettifyMetricName(match[1])}`;
    if (metricName.startsWith("io_")) return `I/O metric: ${prettifyMetricName(metricName.slice(3))}`;
    return `ZONT metric: ${prettifyMetricName(metricName)}`;
}

function describeMetric(deviceType, metricName) {
    if (!metricName) return "—";
    if (deviceType === "zont") return describeZontMetric(metricName);
    return prettifyMetricName(metricName);
}

function updateRemoveButtons() {
    metricRows.forEach((row) => {
        row.removeBtn.hidden = metricRows.length <= 1;
    });
}

function closeAllMetricDropdowns(exceptRowId = null) {
    metricRows.forEach((row) => {
        if (row.id !== exceptRowId) row.dropdownEl.hidden = true;
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
    row.humanEl.textContent = describeMetric(row.deviceTypeEl.value, metricName);
}

function selectMetricForRow(row, value) {
    const selected = row.options.find((item) => item.value === value);
    if (!selected) {
        row.metricValueEl.value = "";
        row.searchEl.value = "";
        updateRowInfo(row);
        return;
    }
    row.metricValueEl.value = selected.value;
    row.searchEl.value = selected.value;
    updateRowInfo(row);
    renderRowDropdown(row, row.searchEl.value);
}

async function loadDeviceTypes() {
    const res = await fetch(apiUrl("/api/metrics/device-types"));
    const data = await res.json();
    deviceTypes = data.device_types || [];
    metricRows.forEach((row) => {
        const prevType = row.deviceTypeEl.value;
        setOptions(row.deviceTypeEl, deviceTypes);
        if (prevType && deviceTypes.includes(prevType)) row.deviceTypeEl.value = prevType;
    });
}

async function loadDeviceIdsForRow(row) {
    const type = row.deviceTypeEl.value;
    if (!type) {
        setOptions(row.deviceIdEl, []);
        return;
    }
    const res = await fetch(apiUrl(`/api/metrics/device-ids?device_type=${encodeURIComponent(type)}`));
    const data = await res.json();
    const prevId = row.deviceIdEl.value;
    const ids = data.device_ids || [];
    setOptions(row.deviceIdEl, ids);
    if (prevId && ids.includes(prevId)) row.deviceIdEl.value = prevId;
}

async function loadMetricsForRow(row) {
    const type = row.deviceTypeEl.value;
    const id = row.deviceIdEl.value;
    if (!type || !id) {
        row.options = [];
        row.metricValueEl.value = "";
        row.searchEl.value = "";
        renderRowDropdown(row, "");
        updateRowInfo(row);
        return;
    }
    const res = await fetch(apiUrl(`/api/metrics/metric-names?device_type=${encodeURIComponent(type)}&device_id=${encodeURIComponent(id)}`));
    const data = await res.json();
    const prevMetric = row.metricValueEl.value;
    row.options = (data.metrics || []).map((value) => ({
        value,
        human: describeMetric(type, value),
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
            deviceType: row.deviceTypeEl.value,
            deviceId: row.deviceIdEl.value,
            metric: row.metricValueEl.value,
            label: row.searchEl.value || row.metricValueEl.value,
        }))
        .filter((item) => Boolean(item.deviceType && item.deviceId && item.metric));
}

function createMetricRow(initialState = null) {
    metricRowSeq += 1;
    const rowId = metricRowSeq;
    const rowEl = document.createElement("div");
    rowEl.className = "metric-row";
    rowEl.innerHTML = `
        <div class="metric-row-head">
            <label for="device-type-${rowId}">Device type</label>
            <select id="device-type-${rowId}"></select>
            <label for="device-id-${rowId}">Device id</label>
            <select id="device-id-${rowId}"></select>
            <div class="metric-picker">
                <input id="metric-search-${rowId}" type="text" autocomplete="off" placeholder="Select or search metric..." />
                <input id="metric-value-${rowId}" type="hidden" />
                <div id="metric-dropdown-${rowId}" class="metric-dropdown" hidden></div>
            </div>
            <button id="metric-remove-${rowId}" type="button" class="metric-remove">−</button>
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
        deviceTypeEl: rowEl.querySelector(`#device-type-${rowId}`),
        deviceIdEl: rowEl.querySelector(`#device-id-${rowId}`),
        searchEl: rowEl.querySelector(`#metric-search-${rowId}`),
        metricValueEl: rowEl.querySelector(`#metric-value-${rowId}`),
        dropdownEl: rowEl.querySelector(`#metric-dropdown-${rowId}`),
        removeBtn: rowEl.querySelector(`#metric-remove-${rowId}`),
        fullNameEl: rowEl.querySelector(`#metric-full-name-${rowId}`),
        humanEl: rowEl.querySelector(`#metric-human-${rowId}`),
        options: [],
    };
    metricRows.push(row);
    setOptions(row.deviceTypeEl, deviceTypes);
    setOptions(row.deviceIdEl, []);
    row.initialState = initialState;

    row.deviceTypeEl.addEventListener("change", async () => {
        await loadDeviceIdsForRow(row);
        await loadMetricsForRow(row);
        persistState();
        loadChart();
    });
    row.deviceIdEl.addEventListener("change", async () => {
        await loadMetricsForRow(row);
        persistState();
        loadChart();
    });
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

async function loadChart() {
    const selectedSeries = collectSelectedSeries();
    if (!selectedSeries.length) return;

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
        const params = new URLSearchParams({
            device_type: seriesItem.deviceType,
            device_id: seriesItem.deviceId,
            metric: seriesItem.metric,
        });
        if (startDate) params.set("start", toIsoWithOffset(startDate, startHour, startMinute, startSecond));
        if (endDate) params.set("end", toIsoWithOffset(endDate, endHour, endMinute, endSecond));
        const res = await fetch(apiUrl(`/api/metrics/data?${params.toString()}`));
        const data = await res.json();
        return {
            label: `${seriesItem.deviceType} ${seriesItem.deviceId} · ${seriesItem.label}`,
            points: data.points || [],
        };
    });
    const metricSeries = await Promise.all(requests);

    if (chart) chart.destroy();
    const datasets = metricSeries.map((seriesData, index) => {
        const gapMs = 10 * 60 * 1000;
        const series = [];
        seriesData.points.forEach((point, pointIndex) => {
            const ts = point.ts;
            if (pointIndex > 0) {
                const prevTs = seriesData.points[pointIndex - 1].ts;
                if (ts - prevTs > gapMs) series.push({ x: prevTs + gapMs, y: null });
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
resetRangeBtn.addEventListener("click", () => {
    applyLast24HoursRange();
    loadChart();
});

const savedState = loadState();
applyLast24HoursRange();

const initialSeries = Array.isArray(savedState && savedState.series) && savedState.series.length
    ? savedState.series
    : [{}];
initialSeries.forEach((series) => createMetricRow(series));

async function restoreRowsFromState() {
    for (const row of metricRows) {
        const initial = row.initialState || {};
        if (initial.device_type && deviceTypes.includes(initial.device_type)) {
            row.deviceTypeEl.value = initial.device_type;
            await loadDeviceIdsForRow(row);
        }
        if (initial.device_id) {
            const idOptions = Array.from(row.deviceIdEl.options).map((opt) => opt.value);
            if (idOptions.includes(initial.device_id)) {
                row.deviceIdEl.value = initial.device_id;
            }
        }
        await loadMetricsForRow(row);
        if (initial.metric) {
            selectMetricForRow(row, initial.metric);
        }
    }
}

loadDeviceTypes().then(async () => {
    await restoreRowsFromState();
    persistState();
    loadChart();
});
