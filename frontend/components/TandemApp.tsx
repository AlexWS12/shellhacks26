"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";

import { boot, useUI } from "@/lib/ui";

import Header from "./Header";
import { Flyout, Rail, RailHandle } from "./Rail";
import RightPanel from "./RightPanel";

const MapView = dynamic(() => import("./MapView"), { ssr: false });

export default function TandemApp() {
  const error = useUI((s) => s.error);
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
    </div>
  );
}
