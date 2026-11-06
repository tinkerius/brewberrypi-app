/** The Cloud Sync panel and its connect dialog (docs/specification/09-user-interface.md §9.9, spec 14). */

import { Modal } from "./dialogs";
import { formatTimeWithSeconds } from "./format";
import { Panel, type SendCommand } from "./panels";
import type { CloudSyncStatus } from "./types";
import { BUTTON_STYLES } from "./ui";

export const CLOUD_SYNC_TITLE = "Cloud Sync";

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
export function lastSyncText(status: CloudSyncStatus): string {
  if (status.state === "syncing") {
    return "Last sync: syncing…";
  }
  return `Last sync: ${status.lastSync === null ? "never" : formatSyncTime(status.lastSync)}`;
}

const CONNECTED = new Set(["idle", "syncing", "offline", "error"]);

/** UI-080..084: hidden while Cloud Sync is unavailable or the status is not known yet. */
export function CloudSyncPanel(props: {
  status: CloudSyncStatus | null;
  connected: boolean;
  send: SendCommand;
  onConnect: () => void;
}) {
  const { status, connected, send } = props;
  if (status === null || status.state === "unavailable") {
    return null;
  }
  const provider = status.provider;
  return (
    <Panel title={CLOUD_SYNC_TITLE} stale={!connected}>
      <div class="space-y-3 text-sm text-stone-700 dark:text-stone-300" data-cloud-sync={status.state}>
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
            <button
              type="button"
              class={BUTTON_STYLES.secondary}
              disabled={!connected}
              onClick={() => send("cloudSyncDisconnect")}
            >
              Cancel
            </button>
          </>
        )}
        {CONNECTED.has(status.state) && (
          <>
            <p>
              Connected to {provider} ({status.account}).
            </p>
            <p data-last-sync="">{lastSyncText(status)}</p>
            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class={BUTTON_STYLES.primary}
                disabled={!connected || status.state === "syncing"}
                onClick={() => send("cloudSyncNow")}
              >
                Sync now
              </button>
              <button
                type="button"
                class={BUTTON_STYLES.secondary}
                disabled={!connected}
                onClick={() => send("cloudSyncDisconnect")}
              >
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
    </Panel>
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
