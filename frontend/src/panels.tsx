/** Page parts without the chart (docs/specification/09-user-interface.md §9.1, §9.2, §9.4, §9.5, §9.7). */

import { useEffect, useRef } from "preact/hooks";
import { isAccepted } from "./commands";
import { descriptionLines, formatHeating, formatPump, formatTemperature } from "./format";
import type { CommandName, EngineCommand, EngineState, Goal, HardwareStatus, ProcessState, Recipe } from "./types";
import { BUTTON_STYLES, CARD_STYLE, SELECT_STYLE, TITLE_STYLE } from "./ui";

export type SendCommand = (command: CommandName, args?: Record<string, string>) => void;

/** Page title (§9.1). */
export const APP_TITLE = "BrewberryPi";

/** UI-002: text of the link to the BrewberryPi OS device web interface. */
export const DEVICE_LINK_TEXT = "Device settings";

/** UI-001: the top bar with the BrewberryPi logo (frontend/BRAND.md); host and version once known.
 * UI-002: a link to the device web interface, when one is configured. */
export function PageHeader(props: { host: string; version: string | null; deviceUrl?: string | null }) {
  const info = [props.host, props.version].filter((part) => part).join(" · ");
  return (
    <header class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink/10 bg-white px-4 py-3 dark:border-white/10 dark:bg-surface-dark">
      <h1>
        {/* The logo carries the name as outlines; the hidden text gives the heading its name. */}
        <picture>
          <source srcset="logo-dark.svg" media="(prefers-color-scheme: dark)" />
          <img src="logo.svg" alt="" class="h-8 w-auto" width={145} height={32} />
        </picture>
        <span class="sr-only">{APP_TITLE}</span>
      </h1>
      {info && <span class="text-stone-500 dark:text-stone-400">{info}</span>}
      {props.deviceUrl && (
        <a
          href={props.deviceUrl}
          target="_blank"
          rel="noopener"
          class="ml-auto text-sm font-medium text-berry hover:underline dark:text-berry-light"
        >
          {DEVICE_LINK_TEXT}
        </a>
      )}
    </header>
  );
}

/** UI-060: non-blocking connection indicator. */
export function ConnectionIndicator(props: { connected: boolean; everConnected: boolean }) {
  if (props.connected) {
    return null;
  }
  return (
    <div
      role="status"
      class="sticky top-2 z-10 mx-auto w-fit rounded-full bg-brass/15 px-4 py-1.5 text-sm font-medium text-ink shadow-sm ring-1 ring-brass/50 backdrop-blur-xs dark:bg-brass-light/15 dark:text-brass-light dark:ring-brass-light/30"
    >
      {props.everConnected ? "disconnected — reconnecting…" : "connecting…"}
    </div>
  );
}

/** A card with a title; while disconnected its content is marked as stale (UI-061). */
export function Panel(props: { title: string; stale?: boolean; children: preact.ComponentChildren }) {
  return (
    <section class={CARD_STYLE} data-stale={props.stale ? "" : undefined}>
      <h2 class={`mb-4 ${TITLE_STYLE}`}>{props.title}</h2>
      <div class={props.stale ? "opacity-40 transition-opacity" : "transition-opacity"}>{props.children}</div>
    </section>
  );
}

function Value(props: { label: string; value: string; alert?: boolean }) {
  return (
    <div class="rounded-lg bg-paper px-4 py-3 ring-1 ring-ink/5 dark:bg-white/5 dark:ring-0">
      <dt class="text-xs font-medium tracking-wide text-stone-500 uppercase dark:text-stone-400">{props.label}</dt>
      <dd
        class={`mt-1 text-2xl font-semibold tabular-nums ${props.alert ? "text-red-600 dark:text-red-400" : "text-ink dark:text-ink-light"}`}
        data-alert={props.alert ? "" : undefined}
      >
        {props.value}
      </dd>
    </div>
  );
}

/** UI-010, UI-011. */
export function HardwareStatusPanel(props: { hardware: HardwareStatus | null; state: EngineState | undefined; stale: boolean }) {
  const { hardware } = props;
  return (
    <Panel title="Hardware Status" stale={props.stale}>
      <dl class="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Value label="Temperature" value={formatTemperature(hardware)} alert={hardware !== null && hardware.temperature === null} />
        <Value label="Pump" value={formatPump(hardware)} />
        <Value label="Heating" value={formatHeating(hardware)} />
        <Value label="Process" value={props.state ?? "--"} />
      </dl>
    </Panel>
  );
}

/** UI-031 colours in the brand: Done green (leaf), Active amber (brass), Waiting neutral. */
export const GOAL_STYLES: Record<Goal["status"], { item: string; badge: string }> = {
  Done: {
    item: "bg-leaf/8 ring-1 ring-leaf/25 dark:bg-leaf-light/10 dark:ring-leaf-light/20",
    badge: "bg-leaf/15 text-leaf dark:bg-leaf-light/20 dark:text-leaf-light",
  },
  Active: {
    item: "bg-brass/10 ring-2 ring-brass/60 dark:bg-brass-light/10 dark:ring-brass-light/40",
    badge: "bg-brass/20 text-ink dark:bg-brass-light/20 dark:text-brass-light",
  },
  Waiting: {
    item: "bg-white ring-1 ring-stone-200 dark:bg-transparent dark:ring-white/10",
    badge: "bg-stone-100 text-stone-600 dark:bg-white/10 dark:text-stone-300",
  },
};

/** UI-031, UI-032. */
export function GoalItem(props: { goal: Goal; connected: boolean; onRespond: (option: string) => void }) {
  const { goal } = props;
  const style = GOAL_STYLES[goal.status];
  return (
    <li class={`rounded-lg p-4 ring-inset ${style.item}`} data-status={goal.status}>
      <div class="flex items-start justify-between gap-3">
        <h3 class="font-semibold text-ink dark:text-ink-light">{goal.name}</h3>
        <span class={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${style.badge}`} data-badge="">
          {goal.status}
        </span>
      </div>
      <p class="mt-1 text-sm text-stone-600 dark:text-stone-400" data-description="">
        {descriptionLines(goal.description).map((line, index) => (
          <span key={index}>
            {index > 0 && <br />}
            {line}
          </span>
        ))}
      </p>
      {goal.status === "Active" && (
        <div
          class="relative mt-3 h-6 overflow-hidden rounded-full bg-white ring-1 ring-ink/10 dark:bg-white/10 dark:ring-0"
          role="progressbar"
          aria-valuenow={goal.statuspercent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuetext={goal.statusmessage}
        >
          <div class="h-full rounded-full bg-berry/30 transition-[width] duration-500 dark:bg-berry-light/45" style={{ width: `${goal.statuspercent}%` }} />
          <span class="absolute inset-0 flex items-center justify-center text-xs font-semibold text-ink dark:text-ink-light">
            {goal.statusmessage}
          </span>
        </div>
      )}
      {goal.activeRequest && (
        <div class="mt-4" role="group" aria-label={goal.activeRequest.message}>
          <p class="mb-2 font-semibold text-ink dark:text-ink-light">{goal.activeRequest.message}</p>
          <div class="flex flex-wrap gap-2">
            {goal.activeRequest.options.map((option) => (
              <button key={option} type="button" class={BUTTON_STYLES.primary} disabled={!props.connected} onClick={() => props.onRespond(option)}>
                {option}
              </button>
            ))}
          </div>
        </div>
      )}
    </li>
  );
}

/** UI-030..033 and the process controls UI-040..043. */
export function ProcessPanel(props: {
  process: ProcessState | null;
  recipes: Recipe[] | null;
  connected: boolean;
  send: SendCommand;
  onStop: () => void;
}) {
  const { process, connected, send } = props;
  const state = process?.state;
  const goals = process !== null && "goals" in process ? process.goals : [];
  const activeIndex = goals.findIndex((goal) => goal.status === "Active");
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    // UI-033: bring the active goal into view when another goal becomes active.
    const item = list.current?.children[activeIndex] as HTMLElement | undefined;
    item?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [activeIndex]);

  const enabled = (command: Exclude<EngineCommand, "respond">) => connected && isAccepted(command, state);

  return (
    <Panel title="Brewing Process" stale={!connected}>
      {process !== null && "processname" in process && (
        <p class="mb-4 font-brand text-xl font-medium text-berry dark:text-berry-light" data-process-name="">
          {process.processname}
        </p>
      )}
      <ol class="mb-6 space-y-2 empty:mb-0" ref={list}>
        {goals.map((goal, index) => (
          <GoalItem
            key={index}
            goal={goal}
            connected={connected}
            onRespond={(option) => send("respond", { request: goal.activeRequest?.id ?? "", response: option })}
          />
        ))}
      </ol>
      <div class="flex flex-wrap gap-2">
        <select
          aria-label="Load Process"
          class={SELECT_STYLE}
          disabled={!enabled("load") || props.recipes === null}
          value=""
          onChange={(event) => {
            const id = (event.target as HTMLSelectElement).value;
            if (id) {
              send("load", { recipe: id });
            }
          }}
        >
          <option value="">{props.recipes === null ? "loading…" : "Load Process"}</option>
          {props.recipes?.map((recipe) => (
            <option key={recipe.id} value={recipe.id}>
              {recipe.name}
            </option>
          ))}
        </select>
        <button type="button" class={BUTTON_STYLES.secondary} disabled={!enabled("unload")} onClick={() => send("unload")}>
          Unload Process
        </button>
      </div>
      <div class="mt-2 flex flex-wrap gap-2">
        <button type="button" class={BUTTON_STYLES.primary} disabled={!enabled("start")} onClick={() => send("start")}>
          Start Process
        </button>
        <button type="button" class={BUTTON_STYLES.secondary} disabled={!enabled("pause")} onClick={() => send("pause")}>
          Pause Process
        </button>
        <button type="button" class={BUTTON_STYLES.secondary} disabled={!enabled("resume")} onClick={() => send("resume")}>
          Resume Process
        </button>
        <button type="button" class={BUTTON_STYLES.danger} disabled={!enabled("stop")} onClick={props.onStop}>
          Stop Process
        </button>
      </div>
    </Panel>
  );
}
