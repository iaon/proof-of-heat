const apiUrl = (path) => `${rootPath}${path}`;
const refreshMs = 15000;

const refreshButton = document.getElementById("status-refresh");
const errorEl = document.getElementById("status-error");

const weatherCodes = {
    0: ["☀️", "Ясно"],
    1: ["🌤️", "Преимущественно ясно"],
    2: ["⛅", "Переменная облачность"],
    3: ["☁️", "Пасмурно"],
    45: ["🌫️", "Туман"],
    48: ["🌫️", "Изморозь"],
    51: ["🌦️", "Слабая морось"],
    53: ["🌦️", "Морось"],
    55: ["🌧️", "Сильная морось"],
    56: ["🌧️", "Ледяная морось"],
    57: ["🌧️", "Сильная ледяная морось"],
    61: ["🌧️", "Небольшой дождь"],
    63: ["🌧️", "Дождь"],
    65: ["🌧️", "Сильный дождь"],
    66: ["🌧️", "Ледяной дождь"],
    67: ["🌧️", "Сильный ледяной дождь"],
    71: ["🌨️", "Небольшой снег"],
    73: ["🌨️", "Снег"],
    75: ["❄️", "Сильный снег"],
    77: ["❄️", "Снежные зерна"],
    80: ["🌦️", "Кратковременный дождь"],
    81: ["🌧️", "Ливень"],
    82: ["⛈️", "Сильный ливень"],
    85: ["🌨️", "Снегопад"],
    86: ["❄️", "Сильный снегопад"],
    95: ["⛈️", "Гроза"],
    96: ["⛈️", "Гроза с градом"],
    99: ["⛈️", "Сильная гроза с градом"],
};

const numberFormatter = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 1,
});

const wattsFormatter = new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 0,
});

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}

function asNumber(value) {
    if (value === null || value === undefined || value === "") {
        return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function formatNumber(value, maximumFractionDigits = 1) {
    const number = asNumber(value);
    if (number === null) {
        return "н/д";
    }
    return new Intl.NumberFormat("ru-RU", {
        maximumFractionDigits,
    }).format(number);
}

function formatTemp(value) {
    const number = asNumber(value);
    return number === null ? "н/д" : `${numberFormatter.format(number)} °C`;
}

function formatPower(value) {
    const watts = asNumber(value);
    if (watts === null) {
        return ["н/д", "текущее потребление"];
    }
    if (Math.abs(watts) >= 1000) {
        return [`${formatNumber(watts / 1000, 2)} кВт`, `${wattsFormatter.format(watts)} Вт`];
    }
    return [`${wattsFormatter.format(watts)} Вт`, "текущее потребление"];
}

function formatDateTime(value) {
    if (!value) {
        return null;
    }
    const date = new Date(value);
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

function describeWeather(weather) {
    if (!weather) {
        return ["🌤️", "Погода недоступна"];
    }
    const code = asNumber(weather.weather_code);
    if (code !== null && weatherCodes[code]) {
        return weatherCodes[code];
    }
    if (asNumber(weather.temperature_c) !== null) {
        return ["🌡️", "Текущие условия"];
    }
    return ["🌤️", "Погода недоступна"];
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

function renderWeather(weather) {
    const [icon, summary] = describeWeather(weather);
    setText("weather-icon", icon);
    setText("weather-temp", weather ? formatTemp(weather.temperature_c) : "н/д");
    setText("weather-summary", weather?.error ? `Ошибка: ${weather.error}` : summary);

    const location = weather?.location_name || "Локация не задана";
    setText("weather-location", `${location} · ${providerLabel(weather?.provider)}`);

    const windSpeed = asNumber(weather?.wind_speed);
    setText("weather-wind", windSpeed === null ? "Ветер: н/д" : `Ветер: ${numberFormatter.format(windSpeed)} м/с`);

    const humidity = asNumber(weather?.humidity_percent);
    setText("weather-humidity", humidity === null ? "Влажность: н/д" : `Влажность: ${wattsFormatter.format(humidity)}%`);

    const observedAt = formatDateTime(weather?.observed_at || weather?.polled_at);
    setText("weather-observed", observedAt ? `Наблюдение: ${observedAt}` : "Наблюдение: н/д");
}

function renderStatus(data) {
    renderWeather(data.weather || {});

    setText("indoor-temp", formatTemp(data.indoor_temperature_c));
    setText("indoor-note", sourceText(data.sources?.indoor_temperature));

    setText("target-temp", formatTemp(data.target_temperature_c));
    setText("target-note", data.mode_label ? `Режим: ${data.mode_label}` : "целевая температура");

    setText("supply-temp", formatTemp(data.supply_temperature_c));
    setText("supply-note", sourceText(data.sources?.supply_temperature));

    const [powerValue, powerNote] = formatPower(data.power_w);
    setText("power", powerValue);
    setText("power-note", asNumber(data.power_w) === null ? powerSourceText(data.sources?.power) : `${powerNote} · ${powerSourceText(data.sources?.power)}`);

    const updatedAt = formatDateTime(data.updated_at_ms);
    setText("status-updated", updatedAt ? `Обновлено ${updatedAt}` : "Нет свежей телеметрии");
}

async function loadStatus() {
    refreshButton.classList.add("is-loading");
    refreshButton.disabled = true;
    errorEl.hidden = true;
    try {
        const response = await fetch(apiUrl("/api/status-summary"));
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        renderStatus(await response.json());
    } catch (err) {
        errorEl.textContent = `Не удалось обновить статус: ${err}`;
        errorEl.hidden = false;
        setText("status-updated", "Обновлено: ошибка");
    } finally {
        refreshButton.classList.remove("is-loading");
        refreshButton.disabled = false;
    }
}

refreshButton.addEventListener("click", loadStatus);
loadStatus();
setInterval(loadStatus, refreshMs);
