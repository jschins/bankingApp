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

export interface BanksResponse {
  folders: string[];
  multi_bank: boolean;
  show_switcher: boolean;
  upload_token?: string;
  person?: string;
  year?: string;
}

export function getYears(): Promise<YearsResponse> {
  return getJson("/api/years");
}

export function getBanks(year?: string): Promise<BanksResponse> {
  const q = year ? `?year=${encodeURIComponent(year)}` : "";
  return getJson(`/api/banks${q}`);
}

export function getMatrix(year?: string, bank?: string): Promise<MatrixResponse> {
  const params = new URLSearchParams();
  if (year) params.set("year", year);
  if (bank) params.set("bank", bank);
  const q = params.toString();
  return getJson(q ? `/api/matrix?${q}` : "/api/matrix");
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
  category: string,
  year?: string,
  bank?: string
): Promise<TransactionsResponse> {
  const params = new URLSearchParams();
  if (year) params.set("year", year);
  if (bank) params.set("bank", bank);
  const q = params.toString();
  const base = `/api/transactions/${encodeURIComponent(short)}/${encodeURIComponent(category)}`;
  return getJson(q ? `${base}?${q}` : base);
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
  /** personal | local | regional_admin */
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
  access: string;
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
