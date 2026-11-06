/**
 * Messages of the client/server interface (docs/specification/08-client-server-interface.md,
 * docs/specification/data/messages.schema.json): REST under `/api`, server-sent events at `/api/events`.
 */

/** Commands of the engine (IF-050). */
export type EngineCommand = "load" | "unload" | "start" | "pause" | "resume" | "stop" | "respond";
/** Cloud Sync commands (IF-091). */
export type CloudSyncCommand = "cloudSyncConnect" | "cloudSyncDisconnect" | "cloudSyncNow";
export type CommandName = EngineCommand | CloudSyncCommand;

/** Event names of the event stream (IF-070). */
export const EVENTS = ["snapshot", "hardware", "process", "recipes", "chart", "cloudSync", "ping"] as const;
export type EventName = (typeof EVENTS)[number];

/** IF-010. `temperature` is `null` when the sensor reading is INVALID. */
export interface HardwareStatus {
  temperature: number | null;
  pumpstatus: boolean;
  heatingstatus: number;
}

export type EngineState = "NoProcess" | "Loaded" | "Running" | "Paused" | "Stopped" | "Finished";
export type GoalStatus = "Waiting" | "Active" | "Done";

export interface OperatorRequest {
  /** Answer it with POST /api/process/requests/{id} (DOM-035). */
  id: string;
  message: string;
  options: string[];
}

export interface Goal {
  name: string;
  description: string;
  status: GoalStatus;
  statuspercent: number;
  statusmessage: string;
  activeRequest?: OperatorRequest;
}

/** IF-020. */
export type ProcessState =
  | { state: "NoProcess" }
  | {
      processid: string;
      processname: string;
      state: Exclude<EngineState, "NoProcess">;
      goals: Goal[];
    };

/** IF-030. */
export interface Recipe {
  id: string;
  name: string;
}

/** IF-040: x in epoch milliseconds, y in °C. */
export interface ChartPoint {
  x: number;
  y: number;
}

export interface ChartSeries {
  key: string;
  values: ChartPoint[];
}

/** IF-073: a new point and the x of each point that thinning removed. */
export interface ChartUpdate {
  add: ChartPoint;
  remove: number[];
}

/** IF-071: the first event of every stream. */
export interface Snapshot {
  hardware: HardwareStatus;
  process: ProcessState;
  recipes: Recipe[];
  chart: ChartSeries[];
  cloudSync: CloudSyncStatus;
  heartbeatMilliseconds: number;
}

export type CloudSyncState = "unavailable" | "disconnected" | "connecting" | "idle" | "syncing" | "offline" | "error";

/** IF-090: the state of Cloud Sync (spec 14). */
export interface CloudSyncStatus {
  state: CloudSyncState;
  provider: string;
  account: string | null;
  /** While connecting: the code to enter at `url`, valid until `expiresAt` (epoch ms). */
  signIn: { code: string; url: string; expiresAt: number } | null;
  /** End of the last successful sync, epoch ms. */
  lastSync: number | null;
  message: string | null;
  issues: { recipe: string; message: string }[];
}

/** IF-050: the reply to a command, shown in the Information modal (UI-050). */
export interface CommandReply {
  command: CommandName;
  success: boolean;
  message: string;
}
