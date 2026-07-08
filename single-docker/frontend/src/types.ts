export interface SettingsResponse {
  categories: string[];
  person: string;
  general: Record<string, string[]>;
  personal: Record<string, Record<string, string[]>>;
  valid_category_codes: number[];
  remainder_category: string;
}

export type Transaction = Record<string, unknown>;

export interface TransactionsResponse {
  person: string;
  category: string;
  columns: string[];
  transactions: Transaction[];
  modified_ids: string[];
  keywords: string[];
  abbreviations: Record<string, string>;
  valid_category_codes: number[];
  remainder_category: string;
}

export interface TermsUpdateResponse {
  group: string;
  category: string;
  terms: string[];
}

export interface AddTermResponse {
  group: string;
  category: string;
  term: string;
  terms: string[];
  totals: Record<string, string>;
}

export interface FetchResponse {
  transaction_count: number;
  totals: Record<string, string>;
  date_from?: string;
  date_to?: string;
  renewal_day?: boolean;
  warnings?: string[];
  account_errors?: string[];
}

export interface BankAccount {
  uid: string;
  iban: string;
  name: string;
  currency: string;
  balance: string;
  balance_currency: string;
  aspsp: string;
  country: string;
  enabled: boolean;
  active: boolean;
}

export interface AccountsResponse {
  accounts: BankAccount[];
  needs_renewal: boolean;
}
