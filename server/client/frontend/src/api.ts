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
  const resp = await fetch(url);
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  return (await resp.json()) as T;
}

export function getMatrix(): Promise<MatrixResponse> {
  return getJson("/api/matrix");
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

export interface CentraleSyncStatus {
  enabled: boolean;
  workspace: string;
  /** Fixed identity from client_config ``author`` (does not follow switcher). */
  author?: string;
  centrale_url: string;
  local_session_active: boolean;
  error: string | null;
  last_event_id?: number;
  notifications?: SyncNotification[];
  port?: number;
  role?: "local" | "central_admin";
  workspaces?: string[];
  data_epoch?: number;
  has_secrets?: boolean;
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
