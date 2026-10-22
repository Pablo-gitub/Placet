/**
 * Format a Date object or UTC ISO string as YYYY-MM-DDTHH:mm in local time
 * for <input type="datetime-local">.
 */
export function toLocalDatetimeString(dateOrIso) {
  if (!dateOrIso) return "";
  const d = new Date(dateOrIso);
  if (isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const year = d.getFullYear();
  const month = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hours = pad(d.getHours());
  const minutes = pad(d.getMinutes());
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

/**
 * Convert a local YYYY-MM-DDTHH:mm string back to a UTC ISO string.
 */
export function toUtcIso(localString) {
  if (!localString) return null;
  const d = new Date(localString);
  if (isNaN(d.getTime())) return null;
  return d.toISOString();
}
