import type { Dataset, Field, Rec, View, ViewConfig } from "../types";

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
  paste?(items: { record_id: string | null; values: Record<string, string> }[]): void;  // grid: a block pasted from a spreadsheet
  /** Open the field editor; null = new field. */
  editField(field: Field | null): void;
  /** Run a button field's action on a record. */
  runButton(rec: Rec, field: Field): void;
  /** Accent color of a record (source color in combined databases, or a select field's choice color). */
  colorFor(rec: Rec, colorField?: string | null): string | undefined;
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
