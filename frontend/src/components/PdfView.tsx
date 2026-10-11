import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.js?url";
import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Doc = Awaited<ReturnType<typeof pdfjs.getDocument>["promise"]>;

/** Every page of a PDF, drawn by pdf.js: the same on every device (phones' browsers show only the first page of an
 * embedded PDF), pages drawn as they scroll into view, fit to the width with zoom. Large files load in byte ranges. */
export default function PdfView({ src, data }: { src?: string; data?: ArrayBuffer }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [sizes, setSizes] = useState<{ w: number; h: number }[]>([]);
  const [err, setErr] = useState("");
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(0);
  const [page, setPage] = useState(1);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    const task = pdfjs.getDocument(data ? { data: new Uint8Array(data.slice(0)) } : { url: src!, withCredentials: true, disableAutoFetch: true });
    task.promise.then(async (d) => {
      const s: { w: number; h: number }[] = [];
      for (let n = 1; n <= d.numPages; n++) {
        const vp = (await d.getPage(n)).getViewport({ scale: 1 });
        s.push({ w: vp.width, h: vp.height });
      }
      if (live) { setDoc(d); setSizes(s); }
    }).catch((e) => live && setErr(`This PDF couldn't be opened: ${(e as Error).message}`));
    return () => { live = false; task.destroy(); };
  }, [src, data]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [doc]);

  if (err) return <div className="viewer-msg error">{err}</div>;
  if (!doc) return <div className="viewer-msg"><Icon name="sync" size={18} className="spin" /> Loading…</div>;
  const maxW = Math.max(...sizes.map((s) => s.w));
  const scale = Math.max(0.2, (Math.min(width - 32, 1100) / maxW) * zoom);
  return (
    <div className="pdf-view">
      <div className="pdf-pages" ref={box} onScroll={(e) => {
        const el = e.currentTarget;
        const kids = Array.from(el.children) as HTMLElement[];
        const mid = el.scrollTop + el.clientHeight / 3;
        const k = kids.findIndex((c) => c.offsetTop + c.offsetHeight > mid);
        if (k >= 0) setPage(k + 1);
      }}>
        {width > 0 && sizes.map((s, n) => <Page key={n} doc={doc} n={n + 1} w={s.w * scale} h={s.h * scale} scale={scale} root={box} />)}
      </div>
      <div className="pdf-controls">
        <span>{page} / {doc.numPages}</span>
        <button onClick={() => setZoom((z) => Math.max(0.3, z / 1.25))} aria-label="Zoom out">−</button>
        <button onClick={() => setZoom(1)} title="Fit to width">{Math.round(zoom * 100)}%</button>
        <button onClick={() => setZoom((z) => Math.min(5, z * 1.25))} aria-label="Zoom in">+</button>
      </div>
    </div>
  );
}

function Page({ doc, n, w, h, scale, root }: { doc: Doc; n: number; w: number; h: number; scale: number; root: React.RefObject<HTMLDivElement> }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { root: root.current, rootMargin: "800px 0px" });
    if (ref.current) io.observe(ref.current);
    return () => io.disconnect();
  }, [root]);
  useEffect(() => {
    if (!visible || !ref.current) return;
    let task: { cancel: () => void } | null = null, live = true;
    const holder = ref.current;
    doc.getPage(n).then((p) => {
      if (!live) return;
      const ratio = window.devicePixelRatio || 1;
      const vp = p.getViewport({ scale: scale * ratio });
      const canvas = document.createElement("canvas");
      canvas.width = vp.width; canvas.height = vp.height;
      canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
      const t = p.render({ canvasContext: canvas.getContext("2d")!, viewport: vp });
      task = t;
      t.promise.then(() => { if (live) holder.replaceChildren(canvas); }).catch(() => undefined);
    });
    return () => { live = false; task?.cancel(); };
  }, [visible, scale, doc, n]);
  return <div ref={ref} className="pdf-page" data-page={n} style={{ width: w, height: h }} />;
}
