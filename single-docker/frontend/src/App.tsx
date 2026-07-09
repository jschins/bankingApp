import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  addCategoryTerm,
  fetchBankData,
  getAuthorizationUrl,
  getBankAccounts,
  getConsentStatus,
  getSettings,
  getTotals,
  getTransactions,
  recalculate,
  recordModification,
  updateBankAccounts,
  updateSettings,
} from "./api";
import type { BankAccount, SettingsResponse, Transaction, TransactionsResponse } from "./types";

const CHANNEL = "single-docker";

function formatTermMatchHint(typerules: { type: string; category: string }[]): string {
  const priority =
    "Priority (highest first): (1) typerules beat all keywords; " +
    "(2) && terms beat single-phrase terms — e.g. general && beats personal single-phrase; " +
    "(3) among single-phrase terms, longer string wins; " +
    "(4) within && or equal-length single-phrase, personal beats general.";
  const wildcards =
    "# matches zero or more letters or dots within one word (not across spaces). " +
    "Use && when both phrases must match (e.g. albert && heijn).";
  const rules =
    typerules.length === 0
      ? ""
      : ` Typerules: ${typerules.map((rule) => `${rule.type} → ${rule.category}`).join("; ")}.`;
  return `${wildcards} ${priority}${rules}`;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  EUR: "€",
  USD: "$",
  GBP: "£",
};

function currencySymbol(code: unknown): string {
  const c = String(code ?? "");
  return CURRENCY_SYMBOLS[c] ?? (c ? `${c} ` : "€");
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

function viewUrl(target: "main" | "terms"): string {
  return target === "terms"
    ? `${window.location.pathname}?view=terms`
    : window.location.pathname;
}

function openView(target: "main" | "terms") {
  const name = target === "terms" ? "bankingApp-terms" : "bankingApp-main";
  window.open(viewUrl(target), name)?.focus();
}

function isPlainAlt(e: KeyboardEvent): boolean {
  if (!e.altKey || e.ctrlKey || e.metaKey) return false;
  const el = e.target as HTMLElement | null;
  return !(
    el &&
    (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)
  );
}

const AIB_HISTORICAL_START = "2024-01-01";

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function previousMonthRange(): { from: string; to: string } {
  const today = new Date();
  const firstThisMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const lastPrev = new Date(firstThisMonth);
  lastPrev.setDate(0);
  const firstPrev = new Date(lastPrev.getFullYear(), lastPrev.getMonth(), 1);
  return { from: isoDate(firstPrev), to: isoDate(lastPrev) };
}

function renewalHistoryRange(): { from: string; to: string } {
  return { from: AIB_HISTORICAL_START, to: isoDate(new Date()) };
}

export default function App() {
  const isTerms = new URLSearchParams(window.location.search).get("view") === "terms";
  return isTerms ? <TermsApp /> : <MainApp />;
}

function MainApp() {
  const [totals, setTotals] = useState<Record<string, string> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TransactionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const [needsConsent, setNeedsConsent] = useState(false);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const range = previousMonthRange();
  const [dateFrom, setDateFrom] = useState(range.from);
  const [dateTo, setDateTo] = useState(range.to);
  const [redirectCode, setRedirectCode] = useState("");
  const [newYear, setNewYear] = useState(false);
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([]);
  const [termAssign, setTermAssign] = useState<{
    transaction: Transaction;
    description: string;
  } | null>(null);
  const [termAssignSettings, setTermAssignSettings] = useState<SettingsResponse | null>(
    null
  );
  const selectedRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);

  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  function markDirty() {
    dirtyRef.current = true;
  }

  function clearDirty() {
    dirtyRef.current = false;
  }

  function loadCategoryDetail(category: string): Promise<void> {
    setDetail(null);
    return getTransactions(category)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }

  /** Reload totals and optional detail from server (no recategorisation). */
  function loadDisplay(category: string | null): Promise<void> {
    setError(null);
    return getTotals()
      .then((totals) => {
        setTotals(totals);
        if (!category) {
          setDetail(null);
          return;
        }
        return loadCategoryDetail(category);
      })
      .catch((e: Error) => setError(e.message));
  }

  /** Recategorise on the server, then reload totals and optional detail. */
  function refreshMainView(category: string | null): Promise<void> {
    setError(null);
    if (category) {
      setDetail(null);
    }
    return recalculate()
      .then((totals) => {
        clearDirty();
        setTotals(totals);
        if (!category) {
          setDetail(null);
          return;
        }
        return getTransactions(category).then(setDetail);
      })
      .catch((e: Error) => {
        setError(e.message);
      });
  }

  function applyIfDirty(category: string | null): Promise<void> {
    if (!dirtyRef.current) {
      return Promise.resolve();
    }
    return refreshMainView(category);
  }

  function loadTotals() {
    return loadDisplay(null);
  }

  function loadBankAccounts() {
    getBankAccounts()
      .then((res) => {
        setBankAccounts(res.accounts);
        setNeedsConsent(res.needs_renewal);
      })
      .catch((e: Error) => setError(e.message));
  }

  function toggleAccount(uid: string, enabled: boolean) {
    const account = bankAccounts.find((acc) => acc.uid === uid);
    if (account && !account.active) {
      return;
    }
    const next = bankAccounts.map((acc) =>
      acc.uid === uid ? { ...acc, enabled } : acc
    );
    const enabledUids = next.filter((acc) => acc.enabled).map((acc) => acc.uid);
    if (enabledUids.length === 0) {
      setError("At least one account must be enabled.");
      return;
    }
    setError(null);
    setBankAccounts(next);
    updateBankAccounts(enabledUids)
      .then((res) => setBankAccounts(res.accounts))
      .catch((e: Error) => {
        setError(e.message);
        loadBankAccounts();
      });
  }

  useEffect(() => {
    window.name = "bankingApp-main";
    document.title = "BankingApp — Transactions";
    loadTotals();
    loadBankAccounts();
    getConsentStatus()
      .then((s) => setNeedsConsent(s.needs_renewal))
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (needsConsent) {
      const range = renewalHistoryRange();
      setDateFrom(range.from);
      setDateTo(range.to);
    }
  }, [needsConsent]);

  useEffect(() => {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (e) => {
      if (e.data === "recalculated") {
        clearDirty();
        void loadDisplay(selectedRef.current);
      }
    };
    const onFocus = () => {
      void applyIfDirty(selectedRef.current);
    };
    window.addEventListener("focus", onFocus);
    return () => {
      channel.close();
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  function selectCategory(category: string) {
    setSelected(category);
    if (dirtyRef.current) {
      void refreshMainView(category);
      return;
    }
    void loadCategoryDetail(category);
  }

  function modifyTransaction(modified: Transaction) {
    if (!selected) return;
    recordModification(modified)
      .then(() => {
        markDirty();
        return refreshMainView(selected);
      })
      .catch((e: Error) => setError(e.message));
  }

  function openTermAssign(transaction: Transaction, description: string) {
    setError(null);
    getSettings()
      .then((settings) => {
        setTermAssignSettings(settings);
        setTermAssign({ transaction, description });
      })
      .catch((e: Error) => setError(e.message));
  }

  function closeTermAssign() {
    setTermAssign(null);
    setTermAssignSettings(null);
  }

  function saveTermAssign(term: string, targetCategory: string, general: boolean) {
    return addCategoryTerm({ category_name: targetCategory, term, general })
      .then(() => {
        closeTermAssign();
        clearDirty();
        return loadDisplay(selectedRef.current);
      })
      .catch((e: Error) => setError(e.message));
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!isPlainAlt(e)) return;
      const key = e.key.toLowerCase();
      if (key === "t") {
        e.preventDefault();
        openView("terms");
      } else if (key === "m") {
        e.preventDefault();
        window.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function doFetch() {
    setFetching(true);
    setError(null);
    setFetchStatus(null);
    fetchBankData({
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      redirect_code: redirectCode.trim() || undefined,
      new_year: newYear,
    })
      .then((res) => {
        const parts = [`${res.transaction_count} transactions retrieved`];
        if (res.date_from && res.date_to) {
          parts.push(`(${res.date_from} .. ${res.date_to})`);
        }
        if (res.renewal_day) {
          parts.push("renewal day");
        }
        if (res.warnings?.length) {
          parts.push(res.warnings.join(" "));
        }
        if (res.account_errors?.length) {
          parts.push(res.account_errors.join(" "));
        }
        setFetchStatus(parts.join(" · "));
        setTotals(res.totals);
        setSelected(null);
        setDetail(null);
        setNeedsConsent(false);
        loadBankAccounts();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setFetching(false));
  }

  function startConsent() {
    getAuthorizationUrl()
      .then((res) => setAuthUrl(res.url))
      .catch((e: Error) => setError(e.message));
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="fetch-form">
          <label>
            date-from
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label>
            date-to
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          {(needsConsent || redirectCode) && (
            <label>
              redirect-code
              <input
                type="text"
                value={redirectCode}
                placeholder="Redirect URL"
                onChange={(e) => setRedirectCode(e.target.value)}
              />
            </label>
          )}
          <label className="checkbox">
            <input type="checkbox" checked={newYear} onChange={(e) => setNewYear(e.target.checked)} />
            new year
          </label>
          <button className="refresh-button" onClick={doFetch} disabled={fetching}>
            {fetching ? "Fetching…" : "↻ Fetch bank data"}
          </button>
          {fetchStatus && <div className="refresh-status">{fetchStatus}</div>}
        </div>

        {bankAccounts.length > 0 && (
          <AccountChecklist accounts={bankAccounts} onToggle={toggleAccount} />
        )}

        {needsConsent && (
          <div className="consent-banner">
            <p>
              Refresh bank consent. On the day you complete bank login and paste the
              redirect code, both accounts can fetch history back to {AIB_HISTORICAL_START}.
            </p>
            <button type="button" className="refresh-button" onClick={startConsent}>
              Authorization URL
            </button>
            {authUrl && (
              <p>
                <a href={authUrl} target="_blank" rel="noreferrer">
                  {authUrl}
                </a>
              </p>
            )}
          </div>
        )}

        <div className="winbar">
          <button
            className="win-link"
            onClick={() => openView("terms")}
            title="Open the terms window"
          >
           { "⚙ Edit Terms (Alt+T)" }
          </button>
        </div>

        {totals && (
          <TotalsTable totals={totals} selected={selected} onPick={selectCategory} />
        )}
      </aside>

      <main className="content">
        {error && <p className="error">{error}</p>}
        {!selected && <p>Select an amount in the sidebar Table</p>}
        {selected && detail && (
          <PTable
            categoryName={selected}
            detail={detail}
            onModify={modifyTransaction}
            onCategoryError={setError}
            onAssignTerm={
              selected === detail.remainder_category ? openTermAssign : undefined
            }
          />
        )}
        {selected && !detail && <p>Loading…</p>}
        {termAssign && termAssignSettings && (
          <TermAssignDialog
            settings={termAssignSettings}
            initialTerm={termAssign.description}
            onClose={closeTermAssign}
            onSave={saveTermAssign}
          />
        )}
      </main>
    </div>
  );
}

function TermsApp() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirtyRef = useRef(false);

  useEffect(() => {
    window.name = "bankingApp-terms";
    document.title = "BankingApp — Terms";
    getSettings()
      .then(setSettings)
      .catch((e: Error) => setError(e.message));
  }, []);

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

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!isPlainAlt(e)) return;
      const key = e.key.toLowerCase();
      if (key === "m") {
        e.preventDefault();
        openView("main");
      } else if (key === "t") {
        e.preventDefault();
        window.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function updateTerms(group: string, category: string, terms: string[]) {
    updateSettings(group, category, terms)
      .then(() => {
        setDirty(true);
        setSettings((prev) => {
          if (!prev) return prev;
          if (group === "general") {
            return { ...prev, general: { ...prev.general, [category]: terms } };
          }
          const personGroup = { ...(prev.personal[group] ?? {}) };
          if (terms.length) personGroup[category] = terms;
          else delete personGroup[category];
          return { ...prev, personal: { ...prev.personal, [group]: personGroup } };
        });
      })
      .catch((e: Error) => setError(e.message));
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="winbar">
          <button className="win-link" onClick={() => openView("main")}>
            ← Transactions (Alt+M) ↗
          </button>
        </div>
        <p className="win-hint">
          Term Window. Return to Overview using <kbd>Ctrl</kbd>+<kbd>Tab</kbd> or <kbd>Alt</kbd>+<kbd>M</kbd>.
          Changes are applied when you leave this window.{" "}
          {settings ? formatTermMatchHint(settings.typerules) : ""}
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

function AccountChecklist({
  accounts,
  onToggle,
}: {
  accounts: BankAccount[];
  onToggle: (uid: string, enabled: boolean) => void;
}) {
  const groups = accounts.reduce<Map<string, BankAccount[]>>((map, acc) => {
    const key = `${acc.aspsp} (${acc.country})`;
    const items = map.get(key) ?? [];
    items.push(acc);
    map.set(key, items);
    return map;
  }, new Map());

  return (
    <div className="account-list">
      <div className="account-list-heading">Bank accounts</div>
      {[...groups.entries()].map(([bank, items]) => (
        <div key={bank} className="account-bank-group">
          <div className="account-bank-heading">{bank}</div>
          {items.map((acc) => (
            <label
              key={acc.uid}
              className={`account-item checkbox${acc.active ? "" : " account-item-inactive"}`}
              title={acc.active ? undefined : "Renew consent for this bank"}
            >
              <input
                type="checkbox"
                checked={acc.enabled}
                disabled={!acc.active}
                onChange={(e) => onToggle(acc.uid, e.target.checked)}
              />
              <span className="account-label">
                {acc.name || acc.iban || acc.uid}
                {acc.iban && acc.name ? ` · ${acc.iban}` : ""}
                {acc.balance && (
                  <span className="account-balance">
                    {currencySymbol(acc.balance_currency || acc.currency)}
                    {acc.balance}
                  </span>
                )}
              </span>
            </label>
          ))}
        </div>
      ))}
    </div>
  );
}

function TotalsTable({
  totals,
  selected,
  onPick,
}: {
  totals: Record<string, string>;
  selected: string | null;
  onPick: (category: string) => void;
}) {
  const rows = Object.entries(totals);
  return (
    <table className="totals-table">
      <thead>
        <tr>
          <th className="cat">Category</th>
          <th className="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, amount]) => (
          <tr key={name} className={name === selected ? "active" : ""}>
            <td className="cat">{name}</td>
            <td className="num clickable" onClick={() => onPick(name)}>
              €{amount}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PTable({
  categoryName,
  detail,
  onModify,
  onCategoryError,
  onAssignTerm,
}: {
  categoryName: string;
  detail: TransactionsResponse;
  onModify: (transaction: Transaction) => void;
  onCategoryError?: (message: string | null) => void;
  onAssignTerm?: (transaction: Transaction, description: string) => void;
}) {
  const validCategoryCodes = new Set(detail.valid_category_codes ?? []);
  const columns =
    detail.columns.length > 0
      ? detail.columns
      : ptableColumns(detail.transactions);

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
      return <td key={column}>{abbreviate(detail.abbreviations, t.type)}</td>;
    }
    if (column === "name") {
      const text = formatCell(t.name);
      return (
        <td key={column} className="name">
          {highlight(text, detail.keywords)}
        </td>
      );
    }
    if (column === "description") {
      const text = formatCell(t.description);
      if (onAssignTerm) {
        return (
          <td key={column} className="desc">
            <span
              className="editable assign-term"
              title="Assign keyword to a category"
              onClick={() => onAssignTerm(t, text)}
            >
              {highlight(text, detail.keywords)}
            </span>
          </td>
        );
      }
      return (
        <td key={column} className="desc">
          <EditableField
            value={text}
            display={highlight(text, detail.keywords)}
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
              if (Number.isNaN(code) || !validCategoryCodes.has(code)) {
                onCategoryError?.(
                  `Unknown category code. Use one of: ${[...validCategoryCodes].sort((a, b) => a - b).join(", ")}`
                );
                return;
              }
              onModify({ ...t, category: code });
            }}
          />
        </td>
      );
    }
    return <td key={column}>{formatCell(t[column])}</td>;
  }

  return (
    <div className="p-panel">
      <div className="p-heading">
        <strong>
          {detail.person} / {categoryName}
        </strong>
      </div>
      {detail.transactions.length === 0 ? (
        <p>No transactions in this category</p>
      ) : (
        <table className="p-table">
          <colgroup>
            {columns.map((c) => (
              <col key={c} className={columnColClass(c)} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} className={columnCellClass(c)}>
                  {headerLabel(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {detail.transactions.map((t, i) => (
              <tr
                key={i}
                className={
                  detail.modified_ids.includes(String(t.id)) ? "modified" : ""
                }
              >
                {columns.map((c) => renderCell(t, c))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TermAssignDialog({
  settings,
  initialTerm,
  onClose,
  onSave,
}: {
  settings: SettingsResponse;
  initialTerm: string;
  onClose: () => void;
  onSave: (term: string, targetCategory: string, general: boolean) => void;
}) {
  const [term, setTerm] = useState(initialTerm);
  const [targetCategory, setTargetCategory] = useState<string | null>(null);
  const [personal, setPersonal] = useState(false);
  const [saving, setSaving] = useState(false);

  const categories = settings.categories.filter(
    (name) => name !== settings.remainder_category
  );

  useEffect(() => {
    setTerm(initialTerm);
    setTargetCategory(null);
    setPersonal(false);
  }, [initialTerm]);

  function submit() {
    const cleaned = term.trim();
    if (!cleaned || !targetCategory || saving) return;
    setSaving(true);
    Promise.resolve(onSave(cleaned, targetCategory, !personal)).finally(() =>
      setSaving(false)
    );
  }

  return (
    <div className="term-assign-overlay" onClick={onClose}>
      <div
        className="term-assign-dialog"
        role="dialog"
        aria-labelledby="term-assign-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="term-assign-title">Assign keyword</h2>
        <p className="term-assign-hint">
          {settings.remainder_category}: pick a keyword and target category. Transactions are
          recategorised after save. {formatTermMatchHint(settings.typerules)}
        </p>
        <label className="term-assign-field">
          Term
          <textarea
            value={term}
            rows={Math.min(8, Math.max(2, term.split("\n").length, Math.ceil(term.length / 72)))}
            autoFocus
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && targetCategory) {
                e.preventDefault();
                submit();
              }
            }}
          />
        </label>
        <div className="term-assign-categories">
          <div className="term-assign-label">Category</div>
          <div className="term-assign-category-list">
            {categories.map((name) => (
              <button
                key={name}
                type="button"
                className={
                  targetCategory === name
                    ? "term-assign-category selected"
                    : "term-assign-category"
                }
                onClick={() => setTargetCategory(name)}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
        <label className="term-assign-field checkbox">
          <input
            type="checkbox"
            checked={personal}
            onChange={(e) => setPersonal(e.target.checked)}
          />
          {settings.person} ({settings.person}_categories.json)
        </label>
        <p className="term-assign-target">
          {personal
            ? `Saved to ${settings.person}_categories.json`
            : "Saved to shared categories.json"}
        </p>
        <div className="term-assign-actions">
          <button type="button" className="refresh-button" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="button"
            className="refresh-button"
            onClick={submit}
            disabled={!term.trim() || !targetCategory || saving}
          >
            {saving ? "Saving…" : "Save term"}
          </button>
        </div>
      </div>
    </div>
  );
}

function STable({
  settings,
  onUpdate,
}: {
  settings: SettingsResponse;
  onUpdate: (group: string, category: string, terms: string[]) => void;
}) {
  const { categories, person, general, personal } = settings;
  return (
    <div className="s-scroll">
      <table className="s-table">
        <thead>
          <tr>
            <th>Category</th>
            <th>General</th>
            <th>{person}</th>
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
              <td>
                <EditableCell
                  terms={personal[person]?.[name] ?? []}
                  onCommit={(terms) => onUpdate(person, name, terms)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
    setDraft([...terms].sort((a, b) => a.localeCompare(b)));
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

/** CSS class for <col> width rules in index.css (col-{column key}). */
function columnColClass(column: string): string {
  return column === "description" ? "desc-col" : `col-${column}`;
}

function columnCellClass(column: string): string | undefined {
  if (column === "description") return "desc";
  return undefined;
}

const HEADER_LABELS: Record<string, string> = {
  amount: "Amount",
  type: "Type",
  name: "Name",
  iban: "IBAN",
  description: "Description",
  date: "Date",
  category: "C",
};

function headerLabel(column: string): string {
  return HEADER_LABELS[column] ?? column;
}

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

function arraysEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function lettersOnly(word: string): string {
  return word.replace(/[^a-z.]/gi, "").toLowerCase();
}

function termToPattern(term: string): string {
  let pattern = "";
  let lastWildcard = false;
  for (const ch of term) {
    if (ch === "#") {
      if (!lastWildcard) {
        pattern += "[a-z.]*";
        lastWildcard = true;
      }
    } else {
      pattern += escapeRegExp(ch);
      lastWildcard = false;
    }
  }
  return pattern;
}

function matchesHashWord(term: string, word: string): boolean {
  const pattern = new RegExp(`^${termToPattern(term)}$`, "i");
  const candidates = new Set<string>([word.toLowerCase(), lettersOnly(word)]);
  for (const candidate of candidates) {
    if (candidate && pattern.test(candidate)) return true;
  }
  return false;
}

function highlightWithRegex(text: string, terms: string[]): ReactNode {
  if (terms.length === 0) return text;
  const pattern = terms
    .sort((a, b) => b.length - a.length)
    .map((t) => (t.includes("#") ? termToPattern(t) : escapeRegExp(t)))
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
  return nodes.length === 1 ? nodes[0] : <>{nodes}</>;
}

function highlightRanges(text: string, ranges: Array<[number, number]>): ReactNode {
  if (ranges.length === 0) return text;
  const merged: Array<[number, number]> = [];
  for (const [start, end] of ranges.sort((a, b) => a[0] - b[0])) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) {
      last[1] = Math.max(last[1], end);
    } else {
      merged.push([start, end]);
    }
  }
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const [start, end] of merged) {
    if (start > last) nodes.push(text.slice(last, start));
    nodes.push(<strong key={start}>{text.slice(start, end)}</strong>);
    last = end;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes.length === 1 ? nodes[0] : <>{nodes}</>;
}

function atomicHighlightTerms(keywords: string[]): string[] {
  const atoms = new Set<string>();
  for (const keyword of keywords) {
    const term = keyword.trim().toLowerCase();
    if (!term) continue;
    const andParts = term.includes(" && ") ? term.split(" && ") : [term];
    for (const part of andParts) {
      const cleaned = part.trim();
      if (cleaned) atoms.add(cleaned);
    }
  }
  return [...atoms];
}

function highlight(text: string, keywords: string[]): ReactNode {
  const terms = atomicHighlightTerms(keywords);
  if (terms.length === 0) return text;

  const hashWordTerms = terms.filter((t) => t.includes("#") && !t.includes(" "));
  const plainTerms = terms.filter((t) => !t.includes("#"));

  if (hashWordTerms.length === 0) {
    return highlightWithRegex(text, plainTerms);
  }

  const ranges: Array<[number, number]> = [];

  if (plainTerms.length > 0) {
    const pattern = plainTerms
      .sort((a, b) => b.length - a.length)
      .map(escapeRegExp)
      .join("|");
    const re = new RegExp(`\\b(?:${pattern})\\b`, "gi");
    for (const match of text.matchAll(re)) {
      const start = match.index ?? 0;
      ranges.push([start, start + match[0].length]);
    }
  }

  if (hashWordTerms.length > 0) {
    for (const match of text.matchAll(/\S+/g)) {
      const word = match[0];
      const start = match.index ?? 0;
      if (
        hashWordTerms.some((term) => matchesHashWord(term, word))
      ) {
        ranges.push([start, start + word.length]);
      }
    }
  }

  if (ranges.length === 0) return text;
  return highlightRanges(text, ranges);
}
