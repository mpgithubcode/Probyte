import Link from "@tiptap/extension-link";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef, type ReactNode } from "react";
import { Markdown } from "tiptap-markdown";

/** A Markdown file as formatted text; editable (WYSIWYG) with a toolbar. Saves back to Markdown. Raw HTML in the file is
 * shown as text, never run. Loaded only when a Markdown file is opened. */
export default function RichText({ markdown, editable, onChange }: {
  markdown: string; editable: boolean; onChange?: (md: string) => void;
}) {
  // only changes made while editing count (the editor also reports one while it loads the file)
  const changed = useRef(onChange);
  changed.current = onChange;
  const editor = useEditor({
    extensions: [
      StarterKit,
      Link.configure({ openOnClick: !editable, autolink: true, protocols: ["http", "https", "mailto"],
        HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" } }),
      Markdown.configure({ html: false, linkify: true, breaks: false, transformPastedText: true, tightLists: true }),
    ],
    content: markdown,
    editable,
    onUpdate: ({ editor: e, transaction }) => {
      if (e.isEditable && transaction.docChanged && transaction.getMeta("preventUpdate") !== true) changed.current?.(markdownOf(e));
    },
  }, [markdown]);
  useEffect(() => { editor?.setEditable(editable); if (editable) editor?.commands.focus("start"); }, [editor, editable]);
  if (!editor) return null;
  return (
    <div className={`rich-text ${editable ? "editing" : ""}`}>
      {editable && <Toolbar editor={editor} />}
      <EditorContent editor={editor} className="rich-text-body" />
    </div>
  );
}

const markdownOf = (e: Editor): string => (e.storage as { markdown: { getMarkdown: () => string } }).markdown.getMarkdown();

export function Toolbar({ editor, extra }: { editor: Editor; extra?: ReactNode }) {
  const b = (label: string, title: string, active: boolean, run: () => void) => (
    <button type="button" title={title} className={`rt-btn ${active ? "on" : ""}`} onMouseDown={(e) => { e.preventDefault(); run(); }}>{label}</button>
  );
  const c = () => editor.chain().focus();
  const link = () => {
    const prev = editor.getAttributes("link").href as string | undefined;
    const url = window.prompt("Link address", prev ?? "https://");
    if (url === null) return;
    if (!url) c().unsetLink().run();
    else if (/^(https?:|mailto:)/i.test(url)) c().extendMarkRange("link").setLink({ href: url }).run();
  };
  return (
    <div className="rt-toolbar">
      {b("¶", "Paragraph", editor.isActive("paragraph"), () => c().setParagraph().run())}
      {b("H1", "Heading 1", editor.isActive("heading", { level: 1 }), () => c().toggleHeading({ level: 1 }).run())}
      {b("H2", "Heading 2", editor.isActive("heading", { level: 2 }), () => c().toggleHeading({ level: 2 }).run())}
      {b("H3", "Heading 3", editor.isActive("heading", { level: 3 }), () => c().toggleHeading({ level: 3 }).run())}
      <span className="rt-sep" />
      {b("B", "Bold (Ctrl/⌘ B)", editor.isActive("bold"), () => c().toggleBold().run())}
      {b("I", "Italic (Ctrl/⌘ I)", editor.isActive("italic"), () => c().toggleItalic().run())}
      {b("S", "Strikethrough", editor.isActive("strike"), () => c().toggleStrike().run())}
      {b("</>", "Code", editor.isActive("code"), () => c().toggleCode().run())}
      {b("🔗", "Link", editor.isActive("link"), link)}
      <span className="rt-sep" />
      {b("•", "Bulleted list", editor.isActive("bulletList"), () => c().toggleBulletList().run())}
      {b("1.", "Numbered list", editor.isActive("orderedList"), () => c().toggleOrderedList().run())}
      {b("❝", "Quote", editor.isActive("blockquote"), () => c().toggleBlockquote().run())}
      {b("{ }", "Code block", editor.isActive("codeBlock"), () => c().toggleCodeBlock().run())}
      {b("—", "Divider", false, () => c().setHorizontalRule().run())}
      <span className="rt-sep" />
      {b("↶", "Undo (Ctrl/⌘ Z)", false, () => c().undo().run())}
      {b("↷", "Redo", false, () => c().redo().run())}
      {extra}
    </div>
  );
}
