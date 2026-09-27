// Follows one run's event stream outside the main map run (the Sources menu reading or activating a filing).
// Model problems go to the failure modal like in a pipeline run.

import { onRunEvent } from "./alerts";
import { API } from "./api";
import type { RoleInfo, RunEvent } from "./types";

export function followRun(runId: string, roles: RoleInfo, onEvent: (e: RunEvent) => void): () => void {
  const src = new EventSource(`${API}/api/runs/${runId}/events`);
  let last = -1;
  src.onmessage = (m) => {
    const e = JSON.parse(m.data) as RunEvent;
    if (e.seq <= last) return; // a reconnect resends the backlog
    last = e.seq;
    onRunEvent(e, true, roles);
    onEvent(e);
  };
  src.addEventListener("end", () => src.close());
  return () => src.close();
}
