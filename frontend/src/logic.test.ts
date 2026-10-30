import { describe, expect, it, vi } from "vitest";
import { commandRequest, deviceUrl, fetchDeviceLink, fetchVersion, NO_CONNECTION, sendCommand } from "./api";
import { isAccepted } from "./commands";
import { EventStream, MISSED_HEARTBEATS, reconnectDelay, type SourceLike } from "./events";
import { descriptionLines, formatDegrees, formatHeating, formatPump, formatTemperature, formatTime } from "./format";
import { toPlotData } from "./Chart";
import { fixLanguage, validLanguage } from "./locale";
import { addReply, applyChartUpdate, dismissReply, initialState, receive, setConnected } from "./state";
import type { ChartSeries, EngineState, EventName, Snapshot } from "./types";

describe("UI-010 hardware status texts", () => {
  it("shows -- before the first message", () => {
    expect([formatTemperature(null), formatPump(null), formatHeating(null)]).toEqual(["--", "--", "--"]);
  });

  it("shows temperature with one decimal, pump on/off and heating in percent", () => {
    const hardware = { temperature: 62, pumpstatus: true, heatingstatus: 0.5 };
    expect([formatTemperature(hardware), formatPump(hardware), formatHeating(hardware)]).toEqual(["62.0°C", "on", "50%"]);
    expect(formatHeating({ ...hardware, heatingstatus: 1 })).toBe("100%");
    expect(formatPump({ ...hardware, pumpstatus: false })).toBe("off");
  });

  it("AC-005 shows Sensor error for an INVALID reading", () => {
    expect(formatTemperature({ temperature: null, pumpstatus: true, heatingstatus: 0 })).toBe("Sensor error");
  });
});

describe("UI-020 chart formatting", () => {
  it("formats time ticks as HH:MM in local time and temperatures with one decimal", () => {
    const local = new Date(2026, 9, 11, 7, 5, 9).getTime() / 1000;
    expect(formatTime(local)).toBe("07:05");
    expect(formatDegrees(62)).toBe("62.0");
  });

  it("UI-022 has no data before the first chart message", () => {
    expect(toPlotData(null)).toEqual([[], []]);
  });

  it("converts epoch milliseconds to the plot's seconds", () => {
    const series = [{ key: "Kettle Temperature", values: [{ x: 1_718_197_200_000, y: 62.5 }] }];
    expect(toPlotData(series)).toEqual([[1_718_197_200], [62.5]]);
  });
});

describe("FIX-24 descriptions", () => {
  it("splits line breaks into lines", () => {
    expect(descriptionLines("Now it's your job to do the iodine test.\nRest at 25.0 degrees (C) for 1min.")).toEqual([
      "Now it's your job to do the iodine test.",
      "Rest at 25.0 degrees (C) for 1min.",
    ]);
  });
});

describe("UI-042 command acceptance matrix", () => {
  const states: EngineState[] = ["NoProcess", "Loaded", "Running", "Paused", "Stopped", "Finished"];
  const allowed = (command: "load" | "unload" | "start" | "pause" | "resume" | "stop") =>
    states.filter((state) => isAccepted(command, state));

  it("matches spec 03 §3.2", () => {
    expect(allowed("load")).toEqual(["NoProcess", "Loaded", "Stopped", "Finished"]);
    expect(allowed("unload")).toEqual(["Loaded", "Stopped", "Finished"]);
    expect(allowed("start")).toEqual(["Loaded"]);
    expect(allowed("pause")).toEqual(["Running"]);
    expect(allowed("resume")).toEqual(["Paused"]);
    expect(allowed("stop")).toEqual(["Running", "Paused"]);
  });

  it("accepts nothing before the first process state", () => {
    expect(isAccepted("load", undefined)).toBe(false);
  });
});

const SNAPSHOT: Snapshot = {
  hardware: { temperature: 20, pumpstatus: false, heatingstatus: 0 },
  process: { state: "NoProcess" },
  recipes: [{ id: "BENIWEISS", name: "München 12.6 - SUD2:BENIWEISS" }],
  chart: [{ key: "Kettle Temperature", values: [{ x: 1000, y: 20 }, { x: 2000, y: 20.1 }] }],
  heartbeatMilliseconds: 5000,
};

describe("state", () => {
  it("AC-070 takes everything from the snapshot, then each change", () => {
    let state = receive(initialState, "snapshot", SNAPSHOT);
    expect(state.hardware?.temperature).toBe(20);
    expect(state.process).toEqual({ state: "NoProcess" });
    expect(state.recipes).toHaveLength(1);
    state = receive(state, "hardware", { temperature: 21, pumpstatus: true, heatingstatus: 1 });
    state = receive(state, "process", { processid: "BENIWEISS", processname: "München 12.6 - SUD2:BENIWEISS", state: "Loaded", goals: [] });
    state = receive(state, "ping", {});
    expect(state.hardware?.temperature).toBe(21);
    expect(state.process?.state).toBe("Loaded");
  });

  it("IF-073 applies chart updates: thinned points out, the new point in", () => {
    let state = receive(initialState, "snapshot", SNAPSHOT);
    state = receive(state, "chart", { add: { x: 3000, y: 20.2 }, remove: [2000] });
    expect(state.chart?.[0].values).toEqual([{ x: 1000, y: 20 }, { x: 3000, y: 20.2 }]);
    expect(applyChartUpdate(null, { add: { x: 1, y: 1 }, remove: [] })).toBeNull(); // before the snapshot
  });

  it("IF-073 drops a new point that thinning removed at once (full chart)", () => {
    let state = receive(initialState, "snapshot", SNAPSHOT);
    state = receive(state, "chart", { add: { x: 2500, y: 20.2 }, remove: [2500] });
    expect(state.chart?.[0].values).toEqual(SNAPSHOT.chart[0].values);
  });

  it("IF-044 applying the updates gives exactly the server's thinned series", () => {
    // The server's thinning (IF-043, chart.py): append, then while over the limit remove point i+1 of the first
    // adjacent pair with the smallest gap.
    const max = 20;
    const server: { x: number; y: number }[] = [];
    let chart: ChartSeries[] | null = [{ key: "Kettle Temperature", values: [] }];
    let x = 0;
    for (let sample = 0; sample < 1000; sample++) {
      x += 1000 + ((sample * 7919) % 13) * 50; // uneven gaps, as from a real sensor loop
      const add = { x, y: 20 + (sample % 10) / 10 };
      server.push(add);
      const remove: number[] = [];
      while (server.length > max) {
        let smallest = 0;
        for (let i = 1; i < server.length - 1; i++) {
          if (server[i + 1].x - server[i].x < server[smallest + 1].x - server[smallest].x) {
            smallest = i;
          }
        }
        remove.push(server.splice(smallest + 1, 1)[0].x);
      }
      chart = applyChartUpdate(chart, { add, remove });
      expect(chart?.[0].values).toEqual(server);
    }
    expect(chart?.[0].values).toHaveLength(max);
  });

  it("AC-076 queues every reply for the Information modal until confirmed", () => {
    let state = addReply(initialState, { command: "load", success: true, message: "first" });
    state = addReply(state, { command: "start", success: false, message: "second" });
    expect(state.replies.map((reply) => reply.message)).toEqual(["first", "second"]);
    state = dismissReply(state);
    expect(state.replies.map((reply) => reply.message)).toEqual(["second"]);
    expect(setConnected(state, true).connected).toBe(true);
  });
});

describe("UI-001 app version", () => {
  it("AC-092 reads the version from /healthz", async () => {
    const paths: string[] = [];
    const healthz = (status: number, body: string) => async (path: string) => {
      paths.push(path);
      return new Response(body, { status });
    };
    expect(await fetchVersion(healthz(200, '{"status":"ok","version":"0.1.0-alpha.4","engine":"NoProcess"}'))).toBe("0.1.0-alpha.4");
    expect(paths).toEqual(["/healthz"]);
    expect(await fetchVersion(healthz(503, '{"version":"0.1.0-alpha.4"}'))).toBeNull();
    expect(await fetchVersion(healthz(200, "not json"))).toBeNull();
    expect(await fetchVersion(() => Promise.reject(new TypeError("offline")))).toBeNull();
  });
});

describe("IF-053 commands over REST", () => {
  it("maps every command to its HTTP request", () => {
    expect(commandRequest("load", { recipe: "BENIWEISS" })).toEqual({ method: "PUT", path: "/api/process", body: { recipe: "BENIWEISS" } });
    expect(commandRequest("unload")).toEqual({ method: "DELETE", path: "/api/process", body: {} });
    for (const command of ["start", "pause", "resume", "stop"] as const) {
      expect(commandRequest(command)).toEqual({ method: "POST", path: `/api/process/${command}`, body: {} });
    }
    expect(commandRequest("respond", { request: "7", response: "Yes" })).toEqual({
      method: "POST",
      path: "/api/process/requests/7",
      body: { response: "Yes" },
    });
  });

  it("sends JSON and turns the HTTP answer into a reply", async () => {
    const calls: [string, RequestInit][] = [];
    const reply = (status: number, body: unknown) => async (path: string, init: RequestInit) => {
      calls.push([path, init]);
      return new Response(JSON.stringify(body), { status });
    };
    expect(await sendCommand("start", {}, reply(200, { command: "start", success: true, message: "Process started." }))).toEqual({
      command: "start",
      success: true,
      message: "Process started.",
    });
    expect(calls[0][1]).toMatchObject({ method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    expect(await sendCommand("start", {}, reply(409, { command: "start", success: false, message: "Process is already running." }))).toEqual({
      command: "start",
      success: false,
      message: "Process is already running.",
    });
    const offline = async () => {
      throw new TypeError("Failed to fetch");
    };
    expect(await sendCommand("stop", {}, offline)).toEqual({ command: "stop", success: false, message: NO_CONNECTION });
    const proxyError = async () => new Response("<html>Bad Gateway</html>", { status: 502 });
    expect(await sendCommand("stop", {}, proxyError)).toEqual({ command: "stop", success: false, message: "HTTP 502" });
  });
});

class FakeSource implements SourceLike {
  readyState = 0;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((event: MessageEvent) => void)[]>();

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  emit(type: string, data: unknown): void {
    this.readyState = 1;
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data: typeof data === "string" ? data : JSON.stringify(data) } as MessageEvent);
    }
  }

  fail(closed = false): void {
    this.readyState = closed ? 2 : 0;
    this.onerror?.({} as Event);
  }
}

function setup() {
  const sources: FakeSource[] = [];
  const timers: { callback: () => void; ms: number; cleared: boolean }[] = [];
  const received: [EventName, unknown][] = [];
  const connected: boolean[] = [];
  const stream = new EventStream({
    url: "/api/events",
    onEvent: (name, data) => received.push([name, data]),
    onConnectedChange: (value) => connected.push(value),
    createSource: () => {
      const source = new FakeSource();
      sources.push(source);
      return source;
    },
    setTimer: (callback, ms) => {
      const timer = {
        ms,
        cleared: false,
        callback: () => {
          timer.cleared = true; // a timer that fired is done
          callback();
        },
      };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => {
      (timer as { cleared: boolean }).cleared = true;
    },
  });
  const live = () => timers.filter((timer) => !timer.cleared);
  return { stream, sources, timers, live, received, connected };
}

describe("UI-060 event stream", () => {
  it("is connected from the snapshot on and delivers events", () => {
    const { stream, sources, received, connected } = setup();
    stream.start();
    expect(connected).toEqual([]);
    sources[0].emit("process", { state: "NoProcess" }); // before the snapshot: ignored
    sources[0].emit("snapshot", SNAPSHOT);
    sources[0].emit("hardware", { temperature: 21, pumpstatus: false, heatingstatus: 0 });
    sources[0].emit("process", "not json");
    expect(received.map(([name]) => name)).toEqual(["snapshot", "hardware"]);
    expect(connected).toEqual([true]);
  });

  it("AC-077 shows disconnected while the browser reconnects, connected again with the next snapshot", () => {
    const { stream, sources, connected } = setup();
    stream.start();
    sources[0].emit("snapshot", SNAPSHOT);
    sources[0].fail(); // the browser retries by itself
    expect(connected).toEqual([true, false]);
    expect(sources).toHaveLength(1);
    sources[0].emit("snapshot", SNAPSHOT); // the server sends a fresh snapshot on reconnect
    expect(connected).toEqual([true, false, true]);
  });

  it("IF-074 reopens the stream when the heartbeat stops", () => {
    const { stream, sources, live, connected } = setup();
    stream.start();
    sources[0].emit("snapshot", { ...SNAPSHOT, heartbeatMilliseconds: 2000 });
    const [watchdog] = live();
    expect(watchdog.ms).toBe(2000 * MISSED_HEARTBEATS);
    sources[0].emit("ping", {}); // re-armed
    expect(live()).toHaveLength(1);
    live()[0].callback(); // nothing arrived for 5 s
    expect(connected).toEqual([true, false]);
    expect(sources[0].closed).toBe(true);
    const [reopen] = live();
    expect(reopen.ms).toBe(500);
    reopen.callback();
    expect(sources).toHaveLength(2);
  });

  it("reopens with growing delays when the browser gives up", () => {
    const { stream, sources, live } = setup();
    stream.start();
    sources[0].fail(true);
    expect(live().map((timer) => timer.ms)).toEqual([500]);
    live()[0].callback();
    sources[1].fail(true);
    expect(live().map((timer) => timer.ms)).toEqual([1000]);
    live()[0].callback();
    sources[2].emit("snapshot", SNAPSHOT);
    sources[2].fail(true);
    expect(live().map((timer) => timer.ms)).toEqual([500]); // starts again after a snapshot
  });

  it("caps the reopen delay at 5 s", () => {
    expect([0, 1, 2, 3, 4, 10].map(reconnectDelay)).toEqual([500, 1000, 2000, 4000, 5000, 5000]);
  });

  it("UI-062 releases the connection while hidden and gets a fresh snapshot when visible again", () => {
    const { stream, sources, live, received, connected } = setup();
    stream.start();
    sources[0].emit("snapshot", SNAPSHOT);
    stream.suspend(); // tab hidden
    expect(sources[0].closed).toBe(true);
    expect(live()).toEqual([]); // no watchdog, no reopen while hidden
    expect(connected).toEqual([true]); // the UI keeps its last state
    stream.resume(); // tab visible again
    expect(sources).toHaveLength(2);
    sources[1].emit("snapshot", SNAPSHOT);
    expect(received.filter(([name]) => name === "snapshot")).toHaveLength(2);
  });

  it("UI-062 opens nothing for a tab that starts hidden, until it is shown", () => {
    const { stream, sources } = setup();
    stream.suspend();
    stream.start();
    expect(sources).toHaveLength(0);
    stream.resume();
    expect(sources).toHaveLength(1);
  });

  it("does not reopen after stop", () => {
    const { stream, sources, live } = setup();
    stream.start();
    sources[0].emit("snapshot", SNAPSHOT);
    stream.stop();
    expect(sources[0].closed).toBe(true);
    expect(live()).toEqual([]);
  });
});

describe("UI-002 device link", () => {
  it("AC-093 reads GET /api/ui and puts in the page's host", async () => {
    const paths: string[] = [];
    const ui = (status: number, body: string) => async (path: string) => {
      paths.push(path);
      return new Response(body, { status });
    };
    expect(await fetchDeviceLink(ui(200, '{"deviceLink":"http://{host}:8081/"}'))).toBe("http://{host}:8081/");
    expect(paths).toEqual(["/api/ui"]);
    expect(await fetchDeviceLink(ui(200, '{"deviceLink":null}'))).toBeNull();
    expect(await fetchDeviceLink(ui(404, '{"success":false,"message":"x"}'))).toBeNull();
    expect(await fetchDeviceLink(() => Promise.reject(new TypeError("offline")))).toBeNull();
    expect(deviceUrl("http://{host}:8081/", "brewberrypi.local")).toBe("http://brewberrypi.local:8081/");
    expect(deviceUrl("http://{host}:8081/", "[fe80::1]")).toBe("http://[fe80::1]:8081/");
  });
});

describe("UI-020 chart with a browser language Intl rejects", () => {
  it("keeps a valid language and cleans up a POSIX one", () => {
    expect(validLanguage("de-DE")).toBe("de-DE");
    expect(validLanguage("en-US@posix")).toBe("en-US");
    expect(validLanguage("de_DE.UTF-8")).toBe("de-DE");
    expect(validLanguage("C")).toBe("en-US");
    expect(validLanguage("")).toBe("en-US");
    expect(validLanguage(undefined)).toBe("en-US");
  });

  it("makes navigator.language usable for Intl.NumberFormat, as uPlot uses it", () => {
    const nav = { language: "en-US@posix" };
    expect(() => new Intl.NumberFormat(nav.language)).toThrow(RangeError);
    fixLanguage(nav);
    expect(nav.language).toBe("en-US");
    expect(new Intl.NumberFormat(nav.language).format(1234.5)).toBe("1,234.5");
  });

  it("leaves a valid navigator.language as it is", () => {
    const nav = { language: "fr-CH" };
    fixLanguage(nav);
    expect(nav.language).toBe("fr-CH");
  });
});
