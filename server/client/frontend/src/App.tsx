import { createContext, useContext, useEffect, useRef, useState, type FormEvent, type MouseEvent, type ReactNode } from "react";
import { flushSync } from "react-dom";
import {
  ackCentralWinsRefusal,
  addCategoryTerm,
  getAuthMe,
  getCentralWinsRefusals,
  getCentraleNotifications,
  getCentraleStatus,
  getMatrix,
  getSettings,
  getTransactions,
  getYears,
  login,
  logout,
  recalculate,
  recordModification,
  refreshAll,
  refreshPerson,
  setWorkspace,
  updateSettings,
  type CentralWinsAlert,
  type CentraleSyncStatus,
  type SyncNotification,
} from "./api";
import type {
  MatrixResponse,
  PersonInfo,
  RefreshPersonResult,
  SettingsResponse,
  Transaction,
  TransactionsResponse,
} from "./types";

const CHANNEL = "boekhouding";
const REFRESH_STATUS_KEY = "boekhouding-refresh-status";
const BANK_SALDO_CATEGORY = "banksaldo";

function categoryLabel(category: string): string {
  return category === BANK_SALDO_CATEGORY ? "Banksaldo" : category;
}

type HeaderAction = {
  id: string;
  label: string;
  disabled?: boolean;
  href?: string;
  onClick?: () => void;
};

const HeaderActionsContext = createContext<(items: HeaderAction[]) => void>(() => {});

type StoredRefreshStatus = {
  results: RefreshPersonResult[];
  warnings: string[];
};

function loadStoredRefreshStatus(): StoredRefreshStatus | null {
  try {
    const raw = sessionStorage.getItem(REFRESH_STATUS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredRefreshStatus | string;
    // Older builds stored a plain string summary.
    if (typeof parsed === "string") {
      return { results: [], warnings: [parsed] };
    }
    if (parsed && Array.isArray(parsed.results)) {
      return {
        results: parsed.results,
        warnings: Array.isArray(parsed.warnings) ? parsed.warnings : [],
      };
    }
  } catch {
    /* ignore */
  }
  return null;
}

function saveStoredRefreshStatus(payload: StoredRefreshStatus): void {
  try {
    sessionStorage.setItem(REFRESH_STATUS_KEY, JSON.stringify(payload));
  } catch {
    /* ignore */
  }
}

function clearStoredRefreshStatus(): void {
  try {
    sessionStorage.removeItem(REFRESH_STATUS_KEY);
  } catch {
    /* ignore */
  }
}

const REFRESH_BUSY_EVENTS = ["keydown", "keyup", "keypress", "contextmenu"] as const;
const refreshBusyListenerOpts: AddEventListenerOptions = { capture: true };
let refreshBusyBlock: ((e: Event) => void) | null = null;

function blockRefreshBusyEvent(e: Event): void {
  e.preventDefault();
  e.stopPropagation();
}

function beginRefreshBusy(): void {
  document.documentElement.classList.add("refresh-busy");
  if (refreshBusyBlock) return;
  refreshBusyBlock = blockRefreshBusyEvent;
  for (const type of REFRESH_BUSY_EVENTS) {
    window.addEventListener(type, blockRefreshBusyEvent, refreshBusyListenerOpts);
  }
  window.addEventListener("wheel", blockRefreshBusyEvent, { capture: true, passive: false });
}

function endRefreshBusy(): void {
  document.documentElement.classList.remove("refresh-busy");
  if (!refreshBusyBlock) return;
  refreshBusyBlock = null;
  for (const type of REFRESH_BUSY_EVENTS) {
    window.removeEventListener(type, blockRefreshBusyEvent, refreshBusyListenerOpts);
  }
  window.removeEventListener("wheel", blockRefreshBusyEvent, refreshBusyListenerOpts);
}

function afterPaint(fn: () => void): () => void {
  let cancelled = false;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (!cancelled) fn();
    });
  });
  return () => {
    cancelled = true;
  };
}

type CellSelection = { short: string; category: string };

function brandTitle(status: CentraleSyncStatus | null): string {
  const access = (status?.access || "").trim().toLowerCase();
  if (access === "regional" || access === "central") return "Regionale Boekhouding";
  const ws = (status?.author || status?.workspace || "").trim();
  if (access === "personal") {
    const person = (status?.person || "").trim();
    if (ws && person) return `Boekhouding ${ws}/${person}`;
    if (ws) return `Boekhouding ${ws}`;
    return "Boekhouding";
  }
  if (access && access !== "local") {
    // Country access (e.g. netherlands): title-case the country name.
    const country = access.replace(/\b\w/g, (ch) => ch.toUpperCase());
    return `Boekhouding ${country}`;
  }
  // local (default)
  return ws ? `Boekhouding ${ws}` : "Boekhouding";
}

function WorkspaceSwitcher({
  workspace,
  workspaces,
  onSelect,
}: {
  workspace: string;
  workspaces: string[];
  onSelect: (ws: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(ev: Event) {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="workspace-switcher" ref={rootRef}>
      <button
        type="button"
        className="workspace-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="workspace-switcher-chevron" aria-hidden>
          ▾
        </span>
        <span className="workspace-switcher-label">{workspace}</span>
      </button>
      {open && (
        <ul className="workspace-switcher-menu" role="listbox">
          {workspaces.map((ws) => (
            <li key={ws}>
              <button
                type="button"
                role="option"
                aria-selected={ws === workspace}
                className={ws === workspace ? "is-selected" : undefined}
                onClick={() => {
                  setOpen(false);
                  if (ws !== workspace) onSelect(ws);
                }}
              >
                {ws}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function YearSwitcher({
  year,
  years,
  onSelect,
}: {
  year: string;
  years: string[];
  onSelect: (y: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(ev: Event) {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="workspace-switcher" ref={rootRef}>
      <button
        type="button"
        className="workspace-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="workspace-switcher-chevron" aria-hidden>
          ▾
        </span>
        <span className="workspace-switcher-label">{year}</span>
      </button>
      {open && (
        <ul className="workspace-switcher-menu" role="listbox">
          {years.map((y) => (
            <li key={y}>
              <button
                type="button"
                role="option"
                aria-selected={y === year}
                className={y === year ? "is-selected" : undefined}
                onClick={() => {
                  setOpen(false);
                  if (y !== year) onSelect(y);
                }}
              >
                {y}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActionsMenu({ items }: { items: HeaderAction[] }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(ev: Event) {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (items.length === 0) return null;

  return (
    <div className="workspace-switcher actions-menu" ref={rootRef}>
      <button
        type="button"
        className="workspace-switcher-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="workspace-switcher-chevron" aria-hidden>
          ▾
        </span>
        <span className="workspace-switcher-label">menu</span>
      </button>
      {open && (
        <ul className="workspace-switcher-menu" role="menu">
          {items.map((item) => (
            <li key={item.id}>
              {item.href ? (
                <a
                  role="menuitem"
                  className="workspace-switcher-link"
                  href={item.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => setOpen(false)}
                >
                  {item.label}
                </a>
              ) : (
                <button
                  type="button"
                  role="menuitem"
                  disabled={item.disabled}
                  onClick={() => {
                    if (item.disabled) return;
                    setOpen(false);
                    item.onClick?.();
                  }}
                >
                  {item.label}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SyncNotifyShell({
  children,
  onWorkspaceChanged,
  termsView = false,
  authUser = null,
  onLogout,
}: {
  children: (brandName: string, activeYear: string) => ReactNode;
  onWorkspaceChanged?: () => void;
  termsView?: boolean;
  authUser?: string | null;
  onLogout?: () => void;
}) {
  const [status, setStatus] = useState<CentraleSyncStatus | null>(null);
  const [notes, setNotes] = useState<SyncNotification[]>([]);
  const [switching, setSwitching] = useState(false);
  const [activeYear, setActiveYear] = useState<string>("");
  const [yearOptions, setYearOptions] = useState<string[]>([]);
  const [refusal, setRefusal] = useState<CentralWinsAlert | null>(null);
  const [headerActions, setHeaderActions] = useState<HeaderAction[]>([]);
  const dataEpochRef = useRef<number | null>(null);

  const brandName = brandTitle(status);

  useEffect(() => {
    getYears()
      .then((res) => {
        setYearOptions(res.years);
        // Client dropdown shows existing years only; if default year does not
        // exist yet, fall back to the latest existing year.
        if (res.years.includes(res.default_year)) {
          setActiveYear(res.default_year);
        } else if (res.years.length > 0) {
          setActiveYear(res.years[res.years.length - 1]);
        } else {
          setActiveYear(res.default_year);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.workspace]);

  useEffect(() => {
    document.title = termsView ? `${brandName} — Terms` : brandName;
  }, [brandName, termsView]);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getCentraleStatus()
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          const epoch = typeof s.data_epoch === "number" ? s.data_epoch : null;
          if (epoch != null) {
            if (dataEpochRef.current == null) {
              dataEpochRef.current = epoch;
            } else if (epoch > dataEpochRef.current) {
              dataEpochRef.current = epoch;
              onWorkspaceChanged?.();
            }
          }
        })
        .catch(() => {});
      getCentraleNotifications()
        .then((payload) => {
          if (!cancelled) setNotes(payload.notifications || []);
        })
        .catch(() => {});
      getCentralWinsRefusals()
        .then((payload) => {
          if (cancelled) return;
          const next = (payload.alerts || [])[0] || null;
          setRefusal((prev) => {
            if (prev && next && prev.id === next.id) return prev;
            return next;
          });
        })
        .catch(() => {});
    }
    poll();
    const id = window.setInterval(poll, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [onWorkspaceChanged]);

  const isRegionalAdmin =
    status?.role === "regional_admin" || status?.role === "central_admin";
  // Multi-workspace (regional / country): switcher when role allows.
  const workspaces = isRegionalAdmin
    ? status?.workspaces?.length
      ? status.workspaces
      : status?.workspace
        ? [status.workspace]
        : []
    : [];

  function handleSelect(ws: string) {
    if (!isRegionalAdmin) return;
    setSwitching(true);
    setWorkspace(ws)
      .then(() => {
        onWorkspaceChanged?.();
      })
      .catch(() => {})
      .finally(() => setSwitching(false));
  }

  function dismissRefusal() {
    if (!refusal) return;
    const id = refusal.id;
    ackCentralWinsRefusal(id)
      .then((res) => {
        const next = (res.alerts || [])[0] || null;
        setRefusal(next);
        onWorkspaceChanged?.();
      })
      .catch(() => {
        setRefusal(null);
        onWorkspaceChanged?.();
      });
  }

  const showBar =
    Boolean(status?.enabled) || isRegionalAdmin || headerActions.length > 0 || Boolean(onLogout);

  return (
    <HeaderActionsContext.Provider value={setHeaderActions}>
    <div className="lock-shell">
      {showBar && (
        <div className="centrale-status-bar">
          <div className="centrale-status-left">
            {isRegionalAdmin ? (
              <WorkspaceSwitcher
                workspace={status?.workspace || "…"}
                workspaces={workspaces}
                onSelect={handleSelect}
              />
            ) : null}
            {!termsView && activeYear ? (
              <YearSwitcher
                year={activeYear}
                years={yearOptions}
                onSelect={(y) => {
                  setActiveYear(y);
                  onWorkspaceChanged?.();
                }}
              />
            ) : null}
            <ActionsMenu items={headerActions} />
            {authUser ? (
              <span className="auth-user-label">{authUser}</span>
            ) : null}
            {onLogout ? (
              <button type="button" className="logout-btn" onClick={onLogout}>
                Log out
              </button>
            ) : null}
            {switching ? <span className="workspace-switcher-busy">switching…</span> : null}
            {status?.error ? <span> · sync error: {status.error}</span> : null}
          </div>
          <div className="sync-notify-row">
            {notes.map((n) => (
              <button key={`${n.file_path}-${n.expires_at}`} type="button" className="sync-notify-btn">
                {n.file_path}
              </button>
            ))}
          </div>
        </div>
      )}
      {refusal && !isRegionalAdmin && (
        <div className="central-wins-overlay" role="alertdialog" aria-modal="true">
          <div className="central-wins-dialog">
            <p>{refusal.message}</p>
            {refusal.path ? <p className="central-wins-path">{refusal.path}</p> : null}
            <button type="button" className="central-wins-ok" onClick={dismissRefusal}>
              OK
            </button>
          </div>
        </div>
      )}
      {children(brandName, activeYear)}
    </div>
    </HeaderActionsContext.Provider>
  );
}

const AIB_HISTORICAL_YEARS = 2;

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function historicalStartDate(ref: Date = new Date()): string {
  const start = new Date(ref);
  start.setFullYear(start.getFullYear() - AIB_HISTORICAL_YEARS);
  return isoDate(start);
}

function previousMonthRange(): { from: string; to: string } {
  const today = new Date();
  const firstThisMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const lastPrev = new Date(firstThisMonth);
  lastPrev.setDate(0);
  const firstPrev = new Date(lastPrev.getFullYear(), lastPrev.getMonth(), 1);
  return { from: isoDate(firstPrev), to: isoDate(lastPrev) };
}

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
  const name = target === "terms" ? "boekhouding-terms" : "boekhouding-main";
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

export default function App() {
  const isTerms = new URLSearchParams(window.location.search).get("view") === "terms";
  const [wsEpoch, setWsEpoch] = useState(0);
  const [authRequired, setAuthRequired] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [authUser, setAuthUser] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAuthMe()
      .then((me) => {
        if (cancelled) return;
        setAuthRequired(me.auth_required);
        setAuthenticated(me.authenticated);
        setAuthUser(me.username);
        setAuthChecked(true);
      })
      .catch(() => {
        if (cancelled) return;
        setAuthRequired(false);
        setAuthenticated(true);
        setAuthChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!authChecked) {
    return <div className="login-screen"><p className="login-muted">Loading…</p></div>;
  }

  if (authRequired && !authenticated) {
    return (
      <LoginScreen
        onSuccess={(username) => {
          setAuthenticated(true);
          setAuthUser(username);
          setWsEpoch((n) => n + 1);
        }}
      />
    );
  }

  return (
    <SyncNotifyShell
      termsView={isTerms}
      authUser={authRequired ? authUser : null}
      onLogout={
        authRequired
          ? () => {
              logout()
                .then(() => {
                  setAuthenticated(false);
                  setAuthUser(null);
                })
                .catch(() => {
                  setAuthenticated(false);
                  setAuthUser(null);
                });
            }
          : undefined
      }
      onWorkspaceChanged={() => setWsEpoch((n) => n + 1)}
    >
      {(brandName, year) =>
        isTerms ? (
          <TermsApp key={wsEpoch} />
        ) : (
          <MainApp key={wsEpoch} brandName={brandName} year={year} />
        )
      }
    </SyncNotifyShell>
  );
}

function LoginScreen({ onSuccess }: { onSuccess: (username: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    login(username.trim(), password)
      .then(() => onSuccess(username.trim()))
      .catch((err: Error) => {
        setError(err.message.includes("401") ? "Invalid username or password" : err.message);
      })
      .finally(() => setBusy(false));
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <h1 className="login-title">Boekhouding</h1>
        <p className="login-muted">Sign in to continue</p>
        <label className="login-label">
          Username
          <input
            className="login-input"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="login-label">
          Password
          <input
            className="login-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
            required
          />
        </label>
        {error ? <p className="login-error">{error}</p> : null}
        <button className="login-submit" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function MainApp({ brandName, year }: { brandName: string; year: string }) {
  const [matrix, setMatrix] = useState<MatrixResponse | null>(null);
  const [selection, setSelection] = useState<CellSelection | null>(null);
  const [detail, setDetail] = useState<TransactionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchingShort, setFetchingShort] = useState<string | null>(null);
  const [personNewYear, setPersonNewYear] = useState<Record<string, boolean>>({});
  const [consentReady, setConsentReady] = useState<Record<string, boolean>>({});
  const [refreshStatus, setRefreshStatus] = useState<StoredRefreshStatus | null>(() =>
    loadStoredRefreshStatus()
  );
  const [hasSecrets, setHasSecrets] = useState(false);
  const [canAddPerson, setCanAddPerson] = useState(false);
  const [addPersonUrl, setAddPersonUrl] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState(() => previousMonthRange().from);
  const [dateTo, setDateTo] = useState(() => previousMonthRange().to);
  const prevMonth = previousMonthRange();
  const [termMenu, setTermMenu] = useState<{
    term: string;
    x: number;
    y: number;
  } | null>(null);
  const [termMenuSettings, setTermMenuSettings] = useState<SettingsResponse | null>(null);
  const selectionRef = useRef<CellSelection | null>(null);
  const dirtyRef = useRef(false);

  useEffect(() => {
    getCentraleStatus()
      .then((s) => {
        setHasSecrets(Boolean(s.has_secrets));
        const scoped = Boolean((s.person || "").trim());
        setCanAddPerson(!scoped);
        const hub = (s.centrale_url || "").replace(/\/$/, "");
        const ws = (s.workspace || "").trim();
        if (hub && ws) {
          setAddPersonUrl(`${hub}/add-person?workspace=${encodeURIComponent(ws)}`);
        } else if (hub) {
          setAddPersonUrl(`${hub}/add-person`);
        } else {
          setAddPersonUrl(null);
        }
      })
      .catch(() => {
        setHasSecrets(false);
        setCanAddPerson(false);
        setAddPersonUrl(null);
      });
  }, []);

  useEffect(() => {
    const awaitingAuth = (refreshStatus?.results || []).some(
      (r) => r.skipped && r.reason === "needs_consent_renewal"
    );
    if (!awaitingAuth) {
      setConsentReady({});
      return;
    }
    let cancelled = false;
    function pollReady() {
      getCentraleStatus()
        .then((s) => {
          if (cancelled) return;
          const next: Record<string, boolean> = {};
          for (const item of s.consent_ready || []) {
            const short = (item.short || "").trim();
            if (short) next[short] = true;
          }
          setConsentReady(next);
        })
        .catch(() => {});
    }
    pollReady();
    const id = window.setInterval(pollReady, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [refreshStatus]);

  useEffect(() => {
    selectionRef.current = selection;
  }, [selection]);

  useEffect(() => {
    return () => endRefreshBusy();
  }, []);

  function markDirty() {
    dirtyRef.current = true;
  }

  function clearDirty() {
    dirtyRef.current = false;
  }

  function loadDetail(short: string, category: string): Promise<void> {
    setDetail(null);
    return getTransactions(short, category)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }

  function loadDisplay(sel: CellSelection | null): Promise<void> {
    setError(null);
    return getMatrix(year)
      .then((payload) => {
        setMatrix(payload);
        if (!sel) {
          setDetail(null);
          return;
        }
        return loadDetail(sel.short, sel.category);
      })
      .catch((e: Error) => setError(e.message));
  }

  function refreshMainView(sel: CellSelection | null): Promise<void> {
    setError(null);
    if (sel) setDetail(null);
    return recalculate()
      .then((payload) => {
        clearDirty();
        setMatrix(payload);
        if (!sel) {
          setDetail(null);
          return;
        }
        return getTransactions(sel.short, sel.category).then(setDetail);
      })
      .catch((e: Error) => setError(e.message));
  }

  function applyIfDirty(sel: CellSelection | null): Promise<void> {
    if (!dirtyRef.current) return Promise.resolve();
    return refreshMainView(sel);
  }

  function loadMatrixOnly() {
    return loadDisplay(null);
  }

  useEffect(() => {
    window.name = "boekhouding-main";
    setSelection(null);
    setDetail(null);
    setError(null);
    void loadMatrixOnly();
  }, [year]);

  useEffect(() => {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (e) => {
      if (e.data === "recalculated") {
        clearDirty();
        void loadDisplay(selectionRef.current);
      }
    };
    const onFocus = () => {
      void applyIfDirty(selectionRef.current);
    };
    window.addEventListener("focus", onFocus);
    return () => {
      channel.close();
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  function selectCell(short: string, category: string) {
    if (category === BANK_SALDO_CATEGORY) return;
    const sel = { short, category };
    setSelection(sel);
    setError(null);
    if (dirtyRef.current) {
      void refreshMainView(sel);
      return;
    }
    void loadDetail(short, category);
  }

  function backToMatrix() {
    setSelection(null);
    setDetail(null);
    setError(null);
  }

  function modifyTransaction(modified: Transaction) {
    if (!selection) return;
    recordModification(selection.short, modified)
      .then(() => {
        markDirty();
        return refreshMainView(selection);
      })
      .catch((e: Error) => setError(e.message));
  }

  function openTermMenu(e: MouseEvent, cellText: string) {
    e.preventDefault();
    e.stopPropagation();
    setError(null);
    const word = wordAtClick(e.currentTarget, e.clientX, e.clientY) || cellText.trim();
    if (!word) return;
    getSettings()
      .then((settings) => {
        setTermMenuSettings(settings);
        setTermMenu({ term: word, x: e.clientX, y: e.clientY });
      })
      .catch((err: Error) => setError(err.message));
  }

  function closeTermMenu() {
    setTermMenu(null);
    setTermMenuSettings(null);
  }

  function saveTermMenu(term: string, targetCategory: string, general: boolean) {
    const short = selectionRef.current?.short;
    if (!general && !short) return Promise.resolve();
    return addCategoryTerm({
      category_name: targetCategory,
      term,
      general,
      person: general ? undefined : short,
    })
      .then((res) => {
        closeTermMenu();
        clearDirty();
        setMatrix(res.matrix);
        const sel = selectionRef.current;
        if (sel) return loadDetail(sel.short, sel.category);
      })
      .catch((err: Error) => setError(err.message));
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

  function doRefresh() {
    if (refreshing || fetchingShort) return;
    beginRefreshBusy();
    flushSync(() => {
      setRefreshing(true);
      setError(null);
      setRefreshStatus(null);
      setPersonNewYear({});
      setConsentReady({});
    });
    clearStoredRefreshStatus();
    afterPaint(() => {
      refreshAll({
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      })
        .then((res) => {
          setMatrix(res.matrix);
          const payload: StoredRefreshStatus = {
            results: res.results || [],
            warnings: res.warnings || [],
          };
          saveStoredRefreshStatus(payload);
          setRefreshStatus(payload);
          setSelection(null);
          setDetail(null);
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => {
          setRefreshing(false);
          endRefreshBusy();
        });
    });
  }

  function doRefreshPerson(short: string) {
    if (refreshing || fetchingShort) return;
    beginRefreshBusy();
    flushSync(() => {
      setFetchingShort(short);
      setError(null);
    });
    afterPaint(() => {
      refreshPerson(short, {
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        new_year: Boolean(personNewYear[short]),
      })
        .then((res) => {
          setMatrix(res.matrix);
          const nextResult = (res.results || [])[0];
          const prev = refreshStatus || { results: [], warnings: [] };
          const results = nextResult
            ? [
                ...prev.results.filter((r) => r.short !== short),
                nextResult,
              ]
            : prev.results.filter((r) => r.short !== short);
          const warnings = [
            ...prev.warnings.filter((w) => !w.startsWith(`${short}:`) && !w.startsWith(`${short} (`)),
            ...(res.warnings || []),
          ];
          const payload: StoredRefreshStatus = { results, warnings };
          saveStoredRefreshStatus(payload);
          setRefreshStatus(payload);
          setSelection(null);
          setDetail(null);
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => {
          setFetchingShort(null);
          endRefreshBusy();
        });
    });
  }

  const awaitingPostConsentFetch = (refreshStatus?.results || []).some(
    (r) =>
      r.skipped &&
      r.reason === "needs_consent_renewal" &&
      Boolean(consentReady[r.short])
  );

  const setHeaderActions = useContext(HeaderActionsContext);
  useEffect(() => {
    const items: HeaderAction[] = [];
    if (hasSecrets && !awaitingPostConsentFetch) {
      items.push({
        id: "refresh",
        label: refreshing ? "Refreshing…" : "↻ Refresh all",
        disabled: refreshing || Boolean(fetchingShort),
        onClick: doRefresh,
      });
    }
    if (canAddPerson && addPersonUrl) {
      items.push({
        id: "add-person",
        label: "Add person",
        href: addPersonUrl,
      });
    }
    items.push({
      id: "terms",
      label: "⚙ Edit Terms (Alt+T)",
      onClick: () => openView("terms"),
    });
    setHeaderActions(items);
    return () => setHeaderActions([]);
  }, [
    hasSecrets,
    awaitingPostConsentFetch,
    refreshing,
    fetchingShort,
    canAddPerson,
    addPersonUrl,
    setHeaderActions,
  ]);

  const inPView = selection !== null;

  return (
    <div className="app">
      <aside className="sidebar">
        <h1 className="app-heading">{brandName}</h1>

        <div
          className={
            awaitingPostConsentFetch ? "fetch-form fetch-form--person-scope" : "fetch-form"
          }
        >
          {hasSecrets && (
            <>
              <label className="sidebar-field">
                <span className="sidebar-field-legend">date-from</span>
                <input
                  type="date"
                  value={dateFrom}
                  placeholder={prevMonth.from}
                  min={historicalStartDate()}
                  onChange={(e) => setDateFrom(e.target.value)}
                />
              </label>
              <label className="sidebar-field">
                <span className="sidebar-field-legend">date-to</span>
                <input
                  type="date"
                  value={dateTo}
                  placeholder={prevMonth.to}
                  min={historicalStartDate()}
                  max={isoDate(new Date())}
                  onChange={(e) => setDateTo(e.target.value)}
                />
              </label>
              {refreshStatus && (
                <div className="refresh-status">
                  {(refreshStatus.results || [])
                    .filter((r) => {
                      if (!awaitingPostConsentFetch) return true;
                      return (
                        r.skipped &&
                        r.reason === "needs_consent_renewal" &&
                        Boolean(consentReady[r.short])
                      );
                    })
                    .map((r) => (
                    <div key={r.short} className="refresh-status-line">
                      {r.skipped ? (
                        r.reason === "needs_consent_renewal" ? (
                          <>
                            {!awaitingPostConsentFetch ? (
                              <span>
                                {r.short}: consent renewal required
                                {!r.authorization_url && !consentReady[r.short]
                                  ? " (no authorization URL)"
                                  : ""}
                              </span>
                            ) : null}
                            {!consentReady[r.short] && r.authorization_url ? (
                              <div className="sidebar-field">
                                <span className="sidebar-field-legend" aria-hidden="true">
                                  {"\u00a0"}
                                </span>
                                <a
                                  className="sidebar-knob"
                                  href={r.authorization_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {`renew ${r.short}`}
                                </a>
                              </div>
                            ) : null}
                            {consentReady[r.short] ? (
                              <>
                                <label className="refresh-person-newyear">
                                  <input
                                    type="checkbox"
                                    checked={Boolean(personNewYear[r.short])}
                                    disabled={Boolean(refreshing || fetchingShort)}
                                    onChange={(e) =>
                                      setPersonNewYear((prev) => ({
                                        ...prev,
                                        [r.short]: e.target.checked,
                                      }))
                                    }
                                  />
                                  new year (overwrite {r.short} only)
                                </label>
                                <div className="sidebar-field">
                                  <span className="sidebar-field-legend" aria-hidden="true">
                                    {"\u00a0"}
                                  </span>
                                  <button
                                    type="button"
                                    className="sidebar-knob"
                                    disabled={Boolean(refreshing || fetchingShort)}
                                    onClick={() => doRefreshPerson(r.short)}
                                  >
                                    {fetchingShort === r.short
                                      ? `Fetching ${r.short}…`
                                      : `fetch for ${r.short}`}
                                  </button>
                                </div>
                              </>
                            ) : null}
                          </>
                        ) : (
                          <span>
                            {r.short}: skipped
                            {r.reason ? ` (${r.reason})` : ""}
                          </span>
                        )
                      ) : (
                        <span>
                          {r.short}: {r.transaction_count ?? 0} transaction
                          {(r.transaction_count ?? 0) === 1 ? "" : "s"}
                          {r.date_from && r.date_to
                            ? ` (${r.date_from} .. ${r.date_to})`
                            : ""}
                          {r.new_year ? " — new year overwrite" : ""}
                        </span>
                      )}
                      {!awaitingPostConsentFetch
                        ? (r.warnings || []).map((w) => (
                            <div key={`${r.short}-w-${w}`} className="refresh-status-note">
                              {r.short}: {w}
                            </div>
                          ))
                        : null}
                      {!awaitingPostConsentFetch
                        ? (r.account_errors || []).map((w) => (
                            <div key={`${r.short}-e-${w}`} className="refresh-status-note">
                              {r.short}: {w}
                            </div>
                          ))
                        : null}
                    </div>
                  ))}
                  {!awaitingPostConsentFetch
                    ? (refreshStatus.warnings || []).map((w) => (
                        <div key={w} className="refresh-status-note">
                          {w}
                        </div>
                      ))
                    : null}
                  {!awaitingPostConsentFetch &&
                  !refreshStatus.results?.length &&
                  !refreshStatus.warnings?.length ? (
                    <div>Refreshed (no person results)</div>
                  ) : null}
                </div>
              )}
            </>
          )}
        </div>

        {inPView && matrix && (
          <>
            <div className="winbar">
              <div className="sidebar-field">
                <span className="sidebar-field-legend" aria-hidden="true">
                  {"\u00a0"}
                </span>
                <button type="button" className="sidebar-knob" onClick={backToMatrix}>
                  ← Matrix
                </button>
              </div>
            </div>
            <PersonColumnTable
              matrix={matrix}
              personShort={selection.short}
              selectedCategory={selection.category}
              onPick={(category) => selectCell(selection.short, category)}
            />
          </>
        )}
      </aside>

      <main className="content">
        {error && <p className="error">{error}</p>}
        {!inPView && !matrix && !error && <p>Loading…</p>}
        {!inPView && matrix && (
          <MatrixTable matrix={matrix} selection={selection} onPick={selectCell} />
        )}
        {inPView && !detail && !error && <p>Loading…</p>}
        {inPView && detail && (
          <PTable
            categoryName={selection.category}
            detail={detail}
            onModify={modifyTransaction}
            onCategoryError={setError}
            onTermContextMenu={openTermMenu}
          />
        )}
        {termMenu && termMenuSettings && (
          <TermContextMenu
            settings={termMenuSettings}
            initialTerm={termMenu.term}
            x={termMenu.x}
            y={termMenu.y}
            onClose={closeTermMenu}
            onPickCategory={saveTermMenu}
          />
        )}
      </main>
    </div>
  );
}

function TermsApp() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const channelRef = useRef<BroadcastChannel | null>(null);

  useEffect(() => {
    window.name = "boekhouding-terms";
    getSettings()
      .then(setSettings)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const channel = new BroadcastChannel(CHANNEL);
    channelRef.current = channel;
    return () => {
      channelRef.current = null;
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
      .then((res) => {
        setSettings((prev) => {
          if (!prev) return prev;
          const nextTerms = res.terms ?? terms;
          if (group === "general") {
            return { ...prev, general: { ...prev.general, [category]: nextTerms } };
          }
          const personGroup = { ...(prev.personal[group] ?? {}) };
          if (nextTerms.length) personGroup[category] = nextTerms;
          else delete personGroup[category];
          return { ...prev, personal: { ...prev.personal, [group]: personGroup } };
        });
        // Hub already recalculated; refresh the main window immediately.
        channelRef.current?.postMessage("recalculated");
      })
      .catch((e: Error) => setError(e.message));
  }

  return (
    <div className="app terms-app">
      <aside className="sidebar">
        <div className="winbar">
          <div className="sidebar-field">
            <span className="sidebar-field-legend" aria-hidden="true">
              {"\u00a0"}
            </span>
            <button type="button" className="sidebar-knob" onClick={() => openView("main")}>
              Matrix (Alt+M)
            </button>
          </div>
        </div>
        <p className="win-hint">
          Term Window. Return to overview using <kbd>Ctrl</kbd>+<kbd>Tab</kbd> or{" "}
          <kbd>Alt</kbd>+<kbd>M</kbd>. Edits save and recalculate immediately.{" "}
          {settings ? formatTermMatchHint(settings.typerules) : ""}
        </p>
      </aside>
      <main className="content terms-content">
        {error && <p className="error">{error}</p>}
        {settings ? (
          <TermsTables settings={settings} onUpdate={updateTerms} />
        ) : (
          <p>Loading…</p>
        )}
      </main>
    </div>
  );
}

function MatrixTable({
  matrix,
  selection,
  onPick,
}: {
  matrix: MatrixResponse;
  selection: CellSelection | null;
  onPick: (short: string, category: string) => void;
}) {
  const { categories, people, cells } = matrix;
  return (
    <table className="totals-table matrix-table">
      <thead>
        <tr>
          <th className="cat">Category</th>
          {people.map((p) => (
            <th key={p.short} className="num">
              {p.short}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {categories.map((cat) => (
          <tr
            key={cat}
            className={`${selection?.category === cat ? "active" : ""}${cat === BANK_SALDO_CATEGORY ? " banksaldo-row" : ""}`}
          >
            <td className="cat">{categoryLabel(cat)}</td>
            {people.map((p) => {
              const amount = cells[cat]?.[p.short] ?? "";
              const isActive =
                selection?.short === p.short && selection?.category === cat;
              const clickable = cat !== BANK_SALDO_CATEGORY && amount !== "";
              return (
                <td
                  key={p.short}
                  className={`num${clickable ? " clickable" : ""}${isActive ? " active-cell" : ""}`}
                  onClick={clickable ? () => onPick(p.short, cat) : undefined}
                >
                  {amount === "" ? "" : `€${amount}`}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PersonColumnTable({
  matrix,
  personShort,
  selectedCategory,
  onPick,
}: {
  matrix: MatrixResponse;
  personShort: string;
  selectedCategory: string | null;
  onPick: (category: string) => void;
}) {
  const { categories, cells } = matrix;
  return (
    <table className="totals-table">
      <thead>
        <tr>
          <th className="cat">Category</th>
          <th className="num">{personShort}</th>
        </tr>
      </thead>
      <tbody>
        {categories.map((cat) => (
          <tr
            key={cat}
            className={`${cat === selectedCategory ? "active" : ""}${cat === BANK_SALDO_CATEGORY ? " banksaldo-row" : ""}`}
          >
            <td className="cat">{categoryLabel(cat)}</td>
            {(() => {
              const amount = cells[cat]?.[personShort] ?? "";
              const clickable = cat !== BANK_SALDO_CATEGORY && amount !== "";
              return (
                <td
                  className={`num${clickable ? " clickable" : ""}`}
                  onClick={clickable ? () => onPick(cat) : undefined}
                >
                  {amount === "" ? "" : `€${amount}`}
                </td>
              );
            })()}
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
  onTermContextMenu,
}: {
  categoryName: string;
  detail: TransactionsResponse;
  onModify: (transaction: Transaction) => void;
  onCategoryError?: (message: string | null) => void;
  onTermContextMenu?: (e: MouseEvent, cellText: string) => void;
}) {
  const transactions = Array.isArray(detail.transactions) ? detail.transactions : [];
  const keywords = Array.isArray(detail.keywords) ? detail.keywords : [];
  const validCategoryCodes = new Set(detail.valid_category_codes ?? []);
  const descriptionModified = new Set(detail.description_modified_ids ?? []);
  const categoryModified = new Set(detail.category_modified_ids ?? []);
  const columns =
    Array.isArray(detail.columns) && detail.columns.length > 0
      ? detail.columns
      : ptableColumns(transactions);

  function safeHighlight(text: string): ReactNode {
    try {
      return highlight(text, keywords);
    } catch {
      return text;
    }
  }

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
        <td
          key={column}
          className="name term-source"
          onContextMenu={
            onTermContextMenu ? (e) => onTermContextMenu(e, text) : undefined
          }
        >
          {safeHighlight(text)}
        </td>
      );
    }
    if (column === "description") {
      const text = formatCell(t.description);
      return (
        <td
          key={column}
          className="desc term-source"
          onContextMenu={
            onTermContextMenu ? (e) => onTermContextMenu(e, text) : undefined
          }
        >
          <EditableField
            value={text}
            display={safeHighlight(text)}
            onCommit={(v) => onModify({ ...t, description: v })}
          />
        </td>
      );
    }
    if (column === "category") {
      const catModified = categoryModified.has(String(t.id));
      return (
        <td key={column} className={`num${catModified ? " category-modified" : ""}`}>
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
      {transactions.length === 0 ? (
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
            {transactions.map((t, i) => {
              const descModified = descriptionModified.has(String(t.id));
              return (
                <tr key={i} className={descModified ? "modified" : undefined}>
                  {columns.map((c) => renderCell(t, c))}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TermContextMenu({
  settings,
  initialTerm,
  x,
  y,
  onClose,
  onPickCategory,
}: {
  settings: SettingsResponse;
  initialTerm: string;
  x: number;
  y: number;
  onClose: () => void;
  onPickCategory: (
    term: string,
    targetCategory: string,
    general: boolean
  ) => void | Promise<void>;
}) {
  const [term, setTerm] = useState(initialTerm);
  const [saving, setSaving] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ left: x, top: y });

  const categories = settings.categories.filter(
    (name) => name !== settings.remainder_category
  );

  useEffect(() => {
    setTerm(initialTerm);
  }, [initialTerm]);

  useEffect(() => {
    const el = menuRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pad = 8;
    setPos({
      left: Math.min(x, window.innerWidth - rect.width - pad),
      top: Math.min(y, window.innerHeight - rect.height - pad),
    });
  }, [x, y, categories.length, initialTerm]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function pick(category: string, general: boolean) {
    const cleaned = term.trim();
    if (!cleaned || saving) return;
    setSaving(true);
    Promise.resolve(onPickCategory(cleaned, category, general)).finally(() =>
      setSaving(false)
    );
  }

  return (
    <div
      className="term-context-backdrop"
      onClick={onClose}
      onContextMenu={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div
        ref={menuRef}
        className="term-context-menu"
        style={{ left: Math.max(8, pos.left), top: Math.max(8, pos.top) }}
        role="menu"
        onClick={(e) => e.stopPropagation()}
        onContextMenu={(e) => e.preventDefault()}
      >
        <input
          className="term-context-title"
          value={term}
          autoFocus
          spellCheck={false}
          onChange={(e) => setTerm(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onClose();
            }
          }}
        />
        <div className="term-context-table-wrap">
          <table className="term-context-table">
            <thead>
              <tr>
                <th className="term-context-cat-head">Category</th>
                <th className="term-context-gp-head" title="General">
                  G
                </th>
                <th className="term-context-gp-head" title="Personal">
                  P
                </th>
              </tr>
            </thead>
            <tbody>
              {categories.map((name) => (
                <tr key={name}>
                  <td className="term-context-cat">{name}</td>
                  <td className="term-context-gp">
                    <input
                      type="checkbox"
                      aria-label={`${name} general`}
                      disabled={saving || !term.trim()}
                      onChange={() => pick(name, true)}
                    />
                  </td>
                  <td className="term-context-gp">
                    <input
                      type="checkbox"
                      aria-label={`${name} personal`}
                      disabled={saving || !term.trim()}
                      onChange={() => pick(name, false)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button
          type="button"
          className="term-context-item term-context-cancel"
          role="menuitem"
          onClick={onClose}
          disabled={saving}
        >
          cancel
        </button>
      </div>
    </div>
  );
}

function charOffsetFromPoint(root: EventTarget & Element, clientX: number, clientY: number): number | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (
      x: number,
      y: number
    ) => { offsetNode: Node; offset: number } | null;
  };
  let node: Node | null = null;
  let offset = 0;
  if (typeof doc.caretRangeFromPoint === "function") {
    const range = doc.caretRangeFromPoint(clientX, clientY);
    if (!range) return null;
    node = range.startContainer;
    offset = range.startOffset;
  } else if (typeof doc.caretPositionFromPoint === "function") {
    const pos = doc.caretPositionFromPoint(clientX, clientY);
    if (!pos) return null;
    node = pos.offsetNode;
    offset = pos.offset;
  } else {
    return null;
  }
  if (!root.contains(node)) return null;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let total = 0;
  let current: Node | null;
  while ((current = walker.nextNode())) {
    if (current === node) return total + offset;
    total += (current.textContent || "").length;
  }
  return null;
}

function wordAtIndex(text: string, index: number): string {
  if (!text) return "";
  if (index < 0) index = 0;
  if (index >= text.length) index = text.length - 1;
  if (index < 0) return "";

  const isWordChar = (ch: string) => /[0-9A-Za-zÀ-ÿ_&-]/.test(ch);
  if (!isWordChar(text[index])) {
    let left = index - 1;
    while (left >= 0 && !isWordChar(text[left])) left--;
    if (left < 0) return "";
    index = left;
  }
  let start = index;
  let end = index + 1;
  while (start > 0 && isWordChar(text[start - 1])) start--;
  while (end < text.length && isWordChar(text[end])) end++;
  return text.slice(start, end);
}

function wordAtClick(root: EventTarget, clientX: number, clientY: number): string {
  if (!(root instanceof Element)) return "";
  const text = root.textContent || "";
  const offset = charOffsetFromPoint(root, clientX, clientY);
  if (offset === null) return text.trim();
  return wordAtIndex(text, offset);
}

function termsTableCategories(settings: SettingsResponse): string[] {
  return settings.categories.filter((name) => name !== settings.remainder_category);
}

function termsColumnWidths(
  columns: string[],
  general: Record<string, string[]>,
  personal: Record<string, Record<string, string[]>>,
  people: PersonInfo[]
): number[] {
  const measure = (text: string) => Math.max(text.length, 1);

  return columns.map((category) => {
    const texts = [category, ...(general[category] ?? []), "+ term"];
    for (const p of people) {
      texts.push(...(personal[p.short]?.[category] ?? []));
    }
    const maxChars = Math.max(...texts.map(measure));
    return Math.ceil(maxChars * 7.5 + 20);
  });
}

/** Stable empty list so `?? EMPTY_TERMS` does not allocate a new [] every render. */
const EMPTY_TERMS: string[] = [];

function TermsTables({
  settings,
  onUpdate,
}: {
  settings: SettingsResponse;
  onUpdate: (group: string, category: string, terms: string[]) => void;
}) {
  const { people, general, personal } = settings;
  const columns = termsTableCategories(settings);
  const columnWidths = termsColumnWidths(columns, general, personal, people);

  return (
    <div className="terms-scroll">
      <div className="terms-panels">
        <section className="terms-panel terms-panel-general" aria-label="General terms">
          <h2 className="terms-panel-label">General</h2>
          <TermsColumnTable
            columns={columns}
            columnWidths={columnWidths}
            termsForCategory={(name) => general[name] ?? EMPTY_TERMS}
            onCommit={(name, terms) => onUpdate("general", name, terms)}
          />
        </section>
        {people.map((p) => (
          <section
            key={p.short}
            className="terms-panel terms-panel-personal"
            aria-label={`${p.short} terms`}
          >
            <h2 className="terms-panel-label">{p.short}</h2>
            <TermsColumnTable
              columns={columns}
              columnWidths={columnWidths}
              termsForCategory={(name) => personal[p.short]?.[name] ?? EMPTY_TERMS}
              onCommit={(name, terms) => onUpdate(p.short, name, terms)}
            />
          </section>
        ))}
      </div>
    </div>
  );
}

function TermsColumnTable({
  columns,
  columnWidths,
  termsForCategory,
  onCommit,
}: {
  columns: string[];
  columnWidths: number[];
  termsForCategory: (category: string) => string[];
  onCommit: (category: string, terms: string[]) => void;
}) {
  return (
    <table className="s-table s-table-terms">
      <colgroup>
        {columns.map((name, index) => (
          <col key={name} style={{ width: `${columnWidths[index]}px` }} />
        ))}
      </colgroup>
      <thead>
        <tr>
          {columns.map((name) => (
            <th key={name} title={name}>
              {name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        <tr>
          {columns.map((name) => (
            <td key={name}>
              <EditableCell
                terms={termsForCategory(name)}
                onCommit={(terms) => onCommit(name, terms)}
              />
            </td>
          ))}
        </tr>
      </tbody>
    </table>
  );
}

function sortTerms(values: string[]): string[] {
  return [...values].sort((a, b) => a.localeCompare(b));
}

function EditableCell({
  terms,
  onCommit,
}: {
  terms: string[];
  onCommit: (terms: string[]) => void;
}) {
  const [draft, setDraft] = useState<string[]>(() => sortTerms(terms));
  const [add, setAdd] = useState("");
  const draftRef = useRef(draft);
  draftRef.current = draft;
  // Sync from props only when content changes. A new `[]` every parent render
  // (empty categories + 1s status poll) used to clear the "+ term" field mid-typing.
  const termsKey = terms.join("\0");
  const prevTermsKey = useRef(termsKey);

  useEffect(() => {
    if (prevTermsKey.current === termsKey) return;
    prevTermsKey.current = termsKey;
    const next = sortTerms(terms);
    draftRef.current = next;
    setDraft(next);
    setAdd("");
  }, [terms, termsKey]);

  function commit(next: string[]) {
    const cleaned = sortTerms(next.map((t) => t.trim()).filter(Boolean));
    const current = sortTerms(terms.map((t) => t.trim()).filter(Boolean));
    if (!arraysEqual(cleaned, current)) onCommit(cleaned);
  }

  function removeAt(index: number) {
    commit(draftRef.current.filter((_, idx) => idx !== index));
  }

  function commitAdd() {
    const t = add.trim();
    if (!t) return;
    commit([...draftRef.current, t]);
    setAdd("");
  }

  return (
    <div className="terms">
      {draft.map((term, i) => (
        <div key={i} className="term-row">
          <input
            className="term-input"
            value={term}
            onChange={(e) => {
              const value = e.target.value;
              setDraft((d) => {
                const next = d.map((t, idx) => (idx === i ? value : t));
                draftRef.current = next;
                return next;
              });
            }}
            onBlur={() => commit(draftRef.current)}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
          />
          <button
            type="button"
            className="term-delete"
            title="Delete term"
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
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
  const hidden = new Set(["id", "currency"]);
  const columns: string[] = [];
  for (const t of transactions) {
    for (const key of Object.keys(t)) {
      if (!hidden.has(key) && !columns.includes(key)) {
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
      if (hashWordTerms.some((term) => matchesHashWord(term, word))) {
        ranges.push([start, start + word.length]);
      }
    }
  }

  if (ranges.length === 0) return text;
  return highlightRanges(text, ranges);
}
