"use client";
import { AlertRow } from "@/lib/api";
import { toET } from "@/lib/time";

export default function AlertsTable({
  rows,
  onSelect,
  selectedId,
}: {
  rows: AlertRow[];
  onSelect: (row: AlertRow) => void;
  selectedId?: string | null;
}) {
  return (
    <div className="border rounded-2xl overflow-hidden">
      <table className="min-w-full text-sm text-white">
        <thead className="bg-gray-50 text-gray-700">
          <tr className="text-left">
            <th className="px-3 py-2">Time (ET)</th>
            <th className="px-3 py-2">Symbol</th>
            <th className="px-3 py-2">Option</th>
            <th className="px-3 py-2">Alert ID</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isSel = r.alert_id === selectedId;
            return (
              <tr
                key={r.alert_id}
                onClick={() => onSelect(r)}
                className={`cursor-pointer hover:bg-gray-600 ${
                  isSel ? "bg-white text-black" : "text-gray-200"
                }`}
              >
                <td className="px-3 py-2 whitespace-nowrap">
                  {toET(r.created_at_utc)}
                </td>
                <td className="px-3 py-2">{r.symbol ?? "—"}</td>
                <td className="px-3 py-2">{r.option_symbol ?? "—"}</td>
                <td className="px-3 py-2 text-gray-500">{r.alert_id}</td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td className="px-3 py-6 text-center text-gray-500" colSpan={5}>
                No data
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
