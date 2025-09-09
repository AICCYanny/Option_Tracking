"use client";
import { AlertRow, GreeksRow, PriceRow, BucketRow } from "@/lib/api";
import { toET } from "@/lib/time";
import { useMemo } from "react";

function N(val: number | null | undefined, digits = 2) {
  if (val === null || val === undefined) return "—";
  if (Number.isInteger(val)) return String(val);
  return Number(val).toFixed(digits);
}

export default function AlertDetails({
  alert,
  greeks,
  price,
  buckets,
}: {
  alert: AlertRow | null;
  greeks: GreeksRow | null;
  price: PriceRow | null;
  buckets: BucketRow[] | null;
}) {
  const hasData = !!alert;

  const rawPairs = useMemo(() => {
    if (!alert) return [];
    const entries: [string, string][] = [
      ["Alert ID", alert.alert_id],
      ["Created (ET)", toET(alert.created_at_utc ?? alert.created_at ?? "")],
      ["Biz Date (ET)", alert.biz_date_et ?? "—"],
      ["Symbol", alert.symbol ?? "—"],
      ["Option", alert.option_symbol ?? "—"],
      ["Ask Vol", N(alert.ask_volume, 0)],
      ["Bid Vol", N(alert.bid_volume, 0)],
      ["Volume", N(alert.volume, 0)],
      ["Avg Fill", N(alert.avg_fill)],
      ["Close", N(alert.close)],
      ["Diff", N(alert.diff)],
      ["Total Premium", N(alert.total_premium)],
      ["IV Change", N(alert.iv_change)],
      ["Open Interest", N(alert.open_interest, 0)],
      ["Vol/OI", N(alert.vol_oi_ratio)],
      ["Multi-leg Vol Ratio", N(alert.multi_leg_vol_ratio)],
    ];
    return entries;
  }, [alert]);

  return (
    <div className="border rounded-2xl p-4">
      <h2 className="font-medium mb-3">Details</h2>
      {!hasData && <div className="text-gray-500 text-sm">Select a row to start.</div>}
      {hasData && (
        <div className="space-y-6">
          {/* Raw Summary */}
          <section>
            <h3 className="text-sm font-semibold mb-2">Alert Summary</h3>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-2 text-sm">
              {rawPairs.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2">
                  <span className="text-gray-500">{k}</span>
                  <span className="font-medium break-all">{v}</span>
                </div>
              ))}
            </div>
          </section>

          {/* Tabs */}
          <div className="border rounded-xl overflow-hidden">
            <input type="radio" name="tab" id="tab-greeks" className="hidden peer/greeks" defaultChecked />
            <input type="radio" name="tab" id="tab-price" className="hidden peer/price" />
            <input type="radio" name="tab" id="tab-buckets" className="hidden peer/buckets" />

            <div className="flex border-b bg-gray-50 text-sm">
              <label htmlFor="tab-greeks" className="px-4 py-2 cursor-pointer hover:bg-gray-100">Greeks</label>
              <label htmlFor="tab-price" className="px-4 py-2 cursor-pointer hover:bg-gray-100">Price</label>
              <label htmlFor="tab-buckets" className="px-4 py-2 cursor-pointer hover:bg-gray-100">Buckets</label>
            </div>

            {/* Greeks Panel */}
            <div className="p-3 peer-checked/greeks:block hidden">
              {!greeks ? (
                <div className="text-sm text-gray-500">No greeks.</div>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-2 text-sm">
                  <KV k="Snapshot (ET)" v={toET(greeks.snapshot_at ?? "")} />
                  <KV k="Option" v={greeks.option_symbol ?? "—"} />
                  <KV k="Side" v={greeks.side ?? "—"} />
                  <KV k="DTE" v={N(greeks.dte, 0)} />
                  <KV k="Strike" v={N(greeks.strike)} />
                  <KV k="Delta" v={N(greeks.delta)} />
                  <KV k="Gamma" v={N(greeks.gamma)} />
                  <KV k="Theta" v={N(greeks.theta)} />
                  <KV k="Rho" v={N(greeks.rho)} />
                  <KV k="Vega" v={N(greeks.vega)} />
                  <KV k="Vanna" v={N(greeks.vanna)} />
                  <KV k="Charm" v={N(greeks.charm)} />
                  <KV k="Volatility" v={N(greeks.volatility)} />
                </div>
              )}
            </div>

            {/* Price Panel */}
            <div className="p-3 peer-checked/price:block hidden">
              {!price ? (
                <div className="text-sm text-gray-500">No price snapshot.</div>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-2 text-sm">
                  <KV k="Snapshot (ET)" v={toET(price.snapshot_at ?? "")} />
                  <KV k="Market Time" v={price.market_time ?? "—"} />
                  <KV k="Stock Close" v={N(price.stock_close)} />
                  <KV k="Prev Close" v={N(price.stock_previous_close)} />
                  <KV k="Stock Volume" v={N(price.stock_volume, 0)} />
                  <KV k="Total Volume" v={N(price.stock_total_volume, 0)} />
                </div>
              )}
            </div>

            {/* Buckets Panel */}
            <div className="p-3 peer-checked/buckets:block hidden">
              {!buckets || buckets.length === 0 ? (
                <div className="text-sm text-gray-500">No buckets.</div>
              ) : (
                <div className="overflow-auto rounded-lg border">
                  <table className="min-w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr className="text-left">
                        <TH>Start (ET)</TH>
                        <TH>End (ET)</TH>
                        <TH>Min</TH>
                        <TH>Volume</TH>
                        <TH>Multi%</TH>
                        <TH>Avg Price</TH>
                        <TH>IV Low</TH>
                        <TH>IV High</TH>
                        <TH>Premium</TH>
                      </tr>
                    </thead>
                    <tbody>
                      {buckets.map(b => (
                        <tr key={b.id} className="border-t">
                          <TD>{toET(b.bucket_start)}</TD>
                          <TD>{toET(b.bucket_end)}</TD>
                          <TD>{N(b.bucket_minutes, 0)}</TD>
                          <TD>{N(b.total_volume, 0)}</TD>
                          <TD>{N(b.bucket_multi_ratio)}</TD>
                          <TD>{N(b.avg_price)}</TD>
                          <TD>{N(b.avg_iv_low)}</TD>
                          <TD>{N(b.avg_iv_high)}</TD>
                          <TD>{N(b.total_premium)}</TD>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-gray-500">{k}</span>
      <span className="font-medium break-all">{v}</span>
    </div>
  );
}

function TH({ children }: { children: React.ReactNode }) {
  return <th className="px-2 py-2">{children}</th>;
}
function TD({ children }: { children: React.ReactNode }) {
  return <td className="px-2 py-2 whitespace-nowrap">{children}</td>;
}
