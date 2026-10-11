/**
 * The message at the bottom of the screen, optionally with an action ("Undo"). Anything can show one;
 * the app shell displays it.
 */
export interface ToastAction { label: string; run: () => void | Promise<void> }
export interface Toast { id: number; message: string; action?: ToastAction }

let next = 1;
const EVENT = "probyte:toast";

export function showToast(message: string, action?: ToastAction) {
  window.dispatchEvent(new CustomEvent<Toast>(EVENT, { detail: { id: next++, message, action } }));
}

export function onToast(fn: (t: Toast) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<Toast>).detail);
  window.addEventListener(EVENT, h);
  return () => window.removeEventListener(EVENT, h);
}
