import type {
  AddTermResponse,
  MatrixResponse,
  RefreshResponse,
  SettingsResponse,
  TermsUpdateResponse,
  Transaction,
  TransactionsResponse,
} from "./types";

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url, { credentials: "include" });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  return (await resp.json()) as T;
}

async function sendJson<T>(
  url: string,
  method: "PUT" | "POST" | "PATCH",
  body: unknown
): Promise<T> {
  const resp = await fetch(url, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  return (await resp.json()) as T;
}

export interface YearsResponse {
  years: string[];
  default_year: string;
}

export function getYears(): Promise<YearsResponse> {
  return getJson("/api/years");
}

export function getMatrix(year?: string): Promise<MatrixResponse> {
  return year ? getJson(`/api/matrix?year=${encodeURIComponent(year)}`) : getJson("/api/matrix");
}

export function recalculate(): Promise<MatrixResponse> {
  return sendJson("/api/recalculate", "POST", {});
}

export function refreshAll(body: {
  date_from?: string;
  date_to?: string;
} = {}): Promise<RefreshResponse> {
  return sendJson("/api/refresh", "POST", body);
}

export function refreshPerson(
  short: string,
  body: {
    date_from?: string;
    date_to?: string;
    new_year?: boolean;
  } = {}
): Promise<RefreshResponse> {
  return sendJson(`/api/refresh/${encodeURIComponent(short)}`, "POST", body);
}

export function getTransactions(
  short: string,
  category: string
): Promise<TransactionsResponse> {
  return getJson(
    `/api/transactions/${encodeURIComponent(short)}/${encodeURIComponent(category)}`
  );
}

export function getSettings(): Promise<SettingsResponse> {
  return getJson("/api/settings");
}

export function updateSettings(
  group: string,
  category: string,
  terms: string[]
): Promise<TermsUpdateResponse> {
  return sendJson(
    `/api/settings/${encodeURIComponent(group)}/${encodeURIComponent(category)}`,
    "PUT",
    { terms }
  );
}

export function addCategoryTerm(body: {
  category_name: string;
  term: string;
  general: boolean;
  person?: string;
}): Promise<AddTermResponse> {
  return sendJson("/api/settings/add-term", "POST", body);
}

export function recordModification(
  short: string,
  transaction: Transaction
): Promise<unknown> {
  return sendJson(`/api/transactions/${encodeURIComponent(short)}/modification`, "PUT", {
    transaction,
  });
}

export interface SyncNotification {
  file_path: string;
  expires_at: number;
}

export interface ConsentReadyPerson {
  short: string;
  folder?: string;
}

export interface CentraleSyncStatus {
  enabled: boolean;
  workspace: string;
  /** Fixed identity from client_config ``workspace`` (does not follow switcher). */
  author?: string;
  /** regional | local | personal | country — from client_config ``access``. */
  access?: string;
  /** Empty / omitted = all people; otherwise only this short is visible. */
  person?: string;
  username?: string;
  auth_required?: boolean;
  centrale_url: string;
  local_session_active: boolean;
  error: string | null;
  last_event_id?: number;
  notifications?: SyncNotification[];
  port?: number;
  role?: "local" | "regional_admin" | "central_admin";
  workspaces?: string[];
  data_epoch?: number;
  has_secrets?: boolean;
  /** People whose bank consent just completed via hub callback. */
  consent_ready?: ConsentReadyPerson[];
}

export interface AuthMeResponse {
  auth_required: boolean;
  authenticated: boolean;
  username: string | null;
  access?: string | null;
}

export function getAuthMe(): Promise<AuthMeResponse> {
  return getJson("/api/auth/me");
}

export function login(username: string, password: string): Promise<CentraleSyncStatus> {
  return sendJson("/api/login", "POST", { username, password });
}

export function logout(): Promise<{ ok: boolean; auth_required: boolean; authenticated: boolean }> {
  return sendJson("/api/logout", "POST", {});
}

export function getCentraleStatus(): Promise<CentraleSyncStatus> {
  return getJson("/api/centrale/status");
}

export function getCentraleNotifications(): Promise<{ notifications: SyncNotification[] }> {
  return getJson("/api/centrale/notifications");
}

export interface CentralWinsAlert {
  id: number;
  path: string;
  message: string;
}

export function getCentralWinsRefusals(): Promise<{ alerts: CentralWinsAlert[] }> {
  return getJson("/api/centrale/refusals");
}

export function ackCentralWinsRefusal(id: number): Promise<{
  ok: boolean;
  removed: number;
  alerts: CentralWinsAlert[];
}> {
  return sendJson("/api/centrale/refusals/ack", "POST", { id });
}

export function getWorkspaces(): Promise<{
  workspaces: string[];
  workspace: string;
  role: string;
}> {
  return getJson("/api/workspaces");
}

export function setWorkspace(workspace: string): Promise<{
  ok: boolean;
  workspace: string;
  people: { short: string; folder: string }[];
}> {
  return sendJson("/api/workspace", "POST", { workspace });
}
