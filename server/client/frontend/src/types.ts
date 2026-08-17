export interface PersonInfo {
  short: string;
  folder: string;
  /**
   * When `personal_categories.json` is missing for this person, term editing /
   * organization is disabled in the Terms view.
   */
  organizable?: boolean;
}

export interface MatrixResponse {
  categories: string[];
  people: PersonInfo[];
  cells: Record<string, Record<string, string>>;
}

export interface RefreshPersonResult {
  short: string;
  folder?: string;
  skipped: boolean;
  reason?: string;
  transaction_count?: number;
  date_from?: string;
  date_to?: string;
  warnings?: string[];
  account_errors?: string[];
  authorization_url?: string | null;
  new_year?: boolean;
}

export interface RefreshResponse {
  matrix: MatrixResponse;
  results: RefreshPersonResult[];
  warnings: string[];
}

export interface TypeRule {
  type: string;
  category: string;
}

export interface SettingsResponse {
  categories: string[];
  people: PersonInfo[];
  general: Record<string, string[]>;
  personal: Record<string, Record<string, string[]>>;
  valid_category_codes: number[];
  remainder_category: string;
  typerules: TypeRule[];
}

export type Transaction = Record<string, unknown>;

export interface TransactionsResponse {
  person: string;
  folder?: string;
  category: string;
  columns: string[];
  transactions: Transaction[];
  description_modified_ids: string[];
  category_modified_ids: string[];
  keywords: string[];
  abbreviations: Record<string, string>;
  valid_category_codes: number[];
  remainder_category: string;
}

export interface TermsUpdateResponse {
  group: string;
  category: string;
  terms: string[];
  matrix?: MatrixResponse;
}

export interface AddTermResponse {
  group: string;
  category: string;
  term: string;
  terms: string[];
  matrix: MatrixResponse;
}
