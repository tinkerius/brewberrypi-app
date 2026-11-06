/** Modal dialogs: Information (UI-050) and the stop confirmation (UI-043); Cloud Sync's is in cloudSync.tsx. */

import type { CommandReply } from "./types";
import { BUTTON_STYLES, TITLE_STYLE } from "./ui";

export function Modal(props: { title: string; children: preact.ComponentChildren; buttons: preact.ComponentChildren }) {
  return (
    <div class="fixed inset-0 z-20 flex items-start justify-center bg-ink/50 p-4 pt-20 backdrop-blur-xs">
      <div
        class="w-full max-w-md rounded-xl bg-white p-6 shadow-xl ring-1 ring-ink/5 dark:bg-surface-dark dark:ring-white/10"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <h2 id="modal-title" class={TITLE_STYLE}>
          {props.title}
        </h2>
        <div class="mt-2 text-sm text-stone-600 dark:text-stone-300">{props.children}</div>
        <div class="mt-6 flex justify-end gap-2">{props.buttons}</div>
      </div>
    </div>
  );
}

/** UI-050 (Q-11 default: a modal for every reply). Shows the oldest unconfirmed reply. */
export function InformationModal(props: { reply: CommandReply | undefined; onConfirm: () => void }) {
  if (props.reply === undefined) {
    return null;
  }
  return (
    <Modal
      title="Information"
      buttons={
        <button type="button" class={BUTTON_STYLES.primary} onClick={props.onConfirm} autoFocus>
          Confirm
        </button>
      }
    >
      <p class={props.reply.success ? undefined : "font-medium text-red-600 dark:text-red-400"}>{props.reply.message}</p>
    </Modal>
  );
}

export const STOP_QUESTION = "Stop the running process? Heating and pump will be switched off.";

/** UI-043: a stopped process cannot be resumed, so stopping is confirmed first. */
export function StopConfirmation(props: { open: boolean; onCancel: () => void; onStop: () => void }) {
  if (!props.open) {
    return null;
  }
  return (
    <Modal
      title="Stop Process"
      buttons={
        <>
          <button type="button" class={BUTTON_STYLES.secondary} onClick={props.onCancel} autoFocus>
            Cancel
          </button>
          <button type="button" class={BUTTON_STYLES.dangerSolid} onClick={props.onStop}>
            Stop Process
          </button>
        </>
      }
    >
      <p>{STOP_QUESTION}</p>
    </Modal>
  );
}
