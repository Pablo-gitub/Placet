/* Protected standalone page: no script, markup or descriptions from the PWA. */
(() => {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const invitation = { id: fragment.get("enroll"), token: fragment.get("token") };
  const proposal = new URLSearchParams(location.search).get("proposal");
  const proposalId = /^[a-f0-9]{32}$/.test(proposal || "") ? proposal : null;
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  window.addEventListener("hashchange", () => { if (alive && location.hash) location.reload(); });
  const button = document.querySelector("#enroll");
  const feedback = document.querySelector("#feedback");
  const language = document.querySelector("#language");
  const confirm = document.querySelector("#confirm");
  let catalogue = {}, feedbackKey = "", commandFeedback = "";
  let busy = false, completed = false, changed = false, alive = true;
  let localeSequence = 0, viewSequence = 0, poll;
  let currentView = null;
  const eligible = !proposalId && /^[a-f0-9]{32}$/.test(invitation.id || "") && (invitation.token || "").length >= 32;
  const supported = Boolean(window.isSecureContext && window.PublicKeyCredential && navigator.credentials?.create);
  const canConfirm = Boolean(window.isSecureContext && window.PublicKeyCredential && navigator.credentials?.get);
  window.addEventListener("pagehide", () => { alive = false; clearTimeout(poll); });
  function render() {
    if (!alive) return;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = catalogue[el.dataset.i18n] || ""; });
    document.querySelector("#enrollment-intro").hidden = Boolean(proposalId);
    document.querySelector("#enrollment-panel").hidden = Boolean(proposalId);
    document.querySelector("#confirmation-panel").hidden = !proposalId;
    document.querySelector("#setup-description").textContent = catalogue[eligible ? "setupReady" : "setupMissing"];
    feedback.textContent = catalogue[feedbackKey] || "";
    button.hidden = !eligible || completed;
    button.disabled = busy || !supported;
    confirm.hidden = !currentView || currentView.state !== "awaiting_confirmation" || !currentView.enabled;
    confirm.disabled = busy || changed || !canConfirm;
    document.querySelector("#operation-description").textContent = catalogue[`${currentView?.action}Description`] || "";
    document.querySelector("#operation-summary").textContent = currentView?.operation.summary || "";
    document.querySelector("#command-state").textContent = catalogue[currentView?.state || (commandFeedback ? "" : "loadingCommand")] || "";
    document.querySelector("#command-evidence").textContent = currentView?.evidence === "user_service_report" ? catalogue.bridgeEvidence : "";
    document.querySelector("#command-feedback").textContent = catalogue[commandFeedback] || "";
    const back = document.querySelector("#return-link");
    back.hidden = !currentView;
    if (currentView) back.href = currentView.return_url;
  }
  async function setLanguage(value) {
    const sequence = ++localeSequence;
    let response = await fetch(`/assets/${value}.json`, { cache: "no-store" });
    if (!response.ok) { value = "en"; response = await fetch("/assets/en.json", { cache: "no-store" }); }
    const next = await response.json();
    if (!alive || sequence !== localeSequence) return;
    catalogue = next; language.value = value; document.documentElement.lang = value;
    try { localStorage.setItem("placet-admin.language", value); } catch { /* Storage is optional. */ }
    render();
  }
  language.addEventListener("change", () => { setLanguage(language.value).catch(() => {}); });
  let initial = navigator.language?.toLowerCase().startsWith("it") ? "it" : "en";
  try { const saved = localStorage.getItem("placet-admin.language"); if (["en", "it"].includes(saved)) initial = saved; } catch { /* Storage is optional. */ }
  if (eligible && !supported) feedbackKey = "unsupported";
  if (proposalId && !canConfirm) commandFeedback = "commandUnsupported";
  setLanguage(initial).catch(() => {});
  function decode(value) {
    const text = atob(value.replaceAll("-", "+").replaceAll("_", "/"));
    return Uint8Array.from(text, (c) => c.charCodeAt(0));
  }
  function encode(buffer) {
    return btoa(String.fromCharCode(...new Uint8Array(buffer))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
  }
  async function request(path, body) {
    const response = await fetch(path, { method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
      credentials: "same-origin", redirect: "error" });
    if (!response.ok) { const error = new Error("request failed"); error.http = true; throw error; }
    return response.json();
  }
  button.addEventListener("click", async () => {
    if (busy || completed) return;
    busy = true; feedbackKey = "waiting"; render();
    try {
      const { publicKey } = await request("/api/enrollment/options", invitation);
      publicKey.challenge = decode(publicKey.challenge); publicKey.user.id = decode(publicKey.user.id);
      for (const item of publicKey.excludeCredentials || []) item.id = decode(item.id);
      const credential = await navigator.credentials.create({ publicKey });
      await request("/api/enrollment/stage", { ...invitation, credential: {
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
  async function loadCommand() {
    const sequence = ++viewSequence;
    clearTimeout(poll);
    try {
      const view = await request(`/api/proposals/${proposalId}`);
      if (!alive || sequence !== viewSequence) return;
      currentView = view;
      if (["commandUnavailable", "commandNetwork"].includes(commandFeedback)) commandFeedback = "";
      if (view.state !== "awaiting_confirmation") commandFeedback = "";
      if (!view.enabled && view.state === "awaiting_confirmation") commandFeedback = "commandUnavailable";
    } catch {
      if (!alive || sequence !== viewSequence) return;
      commandFeedback = "commandUnavailable";
      // An old preview is not an executable fallback when the broker is offline.
      if (currentView) currentView = { ...currentView, enabled: false };
    }
    render();
    if (alive && !["succeeded", "failed"].includes(currentView?.state)) poll = setTimeout(loadCommand, 2000);
  }
  confirm.addEventListener("click", async () => {
    if (busy || changed || !currentView?.enabled || currentView.state !== "awaiting_confirmation") return;
    const reviewedOperation = JSON.stringify(currentView.operation);
    busy = true; commandFeedback = ""; render();
    try {
      const challenge = await request(`/api/proposals/${proposalId}/challenge`, {});
      if (challenge.job_id) { await loadCommand(); return; }
      if (JSON.stringify(challenge.operation) !== reviewedOperation) {
        changed = true; commandFeedback = "operationChanged"; return;
      }
      const publicKey = challenge.publicKey;
      publicKey.challenge = decode(publicKey.challenge);
      for (const item of publicKey.allowCredentials || []) item.id = decode(item.id);
      const credential = await navigator.credentials.get({ publicKey });
      const result = await request(`/api/proposals/${proposalId}/authorize`, {
        challenge_id: challenge.id, credential: { id: credential.id, rawId: encode(credential.rawId), type: credential.type,
          response: { clientDataJSON: encode(credential.response.clientDataJSON),
            authenticatorData: encode(credential.response.authenticatorData), signature: encode(credential.response.signature),
            userHandle: credential.response.userHandle ? encode(credential.response.userHandle) : null } }
      });
      ++viewSequence; currentView = result;
      clearTimeout(poll); poll = setTimeout(loadCommand, 2000);
    } catch (error) {
      commandFeedback = error.name === "NotAllowedError" ? "commandCancelled" : error.http ? "commandUnavailable" : "commandNetwork";
      // GET-only recovery; never repeat a signature or authorization POST.
      clearTimeout(poll); poll = setTimeout(loadCommand, 2000);
    } finally { busy = false; render(); }
  });
  render();
  if (proposalId) loadCommand();
})();
