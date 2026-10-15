import Link from "@tiptap/extension-link";
import { EditorContent, Node, NodeViewWrapper, ReactNodeViewRenderer, useEditor, type Editor, type NodeViewProps } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { Markdown } from "tiptap-markdown";
import type { Field } from "../types";
import { FieldIcon, Icon } from "./Icon";
import { Popover, usePopover } from "./Overlay";
import { Toolbar } from "./RichText";

/** A long text field as a page's body: formatted text (saved as Markdown) with the record's fields placed in it, each on
 * a line of its own ({{field:<id>}} in the text), shown and edited where they stand. Loaded only when a page has one. */

interface Embeds { render: (fieldId: string) => ReactNode; editable: boolean }
const EmbedContext = createContext<Embeds>({ render: () => null, editable: false });

export const EMBED_LINE = /^\{\{field:([\w-]+)\}\}$/;

function EmbedView({ node, deleteNode, selected }: NodeViewProps) {
  const { render, editable } = useContext(EmbedContext);
  return (
    <NodeViewWrapper className={`page-embed ${selected ? "selected" : ""}`} contentEditable={false}>
      {editable && <span className="page-embed-grip" data-drag-handle title="Drag to move"><Icon name="menu" size={14} /></span>}
      <div className="page-embed-body">{render(String(node.attrs.field))}</div>
      {editable && <button type="button" className="icon-btn sm page-embed-x" title="Take it out of the text (the field keeps its value)"
        aria-label="Remove from the page" onClick={deleteNode}><Icon name="x" size={13} /></button>}
    </NodeViewWrapper>
  );
}

const FieldEmbed = Node.create({
  name: "fieldEmbed",
  group: "block",
  atom: true,
  draggable: true,
  selectable: true,
  addAttributes() {
    return { field: { default: null, parseHTML: (el) => el.getAttribute("data-field-embed"), renderHTML: (a) => ({ "data-field-embed": a.field }) } };
  },
  parseHTML() { return [{ tag: "div[data-field-embed]" }]; },
  renderHTML({ HTMLAttributes }) { return ["div", HTMLAttributes]; },
  addNodeView() { return ReactNodeViewRenderer(EmbedView, { stopEvent: () => true }); },
  addStorage() {
    return {
      markdown: {
        serialize(state: { write: (s: string) => void; closeBlock: (n: unknown) => void }, node: { attrs: { field: string } }) {
          state.write(`{{field:${node.attrs.field}}}`);
          state.closeBlock(node);
        },
        parse: {
          // a paragraph that's only {{field:<id>}}: the field, placed there
          updateDOM(element: HTMLElement) {
            element.querySelectorAll("p").forEach((p) => {
              const m = EMBED_LINE.exec((p.textContent ?? "").trim());
              if (m && p.children.length === 0) {
                const d = document.createElement("div");
                d.setAttribute("data-field-embed", m[1]);
                p.replaceWith(d);
              }
            });
          },
        },
      },
    };
  },
});

const markdownOf = (e: Editor): string => (e.storage as { markdown: { getMarkdown: () => string } }).markdown.getMarkdown();

export default function PageDoc({ value, editable, onChange, fields, renderField }: {
  value: string; editable: boolean; onChange: (md: string) => void;
  fields: Field[];                                // what can be placed in the text
  renderField: (fieldId: string) => ReactNode;    // a placed field, as the page shows it
}) {
  const sent = useRef(value);          // the text as last saved from here
  const timer = useRef<number>();
  const changed = useRef(onChange);
  changed.current = onChange;
  const flush = (e: Editor) => {
    window.clearTimeout(timer.current);
    const md = markdownOf(e);
    if (md !== sent.current) { sent.current = md; changed.current(md); }
  };
  const editor = useEditor({
    extensions: [
      StarterKit,
      Link.configure({ openOnClick: !editable, autolink: true, protocols: ["http", "https", "mailto"],
        HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" } }),
      Markdown.configure({ html: false, linkify: true, breaks: true, transformPastedText: true, tightLists: true }),
      FieldEmbed,
    ],
    content: value,
    editable,
    onUpdate: ({ editor: e, transaction }) => {
      if (!e.isEditable || !transaction.docChanged) return;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => flush(e), 700);   // saved a moment after typing stops (and on leaving)
    },
    onBlur: ({ editor: e }) => flush(e),
  });
  useEffect(() => editor?.setEditable(editable), [editor, editable]);
  // changed elsewhere (another window, the record panel): shown here, unless it's being typed in
  useEffect(() => {
    if (!editor || value === sent.current || editor.isFocused) return;
    sent.current = value;
    editor.commands.setContent(value, false);
  }, [editor, value]);
  useEffect(() => () => { if (editor && !editor.isDestroyed) flush(editor); }, [editor]);
  const add = usePopover();
  if (!editor) return null;
  const insert = (id: string) => {
    add.close();
    editor.chain().focus().insertContent({ type: "fieldEmbed", attrs: { field: id } }).run();
  };
  return (
    <EmbedContext.Provider value={{ render: renderField, editable }}>
      <div className={`page-doc rich-text ${editable ? "editing" : ""}`}>
        {editable && (
          <Toolbar editor={editor} extra={<>
            <span className="rt-sep" />
            <button type="button" className="rt-btn wide" title="Place a field in the text" onMouseDown={(e) => { e.preventDefault(); add.open(e); }}>
              <Icon name="plus" size={13} /> Field</button>
          </>} />
        )}
        <EditorContent editor={editor} className="rich-text-body page-doc-body" />
        {add.anchor && (
          <Popover anchor={add.anchor} onClose={add.close} width={260}>
            <div className="menu-list">
              {fields.length ? fields.map((f) => (
                <button key={f.id} className="menu-item" onClick={() => insert(f.id)}><FieldIcon type={f.type} /> {f.name}</button>
              )) : <div className="muted pad">No fields to place</div>}
            </div>
          </Popover>
        )}
      </div>
    </EmbedContext.Provider>
  );
}
