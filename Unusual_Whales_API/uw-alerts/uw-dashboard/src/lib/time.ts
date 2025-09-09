export function toET(utcIsoLike: string | null | undefined): string {
  if (!utcIsoLike) return "—";
  
  // "YYYY-MM-DD HH:MM:SS+00:00" → "YYYY-MM-DDTHH:MM:SS+00:00"
  const s = utcIsoLike.replace(" ", "T");

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