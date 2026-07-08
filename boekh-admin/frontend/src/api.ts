import type {
  CategoriesResponse,
  FileListResponse,
  FileSummary,
  SettingsResponse,
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
  method: "PUT" | "POST",
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

function putJson<T>(url: string, body: unknown): Promise<T> {
  return sendJson<T>(url, "PUT", body);
}

function postJson<T>(url: string, body: unknown): Promise<T> {
  return sendJson<T>(url, "POST", body);
}

export function listFiles(): Promise<FileListResponse> {
  return getJson<FileListResponse>("/api/files");
}

export function listCategories(): Promise<CategoriesResponse> {
  return getJson<CategoriesResponse>("/api/categories");
}

export function recalculate(): Promise<CategoriesResponse> {
  return postJson<CategoriesResponse>("/api/recalculate", {});
}

export interface CollectResult {
  person: string;
  status: string;
  accounts: number;
  transactions: number;
  error: string | null;
}

export interface RefreshResponse {
  collect: {
    ok: boolean;
    returncode?: number;
    error?: string;
    summary?: { results: CollectResult[] };
  };
  reloaded: boolean;
  count: number;
  refresh_error: string | null;
}

// Fetch every person's bank data, upload to storage, then distill locally.
// This can take a while (one network round-trip per person).
export function refreshData(): Promise<RefreshResponse> {
  return postJson<RefreshResponse>("/api/refresh", {});
}

export function getTransactions(
  short: string,
  category: number
): Promise<TransactionsResponse> {
  return getJson<TransactionsResponse>(
    `/api/transactions/${encodeURIComponent(short)}/${category}`
  );
}

export function getSettings(): Promise<SettingsResponse> {
  return getJson<SettingsResponse>("/api/settings");
}

export function recordModification(
  short: string,
  transaction: Transaction
): Promise<unknown> {
  return putJson(
    `/api/transactions/${encodeURIComponent(short)}/modification`,
    { transaction }
  );
}

export interface TermsUpdateResponse {
  group: string;
  category: string;
  terms: string[];
}

export function updateSettings(
  group: string,
  category: string,
  terms: string[]
): Promise<TermsUpdateResponse> {
  return putJson<TermsUpdateResponse>(
    `/api/settings/${encodeURIComponent(group)}/${encodeURIComponent(category)}`,
    { terms }
  );
}

// `id` is a "person/name" path; encode each segment but keep the separator.
function encodeId(id: string): string {
  return id.split("/").map(encodeURIComponent).join("/");
}

export function getFile(id: string): Promise<unknown> {
  return getJson<unknown>(`/api/files/${encodeId(id)}`);
}

export function getFileSummary(id: string): Promise<FileSummary> {
  return getJson<FileSummary>(`/api/files/${encodeId(id)}/summary`);
}
