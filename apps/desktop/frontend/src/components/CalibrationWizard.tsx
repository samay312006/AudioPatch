import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Hand, Mic, Waves, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Calibration, Device, Routing } from "../types";
import { post } from "../useEngine";

type Mode = "mic" | "tap";
type Quiet = { id: string; name: string; volume: number };

const btn = "rounded-lg px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-sky-300 disabled:opacity-50";
const primary = `${btn} bg-sky-500 text-zinc-950 hover:bg-sky-400`;
const secondary = `${btn} border border-zinc-700 text-zinc-200 hover:bg-zinc-800`;

/** Measures each output's lag with a chirp and lines them all up. */
export function CalibrationWizard({ open, onClose, routing, devices, calibration, resetCalibration, onError }: {
  open: boolean; onClose: () => void; routing: Routing; devices: Device[];
  calibration: Calibration | null; resetCalibration: () => void; onError: (m: string) => void;
}) {
  const running = devices.filter((d) => routing.outputs.includes(d.id) && d.connected);
  const [mode, setMode] = useState<Mode>("mic");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [mic, setMic] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [quiet, setQuiet] = useState<Quiet[]>([]);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setStarted(false);
    setQuiet([]);
    setChosen(new Set(running.map((d) => d.id)));
    fetch("/api/calibrate/mic").then((r) => r.json()).then((m) => setMic(m.name)).catch(() => setMic(null));
    dialog.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const state = started ? calibration?.state : undefined;
  const isRunning = state === "running";

  const close = () => {
    if (isRunning) post("/api/calibrate/cancel", {}).catch(() => {});
    onClose();
  };

  // Space taps along in tap mode; Escape closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.code === "Space" && isRunning && mode === "tap") {
        e.preventDefault();
        post("/api/calibrate/tap", {}).catch(() => {});
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const start = async () => {
    resetCalibration();  // don't flash the previous run's results
    try {
      const r = await fetch("/api/calibrate/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, outputs: [...chosen] }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? r.statusText);
      setQuiet(j.quiet ?? []);
      setStarted(true);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const apply = async () => {
    try {
      await post("/api/calibrate/apply", {});
      onClose();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const nameOf = (id: string) => devices.find((d) => d.id === id)?.name ?? routing.names[id] ?? id;

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 grid place-items-center bg-black/70 px-4"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}
        >
          <motion.div
            ref={dialog}
            role="dialog" aria-modal="true" aria-labelledby="calib-title" tabIndex={-1}
            initial={{ y: 16, scale: 0.98 }} animate={{ y: 0, scale: 1 }} exit={{ y: 16, opacity: 0 }}
            className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-zinc-700 bg-zinc-900 p-6 text-zinc-100 shadow-2xl outline-none"
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <h2 id="calib-title" className="text-lg font-semibold">Calibrate sync</h2>
              <button onClick={close} aria-label="Close" className="grid size-8 place-items-center rounded-lg text-zinc-400 hover:bg-zinc-800">
                <X className="size-4" />
              </button>
            </div>

            {!started && (
              <Intro
                mode={mode} setMode={setMode} mic={mic} running={running} chosen={chosen} setChosen={setChosen}
                onStart={start}
              />
            )}

            {started && (!calibration || isRunning) && (
              <Running calibration={calibration} mode={mode} nameOf={nameOf} quiet={quiet} onCancel={close} />
            )}

            {state === "done" && calibration?.state === "done" && (
              <Results calibration={calibration} nameOf={nameOf} onApply={apply} onRetry={() => setStarted(false)} />
            )}

            {(state === "error" || state === "cancelled") && (
              <div className="space-y-4">
                <p className="text-sm text-zinc-300">
                  {calibration?.state === "error" ? `Calibration failed: ${calibration.error}` : "Calibration cancelled."}
                </p>
                <button className={secondary} onClick={() => setStarted(false)}>Start over</button>
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Intro({ mode, setMode, mic, running, chosen, setChosen, onStart }: {
  mode: Mode; setMode: (m: Mode) => void; mic: string | null; running: Device[];
  chosen: Set<string>; setChosen: (s: Set<string>) => void; onStart: () => void;
}) {
  const toggle = (id: string) => {
    const next = new Set(chosen);
    next.has(id) ? next.delete(id) : next.add(id);
    setChosen(next);
  };
  const modes = [
    { id: "mic" as const, icon: Mic, title: "Microphone (most accurate)",
      text: mic ? `Hold each device right next to ${mic} while it chirps.` : "No usable microphone found." },
    { id: "tap" as const, icon: Hand, title: "Tap along",
      text: "Press Space each time you hear the chirp. Good to about ±30 ms." },
  ];
  return (
    <div className="space-y-5">
      <p className="text-sm text-zinc-300">
        Every device has its own lag (Bluetooth adds 100-300 ms). AudioPatch plays a short chirp through each one,
        measures when it's heard, and delays the faster devices so everything plays together.
        Pause any video first.
      </p>

      <fieldset className="space-y-2">
        <legend className="mb-1 text-xs font-semibold uppercase tracking-wider text-zinc-500">How to measure</legend>
        {modes.map((m) => (
          <label
            key={m.id}
            className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${mode === m.id ? "border-sky-500/60 bg-sky-500/10" : "border-zinc-700 hover:border-zinc-500"}`}
          >
            <input type="radio" name="mode" className="sr-only" checked={mode === m.id} onChange={() => setMode(m.id)} />
            <m.icon className="mt-0.5 size-5 shrink-0 text-sky-400" aria-hidden />
            <span>
              <span className="block text-sm font-medium">{m.title}</span>
              <span className="block text-xs text-zinc-400">{m.text}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">Devices</legend>
        {running.length === 0 && <p className="text-sm text-zinc-500">Add and connect at least one output first.</p>}
        <div className="space-y-1">
          {running.map((d) => (
            <label key={d.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-zinc-800">
              <input type="checkbox" checked={chosen.has(d.id)} onChange={() => toggle(d.id)} className="accent-sky-400" />
              <span className="truncate">{d.name}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex justify-end">
        <button className={primary} disabled={chosen.size === 0 || (mode === "mic" && !mic)} onClick={onStart}>
          Start
        </button>
      </div>
    </div>
  );
}

function Running({ calibration, mode, nameOf, quiet, onCancel }: {
  calibration: Calibration | null; mode: Mode; nameOf: (id: string) => string; quiet: Quiet[]; onCancel: () => void;
}) {
  const c = calibration?.state === "running" ? calibration : null;
  const name = c ? nameOf(c.output) : "Getting ready…";
  return (
    <div className="space-y-5 text-center">
      {quiet.length > 0 && (
        <p className="flex items-start gap-2 rounded-lg border border-amber-700 bg-amber-950 px-3 py-2 text-left text-xs text-amber-200">
          <AlertTriangle className="mt-px size-4 shrink-0" />
          <span>
            {quiet.map((q) => `${q.name} is at ${q.volume}%`).join(", ")} in Windows' volume settings.
            Turn it up or the {mode === "mic" ? "mic" : "you"} may not hear the chirps.
          </span>
        </p>
      )}
      {c && <p className="text-xs text-zinc-500">Device {c.index + 1} of {c.total}</p>}
      <motion.div
        key={`${c?.output}-${c?.click}`}
        initial={{ scale: 0.85, opacity: 0.4 }} animate={{ scale: 1, opacity: 1 }}
        className="mx-auto grid size-24 place-items-center rounded-full bg-sky-500/15 text-sky-300"
      >
        <Waves className="size-10" aria-hidden />
      </motion.div>
      <div aria-live="polite">
        <p className="text-lg font-semibold">{name}</p>
        <p className="text-sm text-zinc-400">
          {mode === "mic" ? "Hold it right next to the laptop's microphone." : "Press Space when you hear each chirp."}
        </p>
        {c && <p className="mt-1 text-xs text-zinc-500">Chirp {c.click} of {c.clicks}</p>}
      </div>
      {mode === "tap" && (
        <button
          className="mx-auto block w-full max-w-xs rounded-xl bg-zinc-800 py-6 text-lg font-semibold hover:bg-zinc-700 active:bg-sky-500 active:text-zinc-950"
          onPointerDown={() => post("/api/calibrate/tap", {}).catch(() => {})}
        >
          Tap
        </button>
      )}
      <button className={secondary} onClick={onCancel}>Cancel</button>
    </div>
  );
}

function Results({ calibration, nameOf, onApply, onRetry }: {
  calibration: Extract<Calibration, { state: "done" }>; nameOf: (id: string) => string;
  onApply: () => void; onRetry: () => void;
}) {
  const rows = Object.entries(calibration.outputs);
  const anyOk = Object.keys(calibration.delays).length > 0;
  const failed = rows.filter(([, r]) => !r.ok);
  return (
    <div className="space-y-5">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-zinc-500">
            <th className="pb-2 font-semibold">Device</th>
            <th className="w-24 pb-2 text-right font-semibold">{calibration.mode === "mic" ? "Lag" : "Heard after"}</th>
            <th className="w-24 pb-2 text-right font-semibold">Set delay</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([id, r]) => (
            <tr key={id} className="border-t border-zinc-800">
              <td className="max-w-0 truncate py-2 pr-2" title={nameOf(id)}>
                <span className="flex items-center gap-1.5">
                  {r.ok ? <CheckCircle2 className="size-4 shrink-0 text-emerald-400" /> : <AlertTriangle className="size-4 shrink-0 text-amber-400" />}
                  <span className="truncate">{nameOf(id)}</span>
                </span>
              </td>
              <td className="py-2 text-right tabular-nums text-zinc-300">
                {r.ok ? <>{Math.round(r.latency_ms!)} ms <span className="text-xs text-zinc-500">±{Math.round(r.spread_ms! / 2)}</span></> : "—"}
              </td>
              <td className="py-2 text-right tabular-nums">{r.ok ? `${calibration.delays[id]} ms` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {failed.length > 0 && (
        <p className="rounded-lg border border-amber-700 bg-amber-950 px-3 py-2 text-xs text-amber-200">
          Couldn't get a steady reading for {failed.map(([id]) => nameOf(id)).join(", ")}. Turn its volume up,
          hold it closer to the mic, keep the room quiet, and try again. Its delay won't be changed.
        </p>
      )}
      {calibration.clamped.length > 0 && (
        <p className="text-xs text-amber-300">
          {calibration.clamped.map(nameOf).join(", ")} would need more than 500 ms of delay; it's set to the maximum.
        </p>
      )}
      {rows.length === 1 && anyOk && (
        <p className="text-xs text-zinc-400">With one device there's nothing to line up, but its lag is saved for reference.</p>
      )}

      <div className="flex justify-end gap-2">
        <button className={secondary} onClick={onRetry}>Measure again</button>
        <button className={primary} disabled={!anyOk} onClick={onApply}>Apply delays</button>
      </div>
    </div>
  );
}
