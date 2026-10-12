"""What a file is (kind, text or not, how a browser shows it), from its type and name. Pure functions."""

SHEET_EXT = {"xlsx", "xlsm", "xls", "ods"}   # shown as tables in the browser; converted only for their cover image
HEIC_MIME = {"image/heic", "image/heif"}       # iPhone photos: most browsers can't show them, so a JPEG is made
CONVERT_EXT = {"doc", "docx", "docm", "dot", "dotx", "odt", "ott", "rtf", "wpd", "pptx", "ppt", "pps", "ppsx", "pptm", "odp", "otp", "odg", "vsd", "vsdx"}
KINDS = ["Image", "Video", "Audio", "PDF", "Document", "Spreadsheet", "Presentation", "Text", "Code", "Archive", "Other"]
_DOC = {"doc", "docx", "odt", "rtf", "pages", "wpd"}
_SHEET = {"xls", "xlsx", "ods", "csv", "tsv", "numbers"}
_SLIDES = {"ppt", "pptx", "odp", "key"}
_ARCHIVE = {"zip", "tar", "gz", "tgz", "7z", "rar", "bz2", "xz", "zst"}
_CODE = {"py", "js", "mjs", "ts", "tsx", "jsx", "css", "scss", "html", "htm", "sh", "bash", "ps1", "bat", "c", "h", "cpp", "hpp",
         "cs", "java", "kt", "go", "rs", "rb", "php", "swift", "sql", "json", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf",
         "vue", "svelte", "lua", "pl", "r", "dart", "svg", "dockerfile", "makefile", "env", "gitignore"}
_TEXT = {"txt", "md", "markdown", "log", "csv", "tsv", "rst", "srt", "vtt", "ics", "vcf"} | _CODE
_RASTER = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[-1] if "." in base else base


def kind_of(mime: str, name: str) -> str:
    e = _ext(name)
    if mime.startswith("image/"):
        return "Image"
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Audio"
    if mime == "application/pdf" or e == "pdf":
        return "PDF"
    for k, exts in (("Document", _DOC), ("Spreadsheet", _SHEET), ("Presentation", _SLIDES), ("Archive", _ARCHIVE), ("Code", _CODE)):
        if e in exts:
            return k
    if mime.startswith("text/") or e in _TEXT:
        return "Text"
    return "Other"


def is_text(mime: str, name: str) -> bool:
    return mime.startswith("text/") or _ext(name) in _TEXT or mime in ("application/json", "application/xml", "application/javascript")


def convertible(name: str) -> bool:
    """Can be turned into PDF by the document converter (Word, PowerPoint, OpenDocument…; spreadsheets for covers)."""
    return _ext(name) in CONVERT_EXT or _ext(name) in SHEET_EXT


def _heic(mime: str, name: str) -> bool:
    return mime in HEIC_MIME or _ext(name) in ("heic", "heif")


def previewable(mime: str, name: str) -> bool:
    """A cover image (and / or searchable text) can be made for it."""
    return (mime in _RASTER or mime in ("image/tiff", "application/pdf") or _heic(mime, name) or is_text(mime, name)
            or convertible(name) or _ext(name) in ("odt", "ods", "odp", "docx", "xlsx", "pptx"))


# SVG pictures are served with a policy that blocks any script inside them (a sandbox), so they can be shown as images
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox"


def is_svg(mime: str, name: str) -> bool:
    return mime == "image/svg+xml" or _ext(name) == "svg"


def view_of(mime: str, name: str) -> str | None:
    """How the browser shows the file: image, pdf, video, audio, text (its source, never rendered) or not at all."""
    if mime in _RASTER or is_svg(mime, name):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("video/") and mime in ("video/mp4", "video/webm", "video/ogg", "video/quicktime"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if is_text(mime, name):
        return "text"
    return None


def _keys(fields) -> dict:
    return {f["system_key"]: str(f["id"]) for f in fields if f.get("system_key")}
