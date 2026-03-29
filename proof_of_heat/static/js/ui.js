const apiUrl = (path) => `${rootPath}${path}`;
const statusEl = document.getElementById("status");
const weatherEl = document.getElementById("weather");
const controlInputsEl = document.getElementById("control-inputs");
const weatherLocationEl = document.getElementById("weather-location");
const targetEl = document.getElementById("target");
const modeEl = document.getElementById("mode");
const powerEl = document.getElementById("power");

async function loadStatus() {
    statusEl.textContent = "Loading...";
    weatherEl.textContent = "Loading...";
    controlInputsEl.textContent = "Loading...";
    weatherLocationEl.textContent = "";
    try {
        const [statusRes, controlInputsRes] = await Promise.all([
            fetch(apiUrl("/status")),
            fetch(apiUrl("/api/control-inputs/latest")),
        ]);
        const statusData = await statusRes.json();
        const controlInputsData = await controlInputsRes.json();

        statusEl.textContent = JSON.stringify(statusData, null, 2);
        if (statusData.target_temperature_c !== undefined) {
            targetEl.value = statusData.target_temperature_c;
        }
        if (statusData.mode) {
            modeEl.value = statusData.mode;
        }
        if (statusData.weather) {
            weatherEl.textContent = JSON.stringify(statusData.weather, null, 2);
            if (statusData.weather.location && statusData.weather.location.name) {
                weatherLocationEl.textContent = statusData.weather.location.name;
            }
        } else {
            weatherEl.textContent = "No weather data configured.";
        }
        if (controlInputsData.data) {
            controlInputsEl.textContent = JSON.stringify(controlInputsData.data, null, 2);
        } else {
            controlInputsEl.textContent = "No control inputs available.";
        }
    } catch (err) {
        statusEl.textContent = "Failed to load status: " + err;
        weatherEl.textContent = "Failed to load weather: " + err;
        controlInputsEl.textContent = "Failed to load control inputs: " + err;
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
