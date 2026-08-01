// RAGSPINE Bridge — MV3 service worker.
// Long-polls GET {server}/browser/cmd, executes the command, POSTs the result
// to {server}/browser/result. Exponential backoff on fetch errors.

const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;
let backoffMs = MIN_BACKOFF_MS;

async function getConfig() {
  const { server, token } = await chrome.storage.local.get(["server", "token"]);
  return { server, token };
}

async function setStatus(text) {
  await chrome.storage.local.set({ lastStatus: `${new Date().toISOString()} ${text}` });
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function runAction(cmd) {
  const { action, selector, value, url } = cmd;
  const tab = await getActiveTab();

  if (action === "navigate") {
    await chrome.tabs.update(tab.id, { url });
    return { ok: true, data: null };
  }

  if (action === "click") {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (sel) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        el.click();
        return true;
      },
      args: [selector],
    });
    return { ok: !!result, data: result };
  }

  if (action === "type") {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (sel, val) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        el.value = val;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      },
      args: [selector, value],
    });
    return { ok: !!result, data: result };
  }

  if (action === "scroll") {
    const amount = typeof value === "number" ? value : 500;
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (amt) => window.scrollBy(0, amt),
      args: [amount],
    });
    return { ok: true, data: null };
  }

  if (action === "screenshot") {
    const dataUrl = await chrome.tabs.captureVisibleTab();
    return { ok: true, data: dataUrl };
  }

  if (action === "read") {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (sel) => (sel ? document.querySelector(sel)?.innerText : document.body.innerText) ?? null,
      args: [selector],
    });
    return { ok: true, data: result };
  }

  return { ok: false, error: `unknown action: ${action}` };
}

async function postResult(server, token, cmdId, result) {
  await fetch(`${server}/browser/result`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ cmd_id: cmdId, result }),
  });
}

async function pollOnce() {
  const { server, token } = await getConfig();
  if (!server || !token) {
    await setStatus("nije konfigurirano (server/token)");
    return false;
  }

  const resp = await fetch(`${server}/browser/cmd`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (resp.status === 204) {
    await setStatus("polling (nema naredbi)");
    return true;
  }

  if (!resp.ok) {
    await setStatus(`greška ${resp.status}`);
    return false;
  }

  const cmd = await resp.json();
  let result;
  try {
    result = await runAction(cmd);
  } catch (err) {
    result = { ok: false, error: String(err && err.message ? err.message : err) };
  }

  await postResult(server, token, cmd.cmd_id, result);
  await setStatus(`izvršeno: ${cmd.action}`);
  return true;
}

async function loop() {
  try {
    const success = await pollOnce();
    backoffMs = success ? MIN_BACKOFF_MS : Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    setTimeout(loop, success ? 0 : backoffMs);
  } catch (err) {
    console.log("RAGSPINE bridge poll error:", err);
    await setStatus(`fetch greška: ${err}`);
    backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    setTimeout(loop, backoffMs);
  }
}

chrome.runtime.onStartup.addListener(loop);
chrome.runtime.onInstalled.addListener(loop);
loop();
