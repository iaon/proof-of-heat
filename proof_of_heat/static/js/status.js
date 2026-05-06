const apiUrl = (path) => `${rootPath}${path}`;

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

function setText(id, value) {
    document.getElementById(id).textContent = value;
}

function asNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value, digits = 1) {
    const number = asNumber(value);
    if (number === null) return "н/д";
    return number.toLocaleString("ru-RU", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    });
}

function formatTemp(value) {
    const number = asNumber(value);
    return number === null ? "н/д" : `${formatNumber(number, 1)} °C`;
}

function formatPower(value) {
    const watts = asNumber(value);
    if (watts === null) return ["н/д", "текущее потребление"];
    if (Math.abs(watts) >= 1000) {
        return [`${formatNumber(watts / 1000, 2)} кВт`, `${formatNumber(watts, 0)} Вт`];
    }
    return [`${formatNumber(watts, 0)} Вт`, "текущее потребление"];
}

function formatTimestamp(value) {
    if (!value) return "Обновлено: н/д";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Обновлено: н/д";
    return `Обновлено: ${date.toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    })}`;
}

function normalizeWindUnit(unit) {
    if (unit === "m/s") return "м/с";
    if (unit === "km/h") return "км/ч";
    return unit || "";
}

function formatWind(weather) {
    if (!weather) return "Ветер: н/д";
    const speed = asNumber(weather.wind_speed);
    if (speed === null) return "Ветер: н/д";
    const unit = normalizeWindUnit(weather.wind_speed_unit);
    return `Ветер: ${formatNumber(speed, 1)}${unit ? ` ${unit}` : ""}`;
}

function describeWeather(weather) {
    if (!weather) return ["🌤️", "Погода недоступна"];
    const code = asNumber(weather.weather_code);
    if (code !== null && weatherCodes[code]) return weatherCodes[code];
    if (asNumber(weather.temperature_c) !== null) return ["🌡️", "Текущие условия"];
    return ["🌤️", "Погода недоступна"];
}

function renderStatus(payload) {
    const weather = payload.weather;
    const [weatherIcon, weatherText] = describeWeather(weather);
    const [powerValue, powerNote] = formatPower(payload.power_w);

    setText("weather-icon", weatherIcon);
    setText("weather-temp", weather ? formatTemp(weather.temperature_c) : "н/д");
    setText("weather-summary", weatherText);
    setText("weather-location", weather?.location_name || "Локация не задана");
    setText("weather-wind", formatWind(weather));

    setText("indoor-temp", formatTemp(payload.indoor_temp_c));
    setText("target-temp", formatTemp(payload.target_indoor_temp_c));
    setText("supply-temp", formatTemp(payload.supply_temp_c));
    setText("power", powerValue);
    setText("power-note", powerNote);
    setText("status-updated", formatTimestamp(payload.generated_at));
}

async function loadStatus() {
    refreshButton.disabled = true;
    errorEl.hidden = true;
    try {
        const response = await fetch(apiUrl("/api/status-page/current"));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        renderStatus(await response.json());
    } catch (err) {
        errorEl.textContent = `Не удалось обновить статус: ${err}`;
        errorEl.hidden = false;
        setText("status-updated", "Обновлено: ошибка");
    } finally {
        refreshButton.disabled = false;
    }
}

refreshButton.addEventListener("click", loadStatus);
loadStatus();
setInterval(loadStatus, 30000);
