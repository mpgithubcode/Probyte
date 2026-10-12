import type { Attachment } from "./types";

/** Where attachments and files of files tables are fetched from: the app's own endpoints, or a public link's. */
let attachmentBase = "/api/attachments";
let fileBase = "/api/files";
export const setAttachmentBase = (base: string) => {
  attachmentBase = base;
  fileBase = base.replace(/attachments$/, "files");
};

export const isSvg = (a: Attachment) => a.mime === "image/svg+xml" || /\.svg$/i.test(a.name);
/** Shown as a picture (SVGs too: served so that nothing in them can run). */
export const isImage = (a: Attachment) => /^image\/(png|jpe?g|gif|webp|avif|bmp)$/.test(a.mime) || isSvg(a);
/** iPhone photos: shown through a JPEG the server makes (most browsers can't show HEIC). */
export const isHeic = (a: Attachment) => !!a.file && (/^image\/hei[cf]$/.test(a.mime) || /\.hei[cf]$/i.test(a.name));

export const attachmentUrl = (a: Attachment, download = false) => a.file
  ? `${fileBase}/${a.id}/content${download ? "?download=1" : ""}`
  : `${a.url ?? `${attachmentBase}/${a.id}`}${download ? "?download=1" : ""}`;  // linked tables carry their own proxy URL

/** The small preview image, if there is one (a file's size in the URL: a new version gets a new preview). */
export const thumbUrl = (a: Attachment) => isSvg(a) ? attachmentUrl(a)   // an SVG is its own preview, sharp at any size
  : a.file ? (a.thumb ? `${fileBase}/${a.id}/thumb?s=${a.size}` : null)
  : isImage(a) ? attachmentUrl(a) : null;

export const textUrl = (a: Attachment) => (a.file ? `${fileBase}/${a.id}/content?text=1` : attachmentUrl(a, true));
/** Word / PowerPoint / OpenDocument files as PDF (made by the server's document converter). */
export const previewPdfUrl = (a: Attachment) => `${fileBase}/${a.id}/preview?s=${a.size}`;
/** The picture the viewer shows: the file itself, or the JPEG made of an iPhone photo. */
export const imageUrl = (a: Attachment) => (isHeic(a) ? previewPdfUrl(a) : attachmentUrl(a));

export const fileExt = (name: string) => (name.includes(".") ? name.split(".").pop()!.toLowerCase().slice(0, 5) : "file");

export type ViewKind = "image" | "pdf" | "video" | "audio" | "markdown" | "text" | "sheet" | "document" | null;

const TEXT = new Set(["txt", "log", "csv", "tsv", "json", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "sql", "py", "js", "mjs",
  "ts", "tsx", "jsx", "css", "scss", "html", "htm", "sh", "bash", "ps1", "bat", "c", "h", "cpp", "hpp", "cs", "java", "kt", "go", "rs",
  "rb", "php", "swift", "vue", "svelte", "lua", "pl", "r", "dart", "svg", "rst", "srt", "vtt", "ics", "vcf", "gitignore", "dockerfile"]);
const SHEET = new Set(["xlsx", "xlsm", "xls", "ods", "numbers", "csv", "tsv"]);
const DOCUMENT = new Set(["doc", "docx", "docm", "dot", "dotx", "odt", "ott", "rtf", "wpd", "pptx", "ppt", "pps", "ppsx", "pptm", "odp", "otp",
  "odg", "vsd", "vsdx"]);
const VIDEO = new Set(["video/mp4", "video/webm", "video/ogg", "video/quicktime"]);

/** How the viewer shows a file. Spreadsheets as tables (CSV too), Word / PowerPoint as pages, Markdown as formatted text. */
export function viewKind(a: Attachment): ViewKind {
  const ext = fileExt(a.name), mime = a.mime || "";
  if (isImage(a) || isHeic(a)) return "image";
  if (mime === "application/pdf" || ext === "pdf") return "pdf";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (SHEET.has(ext)) return "sheet";
  if (DOCUMENT.has(ext)) return "document";
  if (!a.file && !a.url) return null;  // plain attachments: the rest isn't served inline
  if (VIDEO.has(mime)) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("text/") || TEXT.has(ext) || mime === "application/json") return "text";
  return null;
}

/** Can be edited in the viewer (files of files tables only). */
export const editableKind = (a: Attachment) => !!a.file && (viewKind(a) === "markdown" || viewKind(a) === "text" || isSvg(a))
  && !["csv", "tsv"].includes(fileExt(a.name));

/** Still used by thumbnails: something to open (else the click downloads). */
export const viewOf = (a: Attachment) => viewKind(a);

export async function fetchBytes(url: string): Promise<ArrayBuffer> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({ detail: r.statusText }))).detail ?? r.statusText);
  return r.arrayBuffer();
}
