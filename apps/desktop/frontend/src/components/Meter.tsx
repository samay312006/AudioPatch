import { useLevel } from "../useEngine";

/** Peak meter on a -60..0 dB scale. Only this component re-renders at 30 Hz. */
export function Meter({ id, label }: { id: string; label: string }) {
  const peak = useLevel(id);
  const db = peak > 0 ? 20 * Math.log10(peak) : -Infinity;
  const pct = Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
  const color = db > -3 ? "bg-rose-400" : db > -12 ? "bg-amber-300" : "bg-emerald-400";
  return (
    <div
      role="meter"
      aria-label={`${label} level`}
      aria-valuemin={-60}
      aria-valuemax={0}
      aria-valuenow={Number.isFinite(db) ? Math.round(db) : -60}
      className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"
    >
      <div className={`h-full rounded-full ${color} transition-[width] duration-75 ease-out`} style={{ width: `${pct}%` }} />
    </div>
  );
}
