"use client";

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import AlertsTable from "@/components/AlertsTable";
import {
  AlertRow,
  ReviewOut,
  ReviewIn,
  fetchAlerts,
  fetchReview,
  saveReview,
} from "@/lib/api";
import { toPng } from "html-to-image";

/** 与后端约定的决策类型 */
type Decision = "accept" | "reject" | "watch" | null;

/** —— 本页用到的 metrics 类型 —— */
type GreeksRow = {
  alert_id: string;
  snapshot_at: string | null;
  option_symbol: string | null;
  side: string | null;
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

type PriceRow = {
  alert_id: string;
  snapshot_at: string | null;
  market_time: string | null;
  stock_close: number | null;
  stock_previous_close: number | null;
  stock_volume: number | null;
  stock_total_volume: number | null;
};

type BucketRow = {
  id: number;
  alert_id: string;
  bucket_start: string;
  bucket_end: string;
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

/** —— 环境变量（客户端可用） —— */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE!;

/** —— 简单的 fetch 包装（仅用于 metrics 调用） —— */
async function requestJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
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

/** —— Metrics 请求（使用后端推荐的新路径） —— */
function fetchAlertGreeks(alertId: string) {
  return requestJSON<GreeksRow>(`/alerts/${encodeURIComponent(alertId)}/metrics/greeks`);
}
function fetchAlertPrice(alertId: string) {
  return requestJSON<PriceRow>(`/alerts/${encodeURIComponent(alertId)}/metrics/price`);
}
function fetchAlertBuckets(alertId: string, limit = 50) {
  const q = new URLSearchParams({ limit: String(limit) }).toString();
  return requestJSON<BucketRow[]>(`/alerts/${encodeURIComponent(alertId)}/metrics/buckets?${q}`);
}

/** —— 工具：UTC → ET —— */
function toET(utcIsoLike: string | null | undefined): string {
  if (!utcIsoLike) return "—";

  let s = utcIsoLike.trim();
  // "YYYY-MM-DD HH:MM:SS" → "YYYY-MM-DDTHH:MM:SS"
  if (s.includes(" ") && !s.includes("T")) s = s.replace(" ", "T");

  // 若无时区标记，默认按 UTC 处理
  const hasZone = /([zZ]|[+\-]\d{2}:\d{2})$/.test(s);
  if (!hasZone) s += "Z";

  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return utcIsoLike;

  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

/** —— 工具：数值格式化（兼容 string/number）—— */
function fmtNum(
  val: string | number | null | undefined,
  digits = 2,
  opts?: { group?: boolean }
): string {
  if (val === null || val === undefined) return "—";
  const n = typeof val === "number" ? val : Number(val);
  if (Number.isNaN(n)) return "—";

  const isInt = Number.isInteger(n);
  const nf = new Intl.NumberFormat("en-US", {
    useGrouping: opts?.group ?? false,
    minimumFractionDigits: isInt ? 0 : 0,
    maximumFractionDigits: isInt ? 0 : digits,
  });
  return nf.format(n);
}

/** -- Color base on side */
function sidePalette(side?: string | null) {
  const s = (side ?? "").toUpperCase();
  const isC = s === "C";
  const isP = s === "P";
  return {
    key: isC ? "text-green-400" : isP ? "text-red-400" : "text-gray-500",   // 参数名颜色
    val: isC ? "text-green-500" : isP ? "text-red-500" : "text-gray-100",    // 参数值颜色
    subtle: isC ? "text-green-300" : isP ? "text-red-300" : "text-gray-300", // 次要文本/徽标
    border: isC ? "border-green-700" : isP ? "border-red-700" : "border-gray-700",
    badge: isC
      ? "bg-green-600/20 text-green-300"
      : isP
      ? "bg-red-600/20 text-red-300"
      : "bg-gray-600/20 text-gray-300",
  };
}

/** —— 市场时段/可见性 —— */
function isMarketOpenEt(d = new Date()): boolean {
  const et = new Date(d.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0 Sun - 6 Sat
  if (day === 0 || day === 6) return false;
  const h = et.getHours();
  const m = et.getMinutes();
  const mins = h * 60 + m;
  const open = 9 * 60 + 30;
  const close = 16 * 60 + 0;
  return mins >= open && mins < close;
}
function isPageVisible(): boolean {
  return typeof document === "undefined" ? true : document.visibilityState === "visible";
}

/** —— Tabs 枚举 —— */
type TabKey = "greeks" | "price" | "buckets";

export default function HomePage() {
  // 默认用今天（本地）；如需严格 ET 可让后端提供
  const todayEt = useMemo(() => {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }, []);

  const [bizDate, setBizDate] = useState<string>(todayEt);
  const [rows, setRows] = useState<AlertRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<AlertRow | null>(null);
  const [review, setReview] = useState<ReviewOut | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string>("");

  // metrics
  const [greeks, setGreeks] = useState<GreeksRow | null>(null);
  const [price, setPrice] = useState<PriceRow | null>(null);
  const [buckets, setBuckets] = useState<BucketRow[] | null>(null);
  const [metricsLoading, setMetricsLoading] = useState<boolean>(false);
  const [tab, setTab] = useState<TabKey>("greeks");

  // 全屏查看
  const [fullOpen, setFullOpen] = useState(false);

  function errMsg(e: unknown): string {
    if (e instanceof Error) return e.message;
    try {
      return String(e);
    } catch {
      return "Unknown error";
    }
  }

  /** 只依赖 bizDate，避免与 selected 产生环路 */
  const loadAlerts = useCallback(async () => {
    setLoading(true);
    setMsg("");
    try {
      const data = await fetchAlerts({ biz_date: bizDate, limit: 500, order: "asc" });
      setRows(data);

      // 用函数式更新，若当前选中行已不在新数据里，则清空选中与 review
      setSelected((prevSel) => {
        if (!prevSel) return prevSel;
        const stillThere = data.some((r) => r.alert_id === prevSel.alert_id);
        if (!stillThere) setReview(null);
        return stillThere ? prevSel : null;
      });
    } catch (e: unknown) {
      setMsg(`Load failed: ${errMsg(e)}`);
    } finally {
      setLoading(false);
    }
  }, [bizDate]);

  // 初始化 / 切换日期时加载
  useEffect(() => {
    const POLL_MS = 5000;
    let timer: number | null = null;
    let inFlight = false;        // 请求中的互斥锁
    let stopped = false;

    const tick = async () => {
      if (stopped) return;
      if (!isMarketOpenEt() || !isPageVisible() || inFlight) return;
      inFlight = true;
      try {
        await loadAlerts();      // 只依赖 bizDate 的函数式回调
      } finally {
        inFlight = false;
      }
    };

    // 首次触发一次（不要依赖 loading 的变动）
    void tick();

    timer = window.setInterval(() => {
      void tick();
    }, POLL_MS);

    const visHandler = () => {
      if (isPageVisible()) void tick();
    };
    document.addEventListener("visibilitychange", visHandler);

    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", visHandler);
    };
  }, [loadAlerts]);

  const onSelect = async (r: AlertRow) => {
    setSelected(r);
    setMsg("");
    setReview(null);
    setGreeks(null);
    setPrice(null);
    setBuckets(null);
    setMetricsLoading(true);
    setTab("greeks");

    try {
      const [rv, gk, pr, bk] = await Promise.allSettled([
        fetchReview(r.alert_id),
        fetchAlertGreeks(r.alert_id),
        fetchAlertPrice(r.alert_id),
        fetchAlertBuckets(r.alert_id, 50),
      ]);

      if (rv.status === "fulfilled") setReview(rv.value);
      if (gk.status === "fulfilled") setGreeks(gk.value);
      if (pr.status === "fulfilled") setPrice(pr.value);
      if (bk.status === "fulfilled") setBuckets(bk.value);

      if (rv.status === "rejected") setMsg((m) => (m ? m + " " : "") + "Load review failed.");
      if (gk.status === "rejected") setMsg((m) => (m ? m + " " : "") + "Load greeks failed.");
      if (pr.status === "rejected") setMsg((m) => (m ? m + " " : "") + "Load price failed.");
      if (bk.status === "rejected") setMsg((m) => (m ? m + " " : "") + "Load buckets failed.");
    } finally {
      setMetricsLoading(false);
    }
  };

  const onSave = async () => {
    if (!selected || !review) return;
    setSaving(true);
    setMsg("");
    try {
      const payload: ReviewIn = {
        decision: (review.decision ?? null) as Decision,
        trade_types: review.trade_types ?? [],
        reason_codes: review.reason_codes ?? [],
        notes: review.notes ?? null,
        reviewed_by: review.reviewed_by ?? "me",
        row_version: review.row_version ?? 0,
      };
      const saved = await saveReview(selected.alert_id, payload);
      setReview(saved);
      setMsg("Saved.");
    } catch (e: unknown) {
      const text = errMsg(e);
      if (/^409\b/.test(text)) {
        try {
          const latest = await fetchReview(selected.alert_id);
          setReview(latest);
          setMsg("Conflict: refreshed to latest. Please retry.");
        } catch {
          /* ignore */
        }
      } else {
        setMsg(`Save failed: ${text}`);
      }
    } finally {
      setSaving(false);
    }
  };

  const decisions: Exclude<Decision, null>[] = ["accept", "reject", "watch"];

  return (
    <main className="p-6 max-w-7xl mx-auto">
      <h1 className="text-2xl font-semibold mb-4">UW Alerts — Review Console</h1>

      {/* 控件栏 */}
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <label className="text-sm">
          Biz Date (ET):
          <input
            type="date"
            value={bizDate}
            onChange={(e) => setBizDate(e.target.value)}
            className="ml-2 border rounded px-2 py-1"
          />
        </label>
        <button
          onClick={loadAlerts}
          className="px-3 py-1 rounded bg-black text-white hover:opacity-90"
          disabled={loading}
        >
          {loading ? "Loading..." : "Reload"}
        </button>
        {msg && <div className="text-sm text-gray-600">{msg}</div>}
      </div>

      {/* 主体：左表 + 右侧详情与编辑 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <AlertsTable
            rows={rows}
            onSelect={onSelect}
            selectedId={selected?.alert_id ?? null}
          />
        </div>

        <div className="lg:col-span-1 space-y-4">
          {/* 详情卡片 */}
          <div className="border border-gray-700 rounded-2xl p-4 bg-gray-900 text-gray-100">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-medium text-white">Details</h2>
              {selected && (
                <button
                  type="button"
                  onClick={() => setFullOpen(true)}
                  className="text-sm px-3 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
                >
                  Full Dispaly
                </button>
              )}
            </div>

            {!selected ? (
              <div className="text-gray-400 text-sm">Select a row to start.</div>
            ) : (
              <>
                {/* Alert Summary */}
                <section className="mb-3">
                  <h3 className="text-sm font-semibold mb-2">Alert Summary</h3>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                    <KV k="Alert ID" v={selected.alert_id} />
                    <KV k="Created (ET)" v={toET(selected.created_at_utc ?? selected.created_at ?? "")} />
                    <KV k="Biz Date (ET)" v={selected.biz_date_et ?? "—"} />
                    <KV k="Symbol" v={selected.symbol ?? "—"} />
                    <KV k="Option" v={selected.option_symbol ?? "—"} />
                    <KV k="Ask Vol" v={fmtNum(selected.ask_volume, 0)} />
                    <KV k="Bid Vol" v={fmtNum(selected.bid_volume, 0)} />
                    <KV k="Volume" v={fmtNum(selected.volume, 0)} />
                    <KV k="Avg Fill" v={fmtNum(selected.avg_fill)} />
                    <KV k="Close" v={fmtNum(selected.close)} />
                    <KV k="Diff" v={fmtNum(selected.diff)} />
                    <KV k="Total Premium" v={fmtNum(selected.total_premium, 2, { group: true })} />
                    <KV k="IV Change" v={fmtNum(selected.iv_change)} />
                    <KV k="Open Interest" v={fmtNum(selected.open_interest, 0)} />
                    <KV k="Vol/OI" v={fmtNum(selected.vol_oi_ratio)} />
                    <KV k="Multi-leg Vol Ratio" v={fmtNum(selected.multi_leg_vol_ratio)} />
                  </div>
                </section>

                {/* Tabs */}
                <div className="border border-gray-700 rounded-xl overflow-hidden">
                  <div className="flex border-b border-gray-200 bg-gray-50 text-sm">
                    <TabBtn active={tab === "greeks"} onClick={() => setTab("greeks")}>
                      Greeks
                    </TabBtn>
                    <TabBtn active={tab === "price"} onClick={() => setTab("price")}>
                      Price
                    </TabBtn>
                    <TabBtn active={tab === "buckets"} onClick={() => setTab("buckets")}>
                      Buckets
                    </TabBtn>
                    {metricsLoading && (
                      <div className="ml-auto px-3 py-2 text-gray-500">Loading…</div>
                    )}
                  </div>

                  {/* Greeks Panel */}
                  {tab === "greeks" && (
                    <div className="p-3">
                      {!greeks ? (
                        <div className="text-sm text-gray-500">No greeks.</div>
                      ) : (
                        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                          <KV k="Snapshot (ET)" v={toET(greeks.snapshot_at ?? "")} />
                          <KV k="Option" v={greeks.option_symbol ?? "—"} />
                          <KV k="Side" v={greeks.side ?? "—"} />
                          <KV k="DTE" v={fmtNum(greeks.dte, 0)} />
                          <KV k="Strike" v={fmtNum(greeks.strike)} />
                          <KV k="Delta" v={fmtNum(greeks.delta)} />
                          <KV k="Gamma" v={fmtNum(greeks.gamma)} />
                          <KV k="Theta" v={fmtNum(greeks.theta)} />
                          <KV k="Rho" v={fmtNum(greeks.rho)} />
                          <KV k="Vega" v={fmtNum(greeks.vega)} />
                          <KV k="Vanna" v={fmtNum(greeks.vanna)} />
                          <KV k="Charm" v={fmtNum(greeks.charm)} />
                          <KV k="Volatility" v={fmtNum(greeks.volatility)} />
                        </div>
                      )}
                    </div>
                  )}

                  {/* Price Panel */}
                  {tab === "price" && (
                    <div className="p-3">
                      {!price ? (
                        <div className="text-sm text-gray-500">No price snapshot.</div>
                      ) : (
                        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                          <KV k="Snapshot (ET)" v={toET(price.snapshot_at ?? "")} />
                          <KV k="Market Time" v={price.market_time ?? "—"} />
                          <KV k="Stock Close" v={fmtNum(price.stock_close)} />
                          <KV k="Prev Close" v={fmtNum(price.stock_previous_close)} />
                          <KV k="Stock Volume" v={fmtNum(price.stock_volume, 0)} />
                          <KV k="Total Volume" v={fmtNum(price.stock_total_volume, 0)} />
                        </div>
                      )}
                    </div>
                  )}

                  {/* Buckets Panel */}
                  {tab === "buckets" && (
                    <div className="p-3">
                      {!buckets || buckets.length === 0 ? (
                        <div className="text-sm text-gray-500">No buckets.</div>
                      ) : (
                        <div className="overflow-auto rounded-lg border border-gray-200">
                          <table className="min-w-full text-xs">
                            <thead className="bg-gray-50 text-gray-700">
                              <tr className="text-left">
                                <TH>Start (ET)</TH>
                                <TH>End (ET)</TH>
                                <TH>Min</TH>
                                <TH>Volume</TH>
                                <TH>Multi Ratio</TH>
                                <TH>Avg Price</TH>
                                <TH>IV Low</TH>
                                <TH>IV High</TH>
                                <TH>Premium</TH>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100">
                              {buckets.map((b) => (
                                <tr key={`${b.alert_id}-${b.bucket_end}`} className="border-t">
                                  <TD>{toET(b.bucket_start)}</TD>
                                  <TD>{toET(b.bucket_end)}</TD>
                                  <TD>{fmtNum(b.bucket_minutes, 0)}</TD>
                                  <TD>{fmtNum(b.total_volume, 0)}</TD>
                                  <TD>{fmtNum(b.bucket_multi_ratio)}</TD>
                                  <TD>{fmtNum(b.avg_price)}</TD>
                                  <TD>{fmtNum(b.avg_iv_low)}</TD>
                                  <TD>{fmtNum(b.avg_iv_high)}</TD>
                                  <TD>{fmtNum(b.total_premium, 2, { group: true })}</TD>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Review 面板 */}
          <div className="border border-gray-700 rounded-2xl p-4 bg-gray-900 text-gray-100">
            <h2 className="font-medium mb-3 text-white">Review</h2>
            {!selected && <div className="text-gray-500 text-sm">Select a row to start.</div>}
            {selected && (
              <>
                <div className="text-xs text-gray-500 mb-2 break-all">
                  <div>Alert ID: {selected.alert_id}</div>
                  <div>UTC: {selected.created_at_utc ?? "—"}</div>
                  <div>Symbol: {selected.symbol ?? "—"}</div>
                  <div>Option: {selected.option_symbol ?? "—"}</div>
                </div>

                {/* 表单 */}
                <div className="space-y-3">
                  <div>
                    <div className="text-sm mb-1">Decision</div>
                    <div className="flex gap-3">
                      {decisions.map((d) => (
                        <label key={d} className="flex items-center gap-1 text-sm">
                          <input
                            type="radio"
                            name="decision"
                            value={d}
                            checked={review?.decision === d}
                            onChange={() =>
                              setReview((prev) => (prev ? { ...prev, decision: d } : prev))
                            }
                          />
                          {d}
                        </label>
                      ))}
                      <label className="flex items-center gap-1 text-sm">
                        <input
                          type="radio"
                          name="decision"
                          value=""
                          checked={!review?.decision}
                          onChange={() =>
                            setReview((prev) => (prev ? { ...prev, decision: null } : prev))
                          }
                        />
                        none
                      </label>
                    </div>
                  </div>

                  <div>
                    <div className="text-sm mb-1">Trade types (comma-separated)</div>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      placeholder="single-leg, spread, earnings, roll..."
                      value={(review?.trade_types ?? []).join(", ")}
                      onChange={(e) =>
                        setReview((prev) =>
                          prev
                            ? {
                                ...prev,
                                trade_types: e.target.value
                                  .split(",")
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                              }
                            : prev
                        )
                      }
                    />
                  </div>

                  <div>
                    <div className="text-sm mb-1">Reason codes (comma-separated)</div>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      placeholder="iv, sweep, block, catalyst..."
                      value={(review?.reason_codes ?? []).join(", ")}
                      onChange={(e) =>
                        setReview((prev) =>
                          prev
                            ? {
                                ...prev,
                                reason_codes: e.target.value
                                  .split(",")
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                              }
                            : prev
                        )
                      }
                    />
                  </div>

                  <div>
                    <div className="text-sm mb-1">Notes</div>
                    <textarea
                      className="w-full border rounded px-2 py-1 text-sm min-h-[80px]"
                      value={review?.notes ?? ""}
                      onChange={(e) =>
                        setReview((prev) => (prev ? { ...prev, notes: e.target.value } : prev))
                      }
                    />
                  </div>

                  <div className="text-xs text-gray-500">
                    row_version: {review?.row_version ?? 0}
                    {review?.reviewed_by ? ` • by ${review.reviewed_by}` : ""}
                    {review?.reviewed_at ? ` • ${review?.reviewed_at}` : ""}
                  </div>

                  <div className="flex gap-2">
                    <button
                      className="px-3 py-1 rounded bg-blue-600 text-white disabled:opacity-50"
                      onClick={onSave}
                      disabled={!selected || !review || saving}
                    >
                      {saving ? "Saving..." : "Save"}
                    </button>
                    <button
                      className="px-3 py-1 rounded border"
                      onClick={() => {
                        if (selected) {
                          fetchReview(selected.alert_id)
                            .then(setReview)
                            .catch(() => {});
                        }
                      }}
                    >
                      Refresh
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* —— 全屏「查看全部」覆盖层 —— */}
      <AlertFullOverlay
        open={fullOpen}
        onClose={() => setFullOpen(false)}
        alert={
          selected
            ? {
                created_at: selected.created_at,
                created_at_utc: selected.created_at_utc,
                biz_date_et: selected.biz_date_et,
                symbol: selected.symbol,
                option_symbol: selected.option_symbol,
                ask_volume: selected.ask_volume ?? null,
                bid_volume: selected.bid_volume ?? null,
                volume: selected.volume ?? null,
                avg_fill: selected.avg_fill ?? null,
                close: selected.close ?? null,
                diff: selected.diff ?? null,
                total_premium: selected.total_premium ?? null,
                iv_change: selected.iv_change ?? null,
                open_interest: selected.open_interest ?? null,
                vol_oi_ratio: selected.vol_oi_ratio ?? null,
                multi_leg_vol_ratio: selected.multi_leg_vol_ratio ?? null,
              }
            : null
        }
        greeks={greeks}
        price={price}
        buckets={buckets}
      />
    </main>
  );
}

/** —— 小组件 —— */
function KV({
  k,
  v,
  keyClassName,
  valueClassName,
}: {
  k: string;
  v: string;
  keyClassName?: string;
  valueClassName?: string;
}) {
  return (
    <div className="flex justify-between gap-2">
      <span className={keyClassName ?? "text-gray-500"}>{k}</span>
      <span className={["font-medium break-all", valueClassName ?? ""].join(" ")}>
        {v}
      </span>
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={[
        "px-4 py-2 border-r border-gray-200",
        "hover:bg-gray-100",
        active
          ? "bg-white text-blue-700 font-medium -mb-px border-b-2 border-b-blue-600"
          : "text-gray-600",
      ].join(" ")}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
function TH({ children }: { children: React.ReactNode }) {
  return <th className="px-2 py-2">{children}</th>;
}
function TD({ children }: { children: React.ReactNode }) {
  return <td className="px-2 py-2 whitespace-nowrap">{children}</td>;
}

/** —— 全屏覆盖层（不显示 alert_id，黑底白字版，支持导出 PNG） —— */
function AlertFullOverlay({
  open,
  onClose,
  alert,
  greeks,
  price,
  buckets,
}: {
  open: boolean;
  onClose: () => void;
  alert: {
    created_at?: string | null;
    created_at_utc?: string | null;
    biz_date_et?: string | null;
    symbol?: string | null;
    option_symbol?: string | null;
    ask_volume?: number | null;
    bid_volume?: number | null;
    volume?: number | null;
    avg_fill?: number | null;
    close?: number | null;
    diff?: number | null;
    total_premium?: number | null;
    iv_change?: number | null;
    open_interest?: number | null;
    vol_oi_ratio?: number | null;
    multi_leg_vol_ratio?: number | null;
  } | null;
  greeks: GreeksRow | null;
  price: PriceRow | null;
  buckets: BucketRow[] | null;
}) {
  /** ✅ Hooks 必须在顶部调用，不能放在条件分支之后 */
  const contentRef = useRef<HTMLDivElement | null>(null);

  const palette = sidePalette(greeks?.side);

  /** 导出为 PNG（非 Hook，普通函数没问题） */
  async function handleDownloadPng() {
    if (!contentRef.current) return;

    // 读取真实内容尺寸
    const node = contentRef.current;
    const sw = node.scrollWidth || node.clientWidth;
    const sh = node.scrollHeight || node.clientHeight;

    // 大多数浏览器的单张 canvas 高度上限大约在 16384 像素
    const MAX_CANVAS = 16384;

    // 如果太高，就按比例整体缩小，保证导出高度 <= 上限
    const scale = sh > MAX_CANVAS ? MAX_CANVAS / sh : 1;

    try {
      const dataUrl = await toPng(node, {
        cacheBust: true,
        backgroundColor: "#111827", // 与 bg-gray-900 一致，避免透明底
        // 导出目标尺寸（缩放后）
        width: Math.floor(sw * scale),
        height: Math.floor(sh * scale),
        pixelRatio: 1, // 我们用 width/height 控制清晰度，避免再次放大导致超限
        // 在克隆节点上应用缩放（等比缩小后再渲染）
        style: {
          transform: `scale(${scale})`,
          transformOrigin: "top left",
          width: `${sw}px`,
          height: `${sh}px`,
        },
      });

      const a = document.createElement("a");
      const sym = alert?.symbol ?? "alert";
      a.href = dataUrl;
      a.download = `${sym}_details_${Date.now()}.png`;
      a.click();
    } catch (e) {
      console.error(e);
      window.alert("导出图片失败，请稍后再试。");
    }
  }

  /** 再做早退就不会违反规则了 */
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-gray-900 text-gray-100">
      {/* 顶部栏 */}
      <div className="sticky top-0 z-10 border-b border-gray-700 bg-gray-900">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="font-medium text-white">Alert Full Details</div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleDownloadPng}
              className="text-sm px-3 py-1 rounded border border-gray-600 bg-gray-800 text-gray-100 hover:bg-gray-700"
            >
              Download PNG
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-sm px-3 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
            >
              Close
            </button>
          </div>
        </div>
      </div>

      {/* 内容主体（用于导出图片的区域） */}
      <div ref={contentRef} className="max-w-5xl mx-auto px-4 py-6 space-y-8">
        {/* Raw Data */}
        <section>
          <h3 className="text-base font-semibold mb-3 text-white">Raw Data</h3>
          {!alert ? (
            <div className="text-gray-400 text-sm">None</div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-sm">
              <KV k="Created (ET)" v={toET(alert.created_at_utc ?? alert.created_at ?? "")} />
              <KV k="Biz Date (ET)" v={alert.biz_date_et ?? "—"} />
              <KV k="Symbol" v={alert.symbol ?? "—"} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Option" v={alert.option_symbol ?? "—"} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Ask Vol" v={fmtNum(alert.ask_volume, 0, { group: true })} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Bid Vol" v={fmtNum(alert.bid_volume, 0)} />
              <KV k="Volume" v={fmtNum(alert.volume, 0, { group: true })} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Avg Fill" v={fmtNum(alert.avg_fill)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Close" v={fmtNum(alert.close)} />
              <KV k="Diff" v={fmtNum(alert.diff)} />
              <KV k="Total Premium" v={fmtNum(alert.total_premium, 2, { group: true })} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Open Interest" v={fmtNum(alert.open_interest, 0, { group: true })} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Vol/OI" v={fmtNum(alert.vol_oi_ratio)} />
              <KV k="Multi-leg Vol Ratio" v={fmtNum(alert.multi_leg_vol_ratio)} keyClassName={palette.key} valueClassName={palette.val} />
            </div>
          )}
        </section>

        {/* Greeks */}
        <section>
          <h3 className="text-base font-semibold mb-3 text-white">Greeks Snapshot</h3>
          {!greeks ? (
            <div className="text-gray-400 text-sm">None</div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-sm">
              <KV k="Snapshot (ET)" v={toET(greeks.snapshot_at ?? "")} />
              <KV k="Option" v={greeks.option_symbol ?? "—"} />
              <KV k="Side" v={greeks.side ?? "—"} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="DTE" v={fmtNum(greeks.dte, 0)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Strike" v={fmtNum(greeks.strike)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Delta" v={fmtNum(greeks.delta)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Gamma" v={fmtNum(greeks.gamma)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Theta" v={fmtNum(greeks.theta)} />
              <KV k="Rho" v={fmtNum(greeks.rho)} />
              <KV k="Vega" v={fmtNum(greeks.vega)} />
              <KV k="Vanna" v={fmtNum(greeks.vanna)} />
              <KV k="Charm" v={fmtNum(greeks.charm)} />
              <KV k="Volatility" v={fmtNum(greeks.volatility)} />
            </div>
          )}
        </section>

        {/* Price */}
        <section>
          <h3 className="text-base font-semibold mb-3 text-white">Price Snapshot</h3>
          {!price ? (
            <div className="text-gray-400 text-sm">None</div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-sm">
              <KV k="Snapshot (ET)" v={toET(price.snapshot_at ?? "")} />
              <KV k="Market Time" v={price.market_time ?? "—"} />
              <KV k="Stock Close" v={fmtNum(price.stock_close)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Prev Close" v={fmtNum(price.stock_previous_close)} />
              <KV k="Stock Volume" v={fmtNum(price.stock_volume, 0)} />
              <KV k="Total Volume" v={fmtNum(price.stock_total_volume, 0)} />
            </div>
          )}
        </section>

        {/* Buckets */}
        <section>
          <h3 className="text-base font-semibold mb-3 text-white">Buckets</h3>
          {!buckets || buckets.length === 0 ? (
            <div className="text-gray-400 text-sm">None</div>
          ) : (
            <div className="rounded-lg border border-gray-700 overflow-hidden">
              <table className="min-w-full text-xs">
                <thead className="bg-gray-800 text-gray-300">
                  <tr className="text-left">
                    <TH>Start (ET)</TH>
                    <TH>End (ET)</TH>
                    <TH>Min</TH>
                    <TH>Volume</TH>
                    <TH>Multi Ratio</TH>
                    <TH>Avg Price</TH>
                    <TH>IV Low</TH>
                    <TH>IV High</TH>
                    <TH>Premium</TH>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700 text-gray-100">
                  {buckets.map((b) => (
                    <tr key={`${b.alert_id}-${b.bucket_end}`}>
                      <TD>
                        <span className={palette.val}>{toET(b.bucket_start)}</span>
                      </TD>
                      <TD>
                        <span className={palette.val}>{toET(b.bucket_end)}</span>
                      </TD>
                      <TD>{fmtNum(b.bucket_minutes, 0)}</TD>
                      <TD>
                        <span className={palette.val}>{fmtNum(b.total_volume, 0)}</span>
                      </TD>
                      <TD>
                        <span className={palette.val}>{fmtNum(b.bucket_multi_ratio)}</span>
                      </TD>
                      <TD>
                        <span className={palette.val}>{fmtNum(b.avg_price)}</span>
                      </TD>
                      <TD>{fmtNum(b.avg_iv_low)}</TD>
                      <TD>{fmtNum(b.avg_iv_high)}</TD>
                      <TD>
                        <span className={palette.val}>{fmtNum(b.total_premium, 2, { group: true })}</span>
                      </TD>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
