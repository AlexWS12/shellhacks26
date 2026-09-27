"use client";

// A floating box to ask questions about the finished run. It appears only once the pipeline has produced results
// (a completed live/replay run, or "Show latest results"). Answers are grounded in the run's facts by POST /api/chat.

import { useEffect, useRef, useState } from "react";
import { MessageCircle, Send, X } from "lucide-react";

import { api } from "@/lib/api";
import { run, useRev } from "@/lib/run";
import type { ChatTurn } from "@/lib/types";
import { useUI } from "@/lib/ui";

const SUGGESTIONS = [
  "Which opportunities involve other utilities?",
  "Which pair is closest, and why does it stand out?",
  "What did the research team find near Savannah?",
];

export default function ChatBot() {
  useRev((s) => s.rev); // re-render as the run advances
  const health = useUI((s) => s.health);
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  const ready = run.phase === "done" && run.ok !== false;

  // The parent keys this component on the run id, so a new run remounts it with an empty conversation.
  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [turns, busy, open]);

  if (!ready) return null;

  const ask = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    const history = turns;
    setInput("");
    setError(null);
    setBusy(true);
    setTurns([...history, { role: "user", text: question }]);
    try {
      const r = await api.chat(question, history);
      setTurns((t) => [...t, { role: "assistant", text: r.answer }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chat">
      {open && (
        <section className="chat-panel" role="dialog" aria-label="Ask about this run">
          <header className="chat-head">
            <b>Ask about this run</b>
            <button className="linkbtn" onClick={() => setOpen(false)} aria-label="Close"><X size={16} /></button>
          </header>
          <div className="chat-list" ref={list}>
            {turns.length === 0 && (
              <div className="chat-intro">
                <p className="chat-note">Answers come only from this run&apos;s facts: its opportunities and the
                  research team&apos;s records on other owners.</p>
                {SUGGESTIONS.map((s) => <button key={s} className="chat-sugg" onClick={() => void ask(s)}>{s}</button>)}
              </div>
            )}
            {turns.map((t, i) => <div key={i} className={`chat-msg ${t.role}`}>{t.text}</div>)}
            {busy && <div className="chat-msg assistant thinking">Thinking…</div>}
            {error && <div className="chat-err" role="alert">{error}</div>}
          </div>
          {health?.chat === false ? (
            <p className="chat-off">No model is set up for questions yet. Open <b>Setup → Models</b> and add one for
              &ldquo;Answer questions about the run&rdquo;.</p>
          ) : (
            <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void ask(input); }}>
              <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
                placeholder="Ask about the opportunities or the research…" aria-label="Your question" />
              <button className="primary" type="submit" disabled={busy || !input.trim()} aria-label="Send">
                <Send size={15} />
              </button>
            </form>
          )}
        </section>
      )}
      <button className="chat-btn" aria-expanded={open} aria-label={open ? "Close the question box" : "Ask about this run"}
        onClick={() => setOpen((o) => !o)}>
        {open ? <X size={18} /> : <MessageCircle size={18} />}
        {!open && <span>Ask</span>}
      </button>
    </div>
  );
}
