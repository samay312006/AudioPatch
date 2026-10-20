import { AnimatePresence, motion } from "framer-motion";
import { Waves } from "lucide-react";
import { useState } from "react";
import { CalibrationWizard } from "./components/CalibrationWizard";
import { PhonesPanel } from "./components/PhonesPanel";
import { ActiveCard, AvailableRow } from "./components/DeviceCard";
import { SetupBanner } from "./components/SetupBanner";
import type { Device, EngineStatus, Routing } from "./types";
import { post, useEngine } from "./useEngine";

function Column({ title, hint, active, available, routing, engine, byId, onError }: {
  title: string; hint: string; active: Device[]; available: Device[]; routing: Routing;
  engine: EngineStatus; byId: Map<string, Device>; onError: (m: string) => void;
}) {
  return (
    <section aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-zinc-400">
        {title} <span className="text-zinc-600">({active.length})</span>
      </h2>
      <ul className="space-y-2">
        <AnimatePresence initial={false}>
          {active.map((d) => <ActiveCard key={d.id} d={d} routing={routing} engine={engine} byId={byId} onError={onError} />)}
        </AnimatePresence>
        {active.length === 0 && <li className="rounded-xl border border-zinc-800 px-4 py-3 text-sm text-zinc-500">{hint}</li>}
      </ul>

      {available.length > 0 && (
        <>
          <h3 className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">Available</h3>
          <ul className="space-y-2">
            <AnimatePresence initial={false}>
              {available.map((d) => <AvailableRow key={d.id} d={d} onError={onError} />)}
            </AnimatePresence>
          </ul>
        </>
      )}
    </section>
  );
}

function ModeToggle({ mode, onError }: { mode: EngineStatus["mode"]; onError: (m: string) => void }) {
  const opts = [
    { id: "stable", label: "Stable", tip: "Bigger buffers: fewer dropouts, ~60 ms extra delay" },
    { id: "low", label: "Low latency", tip: "Smaller buffers: ~20 ms extra delay, may crackle on busy PCs" },
  ] as const;
  return (
    <div role="radiogroup" aria-label="Engine mode" className="flex rounded-lg border border-zinc-800 p-0.5 text-xs">
      {opts.map((o) => (
        <button
          key={o.id}
          role="radio"
          aria-checked={mode === o.id}
          title={o.tip}
          onClick={() => post("/api/engine/mode", { mode: o.id }).catch((e) => onError(e.message))}
          className={`rounded-md px-2.5 py-1 focus-visible:outline-2 focus-visible:outline-sky-400 ${
            mode === o.id ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const { devices, routing, system, engine, online, calibration, setCalibration } = useEngine();
  const [error, setError] = useState<string | null>(null);
  const [calibOpen, setCalibOpen] = useState(false);
  const runningOutputs = routing.outputs.filter((id) => engine.devices[id]?.running).length;
  const byId = new Map(devices.map((d) => [d.id, d]));

  // Keep the user's chosen order; fall back to a placeholder if a chosen device isn't known yet.
  const pick = (ids: string[], direction: Device["direction"]) =>
    ids.map((id) => byId.get(id) ?? {
      id, name: routing.names[id] ?? "Unknown device", direction, kind: "unknown" as const,
      connected: false, channels: 0, samplerate: 0, latency_ms: 0, is_default: false,
    });
  const chosen = new Set([...routing.sources, ...routing.outputs]);
  const availableOf = (dir: Device["direction"]) => devices.filter((d) => d.direction === dir && d.connected && !chosen.has(d.id));

  const onError = (m: string) => {
    setError(m);
    window.setTimeout(() => setError(null), 5000);
  };

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 text-zinc-100">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight">AudioPatch</h1>
        <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
          <button
            onClick={() => setCalibOpen(true)}
            disabled={runningOutputs === 0}
            title={runningOutputs ? "Measure each output's lag and line them all up" : "Add a connected output first"}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 focus-visible:outline-2 focus-visible:outline-sky-400 disabled:opacity-40"
          >
            <Waves className="size-3.5" aria-hidden /> Calibrate sync
          </button>
          <ModeToggle mode={engine.mode} onError={onError} />
          <span className={`flex items-center gap-1.5 ${online ? "text-emerald-400" : "text-rose-400"}`}>
            <span className={`size-2 rounded-full ${online ? "bg-emerald-400" : "bg-rose-400"}`} /> {online ? "engine connected" : "engine offline"}
          </span>
        </div>
      </header>

      {system && <SetupBanner system={system} onError={onError} />}

      <AnimatePresence>
        {error && (
          <motion.p
            role="alert"
            initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="mb-6 rounded-lg border border-rose-800 bg-rose-950 px-4 py-3 text-sm text-rose-200"
          >
            {error}
          </motion.p>
        )}
      </AnimatePresence>

      <div className="grid gap-8 md:grid-cols-2">
        <Column
          title="Sources" hint="No sources yet. Add System audio or a mic below."
          active={pick(routing.sources, "input")} available={availableOf("input")}
          routing={routing} engine={engine} byId={byId} onError={onError}
        />
        <Column
          title="Outputs" hint="No outputs yet. Add headphones or speakers below."
          active={pick(routing.outputs, "output")} available={availableOf("output")}
          routing={routing} engine={engine} byId={byId} onError={onError}
        />
      </div>
      <PhonesPanel system={system} devices={devices} onError={onError} />

      <CalibrationWizard
        open={calibOpen} onClose={() => setCalibOpen(false)} routing={routing} devices={devices}
        calibration={calibration} resetCalibration={() => setCalibration(null)} onError={onError}
      />
      <p className="mt-8 text-xs text-zinc-500">Connect a device (e.g. Bluetooth headphones) and it shows up under Available within a couple of seconds.</p>
    </main>
  );
}
