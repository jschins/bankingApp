export interface UploadMeta {
  id: string;
  owner_id: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: string;
  stored_path: string;
}

export interface FileListResponse {
  count: number;
  files: UploadMeta[];
}

export interface CategoriesResponse {
  count: number;
  categories: string[];
  shorts: string[];
  headers: string[];
  rows: string[][];
  abbreviations: Record<string, string>;
  widths: Record<string, number>;
}

export interface SettingsResponse {
  categories: string[];
  shorts: string[];
  general: Record<string, string[]>;
  personal: Record<string, Record<string, string[]>>;
}

export type Transaction = Record<string, unknown>;

export interface TransactionsResponse {
  short: string;
  category: number;
  count: number;
  transactions: Transaction[];
  modified_ids: string[];
  keywords: string[];
}

export interface NumericStat {
  count: number;
  sum: number;
  mean: number;
  min: number;
  max: number;
}

export interface FileSummary {
  row_count: number;
  columns: string[];
  numeric_summary: Record<string, NumericStat>;
  preview: Record<string, unknown>[];
}
