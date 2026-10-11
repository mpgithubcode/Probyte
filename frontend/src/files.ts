import type { Attachment } from "./types";

/** Where attachments and files of files tables are fetched from: the app's own endpoints, or a public link's. */
let attachmentBase = "/api/attachments";
let fileBase = "/api/files";
export const setAttachmentBase = (base: string) => {
  attachmentBase = base;
  fileBase = base.replace(/attachments$/, "files");
};

export const isImage = (a: Attachment) => /^image\/(png|jpe?g|gif|webp|avif|bmp)$/.test(a.mime);

export const attachmentUrl = (a: Attachment, download = false) => a.file
  ? `${fileBase}/${a.id}/content${download ? "?download=1" : ""}`
  : `${a.url ?? `${attachmentBase}/${a.id}`}${download ? "?download=1" : ""}`;  // linked tables carry their own proxy URL

/** The small preview image, if there is one (a file's size in the URL: a new version gets a new preview). */
export const thumbUrl = (a: Attachment) => a.file
  ? (a.thumb ? `${fileBase}/${a.id}/thumb?s=${a.size}` : null)
  : isImage(a) ? attachmentUrl(a) : null;

/** How the browser can show it. Plain attachments: only images (everything else downloads). */
export const viewOf = (a: Attachment) => (a.file ? a.view ?? null : isImage(a) ? "image" : null);

export const textUrl = (a: Attachment) => `${fileBase}/${a.id}/content?text=1`;

export const fileExt = (name: string) => (name.includes(".") ? name.split(".").pop()!.slice(0, 4) : "file");
