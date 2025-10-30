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
  expiry: string | null;
  otm_pct: number | null;
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

function fmtPct(
  val: string | number | null | undefined,
  digits = 2
): string {
  if (val === null || val === undefined) return "—";
  const n = typeof val === "number" ? val : Number(val);
  if (Number.isNaN(n)) return "—";
  return `${fmtNum(n * 100, digits)}%`;
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

/** —— CSV 工具 —— */
function csvEscape(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}
function toCsv(headers: string[], rows: (string | number | null | undefined)[][]): string {
  const head = headers.map(csvEscape).join(",");
  const body = rows.map(r => r.map(csvEscape).join(",")).join("\n");
  return head + "\n" + body;
}
function downloadText(filename: string, text: string, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

/** —— 简易并发限制器（默认 6 并发） —— */
function pLimit(concurrency = 6) {
  let active = 0;
  const queue: (() => void)[] = [];
  const next = () => {
    active--;
    if (queue.length) {
      const fn = queue.shift()!;
      fn();
    }
  };
  return async function run<T>(task: () => Promise<T>): Promise<T> {
    if (active >= concurrency) {
      await new Promise<void>(res => queue.push(res));
    }
    active++;
    try {
      return await task();
    } finally {
      next();
    }
  };
}

/** —— 业务用常量：两组 ticker —— */
const INDICES = new Set(["QQQ", "SPY", "IWM"]);
const M7 = new Set(["AAPL", "NVDA", "TSLA", "META", "AMZN", "GOOGL", "GOOG", "MSFT"]);

/** —— 解析 ticker 过滤输入 —— */
function parseTickerFilter(input: string) {
  // tokens: 用 , 或空白分割；支持 ! 和 *（前缀通配）
  const tokens = input
    .split(/[,\s]+/)
    .map(s => s.trim().toUpperCase())
    .filter(Boolean);

  const include: string[] = [];
  const exclude: string[] = [];

  for (const t of tokens) {
    if (t.startsWith("!")) exclude.push(t.slice(1));
    else include.push(t);
  }
  return { include, exclude };
}

/** —— 符号是否匹配 tokens —— */
function matchSymbol(symRaw: string | null | undefined, include: string[], exclude: string[]): boolean {
  const sym = (symRaw ?? "").toUpperCase();
  if (!sym) return false;

  // 命中排除：直接 false
  for (const ex of exclude) {
    if (ex.endsWith("*")) {
      const pre = ex.slice(0, -1);
      if (pre && sym.startsWith(pre)) return false;
    } else if (sym === ex) {
      return false;
    }
  }

  // include 为空 => 不做包含限制（= 全部通过）
  if (include.length === 0) return true;

  // 否则需要命中任一 include
  for (const inc of include) {
    if (inc.endsWith("*")) {
      const pre = inc.slice(0, -1);
      if (pre && sym.startsWith(pre)) return true;
    } else if (sym === inc) {
      return true;
    }
  }
  return false;
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

  // review input
  const [tradeTypesDraft, setTradeTypesDraft] = useState("");
  const [reasonCodesDraft, setReasonCodesDraft] = useState("");

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

    // 过滤输入框
  const [symbolFilter, setSymbolFilter] = useState<string>("");

  // 派生：按过滤条件得到要渲染的行
  const filteredRows = useMemo(() => {
    const { include, exclude } = parseTickerFilter(symbolFilter);
    if (include.length === 0 && exclude.length === 0) return rows;
    return rows.filter(r => matchSymbol(r.symbol, include, exclude));
  }, [rows, symbolFilter]);

  // 只根据 symbolFilter 判断当前 selected 是否仍然匹配过滤条件
  useEffect(() => {
    if (!selected) return;
    const { include, exclude } = parseTickerFilter(symbolFilter);
    if (!matchSymbol(selected.symbol, include, exclude)) {
      setSelected(null);
      setReview(null);
    }
  }, [symbolFilter, selected]);



    //Synchronize review -> draft
    useEffect(() => {
      setTradeTypesDraft((review?.trade_types ?? []).join(", "));
      setReasonCodesDraft((review?.reason_codes ?? []).join(", "));
    }, [review?.trade_types, review?.reason_codes]);

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
        trade_types: parseCsvToList(tradeTypesDraft),
        reason_codes: parseCsvToList(reasonCodesDraft),
        notes: review.notes ?? null,
        reviewed_by: review.reviewed_by ?? "MC",
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

  /** —— 将 alert + greeks + price 拍平为一行 —— */
function buildCsvRow(
  a: AlertRow,
  g: GreeksRow | null,
  p: PriceRow | null
) {
  // 注意：所有时间统一转 ET 可读格式
  return [
    a.alert_id,
    toET(a.created_at_utc ?? a.created_at ?? ""),
    a.biz_date_et ?? "",
    a.symbol ?? "",
    a.option_symbol ?? "",
    a.ask_volume ?? "",
    a.bid_volume ?? "",
    a.volume ?? "",
    a.avg_fill ?? "",
    a.close ?? "",
    a.diff ?? "",
    a.total_premium ?? "",
    a.iv_change ?? "",
    a.open_interest ?? "",
    a.vol_oi_ratio ?? "",
    a.multi_leg_vol_ratio ?? "",

    // —— Greeks（可为空）
    g ? toET(g.snapshot_at ?? "") : "",
    g?.option_symbol ?? "",
    g?.side ?? "",
    g?.expiry ?? "",
    g?.dte ?? "",
    g?.strike ?? "",
    g?.otm_pct ?? "",
    g?.delta ?? "",
    g?.gamma ?? "",
    g?.theta ?? "",
    g?.rho ?? "",
    g?.vega ?? "",
    g?.vanna ?? "",
    g?.charm ?? "",
    g?.volatility ?? "",

    // —— Price（可为空）
    p ? toET(p.snapshot_at ?? "") : "",
    p?.market_time ?? "",
    p?.stock_close ?? "",
    p?.stock_previous_close ?? "",
    p?.stock_volume ?? "",
    p?.stock_total_volume ?? "",
  ];
}

/** —— CSV 表头 —— */
const CSV_HEADERS = [
  // Alert
  "alert_id","created_at_et","biz_date_et","symbol","option_symbol",
  "ask_volume","bid_volume","volume","avg_fill","close","diff",
  "total_premium","iv_change","open_interest","vol_oi_ratio","multi_leg_vol_ratio",
  // Greeks
  "greeks_snapshot_et","greeks_option","greeks_side","greeks_expiry","greeks_dte",
  "greeks_strike","greeks_otm_pct","delta","gamma","theta","rho","vega","vanna","charm","volatility",
  // Price
  "price_snapshot_et","market_time","stock_close","stock_prev_close","stock_volume","stock_total_volume"
];

/** —— 导出某一组（Indices / M7）为 CSV —— */
async function downloadGroupCsv(groupName: "Indices" | "M7", symbolSet: Set<string>) {
  try {
    setMsg("");

    // 默认用当前页面的数据（避免 limit 超限）。若想只导出过滤后的，改成 filteredRows
    const data = rows;
    const targets = data.filter(r => symbolSet.has((r.symbol ?? "").toUpperCase()));
    if (targets.length === 0) {
      setMsg(`No alerts for ${groupName} on ${bizDate}.`);
      return;
    }

    const limit = pLimit(6);
    const rowsOut: (string | number | null | undefined)[][] = [];

    await Promise.all(targets.map(a =>
      limit(async () => {
        const [gk, pr] = await Promise.allSettled([
          fetchAlertGreeks(a.alert_id),
          fetchAlertPrice(a.alert_id),
        ]);
        const g = gk.status === "fulfilled" ? gk.value : null;
        const p = pr.status === "fulfilled" ? pr.value : null;
        rowsOut.push(buildCsvRow(a, g, p));
      })
    ));

    // 可选：按创建时间（CSV_HEADERS 的第2列 created_at_et）排序
    rowsOut.sort((ra, rb) => {
      const aT = new Date(String(ra[1] ?? "")).getTime();
      const bT = new Date(String(rb[1] ?? "")).getTime();
      return (isNaN(aT) ? 0 : aT) - (isNaN(bT) ? 0 : bT);
    });

    const csv = toCsv(
      CSV_HEADERS.slice(1),       
      rowsOut.map(r => r.slice(1)) 
    );
    downloadText(`${groupName}_${bizDate}.csv`, csv);
    setMsg(`${groupName} CSV exported (${rowsOut.length} rows).`);
  } catch (e) {
    setMsg(`Export failed: ${errMsg(e)}`);
  }
}

/** —— 导出“当前过滤结果”为 CSV（含 greeks / price） —— */
async function downloadFilteredCsv() {
  try {
    setMsg("");
    if (filteredRows.length === 0) {
      setMsg("No filtered alerts.");
      return;
    }

    const limit = pLimit(6);
    const rowsOut: (string | number | null | undefined)[][] = [];

    await Promise.all(
      filteredRows.map(a =>
        limit(async () => {
          const [gk, pr] = await Promise.allSettled([
            fetchAlertGreeks(a.alert_id),
            fetchAlertPrice(a.alert_id),
          ]);
          const g = gk.status === "fulfilled" ? gk.value : null;
          const p = pr.status === "fulfilled" ? pr.value : null;
          rowsOut.push(buildCsvRow(a, g, p));
        })
      )
    );

    // 按创建时间排序（CSV_HEADERS 第2列 created_at_et）
    rowsOut.sort((ra, rb) => {
      const aT = new Date(String(ra[1] ?? "")).getTime();
      const bT = new Date(String(rb[1] ?? "")).getTime();
      return (isNaN(aT) ? 0 : aT) - (isNaN(bT) ? 0 : bT);
    });

    const csv = toCsv(
      CSV_HEADERS.slice(1),       
      rowsOut.map(r => r.slice(1)) 
    );
    downloadText(`Filtered_${bizDate}.csv`, csv);
    setMsg(`Filtered CSV exported (${rowsOut.length} rows).`);
  } catch (e) {
    setMsg(`Export failed: ${errMsg(e)}`);
  }
}




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
        <label className="text-sm">
          Filter tickers:
          <input
            type="text"
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value)}
            placeholder="e.g. NVDA, AAPL  or  SP*  or  !QQQ"
            className="ml-2 border rounded px-2 py-1 w-64"
          />
        </label>
        <button
          onClick={() => setSymbolFilter("")}
          className="px-3 py-1 rounded border border-gray-700 bg-gray-900 text-gray-100 hover:bg-gray-800"
        >
          Clear
        </button>

        {/* 小统计：当前/总数 */}
        <span className="text-xs text-gray-500 ml-1">
          {filteredRows.length}/{rows.length}
        </span>

        <button
          onClick={() => downloadGroupCsv("Indices", INDICES)}
          className="px-3 py-1 rounded border border-gray-700 bg-gray-900 text-gray-100 hover:bg-gray-800"
          disabled={loading}
        >
          Download Indices (CSV)
        </button>

        <button
          onClick={() => downloadGroupCsv("M7", M7)}
          className="px-3 py-1 rounded border border-gray-700 bg-gray-900 text-gray-100 hover:bg-gray-800"
          disabled={loading}
        >
          Download M7 (CSV)
        </button>

        <button
          type="button"
          onClick={() => { void downloadFilteredCsv(); }}
          className="px-3 py-1 rounded border border-gray-700 bg-gray-900 text-gray-100 hover:bg-gray-800"
          disabled={filteredRows.length === 0}
          title={filteredRows.length ? "Export current filtered alerts" : "No filtered rows"}
        >
          Download Filtered (CSV)
        </button>





        {msg && <div className="text-sm text-gray-600">{msg}</div>}
      </div>

      {/* 主体：左表 + 右侧详情与编辑 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <AlertsTable
            rows={filteredRows}
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
                          <KV k="Expiry" v={greeks.expiry ?? "—"} />
                          <KV k="DTE" v={fmtNum(greeks.dte, 0)} />
                          <KV k="Strike" v={fmtNum(greeks.strike)} />
                          <KV k="OTM %" v={fmtPct(greeks.otm_pct)} />
                          <KV k="Delta" v={fmtNum(greeks.delta, 5)} />
                          <KV k="Gamma" v={fmtNum(greeks.gamma, 5)} />
                          <KV k="Theta" v={fmtNum(greeks.theta, 5)} />
                          <KV k="Rho" v={fmtNum(greeks.rho, 5)} />
                          <KV k="Vega" v={fmtNum(greeks.vega, 5)} />
                          <KV k="Vanna" v={fmtNum(greeks.vanna, 5)} />
                          <KV k="Charm" v={fmtNum(greeks.charm, 5)} />
                          <KV k="Volatility" v={fmtNum(greeks.volatility, 5)} />
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
                      value={tradeTypesDraft}
                      onChange={(e) => setTradeTypesDraft(e.target.value)}
                    />
                  </div>

                  <div>
                    <div className="text-sm mb-1">Reason codes (comma-separated)</div>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      placeholder="iv, sweep, block, catalyst..."
                      value={reasonCodesDraft}
                      onChange={(e) => setReasonCodesDraft(e.target.value)}
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
        review={review}
      />
    </main>
  );
}

/** —— 小组件 —— */
function parseCsvToList(s: string): string[] {
  return s.split(/[,\n]/).map(x => x.trim()).filter(Boolean);
}

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

function DecisionBadge({ decision }: { decision: Decision | null }) {
  const map: Record<string, string> = {
    accept: "bg-green-600/20 text-green-300 border-green-700",
    reject: "bg-red-600/20 text-red-300 border-red-700",
    watch:  "bg-amber-500/20 text-amber-300 border-amber-600",
  };
  const cls =
    decision ? map[decision] ?? "bg-gray-600/20 text-gray-300 border-gray-700" : "bg-gray-600/20 text-gray-300 border-gray-700";
  const label = decision ?? "none";
  return (
    <span className={`px-2 py-0.5 rounded border text-xs ${cls}`}>{label}</span>
  );
}

function Chips({ items }: { items: string[] }) {
  if (!items || items.length === 0) {
    return <span className="text-gray-500">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((t, i) => (
        <span
          key={`${t}-${i}`}
          className="px-2 py-0.5 rounded-full border border-gray-700 bg-gray-800/60 text-gray-100 text-xs"
        >
          {t}
        </span>
      ))}
    </div>
  );
}

/** —— 全屏覆盖层（不显示 alert_id，黑底白字版，支持导出 PNG） —— */
function AlertFullOverlay({
  open,
  onClose,
  alert,
  greeks,
  price,
  buckets,
  review,
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
  review: ReviewOut | null;
}) {
  /** ✅ Hooks 必须在顶部调用，不能放在条件分支之后 */
  const contentRef = useRef<HTMLDivElement | null>(null);

  const palette = sidePalette(greeks?.side);

  /** Download PNG */
  async function handleDownloadPng(hidpi: number = 2) {
    const node = contentRef.current;
    if (!node) return;

    // 实际内容尺寸
    const rect = node.getBoundingClientRect();
    const sw = Math.max(node.scrollWidth,  Math.ceil(rect.width));
    const sh = Math.max(node.scrollHeight, Math.ceil(rect.height));

    // 浏览器安全上限（保守，兼容 Safari）
    const MAX_DIM = 14336;               // 单边像素上限
    const MAX_PIXELS = 180_000_000;      // 总像素上限（约 180MP）

    // 先算“为了不超限，需要的基础缩放”
    const byW = MAX_DIM / sw;
    const byH = MAX_DIM / sh;
    const byA = Math.sqrt(MAX_PIXELS / (sw * sh));
    const baseScale = Math.min(1, byW, byH, byA);

    // 缩放后的导出逻辑尺寸（不含 pixelRatio）
    const outW = Math.max(1, Math.floor(sw * baseScale));
    const outH = Math.max(1, Math.floor(sh * baseScale));

    // 在不突破上限的前提下，尽量把 pixelRatio 拉高（= 超采样）
    const maxPrByW = MAX_DIM / outW;
    const maxPrByH = MAX_DIM / outH;
    const maxPrByA = Math.sqrt(MAX_PIXELS / (outW * outH));
    const dpr = window.devicePixelRatio || 1;

    // 期望的清晰度倍率：设备 DPR * 目标 hidpi（2/3），再夹到 3x 以内
    const wanted = Math.min(Math.max(1, dpr * hidpi), 3);
    const pixelRatio = Math.min(wanted, maxPrByW, maxPrByH, maxPrByA);

    try {
      const dataUrl = await toPng(node, {
        cacheBust: true,
        backgroundColor: "#111827",
        // 用宽高 + pixelRatio 控制实际画布像素数
        width: outW,
        height: outH,
        pixelRatio,
        // 克隆节点样式：移除左右留白、锁定尺寸、按 baseScale 等比整体缩放
        style: {
          margin: "0",
          maxWidth: "none",
          width: `${sw}px`,
          height: `${sh}px`,
          transform: `scale(${baseScale})`,
          transformOrigin: "top left",
          paddingLeft: "16px",
          paddingRight: "16px",
          // 文本抗锯齿（对某些平台有帮助）
          // @ts-expect-error -- vendor-prefixed property not in React.CSSProperties
          "-webkit-font-smoothing": "antialiased",
          "text-rendering": "geometricPrecision",
        },
      });

      const a = document.createElement("a");
      const sym = alert?.symbol ?? "alert";
      a.href = dataUrl;
      a.download = `${sym}_details_${Date.now()}_${Math.round(pixelRatio*100)}ppi.png`;
      a.click();
    } catch (e) {
      console.error(e);
      window.alert("导出图片失败，请稍后再试。");
    }
  }


  /** 再做早退就不会违反规则了 */
  if (!open) return null;

const onDownloadPngClick = () => { void handleDownloadPng(2); };

  return (
    <div className="fixed inset-0 z-50 bg-gray-900 text-gray-100">
      {/* 顶部栏 */}
      <div className="sticky top-0 z-10 border-b border-gray-700 bg-gray-900">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="font-medium text-white">Alert Full Details</div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onDownloadPngClick}
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
              <KV k="Ask Vol" v={fmtNum(alert.ask_volume, 0)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Bid Vol" v={fmtNum(alert.bid_volume, 0)} />
              <KV k="Volume" v={fmtNum(alert.volume, 0)} keyClassName={palette.key} valueClassName={palette.val} />
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
              <KV k="Expiry" v={greeks.expiry ?? "—"} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="DTE" v={fmtNum(greeks.dte, 0)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Strike" v={fmtNum(greeks.strike)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="OTM %" v={fmtPct(greeks.otm_pct)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Delta" v={fmtNum(greeks.delta, 5)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Gamma" v={fmtNum(greeks.gamma, 5)} keyClassName={palette.key} valueClassName={palette.val} />
              <KV k="Theta" v={fmtNum(greeks.theta, 5)} />
              <KV k="Rho" v={fmtNum(greeks.rho, 5)} />
              <KV k="Vega" v={fmtNum(greeks.vega, 5)} />
              <KV k="Vanna" v={fmtNum(greeks.vanna, 5)} />
              <KV k="Charm" v={fmtNum(greeks.charm, 5)} />
              <KV k="Volatility" v={fmtNum(greeks.volatility, 5)} />
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

        {/* Review */}
        <section>
          <h3 className="text-base font-semibold mb-3 text-white">Review</h3>
          {!review ? (
            <div className="text-gray-400 text-sm">None</div>
          ) : (
            <div className="space-y-3 text-sm">
              {/* 第一行：Decision + 审核人/时间 */}
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-gray-400">Decision:</span>
                <DecisionBadge decision={review.decision ?? null} />
                <span className="text-gray-500">•</span>
                <span className="text-gray-400">Reviewer:</span>
                <span className="text-gray-100">{review.reviewed_by ?? "—"}</span>
                <span className="text-gray-500">•</span>
                <span className="text-gray-400">Reviewed at:</span>
                <span className="text-gray-100">
                  {review.reviewed_at ? toET(review.reviewed_at) : "—"}
                </span>
                <span className="text-gray-500">•</span>
                <span className="text-gray-400">Row ver:</span>
                <span className="text-gray-100">{review.row_version ?? 0}</span>
              </div>

              {/* 第二行：Trade types */}
              <div className="flex items-start gap-2">
                <span className="text-gray-400 min-w-24">Trade types:</span>
                <Chips items={review.trade_types ?? []} />
              </div>

              {/* 第三行：Reason codes */}
              <div className="flex items-start gap-2">
                <span className="text-gray-400 min-w-24">Reason codes:</span>
                <Chips items={review.reason_codes ?? []} />
              </div>

              {/* 备注 */}
              <div>
                <div className="text-gray-400 mb-1">Notes</div>
                <div className="whitespace-pre-wrap rounded-lg border border-gray-700 bg-gray-800/60 p-3 text-gray-100">
                  {review.notes && review.notes.trim() ? review.notes : "—"}
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
