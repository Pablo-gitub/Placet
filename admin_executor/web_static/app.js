/* Protected, standalone page: never load script or markup from the Placet PWA. */
const fragment = new URLSearchParams(location.hash.slice(1));
const invitation = { id: fragment.get("enroll"), token: fragment.get("token") };
if (location.hash) history.replaceState(null, "", location.pathname);
// A new invitation opened in an existing tab must start a fresh ceremony.
window.addEventListener("hashchange", () => { if (location.hash) location.reload(); });
const button = document.querySelector("#enroll");
const feedback = document.querySelector("#feedback");
const language = document.querySelector("#language");
let catalogue = {};
let feedbackKey = "";
let busy = false;
let completed = false;
let localeSequence = 0;
const eligible = /^[a-f0-9]{32}$/.test(invitation.id || "") && (invitation.token || "").length >= 32;
const supported = Boolean(window.isSecureContext && window.PublicKeyCredential && navigator.credentials?.create);
function render() {
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = catalogue[el.dataset.i18n] || ""; });
  document.querySelector("#setup-description").textContent = catalogue[eligible ? "setupReady" : "setupMissing"];
  feedback.textContent = catalogue[feedbackKey] || "";
  button.hidden = !eligible || completed;
  button.disabled = busy || !supported;
}
async function setLanguage(value) {
  const sequence = ++localeSequence;
  let response = await fetch(`/assets/${value}.json`, { cache: "no-store" });
  if (!response.ok) { value = "en"; response = await fetch("/assets/en.json", { cache: "no-store" }); }
  const next = await response.json();
  if (sequence !== localeSequence) return;
  catalogue = next;
  language.value = value;
  document.documentElement.lang = value;
  try { localStorage.setItem("placet-admin.language", value); } catch { /* Storage is optional. */ }
  render();
}
language.addEventListener("change", () => { setLanguage(language.value).catch(() => {}); });
let initial = navigator.language?.toLowerCase().startsWith("it") ? "it" : "en";
try { const saved = localStorage.getItem("placet-admin.language"); if (["en", "it"].includes(saved)) initial = saved; } catch { /* Storage is optional. */ }
if (eligible && !supported) feedbackKey = "unsupported";
setLanguage(initial).catch(() => {});
function decode(value) {
  const text = atob(value.replaceAll("-", "+").replaceAll("_", "/"));
  return Uint8Array.from(text, (c) => c.charCodeAt(0));
}
function encode(buffer) {
  return btoa(String.fromCharCode(...new Uint8Array(buffer))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}
async function post(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), cache: "no-store", credentials: "same-origin", redirect: "error" });
  if (!response.ok) { const error = new Error("request failed"); error.http = true; throw error; }
  return response.json();
}
button.addEventListener("click", async () => {
  if (busy || completed) return;
  busy = true; feedbackKey = "waiting"; render();
  try {
    const { publicKey } = await post("/api/enrollment/options", invitation);
    publicKey.challenge = decode(publicKey.challenge);
    publicKey.user.id = decode(publicKey.user.id);
    for (const item of publicKey.excludeCredentials || []) item.id = decode(item.id);
    const credential = await navigator.credentials.create({ publicKey });
    await post("/api/enrollment/stage", { ...invitation, credential: {
      id: credential.id, rawId: encode(credential.rawId), type: credential.type,
      response: { clientDataJSON: encode(credential.response.clientDataJSON),
        attestationObject: encode(credential.response.attestationObject),
        transports: credential.response.getTransports?.() || [] }
    } });
    completed = true; feedbackKey = "staged";
  } catch (error) {
    feedbackKey = error.name === "NotAllowedError" ? "cancelled" : error.http ? "unavailable" : "network";
  } finally { busy = false; render(); }
});
