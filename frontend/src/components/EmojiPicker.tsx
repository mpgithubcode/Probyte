import { useState } from "react";

/** A small emoji picker for table icons: common emojis by group, a search by word, or paste any emoji. */
const GROUPS: [string, [string, string][]][] = [
  ["Work", [["📋", "clipboard list tasks"], ["✅", "check done tasks"], ["📌", "pin"], ["📎", "clip"], ["🗂️", "folders files"], ["📁", "folder"],
    ["📊", "chart bar stats"], ["📈", "chart up growth sales"], ["📉", "chart down"], ["💼", "briefcase work business"], ["🧾", "receipt invoice"],
    ["💰", "money bag finance"], ["💳", "card payment"], ["🏦", "bank"], ["📦", "package box inventory stock"], ["🛒", "cart shop orders"],
    ["🏷️", "tag label"], ["🧮", "abacus accounting"], ["🗃️", "card box archive"], ["🗄️", "cabinet files"], ["📇", "contacts cards"],
    ["📝", "memo notes write"], ["🖊️", "pen"], ["📐", "ruler design"], ["🛠️", "tools build"], ["⚙️", "gear settings"], ["🔧", "wrench fix"],
    ["🧪", "test lab"], ["🚀", "rocket launch project"], ["🎯", "target goal opportunity"], ["🏆", "trophy win"], ["💡", "idea bulb"]]],
  ["Time", [["📅", "calendar date"], ["🗓️", "calendar planner"], ["⏰", "alarm clock reminder"], ["⏳", "hourglass waiting"], ["🕒", "clock time"],
    ["📆", "calendar tear"], ["🔔", "bell notification"], ["⌛", "hourglass done"]]],
  ["People", [["👤", "person contact"], ["👥", "people team"], ["🤝", "handshake deal client"], ["👨‍👩‍👧", "family"], ["🧑‍💻", "developer"],
    ["🧑‍🍳", "cook"], ["🧑‍🎓", "student school"], ["🧑‍⚕️", "doctor health"], ["💬", "chat message"], ["📞", "phone call"], ["✉️", "email mail"],
    ["📣", "megaphone marketing"], ["❤️", "heart love"], ["⭐", "star favourite"], ["🙂", "smile"], ["🎉", "party celebrate event"]]],
  ["Home", [["🏠", "home house"], ["🛏️", "bed"], ["🧹", "broom chores cleaning"], ["🧺", "laundry basket"], ["🍳", "cooking pan"], ["🍽️", "dinner meals"],
    ["🥗", "salad food"], ["🛍️", "shopping bags"], ["🌱", "plant seedling garden"], ["🪴", "potted plant"], ["🐶", "dog pet"], ["🐱", "cat pet"],
    ["🚗", "car"], ["✈️", "plane travel trip"], ["🏖️", "beach holiday"], ["🗺️", "map"], ["🏋️", "gym workout"], ["🏃", "run sport"],
    ["📚", "books reading"], ["🎵", "music"], ["🎬", "film movies"], ["🎮", "games"], ["📷", "camera photos"], ["🎨", "art paint"]]],
  ["Symbols", [["🔴", "red circle"], ["🟠", "orange circle"], ["🟡", "yellow circle"], ["🟢", "green circle"], ["🔵", "blue circle"], ["🟣", "purple circle"],
    ["⚫", "black circle"], ["⚪", "white circle"], ["❌", "cross no"], ["⚠️", "warning"], ["❗", "exclamation important"], ["❓", "question"],
    ["🔥", "fire hot"], ["✨", "sparkles new"], ["🔒", "lock private"], ["🔑", "key"], ["🌍", "globe world"], ["⚡", "lightning fast"]]],
];

export function EmojiPicker({ onPick, onRemove }: { onPick: (emoji: string) => void; onRemove?: () => void }) {
  const [q, setQ] = useState("");
  const words = q.trim().toLowerCase();
  // a typed / pasted emoji (anything that isn't plain letters) can be used as it is
  const custom = /\p{Extended_Pictographic}/u.test(q) ? q.trim() : "";
  return (
    <div className="pop-body emoji-picker">
      <input className="input sm" autoFocus placeholder="Search, or paste an emoji" value={q} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && custom) onPick(custom); }} />
      {custom && <button className="btn sm" onClick={() => onPick(custom)}>Use {custom}</button>}
      <div className="emoji-scroll">
        {GROUPS.map(([name, items]) => {
          const shown = items.filter(([, k]) => !words || k.includes(words));
          return shown.length ? (
            <div key={name}>
              <div className="menu-heading">{name}</div>
              <div className="emoji-grid">
                {shown.map(([e, k]) => <button key={e} className="emoji-btn" title={k.split(" ")[0]} onClick={() => onPick(e)}>{e}</button>)}
              </div>
            </div>
          ) : null;
        })}
      </div>
      {onRemove && <button className="ghost-btn" onClick={onRemove}>Remove icon</button>}
    </div>
  );
}
