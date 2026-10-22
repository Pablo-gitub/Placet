import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const EVERY_MS = 60_000;
// The bridge counts once a minute: older numbers say nothing about now.
const STALE_MS = 3 * 60_000;

/**
 * How many Claude Desktop sessions are loaded against the app's cap (counted by the bridge, Linux only).
 * At the cap the app does not start another one for a wake-up from Placet, so it is worth saying
 * before it happens. Nothing is shown without recent numbers.
 */
export default function DesktopCapacity() {
  const { t } = useTranslation();
  const [capacity, setCapacity] = useState(null);

  useEffect(() => {
    if (typeof fetch !== "function") return undefined;
    let cancelled = false;
    const load = () => {
      Promise.resolve()
        .then(() => fetch("/api/claude-desktop/capacity"))
        .then((res) => (res && res.ok ? res.json() : null))
        // Staleness is judged when the numbers arrive; the next look is a minute later.
        .then((data) => { if (!cancelled) setCapacity(data && Date.now() - Date.parse(data.observed_at) < STALE_MS ? data : null); })
        .catch(() => { if (!cancelled) setCapacity(null); });
    };
    load();
    const timer = setInterval(load, EVERY_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  if (!capacity || !Number.isFinite(capacity.engines) || !Number.isFinite(capacity.cap)) return null;
  const full = capacity.engines >= capacity.cap;
  return (
    <div className={`desktop-capacity${full ? " capacity-full" : ""}`} data-testid="desktop-capacity">
      <span>{t("capacity.line", { engines: capacity.engines, cap: capacity.cap })}</span>
      {full && <small data-testid="desktop-capacity-hint">{t("capacity.hint")}</small>}
    </div>
  );
}
