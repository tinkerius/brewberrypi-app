/** UI state built from the event stream and the command replies. */

import type {
  ChartSeries,
  ChartUpdate,
  CloudSyncStatus,
  CommandReply,
  EventName,
  HardwareStatus,
  ProcessState,
  Recipe,
  Snapshot,
} from "./types";

export interface AppState {
  connected: boolean;
  /** `null` until the first snapshot (UI-010 `--`, UI-022 empty chart, UI-040 "loading…"). */
  hardware: HardwareStatus | null;
  process: ProcessState | null;
  recipes: Recipe[] | null;
  chart: ChartSeries[] | null;
  /** IF-090; `null` until the first snapshot. */
  cloudSync: CloudSyncStatus | null;
  /** Replies waiting to be shown in the Information modal, oldest first (UI-050). */
  replies: CommandReply[];
}

export const initialState: AppState = {
  connected: false,
  hardware: null,
  process: null,
  recipes: null,
  chart: null,
  cloudSync: null,
  replies: [],
};

export function receive(state: AppState, name: EventName, data: unknown): AppState {
  switch (name) {
    case "snapshot": {
      const snapshot = data as Snapshot;
      return {
        ...state,
        hardware: snapshot.hardware,
        process: snapshot.process,
        recipes: snapshot.recipes,
        chart: snapshot.chart,
        cloudSync: snapshot.cloudSync ?? null,
      };
    }
    case "hardware":
      return { ...state, hardware: data as HardwareStatus };
    case "process":
      return { ...state, process: data as ProcessState };
    case "recipes":
      return { ...state, recipes: data as Recipe[] };
    case "chart":
      return { ...state, chart: applyChartUpdate(state.chart, data as ChartUpdate) };
    case "cloudSync":
      return { ...state, cloudSync: data as CloudSyncStatus };
    default:
      return state; // ping
  }
}

/**
 * IF-073: append the new point, then drop the thinned ones. The new point itself is usually among them once the
 * chart is full (IF-043 removes the later point of the closest pair), so appending last would keep it and the
 * chart would grow by one point per sample, forever.
 */
export function applyChartUpdate(chart: ChartSeries[] | null, update: ChartUpdate): ChartSeries[] | null {
  if (chart === null || chart.length === 0) {
    return chart;
  }
  const removed = new Set(update.remove);
  const [series, ...rest] = chart;
  return [{ ...series, values: [...series.values, update.add].filter((point) => !removed.has(point.x)) }, ...rest];
}

export function setConnected(state: AppState, connected: boolean): AppState {
  return { ...state, connected };
}

const CLOUD_SYNC_COMMANDS: ReadonlySet<CommandReply["command"]> = new Set([
  "cloudSyncConnect",
  "cloudSyncDisconnect",
  "cloudSyncNow",
]);

/** UI-050, UI-084: every reply is shown, except a successful Cloud Sync one (the top bar shows its effect). */
export function isShown(reply: CommandReply): boolean {
  return !(reply.success && CLOUD_SYNC_COMMANDS.has(reply.command));
}

export function addReply(state: AppState, reply: CommandReply): AppState {
  return isShown(reply) ? { ...state, replies: [...state.replies, reply] } : state;
}

/** Close the Information modal: the next queued reply, if any, is shown next. */
export function dismissReply(state: AppState): AppState {
  return { ...state, replies: state.replies.slice(1) };
}
