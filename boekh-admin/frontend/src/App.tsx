import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  getSettings,
  getTransactions,
  listCategories,
  recalculate,
  recordModification,
  refreshData,
  updateSettings,
} from "./api";
import type { CategoriesResponse, SettingsResponse, Transaction } from "./types";

const CURRENCY_SYMBOLS: Record<string, string> = {
  EUR: "€",
  USD: "$",
  GBP: "£",
};

function currencySymbol(code: unknown): string {
  const c = String(code ?? "");
  return CURRENCY_SYMBOLS[c] ?? (c ? `${c} ` : "");
}

function abbreviate(map: Record<string, string>, type: unknown): string {
  const t = String(type ?? "");
  if (map[t]) return map[t];
  const lower = t.toLowerCase();
  for (const [key, value] of Object.entries(map)) {
    if (key.toLowerCase() === lower) return value;
  }
  return t;
}

interface Selection {
  short: string;
  code: number;
}

/** Cross-tab channel name + the URL/window-name for each of the two windows. */
const CHANNEL = "bankingApp-admin";

function viewUrl(target: "main" | "settings"): string {
  return target === "settings"
    ? `${window.location.pathname}?view=settings`
    : window.location.pathname;
}

/** Open (or focus, if already open) the O/P or S window in its own browser tab.
 *  Using a fixed window name means a second Alt+S reuses the existing tab. */
function openView(target: "main" | "settings") {
  const name = target === "settings" ? "bankingApp-settings" : "bankingApp-main";
  const w = window.open(viewUrl(target), name);
  w?.focus();
}

/** Ignore Alt shortcuts while typing in a field. */
function isPlainAlt(e: KeyboardEvent): boolean {
  if (!e.altKey || e.ctrlKey || e.metaKey) return false;
  const el = e.target as HTMLElement | null;
  return !(
    el &&
    (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)
  );
}

export default function App() {
  const isSettings =
    new URLSearchParams(window.location.search).get("view") === "settings";
  return isSettings ? <SettingsApp /> : <MainApp />;
}

/** The O/P window: consolidation table + per-category drill-down + refresh. */
function MainApp() {
  const [view, setView] = useState<"O" | "P">("O");
  const [htable, setHtable] = useState<CategoriesResponse | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [transactions, setTransactions] = useState<Transaction[] | null>(null);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [modifiedIds, setModifiedIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState<string | null>(null);

  function loadHtable() {
    listCategories()
      .then(setHtable)
      .catch((e: Error) => setError(e.message));
  }

  useEffect(() => {
    window.name = "bankingApp-main";
    document.title = "BankingApp — Tables";
    loadHtable();
  }, []);

  // Keep the O-table current: refresh when the Settings window reports a
  // re-categorisation, and whenever this tab regains focus.
  useEffect(() => {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (e) => {
      if (e.data === "recalculated") loadHtable();
    };
    const onFocus = () => loadHtable();
    window.addEventListener("focus", onFocus);
    return () => {
      channel.close();
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  function select(short: string, code: number) {
    setSelected({ short, code });
    setTransactions(null);
    setKeywords([]);
    setModifiedIds([]);
    getTransactions(short, code)
      .then((res) => {
        setTransactions(res.transactions);
        setKeywords(res.keywords);
        setModifiedIds(res.modified_ids);
      })
      .catch((e: Error) => setError(e.message));
  }

  function reset() {
    setSelected(null);
    setTransactions(null);
    setKeywords([]);
    setModifiedIds([]);
    setView("O");
  }

  function modifyTransaction(modified: Transaction) {
    if (!selected) return;
    const { short, code } = selected;
    // Persist the modification, then recompute the O- and P-tables from scratch
    // (both apply modifications server-side) and refresh the display.
    recordModification(short, modified)
      .then(() => {
        loadHtable();
        select(short, code);
      })
      .catch((e: Error) => setError(e.message));
  }

  function showTransactions(short: string, code: number) {
    select(short, code);
    setView("P");
  }

  // Alt+O: back to the O-table here. Alt+S: open/focus the Settings window.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!isPlainAlt(e)) return;
      const key = e.key.toLowerCase();
      if (key === "o") {
        e.preventDefault();
        reset();
      } else if (key === "s") {
        e.preventDefault();
        openView("settings");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function doRefresh() {
    setRefreshing(true);
    setError(null);
    setRefreshStatus(null);
    refreshData()
      .then((res) => {
        const results = res.collect.summary?.results ?? [];
        const parts = results.map((r) =>
          r.status === "ok"
            ? `${r.person} ✓${r.transactions}`
            : `${r.person} ⚠ ${r.status}`
        );
        if (results.length === 0 && res.collect.error) {
          parts.push(res.collect.error);
        }
        if (res.refresh_error) parts.push(`reload: ${res.refresh_error}`);
        setRefreshStatus(parts.join("  ·  ") || "done");
        // Pull the freshly distilled data into the O-table and clear any view.
        reset();
        return listCategories().then(setHtable);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="refresh">
          <button
            className="refresh-button"
            onClick={doRefresh}
            disabled={refreshing}
            title="Fetch all bank data, then reload"
          >
            {refreshing ? "Refreshing…" : "↻ Refresh data"}
          </button>
          {refreshStatus && <div className="refresh-status">{refreshStatus}</div>}
        </div>
        <div className="winbar">
          <button
            className="win-link"
            onClick={() => openView("settings")}
            title="Open the settings window (Ctrl+Tab to switch)"
          >
            ⚙ Settings (Alt+S) ↗
          </button>
        </div>
        {view === "P" && htable && selected && (
          <ColumnTable htable={htable} selected={selected} onPick={select} />
        )}
      </aside>

      <main className="content">
        {error && <p className="error">{error}</p>}

        {view === "O" &&
          (htable ? <HTable htable={htable} onPick={showTransactions} /> : <p>Loading…</p>)}

        {view === "P" &&
          (htable && selected ? (
            <PTable
              short={selected.short}
              categoryName={categoryName(htable, selected.code)}
              transactions={transactions}
              abbreviations={htable.abbreviations}
              widths={htable.widths}
              keywords={keywords}
              modifiedIds={modifiedIds}
              onModify={modifyTransaction}
            />
          ) : (
            <p>Select an amount in the O-table (Alt+O).</p>
          ))}
      </main>
    </div>
  );
}

/** The S window: editable keyword lists. On leaving (tab switch/close) after an
 *  edit, it re-categorises and signals the O/P window to refresh its totals. */
function SettingsApp() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    window.name = "bankingApp-settings";
     = "bankingApp — Settings";
    getSettings()
      .then(setSettings)
      .catch((e: Error) => setError(e.message));
  }, []);

  // Apply edits when this tab is hidden or closed (mirrors the old "leaving S"
  // behaviour). A ref lets the listeners see the latest `dirty` value.
  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);
  useEffect(() => {
    const channel = new BroadcastChannel(CHANNEL);
    function applyIfDirty() {
      if (!dirtyRef.current) return;
      dirtyRef.current = false;
      setDirty(false);
      recalculate()
        .then(() => channel.postMessage("recalculated"))
        .catch((e: Error) => setError(e.message));
    }
    function onVisibility() {
      if (document.hidden) applyIfDirty();
    }
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", applyIfDirty);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", applyIfDirty);
      channel.close();
    };
  }, []);

  // Alt+O: open/focus the tables window. Alt+S: focus this one.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!isPlainAlt(e)) return;
      const key = e.key.toLowerCase();
      if (key === "o") {
        e.preventDefault();
        openView("main");
      } else if (key === "s") {
        e.preventDefault();
        window.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function updateTerms(group: string, category: string, terms: string[]) {
    updateSettings(group, category, terms)
      .then((res) => {
        setDirty(true);
        setSettings((prev) => {
          if (!prev) return prev;
          if (group === "general") {
            return {
              ...prev,
              general: { ...prev.general, [category]: res.terms },
            };
          }
          const personGroup = { ...(prev.personal[group] ?? {}) };
          if (res.terms.length) personGroup[category] = res.terms;
          else delete personGroup[category];
          return {
            ...prev,
            personal: { ...prev.personal, [group]: personGroup },
          };
        });
      })
      .catch((e: Error) => setError(e.message));
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="winbar">
          <button
            className="win-link"
            onClick={() => openView("main")}
            title="Open the tables window (Ctrl+Tab to switch)"
          >
            ← Tables (Alt+O) ↗
          </button>
        </div>
        <p className="win-hint">
          Settings window. Switch with <kbd>Ctrl</kbd>+<kbd>Tab</kbd> or
          <kbd>Alt</kbd>+<kbd>O</kbd>. Edits apply when you leave this window.
        </p>
      </aside>

      <main className="content">
        {error && <p className="error">{error}</p>}
        {settings ? (
          <STable settings={settings} onUpdate={updateTerms} />
        ) : (
          <p>Loading…</p>
        )}
      </main>
    </div>
  );
}

/** Leading two-digit code of a category name, e.g. "20 Werk" -> 20. */
function codeOf(categoryName: string): number {
  return parseInt(categoryName.slice(0, 2), 10);
}

function categoryName(htable: CategoriesResponse, code: number): string {
  const row = htable.rows.find((r) => codeOf(r[0]) === code);
  return row ? row[0] : String(code);
}

function HTable({
  htable,
  onPick,
}: {
  htable: CategoriesResponse;
  onPick: (short: string, code: number) => void;
}) {
  const { headers, rows } = htable;
  return (
    <table className="h-table">
      <thead>
        <tr>
          {headers.map((header, i) => (
            <th key={header} className={i === 0 ? "cat" : "num"}>
              {header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row[0]}>
            {row.map((cell, i) => (
              <td
                key={i}
                className={i === 0 ? "cat" : "num clickable"}
                onClick={
                  i === 0 ? undefined : () => onPick(headers[i], codeOf(row[0]))
                }
              >
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Sidebar: the selected person's column as C (category code) / amount. */
function ColumnTable({
  htable,
  selected,
  onPick,
}: {
  htable: CategoriesResponse;
  selected: Selection;
  onPick: (short: string, code: number) => void;
}) {
  const col = htable.headers.indexOf(selected.short);
  return (
    <table className="h-table col-table">
      <thead>
        <tr>
          <th className="num">C</th>
          <th className="num">
            <ShortPicker
              value={selected.short}
              options={htable.shorts}
              onChange={(s) => onPick(s, selected.code)}
            />
          </th>
        </tr>
      </thead>
      <tbody>
        {htable.rows.map((row) => {
          const code = codeOf(row[0]);
          return (
            <tr key={row[0]} className={code === selected.code ? "active" : ""}>
              <td
                className="num clickable"
                onClick={() => onPick(selected.short, code)}
              >
                {code}
              </td>
              <td
                className="num clickable"
                onClick={() => onPick(selected.short, code)}
              >
                {col >= 0 ? row[col] : ""}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Main panel: all transactions for the selected person + category. */
/** Map a P-table column to its key in the "widths" config (and "cat" label). */
function widthKey(column: string): string {
  return column === "category" ? "cat" : column.toLowerCase();
}

const HEADER_LABELS: Record<string, string> = {
  amount: "Bedrag",
  type: "Soort",
  name: "Naam",
  description: "Beschrijving",
  date: "Datum",
  category: "C",
};

function headerLabel(column: string): string {
  return HEADER_LABELS[column] ?? column;
}

function PTable({
  short,
  categoryName,
  transactions,
  abbreviations,
  widths,
  keywords,
  modifiedIds,
  onModify,
}: {
  short: string;
  categoryName: string;
  transactions: Transaction[] | null;
  abbreviations: Record<string, string>;
  widths: Record<string, number>;
  keywords: string[];
  modifiedIds: string[];
  onModify: (transaction: Transaction) => void;
}) {
  const columns = ptableColumns(transactions ?? []);

  function renderCell(t: Transaction, column: string) {
    if (column === "amount") {
      const amount = formatCell(t.amount);
      const negative = amount.trim().startsWith("-");
      return (
        <td key={column} className={negative ? "amount num neg" : "amount num"}>
          {currencySymbol(t.currency)}
          {amount}
        </td>
      );
    }
    if (column === "type") {
      return <td key={column}>{abbreviate(abbreviations, t.type)}</td>;
    }
    if (column === "name") {
      return (
        <td key={column} className="name">
          {formatCell(t.name)}
        </td>
      );
    }
    if (column === "description") {
      return (
        <td key={column} className="desc">
          <EditableField
            value={formatCell(t.description)}
            display={highlight(formatCell(t.description), keywords)}
            multiline
            onCommit={(v) => onModify({ ...t, description: v })}
          />
        </td>
      );
    }
    if (column === "category") {
      return (
        <td key={column} className="num">
          <EditableField
            value={formatCell(t.category)}
            onCommit={(v) => {
              const code = parseInt(v, 10);
              if (!Number.isNaN(code)) onModify({ ...t, category: code });
            }}
          />
        </td>
      );
    }
    return <td key={column}>{formatCell(t[column])}</td>;
  }

  return (
    <>
      <div style={{ marginBottom: "1rem" }}>
        <strong>
          {short} / {categoryName}
        </strong>
      </div>

      {transactions === null && <p>Loading…</p>}

      {transactions !== null && (
        <table className="p-table">
          <colgroup>
            {columns.map((c) => {
              const fraction = widths[widthKey(c)] ?? widths[c];
              return (
                <col
                  key={c}
                  style={fraction ? { width: `${fraction * 100}%` } : undefined}
                />
              );
            })}
          </colgroup>
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c}
                  className={c === "description" ? "desc" : c === "name" ? "name" : ""}
                >
                  {headerLabel(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {transactions.map((t, i) => (
              <tr
                key={i}
                className={modifiedIds.includes(String(t.id)) ? "modified" : ""}
              >
                {columns.map((c) => renderCell(t, c))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {transactions !== null && transactions.length === 0 && (
        <p>No transactions in this category.</p>
      )}
    </>
  );
}

/** The clickable short in the P-view title, opening a dropdown of all shorts. */
function ShortPicker({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (short: string) => void;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  return (
    <span className="short-picker">
      <button
        className="short-button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {value} ▾
      </button>
      {open && (
        <ul className="short-menu">
          {options.map((s) => (
            <li
              key={s}
              className={s === value ? "active" : ""}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                if (s !== value) onChange(s);
              }}
            >
              {s}
            </li>
          ))}
        </ul>
      )}
    </span>
  );
}

/** Settings (S) table: editable keyword lists per category. */
function STable({
  settings,
  onUpdate,
}: {
  settings: SettingsResponse;
  onUpdate: (group: string, category: string, terms: string[]) => void;
}) {
  const { categories, shorts, general, personal } = settings;
  return (
    <div className="s-scroll">
      <table className="s-table">
      <thead>
        <tr>
          <th>Categorie</th>
          <th>Algemeen</th>
          {shorts.map((s) => (
            <th key={s}>{s}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {categories.map((name) => (
          <tr key={name}>
            <td>{name}</td>
            <td>
              <EditableCell
                terms={general[name] ?? []}
                onCommit={(terms) => onUpdate("general", name, terms)}
              />
            </td>
            {shorts.map((s) => (
              <td key={s}>
                <EditableCell
                  terms={personal[s]?.[name] ?? []}
                  onCommit={(terms) => onUpdate(s, name, terms)}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      </table>
    </div>
  );
}

function arraysEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** A cell holding an editable keyword list: one input per term, plus a trailing
 *  input to add a new term. Clearing a term deletes it on commit. */
function EditableCell({
  terms,
  onCommit,
}: {
  terms: string[];
  onCommit: (terms: string[]) => void;
}) {
  const [draft, setDraft] = useState<string[]>(terms);
  const [add, setAdd] = useState("");

  useEffect(() => {
    setDraft(terms);
    setAdd("");
  }, [terms]);

  function commit(next: string[]) {
    const cleaned = next.map((t) => t.trim()).filter(Boolean);
    if (!arraysEqual(cleaned, terms)) onCommit(cleaned);
  }

  function commitAdd() {
    if (!add.trim()) return;
    commit([...draft, add]);
  }

  return (
    <div className="terms">
      {draft.map((term, i) => (
        <input
          key={i}
          className="term-input"
          value={term}
          onChange={(e) =>
            setDraft((d) => d.map((t, idx) => (idx === i ? e.target.value : t)))
          }
          onBlur={() => commit(draft)}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
        />
      ))}
      <input
        className="term-input add"
        value={add}
        placeholder="+ term"
        onChange={(e) => setAdd(e.target.value)}
        onBlur={commitAdd}
        onKeyDown={(e) => {
          if (e.key === "Enter") commitAdd();
        }}
      />
    </div>
  );
}

/** Ordered union of transaction keys, excluding "id" and "currency"
 *  ("currency" is folded into the amount column as a symbol). */
function ptableColumns(transactions: Transaction[]): string[] {
  const columns: string[] = [];
  for (const t of transactions) {
    for (const key of Object.keys(t)) {
      if (key !== "id" && key !== "currency" && !columns.includes(key)) {
        columns.push(key);
      }
    }
  }
  return columns;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** A click-to-edit cell value. Display mode shows `display` (defaults to the
 *  value); clicking switches to an input/textarea that commits on blur. */
function EditableField({
  value,
  display,
  multiline,
  onCommit,
}: {
  value: string;
  display?: ReactNode;
  multiline?: boolean;
  onCommit: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  if (!editing) {
    return (
      <span className="editable" onClick={() => setEditing(true)}>
        {display ?? value}
      </span>
    );
  }

  function commit() {
    setEditing(false);
    if (draft !== value) onCommit(draft);
  }

  if (multiline) {
    return (
      <textarea
        className="cell-edit"
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    );
  }

  return (
    <input
      className="cell-edit"
      autoFocus
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
      }}
    />
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Render text with any matching keyword occurrences printed in bold. */
function highlight(text: string, keywords: string[]): ReactNode {
  const terms = [...new Set(keywords.map((k) => k.trim()).filter(Boolean))];
  if (terms.length === 0) return text;

  // Longest first so the longer keyword wins when several match at one spot.
  // \b...\b restricts matches to full words ("ns" won't match "transaction").
  const pattern = terms
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp)
    .join("|");
  const re = new RegExp(`\\b(?:${pattern})\\b`, "gi");

  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(re)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (start > last) nodes.push(text.slice(last, start));
    nodes.push(<strong key={start}>{match[0]}</strong>);
    last = end;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
