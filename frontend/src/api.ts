/**
 * Commands over REST (IF-053): each command is one HTTP request, answered with a status and a CommandReply.
 * Commands are sent as JSON, which the server requires (IF-061).
 */

import type { CommandName, CommandReply } from "./types";

export type CommandArgs = Record<string, string>;

/** UI-050: shown when a command did not reach the server. */
export const NO_CONNECTION = "No connection to the server.";

export interface HttpRequest {
  method: "PUT" | "POST" | "DELETE";
  path: string;
  body: Record<string, string>;
}

/** The HTTP request of a command; `respond` needs `request` (the request id) and `response`. */
export function commandRequest(command: CommandName, args: CommandArgs = {}): HttpRequest {
  switch (command) {
    case "load":
      return { method: "PUT", path: "/api/process", body: { recipe: args.recipe } };
    case "unload":
      return { method: "DELETE", path: "/api/process", body: {} };
    case "respond":
      return {
        method: "POST",
        path: `/api/process/requests/${encodeURIComponent(args.request ?? "")}`,
        body: { response: args.response },
      };
    case "cloudSyncConnect":
      return { method: "POST", path: "/api/cloud-sync/connect", body: {} };
    case "cloudSyncDisconnect":
      return { method: "POST", path: "/api/cloud-sync/disconnect", body: {} };
    case "cloudSyncNow":
      return { method: "POST", path: "/api/cloud-sync/sync", body: {} };
    default:
      return { method: "POST", path: `/api/process/${command}`, body: {} };
  }
}

export type Fetch = (path: string, init: RequestInit) => Promise<Response>;

/** Send a command; never throws: a network failure becomes an unsuccessful reply. */
export async function sendCommand(
  command: CommandName,
  args: CommandArgs = {},
  fetchFn: Fetch = (path, init) => fetch(path, init),
): Promise<CommandReply> {
  const request = commandRequest(command, args);
  let response: Response;
  try {
    response = await fetchFn(request.path, {
      method: request.method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request.body),
    });
  } catch {
    return { command, success: false, message: NO_CONNECTION };
  }
  let data: { success?: unknown; message?: unknown } = {};
  try {
    data = (await response.json()) as typeof data;
  } catch {
    // a body that is not JSON (e.g. a proxy error page): fall back to the HTTP status
  }
  const message = typeof data.message === "string" ? data.message : `HTTP ${response.status}`;
  return { command, success: response.ok && data.success !== false, message };
}

/** UI-002: the device link of `GET /api/ui` (IF-080) with `{host}` not yet replaced; null if none. */
export async function fetchDeviceLink(fetchFn: Fetch = (path, init) => fetch(path, init)): Promise<string | null> {
  try {
    const response = await fetchFn("/api/ui", { method: "GET", cache: "no-store" });
    const data = (await response.json()) as { deviceLink?: unknown };
    return response.ok && typeof data.deviceLink === "string" ? data.deviceLink : null;
  } catch {
    return null;
  }
}

/** UI-002: the device link for the host the page was opened with. */
export function deviceUrl(link: string, host: string): string {
  return link.replaceAll("{host}", host);
}

/** UI-001: the application version from `GET /healthz` (runtime contract §5); null if it cannot be read. */
export async function fetchVersion(fetchFn: Fetch = (path, init) => fetch(path, init)): Promise<string | null> {
  try {
    const response = await fetchFn("/healthz", { method: "GET", cache: "no-store" });
    const data = (await response.json()) as { version?: unknown };
    return response.ok && typeof data.version === "string" ? data.version : null;
  } catch {
    return null;
  }
}
