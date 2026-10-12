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

/** Things an extension can do with a file of a files table (shown in the file viewer when the extension is on). */
export interface FileAction {
  ext: string; label: string; icon: string;
  accepts: (file: { name: string; mime?: string | null }) => boolean;
  run: (recordId: string, name: string) => Promise<string>;   // the address (#…) to go to
}

export const FILE_ACTIONS: FileAction[] = [
  {
    ext: "takeoff", label: "Open in Blueprint takeoff", icon: "ruler",
    accepts: (f) => /\.pdf$/i.test(f.name) || f.mime === "application/pdf",
    run: async (recordId, name) => {
      const { tk } = await import("./takeoff/tk");
      const existing = await tk.projects(recordId);
      const id = existing[0]?.id ?? (await tk.fromFile(recordId, name.replace(/\.pdf$/i, ""))).id;
      return `/x/takeoff/${id}`;
    },
  },
];

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
