import type { Rec } from "../types";
import { primaryField, recordTitle } from "../util";
import { CellValue } from "./Cells";
import type { ViewProps } from "./viewTypes";

const empty = (v: unknown) => v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);

/** A table view on a phone: each record a card, its title wrapping and every field the view shows below it (as the
 * table shows them), so nothing needs scrolling sideways. A tap opens the record. (Table | Cards above switches.) */
export function PhoneCards({ visible, fields, records, ops }: ViewProps) {
  const p = primaryField(fields);
  const shown = visible.filter((f) => f.id !== p?.id && f.type !== "button" && f.type !== "record_id");
  return (
    <div className="phone-cards">
      {records.map((r: Rec) => (
        <div key={r.id} role="button" tabIndex={0} className="phone-card" onClick={() => ops.open(r)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ops.open(r); } }}>
          <span className="phone-card-title">{recordTitle(fields, r)}</span>
          {shown.map((f) => empty(r.data[f.id]) ? null : (
            <span key={f.id} className="phone-card-field">
              <span className="phone-card-label">{f.name}</span>
              <span className="phone-card-value"><CellValue field={f} value={r.data[f.id]} /></span>
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
