const PATHS: Record<string, string> = {
  density: "M4 5h16M4 10h16M4 15h16M4 20h16",
  down: "M6 9l6 6 6-6",
  ruler: "M3 17L17 3l4 4L7 21zM7 13l2 2M10 10l2 2M13 7l2 2",
  hand: "M8 13V5a1.5 1.5 0 0 1 3 0v6M11 11V4a1.5 1.5 0 0 1 3 0v7M14 11V5.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-.5a6 6 0 0 1-5-2.7L3 15.5a1.6 1.6 0 0 1 2.6-1.8L8 16",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01",
  polyline: "M4 18l5-9 6 5 5-9M4 18h.01M9 9h.01M15 14h.01M20 5h.01",
  area: "M4 7l8-4 8 5-2 11-11 2zM4 7h.01M12 3h.01M20 8h.01M18 19h.01M7 21h.01",
  crosshair: "M12 2v6M12 16v6M2 12h6M16 12h6M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  check: "M5 12l5 5 9-10",
  ban: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM5.6 5.6l12.8 12.8",
  boxselect: "M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4M9 9h6v6H9z",
  "zoom-in": "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5M11 8v6M8 11h6",
  "zoom-out": "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5M8 11h6",
  fit: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
  table: "M3 5h18v14H3zM3 10h18M3 15h18M9 5v14",
  tasks: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
  calendar: "M4 6h16v15H4zM4 10h16M8 3v4M16 3v4",
  "calendar-check": "M4 6h16v15H4zM4 10h16M8 3v4M16 3v4M9 15.5l2 2 4-4",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  contacts: "M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM8 17a4 4 0 0 1 8 0M2 8h2M2 16h2",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  phone: "M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM11 18h2",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  sparkle: "M12 3l1.8 4.9L19 9.7l-5.2 1.8L12 16.4l-1.8-4.9L5 9.7l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
  code: "M8 6l-6 6 6 6M16 6l6 6-6 6M14 4l-4 16",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  menu: "M4 6h16M4 12h16M4 18h16",
  share: "M4 12v8h16v-8M12 3v13M7 8l5-5 5 5",
  key: "M15 7a4 4 0 1 1-3.9 4.9L3 20v-3h2v-2h2v-2h2l1.1-1.1A4 4 0 0 1 15 7zM16 9h.01",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  folder: "M3 6h6l2 2h10v11H3z",
  file: "M14 3H6v18h12V7zM14 3v4h4",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  plus: "M12 5v14M5 12h14",
  sync: "M20 11a8 8 0 0 0-14.9-4M4 4v4h4M4 13a8 8 0 0 0 14.9 4M20 20v-4h-4",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  grid: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18M15 3v18",
  kanban: "M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v13h-4z",
  gallery: "M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z",
  timeline: "M3 6h9M7 12h11M5 18h7M3 3v18",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15",
  expand: "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5",
  filter: "M3 5h18l-7 8v6l-4 2v-8z",
  sort: "M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  left: "M15 18l-6-6 6-6",
  repeat: "M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3",
  subtasks: "M4 5h4M4 5v12a2 2 0 0 0 2 2h4M4 12h6M14 5h7M14 12h7M14 19h7",
  right: "M9 18l6-6-6-6",
  up: "M18 15l-6-6-6 6",
  x: "M6 6l12 12M18 6L6 18",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  group: "M4 6h16M4 12h10M4 18h6",
  palette: "M12 3a9 9 0 1 0 0 18c1 0 1.5-.8 1.5-1.5 0-1-.8-1.2-.8-2.2 0-.8.7-1.3 1.5-1.3H17a4 4 0 0 0 4-4c0-5-4-9-9-9z",
};

export function Icon({ name, size = 16, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={PATHS[name] ?? PATHS.table} />
    </svg>
  );
}

const FIELD_ICONS: Record<string, string> = {
  text: "A", long_text: "¶", recurrence: "↻", record_id: "⌗", number: "#", checkbox: "☑", single_select: "◉", multi_select: "☰",
  date: "◷", url: "⌁", email: "@", phone: "☏", rating: "★", user: "☺", attachment: "⎘", link: "⇄",
  formula: "ƒ", lookup: "⤷", rollup: "Σ", conditional_lookup: "⤵", conditional_rollup: "∑", button: "▣",
  autonumber: "№", created_time: "◴", last_modified_time: "◵", created_by: "☻", last_modified_by: "☻",
};

export function FieldIcon({ type }: { type: string }) {
  return <span className="field-icon">{FIELD_ICONS[type] ?? "A"}</span>;
}

export function tableIcon(kind: string) {
  return kind === "tasks" ? "tasks" : kind === "events" ? "calendar" : kind === "mixed" ? "calendar-check" : kind === "contacts" ? "contacts" : kind === "remote" ? "link" : kind === "files" ? "folder" : "table";
}
