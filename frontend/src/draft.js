/**
 * A sent message back into its parts: the text, the sessions it mentioned
 * (tokens end with the session's short id) and the names of its attachments,
 * which the phone must pick again.
 */
export function splitPrompt(prompt, sessions = []) {
  let text = prompt || "";
  let files = [];
  const match = text.match(/\n\n(?:Allegati|Attachments): ([^\n]*)$/);
  if (match) {
    files = match[1].split(", ").filter(Boolean);
    text = text.slice(0, match.index);
    if (["Leggi gli allegati che ti ho inviato.", "Read the attachments I sent you."].includes(text)) text = "";
  }
  const mentions = [];
  for (const m of text.matchAll(/@[^@\n]*? \[([0-9a-f-]{8})\]/g)) {
    const target = sessions.find((s) => s.id.startsWith(m[1]));
    if (target && !mentions.some((x) => x.token === m[0])) mentions.push({ session_id: target.id, token: m[0] });
  }
  return { text, mentions, files };
}
