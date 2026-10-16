import React, { useCallback, useEffect, useState } from "react";
import { FolderPlus, RefreshCw } from "lucide-react";
import { PROVIDERS } from "./sessionInfo";

const STATE_LABELS = {
  on: "Attivo",
  inherited: "Attivo (dalla cartella che lo contiene)",
  excluded: "Escluso",
  off: "Spento",
};

const providerLabel = (key) => PROVIDERS.find((p) => p.key === key)?.label || key;

async function requestJson(url, options) {
  const res = await fetch(url, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Errore ${res.status}`);
  return body;
}

/**
 * Which projects go through Placet (opt-in per project): the hooks of a
 * session stay silent unless its folder is on. The bridge writes the
 * `.placet-on` / `.placet-off` markers.
 */
export default function Settings() {
  const [folders, setFolders] = useState([]);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");
  const [newPath, setNewPath] = useState("");
  const [busyPath, setBusyPath] = useState(null);

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const body = await requestJson("/api/folders");
      setFolders(body.folders || []);
      setError(null);
      setStatus("ready");
    } catch (e) {
      setError(e.message);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    // Data fetching: state changes when the list arrives.
    // oxlint-disable-next-line react/set-state-in-effect
    load();
  }, [load]);

  const switchFolder = async (path, active) => {
    setBusyPath(path);
    try {
      const updated = await requestJson("/api/folders", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, active }),
      });
      setFolders((prev) => {
        const known = prev.some((f) => f.path === updated.path);
        const next = known
          ? prev.map((f) => (f.path === updated.path ? { ...f, ...updated } : f))
          : [{ providers: [], last_used: 0, ...updated }, ...prev];
        // Folders inside the switched one may have changed too.
        return next.map((f) =>
          f.path.startsWith(`${updated.path}/`) && f.state !== "on" && f.state !== "excluded"
            ? { ...f, state: updated.state === "on" ? "inherited" : "off" }
            : f
        );
      });
      setError(null);
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    } finally {
      setBusyPath(null);
    }
  };

  const shown = folders.filter(
    (f) => !filter.trim() || f.path.toLowerCase().includes(filter.trim().toLowerCase())
  );

  return (
    <div data-testid="settings-section">
      <section className="home-section">
        <h2 className="home-title">Progetti in Placet</h2>
        <p className="settings-help">
          Le sessioni passano da Placet solo nei progetti attivi. Negli altri gli hook restano in silenzio e la sessione
          lavora come se Placet non ci fosse. Dal terminale: <code>touch .placet-on</code> nella cartella del progetto.
        </p>
        {status === "error" && (
          <div className="chat-error" data-testid="folders-error">
            <p>{error}</p>
            <button type="button" className="btn btn-secondary" onClick={load}>
              <RefreshCw size={14} /> Riprova
            </button>
          </div>
        )}
        {status === "loading" && <p className="chat-status">Carico le cartelle…</p>}
        {status === "ready" && (
          <>
            {error && <p className="folder-error">{error}</p>}
            <input
              className="settings-input"
              placeholder="Filtra le cartelle"
              aria-label="Filtra le cartelle"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <ul className="folder-list">
              {shown.map((f) => {
                const active = f.state === "on" || f.state === "inherited";
                return (
                  <li key={f.path} className="folder-row" data-testid={`folder-${f.name}`}>
                    <div className="folder-info">
                      <strong>{f.name}</strong>
                      <span className="session-meta-line">{f.path}</span>
                      <span className="session-meta-line">
                        {[...f.providers.map(providerLabel), STATE_LABELS[f.state]].filter(Boolean).join(" · ")}
                      </span>
                    </div>
                    <label className="switch">
                      <input
                        type="checkbox"
                        role="switch"
                        aria-label={`Placet in ${f.name}`}
                        data-testid={`folder-switch-${f.name}`}
                        checked={active}
                        disabled={busyPath === f.path}
                        onChange={(e) => switchFolder(f.path, e.target.checked)}
                      />
                      <span className="slider" />
                    </label>
                  </li>
                );
              })}
              {shown.length === 0 && <li className="empty-hint">Nessuna cartella.</li>}
            </ul>
            <form
              className="add-folder"
              onSubmit={async (e) => {
                e.preventDefault();
                if (newPath.trim() && (await switchFolder(newPath.trim(), true))) setNewPath("");
              }}
            >
              <input
                className="settings-input"
                placeholder="/home/paolo/Projects/nuovo-progetto"
                aria-label="Cartella da attivare"
                data-testid="new-folder"
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
              />
              <button type="submit" className="btn btn-secondary" disabled={!newPath.trim()}>
                <FolderPlus size={16} /> Attiva
              </button>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
