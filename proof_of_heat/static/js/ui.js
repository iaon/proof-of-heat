const apiUrl = (path) => `${rootPath}${path}`;
const controlInputsEl = document.getElementById("control-inputs");

async function loadControlInputs() {
    controlInputsEl.textContent = "Loading...";
    try {
        const controlInputsRes = await fetch(apiUrl("/api/control-inputs/latest"));
        const controlInputsData = await controlInputsRes.json();
        if (controlInputsData.data) {
            controlInputsEl.textContent = JSON.stringify(controlInputsData.data, null, 2);
        } else {
            controlInputsEl.textContent = "No control inputs available.";
        }
    } catch (err) {
        controlInputsEl.textContent = "Failed to load control inputs: " + err;
    }
}

document.getElementById("refresh").addEventListener("click", loadControlInputs);

loadControlInputs();
