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

/** Append a UI step to ``data/category_table.log`` (best-effort; never throws). */
export function logCategoryTableStep(
  step: string,
  detail: Record<string, unknown> = {}
): void {
  void fetch("/api/debug/client-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step, detail }),
  }).catch(() => {
    /* ignore logging failures */
  });
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

export function getPendingRedirectCode(): Promise<{
  redirect_code: string | null;
  error?: string | null;
  consent_saved?: boolean;
}> {
  return getJson("/api/consent/pending");
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
