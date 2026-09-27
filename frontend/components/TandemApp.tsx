"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";

import { run, useRev } from "@/lib/run";
import { boot, useUI } from "@/lib/ui";

import ChatBot from "./ChatBot";
import FailureModal from "./FailureModal";
import Header from "./Header";
import Notices from "./Notices";
import { Flyout, Rail, RailHandle } from "./Rail";
import RightPanel from "./RightPanel";
import Setup from "./Setup";

const MapView = dynamic(() => import("./MapView"), { ssr: false });

export default function TandemApp() {
  useRev((s) => s.rev); // a new run gets a fresh question box
  const error = useUI((s) => s.error);
  const setupOpen = useUI((s) => s.setup !== null);
  const railOpen = useUI((s) => s.railOpen);
  const railMode = useUI((s) => s.railMode);
  const hidden = railMode === "hidden";
  useEffect(() => {
    void boot();
  }, []);
  return (
    <div className="app">
      <div>
        <Header />
        {error && <div className="err" role="alert">{error}</div>}
      </div>
      <div className={`main ${railOpen && !hidden ? "rail-open" : ""} ${railMode === "full" ? "rail-expanded" : ""} ${hidden ? "rail-hidden" : ""}`}>
        {hidden ? <RailHandle /> : <Rail />}
        {railOpen && !hidden && <Flyout />}
        <section className="mapwrap" aria-label="Map">
          <MapView />
        </section>
        <aside className="col right" aria-label="Coordination opportunities">
          <RightPanel />
        </aside>
      </div>
      {setupOpen && <Setup />}
      <FailureModal />
      <Notices />
      <ChatBot key={run.runId ?? "idle"} />
    </div>
  );
}
