const apiUrl = (path) => `${rootPath}${path}`;
const settingsEl = document.getElementById("settings");
const previewEl = document.getElementById("preview");

async function refreshPreview(data) {
    previewEl.textContent = JSON.stringify(data, null, 2);
}

async function loadSettings() {
    previewEl.textContent = "Loading...";
    const res = await fetch(apiUrl("/api/config"));
    const data = await res.json();
    if (!res.ok) {
        previewEl.textContent = "Error: " + (data.detail || "Failed to load");
        return;
    }
    settingsEl.value = data.raw_yaml || "";
    await refreshPreview(data.parsed || {});
}

async function saveSettings() {
    const res = await fetch(apiUrl("/api/config"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_yaml: settingsEl.value }),
    });
    const data = await res.json();
    if (res.ok) {
        await refreshPreview(data.parsed || {});
    } else {
        previewEl.textContent = "Error: " + (data.detail || "Failed to save");
    }
}

document.getElementById("load").addEventListener("click", loadSettings);
document.getElementById("save").addEventListener("click", saveSettings);

loadSettings();
