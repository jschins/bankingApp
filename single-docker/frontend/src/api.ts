import type {
  AccountsResponse,
  AddTermResponse,
  FetchResponse,
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

export function getTotals(): Promise<Record<string, string>> {
  return getJson<Record<string, string>>("/api/totals");
}

export function recalculate(): Promise<Record<string, string>> {
  return sendJson<Record<string, string>>("/api/recalculate", "POST", {});
}

export function getTransactions(category: string): Promise<TransactionsResponse> {
  return getJson<TransactionsResponse>(
    `/api/transactions/${encodeURIComponent(category)}`
  );
}

export function getSettings(): Promise<SettingsResponse> {
  return getJson<SettingsResponse>("/api/settings");
}

export function updateSettings(
  group: string,
  category: string,
  terms: string[]
): Promise<TermsUpdateResponse> {
  return sendJson<TermsUpdateResponse>(
    `/api/settings/${encodeURIComponent(group)}/${encodeURIComponent(category)}`,
    "PUT",
    { terms }
  );
}

export function addCategoryTerm(body: {
  category_name: string;
  term: string;
  general: boolean;
}): Promise<AddTermResponse> {
  return sendJson("/api/settings/add-term", "POST", body);
}

export function recordModification(transaction: Transaction): Promise<unknown> {
  return sendJson("/api/transactions/modification", "PUT", { transaction });
}

export function getConsentStatus(): Promise<{ needs_renewal: boolean }> {
  return getJson("/api/consent/status");
}

export function getAuthorizationUrl(): Promise<{ url: string }> {
  return sendJson("/api/consent/authorize", "POST", {});
}

export function getBankAccounts(): Promise<AccountsResponse> {
  return getJson<AccountsResponse>("/api/accounts");
}

export function updateBankAccounts(enabled_uids: string[]): Promise<AccountsResponse> {
  return sendJson<AccountsResponse>("/api/accounts", "PUT", { enabled_uids });
}

export function fetchBankData(body: {
  date_from?: string;
  date_to?: string;
  redirect_code?: string;
  new_year?: boolean;
}): Promise<FetchResponse> {
  return sendJson<FetchResponse>("/api/fetch", "POST", body);
}

export function uploadData(): Promise<unknown> {
  return sendJson("/api/upload", "POST", {});
}
