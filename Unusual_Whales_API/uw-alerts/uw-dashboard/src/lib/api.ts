export type AlertRow = {
    alert_id: string;
  created_at: string | null;
  created_at_utc: string | null;
  biz_date_et: string | null;
  symbol: string | null;
  option_symbol: string | null;
  ask_volume: number | null;
  bid_volume: number | null;
  volume: number | null;
  avg_fill: number | null;
  close: number | null;
  diff: number | null;
  total_premium: number | null;
  iv_change: number | null;
  open_interest: number | null;
  vol_oi_ratio: number | null;
  multi_leg_vol_ratio: number | null;
};

export type ReviewOut = {
  alert_id: string;
  decision: "accept" | "reject" | "watch" | null;
  trade_types: string[];
  reason_codes: string[];
  notes: string | null;
  row_version: number;
  reviewed_by: string | null;
  reviewed_at: string | null;
};

export type ReviewIn = Partial<Omit<ReviewOut, "alert_id">> & {
  row_version?: number | null;
};

const API = process.env.NEXT_PUBLIC_API_BASE!;

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${text}`);
  }
  return res.json() as Promise<T>;
}

export const fetchAlerts = (params: {
  biz_date?: string;
  limit?: number;
  order?: "asc" | "desc";
  symbol?: string;
  option_symbol?: string;
}) => {
  const q = new URLSearchParams();
  if (params.biz_date) q.set("biz_date", params.biz_date);
  q.set("limit", String(params.limit ?? 200));
  q.set("order", params.order ?? "asc");
  if (params.symbol) q.set("symbol", params.symbol);
  if (params.option_symbol) q.set("option_symbol", params.option_symbol);
  return http<AlertRow[]>(`/alerts?${q.toString()}`);
};

export const fetchReview = (alertId: string) =>
  http<ReviewOut>(`/review/${encodeURIComponent(alertId)}`);

export const saveReview = (alertId: string, payload: ReviewIn) =>
  http<ReviewOut>(`/review/${encodeURIComponent(alertId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export type GreeksRow = {
  alert_id: string;
  snapshot_at: string | null; // ISO
  option_symbol: string | null;
  side: string | null; // "C"/"P" 或你后端的 "B"/"S"，保持 string
  dte: number | null;
  strike: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  rho: number | null;
  vega: number | null;
  vanna: number | null;
  charm: number | null;
  volatility: number | null;
};

export type PriceRow = {
  alert_id: string;
  snapshot_at: string | null; // ISO
  market_time: string | null;
  stock_close: number | null;
  stock_previous_close: number | null;
  stock_volume: number | null;
  stock_total_volume: number | null;
};

export type BucketRow = {
  id: number;
  alert_id: string;
  bucket_start: string; // ISO
  bucket_end: string;   // ISO
  bucket_minutes: number;
  option_symbol: string | null;
  avg_price_ask: number | null;
  avg_price_bid: number | null;
  avg_price_mid: number | null;
  avg_price_no: number | null;
  avg_price: number | null;
  avg_iv_low: number | null;
  avg_iv_high: number | null;
  volume_ask: number | null;
  volume_bid: number | null;
  volume_mid: number | null;
  volume_no: number | null;
  volume_multi: number | null;
  total_volume: number | null;
  bucket_multi_ratio: number | null;
  premium_ask: number | null;
  premium_bid: number | null;
  premium_mid: number | null;
  premium_no: number | null;
  total_premium: number | null;
};

export const fetchAlertGreeks = (alertId: string) =>
  http<GreeksRow>(`/alerts/${encodeURIComponent(alertId)}/metrics/greeks`);

export const fetchAlertPrice = (alertId: string) =>
  http<PriceRow>(`/alerts/${encodeURIComponent(alertId)}/metrics/price`);

export const fetchAlertBuckets = (alertId: string, limit = 20) => {
  const q = new URLSearchParams({ limit: String(limit) });
  return http<BucketRow[]>(
    `/alerts/${encodeURIComponent(alertId)}/metrics/buckets?${q.toString()}`
  );
};