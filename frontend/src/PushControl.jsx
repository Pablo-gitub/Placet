import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Bell, BellOff, X } from "lucide-react";

function keyBytes(base64url) {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64url.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

const supported = () => typeof window !== "undefined" && "serviceWorker" in navigator
  && "PushManager" in window && "Notification" in window;
// iOS delivers Web Push only to the app installed on the Home Screen.
const standalone = () => window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;

async function currentSubscription() {
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

async function post(method, url, body) {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.status === 204 ? null : res.json();
}

function PushDialog({ subscribed, setSubscribed, onClose }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const run = async (action) => {
    setBusy(true); setNote(null);
    try { await action(); } catch { setNote({ key: "push.error", error: true }); } finally { setBusy(false); }
  };

  const enable = () => run(async () => {
    // Must run within the tap: iOS asks for permission only on a user gesture.
    const permission = await Notification.requestPermission();
    if (permission !== "granted") { setNote({ key: "push.denied", error: true }); return; }
    const { key } = await post("GET", "/api/push/key");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
    await post("POST", "/api/push/subscription", sub.toJSON());
    setSubscribed(true);
    setNote({ key: "push.enabled" });
  });
  const disable = () => run(async () => {
    const sub = await currentSubscription();
    if (sub) {
      await post("DELETE", "/api/push/subscription", { endpoint: sub.endpoint });
      await sub.unsubscribe();
    }
    setSubscribed(false);
    setNote({ key: "push.disabled" });
  });
  const test = () => run(async () => {
    const { sent } = await post("POST", "/api/push/test");
    setNote({ key: sent ? "push.testSent" : "push.testNone", error: !sent });
  });

  let content;
  if (!supported() || !standalone()) content = <p>{t("push.install")}</p>;
  else if (Notification.permission === "denied") content = <p>{t("push.blocked")}</p>;
  else if (subscribed) {
    content = <>
      <p>{t("push.on")}</p>
      <div className="dialog-actions">
        <button type="button" className="btn btn-primary" disabled={busy} data-testid="push-test" onClick={test}>{t("push.test")}</button>
        <button type="button" className="btn btn-secondary" disabled={busy} data-testid="push-disable" onClick={disable}>{t("push.disable")}</button>
      </div>
    </>;
  } else {
    content = <>
      <p>{t("push.off")}</p>
      <button type="button" className="btn btn-primary" disabled={busy} data-testid="push-enable" onClick={enable}>{t("push.enable")}</button>
    </>;
  }
  return createPortal(
    <div className="dialog-backdrop" onClick={() => !busy && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={t("push.title")} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-header">
          <h2>{t("push.title")}</h2>
          <button type="button" className="icon-button" aria-label={t("picker.close")} disabled={busy} onClick={onClose}><X size={18} /></button>
        </div>
        <div className="dialog-body">
          {content}
          <p className="composer-note">{t("push.privacy")}</p>
          {note && <p role={note.error ? "alert" : "status"}>{t(note.key)}</p>}
        </div>
      </div>
    </div>, document.body);
}

/** Web Push on this device: a bell in the app bar. */
export default function PushControl() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [subscribed, setSubscribed] = useState(false);
  useEffect(() => {
    if (!supported()) return;
    let cancelled = false;
    currentSubscription().then((sub) => { if (!cancelled) setSubscribed(Boolean(sub)); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  const Icon = subscribed ? Bell : BellOff;
  return <>
    <button type="button" className="icon-button" data-testid="push-control" aria-label={t("push.title")}
      title={t("push.title")} onClick={() => setOpen(true)}>
      <Icon size={16} />
    </button>
    {open && <PushDialog subscribed={subscribed} setSubscribed={setSubscribed} onClose={() => setOpen(false)} />}
  </>;
}
