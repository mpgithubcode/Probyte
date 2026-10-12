-- Blueprint takeoff (extension "takeoff"): projects (a drawing set, one PDF), its sheets, the items being taken off
-- (from the legends, or added by hand) and the marks on the sheets (counted symbols, measured runs and areas).
CREATE TABLE IF NOT EXISTS tk_projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL,
    file_name   text NOT NULL,
    source      jsonb NOT NULL DEFAULT '{}',   -- where the PDF came from (an upload, a files table's record)
    page_count  integer NOT NULL DEFAULT 0,
    settings    jsonb NOT NULL DEFAULT '{}',   -- waste %, default units, notes…
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tk_projects_user ON tk_projects (user_id);

CREATE TABLE IF NOT EXISTS tk_pages (
    project_id  uuid NOT NULL REFERENCES tk_projects(id) ON DELETE CASCADE,
    page_no     integer NOT NULL,             -- 0-based, as in the PDF
    sheet_no    text,                         -- E1.0 (read from the title block; editable)
    title       text,
    discipline  text,                         -- electrical, architectural…
    included    boolean NOT NULL DEFAULT false,
    confirmed   boolean NOT NULL DEFAULT false,  -- someone checked sheet no., title and scale
    width       real NOT NULL,                -- PDF points
    height      real NOT NULL,
    scale       jsonb,                        -- {"ft_per_pt", "label", "source": "text"|"calibrated"|"manual"}
    scales      jsonb NOT NULL DEFAULT '[]',  -- the scales written on the sheet (suggestions)
    excluded    jsonb NOT NULL DEFAULT '[]',  -- areas left out of automatic counts ([x0,y0,x1,y1] in points)
    has_text    boolean NOT NULL DEFAULT true,
    PRIMARY KEY (project_id, page_no)
);

CREATE TABLE IF NOT EXISTS tk_items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES tk_projects(id) ON DELETE CASCADE,
    name        text NOT NULL,
    category    text,                         -- Lighting, Power, Fire alarm… (from the legend's heading)
    kind        text NOT NULL DEFAULT 'count',  -- count | length | area
    unit        text NOT NULL DEFAULT 'EA',
    color       text NOT NULL DEFAULT '#e5484d',
    tag         text,                         -- the text label on the drawings (fixture type "A", "EM-2")
    symbol      jsonb,                        -- {"page", "rect"}: the symbol's picture, to find it on sheets
    details     jsonb NOT NULL DEFAULT '{}',  -- columns of its schedule row (manufacturer, catalog no., watts…)
    status      text NOT NULL DEFAULT 'confirmed',  -- suggested (from a legend, not yet accepted) | confirmed
    waste_pct   real NOT NULL DEFAULT 0,
    per_run     real NOT NULL DEFAULT 0,      -- lengths: added to each run (drops, makeup), in feet
    unit_cost   real,
    notes       text,
    position    integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tk_items_project ON tk_items (project_id);

CREATE TABLE IF NOT EXISTS tk_marks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id     uuid NOT NULL REFERENCES tk_items(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL REFERENCES tk_projects(id) ON DELETE CASCADE,
    page_no     integer NOT NULL,
    geom        jsonb NOT NULL,               -- {"type": "point"|"line"|"area", "points": [[x, y], …]} in PDF points
    value       real NOT NULL DEFAULT 1,      -- 1 for a count, feet for a run, square feet for an area
    status      text NOT NULL DEFAULT 'confirmed',  -- suggested | confirmed | rejected
    source      text NOT NULL DEFAULT 'manual',     -- manual | symbol | tag | search
    score       real,                         -- how closely an automatic find matched
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tk_marks_item ON tk_marks (item_id);
CREATE INDEX IF NOT EXISTS tk_marks_page ON tk_marks (project_id, page_no);
