"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";

import { boot, useUI } from "@/lib/ui";

import Header from "./Header";
import PipelinePanel from "./PipelinePanel";
import RightPanel from "./RightPanel";

const MapView = dynamic(() => import("./MapView"), { ssr: false });

export default function TandemApp() {
  const error = useUI((s) => s.error);
  useEffect(() => {
    void boot();
  }, []);
  return (
    <div className="app">
      <div>
        <Header />
        {error && <div className="err" role="alert">{error}</div>}
      </div>
      <div className="main">
        <aside className="col left" aria-label="Pipeline">
          <PipelinePanel />
        </aside>
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
