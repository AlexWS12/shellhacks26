import type { SetupTab } from "@/lib/ui";

// Moves between the Setup modal's sections. step: which Models step; review: a source to open in the add flow.
export type Go = (tab: SetupTab, opts?: { step?: ModelStep; review?: string | null }) => void;
export type ModelStep = "keys" | "jobs" | "test";

// What the Models section knows after testing, for the Run section's readiness strip and the sidebar.
export interface ModelStatus { noneWork: number; backupFails: number; tested: boolean }
