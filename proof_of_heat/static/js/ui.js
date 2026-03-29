const apiUrl = (path) => `${rootPath}${path}`;
const statusEl = document.getElementById("status");
const weatherEl = document.getElementById("weather");
const weatherLocationEl = document.getElementById("weather-location");
const targetEl = document.getElementById("target");
const modeEl = document.getElementById("mode");
const powerEl = document.getElementById("power");

async function loadStatus() {
    statusEl.textContent = "Loading...";
    weatherEl.textContent = "Loading...";
    weatherLocationEl.textContent = "";
    try {
        const res = await fetch(apiUrl("/status"));
        const data = await res.json();
        statusEl.textContent = JSON.stringify(data, null, 2);
        if (data.target_temperature_c !== undefined) {
            targetEl.value = data.target_temperature_c;
        }
        if (data.mode) {
            modeEl.value = data.mode;
        }
        if (data.weather) {
            weatherEl.textContent = JSON.stringify(data.weather, null, 2);
            if (data.weather.location && data.weather.location.name) {
                weatherLocationEl.textContent = data.weather.location.name;
            }
        } else {
            weatherEl.textContent = "No weather data configured.";
        }
    } catch (err) {
        statusEl.textContent = "Failed to load status: " + err;
        weatherEl.textContent = "Failed to load weather: " + err;
    }
}

async function setTarget() {
    const temp = targetEl.value;
    const res = await fetch(apiUrl(`/target-temperature?temp_c=${encodeURIComponent(temp)}`), { method: "POST" });
    const data = await res.json();
    statusEl.textContent = JSON.stringify(data, null, 2);
}

async function setMode() {
    const mode = modeEl.value;
    const res = await fetch(apiUrl(`/mode/${mode}`), { method: "POST" });
    const data = await res.json();
    statusEl.textContent = JSON.stringify(data, null, 2);
}

async function startMiner() {
    const res = await fetch(apiUrl("/miner/start"), { method: "POST" });
    statusEl.textContent = JSON.stringify(await res.json(), null, 2);
}

async function stopMiner() {
    const res = await fetch(apiUrl("/miner/stop"), { method: "POST" });
    statusEl.textContent = JSON.stringify(await res.json(), null, 2);
}

async function setPower() {
    const watts = powerEl.value;
    const res = await fetch(apiUrl(`/miner/power-limit?watts=${encodeURIComponent(watts)}`), { method: "POST" });
    statusEl.textContent = JSON.stringify(await res.json(), null, 2);
}

document.getElementById("refresh").addEventListener("click", loadStatus);
document.getElementById("apply-target").addEventListener("click", setTarget);
document.getElementById("apply-mode").addEventListener("click", setMode);
document.getElementById("start").addEventListener("click", startMiner);
document.getElementById("stop").addEventListener("click", stopMiner);
document.getElementById("apply-power").addEventListener("click", setPower);

loadStatus();
