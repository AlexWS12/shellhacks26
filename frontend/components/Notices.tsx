"use client";

// Small, non-blocking notices for problems a live run recovers from on its own: rate limits, timeouts, unusable
// answers, and a backup model taking over. Each fades after a few seconds; repeats add to its count.

import { dismissNotice, useAlerts } from "@/lib/alerts";

export default function Notices() {
  const notices = useAlerts((s) => s.notices);
  return (
    <div className="notices" role="status" aria-live="polite" aria-atomic="false">
      {notices.map((n) => (
        <div key={n.key} className="notice">
          <span>{n.text}{n.count > 1 && <span className="sub-inline"> ×{n.count}</span>}</span>
          <button className="linkbtn" onClick={() => dismissNotice(n.key)} aria-label="Dismiss notice">✕</button>
        </div>
      ))}
    </div>
  );
}
