import type { Dataset, Density, Field, Rec, View, ViewConfig } from "../types";

/** What the current viewer may do (everything in the app; limited on public links). */
export interface Can { add: boolean; edit: boolean; delete: boolean; manage: boolean }
export const CAN_ALL: Can = { add: true, edit: true, delete: true, manage: true };

export interface Ops {
  can: Can;
  /** Create a record. For combined databases the user is asked which source table to use. */
  create(data?: Record<string, any>, openAfter?: boolean): void;
  update(rec: Rec, patch: Record<string, any>): void;
  remove(rec: Rec): void;
  open(rec: Rec): void;
  /** Update or delete many records at once (not offered on public links). */
  bulk?(action: "update" | "delete", recs: Rec[], patch?: Record<string, any>): void;
  /** Copies of records (their own values; calculated ones work themselves out). Not in files tables. */
  duplicate?(recs: Rec[]): void;
  /** Mark one date of a repeating record done (its current date moves it on; a later one is kept done and skipped). */
  completeOccurrence?(rec: Rec, occurrence: string): void;
  paste?(items: { record_id: string | null; values: Record<string, string> }[]): void;  // grid: a block pasted from a spreadsheet
  /** Open the field editor; null = new field. */
  editField(field: Field | null): void;
  /** Run a button field's action on a record. */
  runButton(rec: Rec, field: Field): void;
  /** Accent color of a record (source color in combined databases, or a select field's choice color). */
  colorFor(rec: Rec, colorField?: string | null): string | undefined;
  /** What adding is called ("Upload" in files tables, where adding uploads files). */
  addLabel?: string;
}

export interface ViewProps {
  ds: Dataset;
  view: View;
  fields: Field[];   // all fields
  visible: Field[];  // fields not hidden in this view
  records: Rec[];    // filtered + sorted (the page shown, when the view is paged)
  allRecords?: Rec[];  // every record the view shows, on all pages (for totals)
  ops: Ops;
  setConfig: (patch: Partial<ViewConfig>) => void;
}

/** Row heights (px) for each density: keep in sync with the .density-* rules in styles.css. */
export const GRID_ROW: Record<Density, number> = { compact: 30, comfortable: 36, roomy: 48 };
export const LIST_ROW: Record<Density, number> = { compact: 34, comfortable: 42, roomy: 54 };
/** How many fields a card shows. */
export const CARD_FIELDS: Record<Density, number> = { compact: 3, comfortable: 5, roomy: 8 };
export const densityOf = (c: ViewConfig): Density => c.density ?? "comfortable";
