const serverInput = document.getElementById("server");
const tokenInput = document.getElementById("token");
const saveBtn = document.getElementById("save");
const statusEl = document.getElementById("status");

async function load() {
  const { server, token, lastStatus } = await chrome.storage.local.get([
    "server",
    "token",
    "lastStatus",
  ]);
  if (server) serverInput.value = server;
  if (token) tokenInput.value = token;
  statusEl.textContent = lastStatus || "";
}

saveBtn.addEventListener("click", async () => {
  await chrome.storage.local.set({
    server: serverInput.value.trim().replace(/\/+$/, ""),
    token: tokenInput.value.trim(),
  });
  statusEl.textContent = "Spremljeno";
});

load();
