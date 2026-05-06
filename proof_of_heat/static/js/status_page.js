const apiUrl = (path) => `${rootPath}${path}`;
const refreshMs = 15000;

const els = {
    refresh: document.getElementById("status-refresh"),
    updated: document.getElementById("status-updated"),
    weatherIcon: document.getElementById("weather-icon"),
    weatherValue: document.getElementById("weather-value"),
    weatherMeta: document.getElementById("weather-meta"),
    indoorValue: document.getElementById("indoor-value"),
    indoorMeta: document.getElementById("indoor-meta"),
    targetValue: document.getElementById("target-value"),
    targetMeta: document.getElementById("target-meta"),
    supplyValue: document.getElementById("supply-value"),
    supplyMeta: document.getElementById("supply-meta"),
    powerValue: document.getElementById("power-value"),
    powerMeta: document.getElementById("power-meta"),
};

const numberFormatter = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 1,
});

const wattsFormatter = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 0,
});

function isNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
}

function formatTemp(value) {
    return isNumber(value) ? `${numberFormatter.format(value)} °C` : "—";
}

function formatPower(value) {
    if (!isNumber(value)) {
        return "—";
    }
    if (Math.abs(value) >= 1000) {
        return `${numberFormatter.format(value / 1000)} кВт`;
    }
    return `${wattsFormatter.format(value)} Вт`;
}

function formatDateTime(value) {
    if (!value) {
        return null;
    }
    const date = typeof value === "number" ? new Date(value) : new Date(value);
    if (Number.isNaN(date.getTime())) {
        return null;
    }
    return new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(date);
}

function providerLabel(provider) {
    if (provider === "open_meteo") {
        return "Open-Meteo";
    }
    if (provider === "met_no") {
        return "MET Norway";
    }
    return provider || "погода";
}

function weatherIcon(code) {
    if (!isNumber(code)) {
        return "🌤️";
    }
    if (code === 0) {
        return "☀️";
    }
    if (code >= 1 && code <= 3) {
        return "⛅";
    }
    if (code === 45 || code === 48) {
        return "🌫️";
    }
    if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) {
        return "🌧️";
    }
    if (code >= 71 && code <= 77) {
        return "🌨️";
    }
    if (code >= 95) {
        return "⛈️";
    }
    return "🌤️";
}

function sourceText(source) {
    return source ? `Источник: ${source}` : "Источник не выбран";
}

function powerSourceText(sources) {
    if (Array.isArray(sources) && sources.length > 0) {
        return `Источники: ${sources.join(", ")}`;
    }
    return "Источник не выбран";
}

function weatherMetaText(weather) {
    if (!weather) {
        return "Нет данных";
    }
    if (weather.error) {
        return `Ошибка: ${weather.error}`;
    }
    const parts = [];
    if (weather.location_name) {
        parts.push(weather.location_name);
    }
    if (isNumber(weather.humidity_percent)) {
        parts.push(`влажность ${wattsFormatter.format(weather.humidity_percent)}%`);
    }
    if (isNumber(weather.wind_speed)) {
        parts.push(`ветер ${numberFormatter.format(weather.wind_speed)} м/с`);
    }
    const observedAt = formatDateTime(weather.observed_at || weather.polled_at);
    if (observedAt) {
        parts.push(observedAt);
    }
    if (parts.length === 0) {
        return providerLabel(weather.provider);
    }
    return `${providerLabel(weather.provider)} · ${parts.join(" · ")}`;
}

function updateStatus(data) {
    const weather = data.weather || {};
    els.weatherIcon.textContent = weatherIcon(weather.weather_code);
    els.weatherValue.textContent = formatTemp(weather.temperature_c);
    els.weatherMeta.textContent = weatherMetaText(weather);

    els.indoorValue.textContent = formatTemp(data.indoor_temperature_c);
    els.indoorMeta.textContent = sourceText(data.sources?.indoor_temperature);

    els.targetValue.textContent = formatTemp(data.target_temperature_c);
    els.targetMeta.textContent = data.mode_label ? `Режим: ${data.mode_label}` : "Целевая температура";

    els.supplyValue.textContent = formatTemp(data.supply_temperature_c);
    els.supplyMeta.textContent = sourceText(data.sources?.supply_temperature);

    els.powerValue.textContent = formatPower(data.power_w);
    els.powerMeta.textContent = isNumber(data.power_w)
        ? `${wattsFormatter.format(data.power_w)} Вт · ${powerSourceText(data.sources?.power)}`
        : powerSourceText(data.sources?.power);

    const updatedAt = formatDateTime(data.updated_at_ms);
    els.updated.textContent = updatedAt ? `Обновлено ${updatedAt}` : "Нет свежей телеметрии";
}

async function loadStatus() {
    els.refresh.classList.add("is-loading");
    els.refresh.disabled = true;
    try {
        const response = await fetch(apiUrl("/api/status-summary"));
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        updateStatus(data);
    } catch (err) {
        els.updated.textContent = `Не удалось обновить статус: ${err}`;
    } finally {
        els.refresh.classList.remove("is-loading");
        els.refresh.disabled = false;
    }
}

els.refresh.addEventListener("click", loadStatus);
loadStatus();
setInterval(loadStatus, refreshMs);
