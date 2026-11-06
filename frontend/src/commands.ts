/**
 * Which commands the engine accepts in which state (command acceptance matrix, spec 03 §3.2), used to
 * disable buttons (UI-042). The server stays authoritative and answers every command anyway.
 */

import type { EngineCommand, EngineState } from "./types";

const ACCEPTED: Record<Exclude<EngineCommand, "respond">, readonly EngineState[]> = {
  load: ["NoProcess", "Loaded", "Stopped", "Finished"],
  unload: ["Loaded", "Stopped", "Finished"],
  start: ["Loaded"],
  pause: ["Running"],
  resume: ["Paused"],
  stop: ["Running", "Paused"],
};

export function isAccepted(command: Exclude<EngineCommand, "respond">, state: EngineState | undefined): boolean {
  return state !== undefined && ACCEPTED[command].includes(state);
}
