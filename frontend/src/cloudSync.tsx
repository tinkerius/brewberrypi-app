/**
 * Cloud Sync in the top bar (docs/specification/09-user-interface.md §9.9, spec 14): a pictogram with the
 * state, a menu with the details and the commands, and the connect dialog.
 */

import { useEffect, useRef, useState } from "preact/hooks";
import { Modal } from "./dialogs";
import { formatTimeWithSeconds } from "./format";
import type { SendCommand } from "./panels";
import type { CloudSyncState, CloudSyncStatus } from "./types";
import { BUTTON_STYLES, TITLE_STYLE } from "./ui";

export const CLOUD_SYNC_TITLE = "Cloud Sync";

/** UI-085: a sync is shown for at least this long, so a quick one is still seen. */
export const MIN_SYNC_DISPLAY_MILLISECONDS = 1500;

/** UI-081 */
export function offText(provider: string): string {
  return `Off. Connect to mirror the recipes from your ${provider}, where the recipe editor saves them.`;
}

/** UI-081: the warning of the connect dialog. */
export function connectWarning(provider: string): string {
  return (
    `The recipes on this device will be replaced by the recipes in your ${provider}. ` +
    "Recipes that are only on this device are moved to recipes/.backup. " +
    "Changes made on the device are overwritten while Cloud Sync is on."
  );
}

/** UI-084: a time of the status (epoch ms) in the browser's local time, `HH:MM:SS`. */
export function formatSyncTime(epochMilliseconds: number): string {
  return formatTimeWithSeconds(epochMilliseconds / 1000);
}

/** UI-083: `Last sync: …`. */
export function lastSyncText(status: CloudSyncStatus, syncing = status.state === "syncing"): string {
  if (syncing) {
    return "Last sync: syncing…";
  }
  return `Last sync: ${status.lastSync === null ? "never" : formatSyncTime(status.lastSync)}`;
}

const CONNECTED: ReadonlySet<CloudSyncState> = new Set(["idle", "syncing", "offline", "error"]);

export function isConnected(status: CloudSyncStatus): boolean {
  return CONNECTED.has(status.state);
}

/** The state the top bar shows: `syncing` stays on for MIN_SYNC_DISPLAY_MILLISECONDS (UI-085). */
export type Shown = CloudSyncState;

/** UI-080: the short text next to the pictogram. */
export function shortText(status: CloudSyncStatus, shown: Shown): string {
  switch (shown) {
    case "disconnected":
      return "Cloud Sync off";
    case "connecting":
      return status.signIn === null ? "Connecting…" : "Enter the code";
    case "syncing":
      return "Syncing…";
    case "offline":
      return "Offline";
    case "error":
      return "Sync error";
    default:
      return status.lastSync === null ? "Not synced yet" : `Synced ${formatSyncTime(status.lastSync).slice(0, 5)}`;
  }
}

/** Colour of the pictogram per state, in the brand colours (styles.css). */
const ICON_COLOUR: Record<CloudSyncState, string> = {
  unavailable: "text-stone-400",
  disconnected: "text-stone-400 dark:text-stone-500",
  connecting: "text-brass dark:text-brass-light",
  idle: "text-leaf dark:text-leaf-light",
  syncing: "text-berry dark:text-berry-light",
  offline: "text-brass dark:text-brass-light",
  error: "text-red-600 dark:text-red-400",
};

const CLOUD = "M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z";

/** UI-080: pictograms (24 × 24, stroked, drawn in the current colour). */
export function CloudSyncIcon(props: { state: Shown; class?: string }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": 2,
    "stroke-linecap": "round" as const,
    "stroke-linejoin": "round" as const,
    "aria-hidden": "true" as const,
    class: props.class ?? "h-5 w-5",
    "data-icon": props.state,
  };
  if (props.state === "syncing") {
    // Two arrows going round: spins while a sync runs (UI-085).
    return (
      <svg {...common} class={`${common.class} animate-spin [animation-duration:1.2s] motion-reduce:animate-pulse`}>
        <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" />
        <path d="M3 21v-5h5" />
        <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" />
        <path d="M21 3v5h-5" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d={CLOUD} />
      {props.state === "idle" && <path d="m9 14 2 2 4-4" />}
      {props.state === "connecting" && <path d="M9 14h.01M12 14h.01M15 14h.01" />}
      {props.state === "error" && <path d="M12 11v3M12 17h.01" />}
      {(props.state === "disconnected" || props.state === "offline") && <path d="m3 3 18 18" />}
    </svg>
  );
}

/** UI-085: `true` while syncing, and for at least `minimum` ms after a sync started. */
export function useShownSyncing(syncing: boolean, minimum = MIN_SYNC_DISPLAY_MILLISECONDS): boolean {
  const [shown, setShown] = useState(syncing);
  const started = useRef<number | null>(syncing ? Date.now() : null);
  useEffect(() => {
    if (syncing) {
      started.current = Date.now();
      setShown(true);
      return undefined;
    }
    if (started.current === null) {
      setShown(false);
      return undefined;
    }
    const left = minimum - (Date.now() - started.current);
    if (left <= 0) {
      started.current = null;
      setShown(false);
      return undefined;
    }
    const timer = setTimeout(() => {
      started.current = null;
      setShown(false);
    }, left);
    return () => clearTimeout(timer);
  }, [syncing, minimum]);
  return shown;
}

/**
 * UI-080..085: the Cloud Sync control of the top bar. Hidden while Cloud Sync is unavailable or the status is not
 * known yet. The menu opens by itself while a sign-in code waits to be entered.
 */
export function CloudSyncHeader(props: {
  status: CloudSyncStatus | null;
  connected: boolean;
  send: SendCommand;
  onConnect: () => void;
}) {
  const { status } = props;
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const syncing = useShownSyncing(status?.state === "syncing");
  const code = status?.state === "connecting" ? status.signIn?.code ?? null : null;

  useEffect(() => {
    if (code !== null) {
      setOpen(true); // UI-082: the code must be seen
    }
  }, [code]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !root.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  if (status === null || status.state === "unavailable") {
    return null;
  }
  const shown: Shown = syncing && isConnected(status) ? "syncing" : status.state;
  const text = shortText(status, shown);
  const issues = status.issues.length;
  return (
    // On narrow screens the menu hangs from the top bar's right edge (the button may sit anywhere in a wrapped
    // top bar); from `sm` on, from the button.
    <div class="sm:relative" ref={root}>
      <button
        type="button"
        class="relative inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium text-stone-600 ring-1 ring-ink/10 transition hover:bg-paper focus-visible:outline-2 focus-visible:outline-berry dark:text-stone-300 dark:ring-white/10 dark:hover:bg-white/5"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${CLOUD_SYNC_TITLE}: ${text}`}
        title={`${CLOUD_SYNC_TITLE}: ${text}`}
        data-cloud-sync={shown}
        onClick={() => setOpen(!open)}
      >
        <CloudSyncIcon state={shown} class={`h-5 w-5 ${ICON_COLOUR[shown]}`} />
        <span class="hidden sm:inline" data-short-text="">
          {text}
        </span>
        {issues > 0 && (
          <span
            class="absolute -top-1.5 -right-1.5 min-w-5 rounded-full bg-brass px-1 text-center text-xs leading-5 font-semibold text-ink dark:bg-brass-light"
            data-issue-count=""
          >
            {issues}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={CLOUD_SYNC_TITLE}
          class="absolute top-full right-4 z-30 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-xl sm:top-auto sm:right-0 bg-white p-5 shadow-xl ring-1 ring-ink/10 dark:bg-surface-dark dark:ring-white/10"
        >
          <h2 class={`mb-3 flex items-center gap-2 ${TITLE_STYLE}`}>
            <CloudSyncIcon state={shown} class={`h-5 w-5 ${ICON_COLOUR[shown]}`} />
            {CLOUD_SYNC_TITLE}
          </h2>
          <CloudSyncDetails
            status={status}
            syncing={shown === "syncing"}
            connected={props.connected}
            send={props.send}
            onConnect={() => {
              setOpen(false);
              props.onConnect();
            }}
          />
        </div>
      )}
    </div>
  );
}

/** UI-081..084: the content of the Cloud Sync menu. */
export function CloudSyncDetails(props: {
  status: CloudSyncStatus;
  syncing: boolean;
  connected: boolean;
  send: SendCommand;
  onConnect: () => void;
}) {
  const { status, connected, send } = props;
  const provider = status.provider;
  return (
    <div
      class={`space-y-3 text-sm text-stone-700 dark:text-stone-300 ${connected ? "" : "opacity-60"}`}
      data-cloud-sync-details={status.state}
    >
      {status.message !== null && (
        <p
          role="alert"
          class="rounded-lg bg-red-50 px-3 py-2 font-medium text-red-700 ring-1 ring-red-200 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-400/30"
        >
          {status.message}
        </p>
      )}
      {status.state === "disconnected" && (
        <>
          <p>{offText(provider)}</p>
          <button type="button" class={BUTTON_STYLES.primary} disabled={!connected} onClick={props.onConnect}>
            Connect {provider}
          </button>
        </>
      )}
      {status.state === "connecting" && (
        <>
          {status.signIn === null ? (
            <p>Asking {provider} for a sign-in code…</p>
          ) : (
            <>
              <p>
                Open{" "}
                <a
                  href={status.signIn.url}
                  target="_blank"
                  rel="noopener"
                  class="font-medium text-berry hover:underline dark:text-berry-light"
                >
                  {status.signIn.url}
                </a>{" "}
                and enter this code:
              </p>
              <p class="font-mono text-3xl font-semibold tracking-widest text-ink select-all dark:text-ink-light" data-code="">
                {status.signIn.code}
              </p>
              <p>The code expires at {formatSyncTime(status.signIn.expiresAt)}.</p>
            </>
          )}
          <button type="button" class={BUTTON_STYLES.secondary} disabled={!connected} onClick={() => send("cloudSyncDisconnect")}>
            Cancel
          </button>
        </>
      )}
      {isConnected(status) && (
        <>
          <p>
            Connected to {provider} ({status.account}).
          </p>
          <p data-last-sync="">{lastSyncText(status, props.syncing)}</p>
          <div class="flex flex-wrap gap-2">
            <button
              type="button"
              class={BUTTON_STYLES.primary}
              disabled={!connected || props.syncing}
              onClick={() => send("cloudSyncNow")}
            >
              Sync now
            </button>
            <button type="button" class={BUTTON_STYLES.secondary} disabled={!connected} onClick={() => send("cloudSyncDisconnect")}>
              Disconnect
            </button>
          </div>
          {status.issues.length > 0 && (
            <div>
              <h3 class="font-semibold text-ink dark:text-ink-light">Not synced</h3>
              <ul class="mt-1 list-disc space-y-1 pl-5" data-issues="">
                {status.issues.map((issue) => (
                  <li key={issue.recipe}>
                    {issue.recipe}: {issue.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** UI-081: confirmation before the recipes on the device are replaced. */
export function ConnectCloudSync(props: {
  open: boolean;
  provider: string;
  onCancel: () => void;
  onConnect: () => void;
}) {
  if (!props.open) {
    return null;
  }
  return (
    <Modal
      title={`Connect ${props.provider}`}
      buttons={
        <>
          <button type="button" class={BUTTON_STYLES.secondary} onClick={props.onCancel} autoFocus>
            Cancel
          </button>
          <button type="button" class={BUTTON_STYLES.primary} onClick={props.onConnect}>
            Connect
          </button>
        </>
      }
    >
      <p>{connectWarning(props.provider)}</p>
    </Modal>
  );
}
