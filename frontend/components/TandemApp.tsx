"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";

import { boot, useUI } from "@/lib/ui";

import FailureModal from "./FailureModal";
import Header from "./Header";
import ModelSetup from "./ModelSetup";
import Notices from "./Notices";
import PipelinePanel from "./PipelinePanel";
import RightPanel from "./RightPanel";

const MapView = dynamic(() => import("./MapView"), { ssr: false });

export default function TandemApp() {
  const error = useUI((s) => s.error);
  const setupOpen = useUI((s) => s.setup !== null);
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
      {setupOpen && <ModelSetup />}
      <FailureModal />
      <Notices />
    </div>
  );
}
