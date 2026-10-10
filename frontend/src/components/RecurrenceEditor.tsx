import { useState } from "react";
import {
  composeValue, describe, fromSimple, ORDINALS, parseValue, toSimple, WD_LONG, WD_SHORT, type Freq, type Simple,
} from "../recur";
import { parseKey } from "../util";

const UNITS: Record<Freq, string> = { DAILY: "day", WEEKLY: "week", MONTHLY: "month", YEARLY: "year" };

/** Friendly editor for a repeat rule. Every change is applied right away. */
export function RecurrenceEditor({ value, anchor, onChange, onDone }: {
  value: unknown; anchor?: string; onChange: (v: string | null) => void; onDone?: () => void;
}) {
  const { exdates, overrides } = parseValue(value);
  const skippedOv = overrides.filter((o) => o.cancelled), changed = overrides.filter((o) => !o.cancelled);
  const [custom, setCustom] = useState(() => toSimple(value, anchor) === null);
  const [s, setS] = useState<Simple>(() => toSimple(value, anchor) ?? toSimple(null, anchor)!);

  const apply = (patch: Partial<Simple>) => {
    const next = { ...s, ...patch };
    if (next.freq === "WEEKLY" && !next.days.length) next.days = toSimple(null, anchor)!.days;
    setS(next);
    setCustom(false);
    onChange(composeValue(fromSimple(next), next.freq === "NONE" ? [] : exdates, overrides));
  };
  const anchorDay = anchor ? parseKey(anchor.slice(0, 10)).getDate() : null;

  if (custom) {
    return (
      <div className="repeat-editor">
        <p className="small"><b>{describe(value)}</b></p>
        <p className="muted small">This repeat rule was set in another app and has options this editor doesn't show. It's kept as is unless you change it.</p>
        <code className="repeat-raw">{parseValue(value).rule}</code>
        <div className="row">
          <button className="btn sm" onClick={() => apply({ freq: "WEEKLY" })}>Replace</button>
          <button className="btn sm danger-ghost" onClick={() => { onChange(null); onDone?.(); }}>Stop repeating</button>
          <span className="spacer" />
          {onDone && <button className="btn sm primary" onClick={onDone}>Done</button>}
        </div>
      </div>
    );
  }

  return (
    <div className="repeat-editor">
      <label className="repeat-row">
        <span>Repeat</span>
        <select className="input sm" value={s.freq} onChange={(e) => apply({ freq: e.target.value as Simple["freq"] })}>
          <option value="NONE">Never</option>
          <option value="DAILY">Daily</option>
          <option value="WEEKLY">Weekly</option>
          <option value="MONTHLY">Monthly</option>
          <option value="YEARLY">Yearly</option>
        </select>
      </label>
      {s.freq !== "NONE" && (
        <>
          <label className="repeat-row">
            <span>Every</span>
            <span className="repeat-inline">
              <input className="input sm" type="number" min={1} max={999} value={s.interval}
                onChange={(e) => apply({ interval: Math.max(1, parseInt(e.target.value) || 1) })} />
              {UNITS[s.freq]}{s.interval > 1 ? "s" : ""}
            </span>
          </label>
          {s.freq === "WEEKLY" && (
            <div className="repeat-days" role="group" aria-label="Days of the week">
              {WD_SHORT.map((d, i) => (
                <button key={d} type="button" title={WD_LONG[i]} className={s.days.includes(i) ? "on" : ""}
                  onClick={() => apply({ days: s.days.includes(i) ? s.days.filter((x) => x !== i) : [...s.days, i] })}>
                  {d[0]}
                </button>
              ))}
            </div>
          )}
          {s.freq === "MONTHLY" && (
            <div className="repeat-month">
              <label className="chip-check">
                <input type="radio" checked={s.monthMode === "date"} onChange={() => apply({ monthMode: "date" })} />
                {anchorDay ? `On day ${anchorDay}` : "On the same date"}
              </label>
              <label className="chip-check">
                <input type="radio" checked={s.monthMode === "nth"} onChange={() => apply({ monthMode: "nth" })} />
                On the
                <select className="input sm" value={s.nth} onChange={(e) => apply({ monthMode: "nth", nth: +e.target.value })}>
                  {ORDINALS.map((o) => <option key={o.n} value={o.n}>{o.label}</option>)}
                </select>
                <select className="input sm" value={s.nthDay} onChange={(e) => apply({ monthMode: "nth", nthDay: +e.target.value })}>
                  {WD_LONG.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
              </label>
            </div>
          )}
          <label className="repeat-row">
            <span>Ends</span>
            <span className="repeat-inline">
              <select className="input sm" value={s.end} onChange={(e) => apply({ end: e.target.value as Simple["end"] })}>
                <option value="never">Never</option>
                <option value="until">On date</option>
                <option value="count">After</option>
              </select>
              {s.end === "until" && <input className="input sm" type="date" value={s.until} onChange={(e) => apply({ until: e.target.value })} />}
              {s.end === "count" && (
                <><input className="input sm" type="number" min={1} max={999} value={s.count}
                  onChange={(e) => apply({ count: Math.max(1, parseInt(e.target.value) || 1) })} /> times</>
              )}
            </span>
          </label>
          {exdates.length + skippedOv.length > 0 && (
            <div className="repeat-skipped">
              <span className="muted small">{exdates.length + skippedOv.length} skipped date{exdates.length + skippedOv.length > 1 ? "s" : ""}</span>
              <button className="btn sm" onClick={() => onChange(composeValue(fromSimple(s), [], changed))}>Restore</button>
            </div>
          )}
          {changed.length > 0 && (
            <div className="repeat-skipped">
              <span className="muted small">{changed.length} date{changed.length > 1 ? "s" : ""} changed on their own</span>
              <button className="btn sm" onClick={() => onChange(composeValue(fromSimple(s), exdates, skippedOv))}>Reset</button>
            </div>
          )}
        </>
      )}
      {onDone && <div className="row"><span className="muted small">{s.freq === "NONE" ? "" : describe(composeValue(fromSimple(s), exdates, overrides))}</span><span className="spacer" /><button className="btn sm primary" onClick={onDone}>Done</button></div>}
    </div>
  );
}
