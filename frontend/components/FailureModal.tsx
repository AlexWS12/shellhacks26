"use client";

// The failure modal: an AI model that can't be used (rejected key, unknown model name, usage limit), a stage that
// produced nothing because no model worked, or a live run the server refused to start. One modal lists them all.

import { useRef } from "react";

import { dismissModal, retryAction, useAlerts } from "@/lib/alerts";
import { useFocusTrap } from "@/lib/focus";
import { run, useRev } from "@/lib/run";
import { openSetup, startRun, useUI } from "@/lib/ui";

export default function FailureModal() {
  useRev((s) => s.rev);
  const modal = useAlerts((s) => s.modal);
  const roles = useUI((s) => s.health?.models) ?? {};
  const box = useRef<HTMLDivElement>(null);
  useFocusTrap(box, dismissModal, [modal?.source]);
  if (!modal) return null;

  const { entries, source } = modal;
  const running = run.phase === "running";
  const label = (id: string) => roles[id]?.label ?? id;
  const title = entries.length === 1 ? entries[0].title
    : source === "preflight" ? "The run can't start" : `${entries.length} problems with the AI models`;

  const setup = () => {
    dismissModal();
    openSetup({ focusRole: entries.flatMap((e) => e.roles)[0] ?? null, problems: source === "preflight" ? modal.problems : [],
                step: entries.some((e) => e.fix === "keys") ? "keys" : "models" });
  };
  const retry = (force = false) => {
    dismissModal();
    const custom = retryAction();
    if (custom && !force) custom();  // e.g. read the filing again
    else void startRun("live", force);
  };

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && dismissModal()}>
      <div ref={box} className="modal alert" role="alertdialog" aria-modal="true" aria-labelledby="alert-title"
        aria-describedby="alert-body" tabIndex={-1}>
        <div className="modal-head">
          <h2 id="alert-title">{title}</h2>
        </div>
        <div id="alert-body">
          {source === "preflight" && <p className="sub">The run didn&apos;t start, so nothing was read or written.</p>}
          {entries.map((e) => (
            <div key={e.key} className="alert-entry">
              {entries.length > 1 && <b>{e.title}</b>}
              <p>
                {e.roles.length ? <>Affects: {e.roles.map(label).join(", ")}</> : null}
                {e.usedBy.length > 0 && <span className="sub-inline"> · the same model also does: {e.usedBy.join(", ")}</span>}
              </p>
              {e.models.length > 1 && <p className="sub-inline">Models: {e.models.map((m) => m.slice(m.indexOf("/") + 1)).join(", ")}</p>}
              <p className="outcome">{e.outcome}</p>
              {e.detail && <p className="note">{e.detail}</p>}
            </div>
          ))}
        </div>
        <div className="row2">
          <button className="primary" onClick={setup}>Open setup</button>
          <button onClick={() => retry()} disabled={running} title={running ? "Available when this run finishes" : undefined}>Retry run</button>
          {source === "preflight" && modal.canForce && (
            <button onClick={() => retry(true)} title="Those models are only busy or over their limit right now; their text comes from templates">
              Run anyway
            </button>
          )}
          <button onClick={dismissModal}>Dismiss</button>
        </div>
        {running && <p className="note">The run keeps going. Retry run is available when it finishes.</p>}
      </div>
    </div>
  );
}
