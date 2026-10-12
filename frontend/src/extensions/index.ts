import { lazy, useEffect, useState, type ComponentType, type LazyExoticComponent } from "react";
import { api } from "../api";

/** Extensions: optional parts of Probyte (backend/app/extensions/<id>), switched on by the admin in Settings →
 * Extensions. Each one's screen is loaded only when it's opened (#/x/<id>/…). */

export interface ExtensionInfo { id: string; name: string; description: string; icon: string; enabled: boolean }

/** What an extension's screen gets: where it is (the part of the address after #/x/<id>/) and a way to move. */
export interface ExtensionProps { path: string; go: (path: string) => void; onMenu: () => void; onError: (m: string) => void; onWorkspaceChanged: () => void }

export const SCREENS: Record<string, LazyExoticComponent<ComponentType<ExtensionProps>>> = {
  takeoff: lazy(() => import("./takeoff/Takeoff")),
};

let cached: Promise<ExtensionInfo[]> | null = null;
const listeners = new Set<(x: ExtensionInfo[]) => void>();

export function loadExtensions(refresh = false): Promise<ExtensionInfo[]> {
  if (!cached || refresh) {
    cached = api.get<ExtensionInfo[]>("/extensions").catch(() => []);
    cached.then((x) => listeners.forEach((f) => f(x)));
  }
  return cached;
}

export function useExtensions(): ExtensionInfo[] {
  const [list, setList] = useState<ExtensionInfo[]>([]);
  useEffect(() => {
    listeners.add(setList);
    loadExtensions().then(setList);
    return () => { listeners.delete(setList); };
  }, []);
  return list;
}
