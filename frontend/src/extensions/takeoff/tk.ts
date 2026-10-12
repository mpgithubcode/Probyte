import { api } from "../../api";

/** The takeoff extension's API (backend/app/extensions/takeoff/api.py). Coordinates are PDF points. */

export type Pt = [number, number];
export type Rect = [number, number, number, number];

export interface Scale { ft_per_pt: number; label: string; source?: string; rect?: Rect }
export interface Sheet {
  page_no: number; sheet_no: string | null; title: string | null; discipline: string | null;
  included: boolean; confirmed: boolean; width: number; height: number;
  scale: Scale | null; scales: Scale[]; excluded: Rect[]; has_text: boolean;
}
export type Kind = "count" | "length" | "area";
export interface Item {
  id: string; name: string; category: string | null; kind: Kind; unit: string; color: string; tag: string | null;
  symbol: { page: number; rect: Rect; legend?: Rect } | null; details: Record<string, string>;
  status: "suggested" | "confirmed"; waste_pct: number; per_run: number; unit_cost: number | null; notes: string | null; position: number;
}
export interface Mark {
  id: string; item_id: string; page_no: number; geom: { type: "point" | "line" | "area"; points: Pt[] };
  value: number; status: "suggested" | "confirmed" | "rejected"; source: string; score: number | null;
}
export interface SummaryRow {
  item_id: string; name: string; category: string | null; kind: Kind; unit: string; tag: string | null; color: string;
  qty: number; waste_pct: number; total: number; unit_cost: number | null; extended: number | null;
  pages: { page_no: number; sheet_no: string | null; qty: number; marks: number }[]; pending: number; unscaled: string[];
  details: Record<string, string>; notes: string | null;
}
export interface Project {
  id: string; name: string; file_name: string; page_count: number; settings: Record<string, unknown>;
  source: Record<string, string>; pages: Sheet[]; items: Item[]; summary: SummaryRow[];
}
export interface ProjectSummary {
  id: string; name: string; file_name: string; page_count: number; items: number; included: number; updated_at: string;
  source?: { kind: string; record_id?: string; table_id?: string };
}
export interface LegendEntry { name: string; tag: string | null; symbol: Rect | null; details: Record<string, string>; category: string | null; rect: Rect }
export interface Legend { heading: string | null; rect: Rect; columns: string[]; rows: LegendEntry[]; cells: string[][] }
export interface SheetTable { rect: Rect; heading: string | null; cells: string[][] }

const X = "/x/takeoff";
export const tk = {
  projects: (recordId?: string) => api.get<ProjectSummary[]>(`${X}/projects${recordId ? `?record_id=${recordId}` : ""}`),
  upload: (file: File, name: string) => { const b = new FormData(); b.append("file", file); b.append("name", name); return api.upload<Project>(`${X}/projects`, b); },
  fromFile: (record_id: string, name: string) => api.post<Project>(`${X}/projects/from-file`, { record_id, name }),
  project: (id: string) => api.get<Project>(`${X}/projects/${id}`),
  rename: (id: string, name: string) => api.patch(`${X}/projects/${id}`, { name }),
  remove: (id: string) => api.del(`${X}/projects/${id}`),
  crop: (id: string, page: number, rect: Rect, h: number) => `/api${X}/projects/${id}/pages/${page}/crop?rect=${rect.map((v) => v.toFixed(1)).join(",")}&h=${h}`,
  image: (id: string, page: number, dpi: number) => `/api${X}/projects/${id}/pages/${page}/image?dpi=${dpi}`,
  page: (id: string, page: number, patch: Partial<Sheet> | { scale: Partial<Scale> | Record<string, never> }) =>
    api.patch<Sheet>(`${X}/projects/${id}/pages/${page}`, patch),
  pages: (id: string, pages: number[], patch: { included?: boolean; confirmed?: boolean }) => api.patch(`${X}/projects/${id}/pages`, { pages, ...patch }),
  calibrate: (id: string, page: number, points: Pt[], feet: number) => api.post<Sheet>(`${X}/projects/${id}/pages/${page}/calibrate`, { points, feet }),
  legends: (id: string, page: number) => api.get<Legend[]>(`${X}/projects/${id}/pages/${page}/legends`),
  legendIn: (id: string, page: number, rect: Rect) => api.post<LegendEntry[]>(`${X}/projects/${id}/pages/${page}/legend-in`, { rect }),
  tables: (id: string, page: number) => api.get<SheetTable[]>(`${X}/projects/${id}/pages/${page}/tables`),
  words: (id: string, page: number) => api.get<[number, number, number, number, string][]>(`${X}/projects/${id}/pages/${page}/words`),
  search: (id: string, q: string, all: boolean) => api.get<{ page_no: number; rects: Rect[] }[]>(`${X}/projects/${id}/search?q=${encodeURIComponent(q)}&pages=${all ? "all" : "included"}`),
  addItems: (id: string, items: Partial<Item>[]) => api.post<Item[]>(`${X}/projects/${id}/items`, { items }),
  item: (iid: string, patch: Partial<Item>) => api.patch<Item>(`${X}/items/${iid}`, patch),
  boxSymbol: (iid: string, page: number, rect: Rect) => api.post<Item>(`${X}/items/${iid}/symbol`, { page, rect }),
  removeItem: (iid: string) => api.del(`${X}/items/${iid}`),
  symbolUrl: (iid: string, v = 0) => `/api${X}/items/${iid}/symbol.png?v=${v}`,
  find: (iid: string, body: { pages?: number[]; method?: string; threshold?: number; rotate?: boolean }) =>
    api.post<{ method: string; found: number; pages: { page_no: number; sheet_no: string | null; found: number }[] }>(`${X}/items/${iid}/find`, body),
  marks: (id: string, page?: number) => api.get<Mark[]>(`${X}/projects/${id}/marks${page !== undefined ? `?page=${page}` : ""}`),
  addMark: (id: string, m: { item_id: string; page_no: number; geom: Mark["geom"]; source?: string }) => api.post<Mark>(`${X}/projects/${id}/marks`, m),
  mark: (mid: string, patch: Partial<Pick<Mark, "status" | "geom" | "item_id">>) => api.patch<Mark>(`${X}/marks/${mid}`, patch),
  removeMark: (mid: string) => api.del(`${X}/marks/${mid}`),
  review: (id: string, body: { ids?: string[]; item_id?: string; page_no?: number; min_score?: number; status: "confirmed" | "rejected" }) =>
    api.post<{ changed: number }>(`${X}/projects/${id}/marks/review`, body),
  summary: (id: string) => api.get<SummaryRow[]>(`${X}/projects/${id}/summary`),
  csvUrl: (id: string) => `/api${X}/projects/${id}/export.csv`,
  pdfUrl: (id: string) => `/api${X}/projects/${id}/export.pdf`,
  toTable: (id: string, base_id: string, name: string) => api.post<{ table_id: string; rows: number }>(`${X}/projects/${id}/export/table`, { base_id, name }),
  scheduleToTable: (id: string, base_id: string, name: string, cells: string[][]) =>
    api.post<{ table_id: string; rows: number }>(`${X}/projects/${id}/export/schedule`, { base_id, name, cells }),
};

/** Feet as a builder writes them: 12'-6" */
export function feet(ft: number): string {
  if (!isFinite(ft)) return "—";
  const neg = ft < 0;
  let whole = Math.floor(Math.abs(ft));
  let inches = Math.round((Math.abs(ft) - whole) * 12);
  if (inches === 12) { whole += 1; inches = 0; }
  return `${neg ? "-" : ""}${whole}'-${inches}"`;
}

/** 12'-6", 12' 6", 12.5, 12'  → feet */
export function parseFeet(s: string): number | null {
  const t = s.trim().replace(/[″”]/g, '"').replace(/[′’]/g, "'");
  const m = t.match(/^(\d+(?:\.\d+)?)\s*'\s*-?\s*(\d+(?:\.\d+)?)?\s*(?:"|in)?$/) || t.match(/^(\d+(?:\.\d+)?)\s*(?:ft|feet)?$/i);
  if (!m) return null;
  return parseFloat(m[1]) + (m[2] ? parseFloat(m[2]) / 12 : 0);
}

export const qty = (n: number, unit: string) => `${Number.isInteger(n) ? n : n.toFixed(unit === "EA" ? 0 : 1)} ${unit}`;

export const sheetName = (s: Pick<Sheet, "sheet_no" | "page_no">) => s.sheet_no || `Page ${s.page_no + 1}`;

export function polyLength(pts: Pt[]) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return d;
}

export function polyArea(pts: Pt[]) {
  let a = 0;
  for (let i = 0; i < pts.length; i++) { const j = (i + 1) % pts.length; a += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]; }
  return Math.abs(a) / 2;
}
