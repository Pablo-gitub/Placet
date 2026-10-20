// Unsent form text kept on this device, so a refresh, a notification or a
// trip to another screen does not lose what the user was typing. Drafts are a
// per-viewer convenience: storage may be unavailable, and then they last only
// as long as the component.
const PREFIX = "placet.draft.";
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

export function loadDraft(key) {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFIX + key));
    if (!saved || typeof saved.at !== "number") return null;
    if (Date.now() - saved.at > MAX_AGE_MS) {
      localStorage.removeItem(PREFIX + key);
      return null;
    }
    return saved.value ?? null;
  } catch {
    return null;
  }
}

export function saveDraft(key, value) {
  try {
    if (value == null) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, JSON.stringify({ value, at: Date.now() }));
  } catch {
    // private mode or full storage: the draft lives only in memory
  }
}

export const clearDraft = (key) => saveDraft(key, null);
