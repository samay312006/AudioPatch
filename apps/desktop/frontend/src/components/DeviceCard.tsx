import { motion } from "framer-motion";
import {
  AlertTriangle, Bell, Bluetooth, Cable, Check, Clock, Headphones, Mic, MonitorSpeaker, Plus, Smartphone, Speaker, Tv, Usb,
  Volume2, VolumeX, X, type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Device, DeviceKind, EngineStatus, Routing } from "../types";
import { post } from "../useEngine";
import { Meter } from "./Meter";

const ICONS: Record<DeviceKind, LucideIcon> = {
  bluetooth: Bluetooth, usb: Usb, hdmi: Tv, wired: Headphones, builtin: Speaker,
  virtual: Cable, loopback: MonitorSpeaker, phone: Smartphone, unknown: Volume2,
};
const LABELS: Record<DeviceKind, string> = {
  bluetooth: "Bluetooth", usb: "USB", hdmi: "HDMI", wired: "Wired", builtin: "Built-in",
  virtual: "Virtual", loopback: "System audio", phone: "Phone app · Bluetooth", unknown: "Other",
};

const focusRing = "focus-visible:outline-2 focus-visible:outline-sky-400";

export function DeviceIcon({ d }: { d: Device }) {
  const Icon = d.direction === "input" && d.kind === "builtin" ? Mic : ICONS[d.kind];
  const tone = !d.connected ? "bg-zinc-800 text-zinc-500"
    : d.kind === "bluetooth" ? "bg-sky-500/15 text-sky-400"
    : d.kind === "phone" ? "bg-violet-500/15 text-violet-300" : "bg-emerald-500/15 text-emerald-400";
  return (
    <span className={`grid size-10 shrink-0 place-items-center rounded-lg ${tone}`}>
      <Icon className="size-5" aria-hidden />
    </span>
  );
}

function Subtitle({ d, measured }: { d: Device; measured?: number }) {
  if (!d.connected) return <p className="text-xs text-amber-400/80">Disconnected · resumes when it reconnects</p>;
  return (
    <p className="truncate text-xs text-zinc-400">
      {d.extra?.captures ? <>Captures {d.extra.captures}</> : LABELS[d.kind]} · {d.channels}ch · {d.samplerate / 1000} kHz
      {measured ? <span title="Measured by the sync calibration"> · {Math.round(measured)} ms lag</span> : null}
    </p>
  );
}

/** Slider that updates locally while dragging and sends at most ~20 updates/s. */
function ParamSlider({ id, param, label, tip, value, min, max, step, format, icon }: {
  id: string; param: string; label: string; tip: string; value: number;
  min: number; max: number; step: number; format: (v: number) => string; icon?: React.ReactNode;
}) {
  const [local, setLocal] = useState(value);
  const dragging = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { if (!dragging.current) setLocal(value); }, [value]);

  const send = (v: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => post("/api/params", { id, [param]: v }).catch(() => {}), 50);
  };
  return (
    <label className="flex flex-1 items-center gap-2" title={tip}>
      {icon}
      <span className="sr-only">{label}</span>
      <input
        type="range" min={min} max={max} step={step} value={local}
        onPointerDown={() => { dragging.current = true; }}
        onPointerUp={() => { dragging.current = false; }}
        onChange={(e) => { const v = Number(e.target.value); setLocal(v); send(v); }}
        className="h-1 min-w-0 flex-1 cursor-pointer accent-sky-400"
      />
      <span className="w-14 text-right text-xs tabular-nums text-zinc-400">{format(local)}</span>
    </label>
  );
}

function IconToggle({ on, onClick, label, title, children }: {
  on: boolean; onClick: () => void; label: string; title: string; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      aria-label={label}
      title={title}
      className={`grid size-8 place-items-center rounded-lg text-xs font-semibold ${focusRing} ${
        on ? "bg-amber-500/20 text-amber-300" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
      }`}
    >
      {children}
    </button>
  );
}

/** A source or output that's part of the setup. */
export function ActiveCard({ d, routing, engine, byId, onError }: {
  d: Device; routing: Routing; engine: EngineStatus; byId: Map<string, Device>; onError: (m: string) => void;
}) {
  const isSource = d.direction === "input";
  const params = routing.params[d.id] ?? {};
  const status = engine.devices[d.id];
  const routed = new Set(routing.routes[d.id] ?? []);
  const feedbackOut = new Set(engine.feedback.filter((f) => f.source === d.id).map((f) => f.output));
  const fail = (e: Error) => onError(e.message);

  const setParam = (p: Record<string, unknown>) => post("/api/params", { id: d.id, ...p }).catch(fail);
  const remove = () => post(`/api/${isSource ? "sources" : "outputs"}/remove`, { id: d.id }).catch(fail);
  const test = () => post("/api/outputs/test", { id: d.id }).catch(fail);

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className={`rounded-xl border px-4 py-3 ${d.connected ? "border-zinc-700 bg-zinc-900" : "border-zinc-800 bg-zinc-900/50"}`}
    >
      <div className="flex items-center gap-3">
        <DeviceIcon d={d} />
        <div className="min-w-0 flex-1">
          <p className={`truncate font-medium ${d.connected ? "text-zinc-100" : "text-zinc-500"}`} title={d.name}>{d.name}</p>
          <Subtitle d={d} measured={params.measured_ms} />
        </div>
        <button
          onClick={remove}
          title={`Remove ${d.name} from the setup`}
          aria-label={`Remove ${d.name}`}
          className={`grid size-9 place-items-center rounded-lg text-zinc-500 hover:bg-zinc-800 hover:text-rose-400 ${focusRing}`}
        >
          <X className="size-4" />
        </button>
      </div>

      {status?.error && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-rose-300"><AlertTriangle className="mt-px size-3.5 shrink-0" />{status.error}</p>
      )}

      {d.connected && (
        <div className="mt-3 space-y-2">
          <Meter id={d.id} label={d.name} />
          <div className="flex items-center gap-1">
            <ParamSlider
              id={d.id} param="volume" label={`${d.name} volume`} tip="Volume (100% = unchanged, up to 200%)"
              value={params.volume ?? 1} min={0} max={2} step={0.01} format={(v) => `${Math.round(v * 100)}%`}
              icon={<Volume2 className="size-3.5 shrink-0 text-zinc-500" aria-hidden />}
            />
            <IconToggle
              on={!!params.muted} onClick={() => setParam({ muted: !params.muted })}
              label={`Mute ${d.name}`} title={params.muted ? "Unmute" : "Mute"}
            >
              {params.muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
            </IconToggle>
            {isSource ? (
              <IconToggle
                on={!!params.mono} onClick={() => setParam({ mono: !params.mono })}
                label={`Mono ${d.name}`} title={params.mono ? "Mono (click for stereo)" : "Stereo (click to fold down to mono)"}
              >
                {params.mono ? "M" : "ST"}
              </IconToggle>
            ) : (
              <button
                onClick={test}
                aria-label={`Play a test sound on ${d.name}`}
                title="Play a short chime on this device, so you can tell which is which"
                className={`grid size-8 place-items-center rounded-lg text-zinc-400 hover:bg-zinc-800 hover:text-sky-300 ${focusRing}`}
              >
                <Bell className="size-4" />
              </button>
            )}
          </div>
          {!isSource && (
            <div className="flex items-center gap-1 pr-[4.5rem]">
              <ParamSlider
                id={d.id} param="delay_ms" label={`${d.name} lip-sync delay`}
                tip="Lip-sync delay: hold this device back so it lines up with slower ones (e.g. Bluetooth). Calibrate sync sets this for you."
                value={params.delay_ms ?? 0} min={0} max={1000} step={1} format={(v) => `${Math.round(v)} ms`}
                icon={<Clock className="size-3.5 shrink-0 text-zinc-500" aria-hidden />}
              />
            </div>
          )}
        </div>
      )}

      {isSource && (
        <div className="mt-3 border-t border-zinc-800 pt-3">
          <p className="mb-2 text-xs text-zinc-500">Plays to</p>
          {routing.outputs.length === 0
            ? <p className="text-xs text-zinc-600">Add an output on the right first.</p>
            : (
              <div className="flex flex-wrap gap-2">
                {routing.outputs.map((oid) => {
                  const on = routed.has(oid);
                  const out = byId.get(oid);
                  const name = out?.name ?? routing.names[oid] ?? "Unknown device";
                  const loop = on && feedbackOut.has(oid);
                  return (
                    <button
                      key={oid}
                      role="switch"
                      aria-checked={on}
                      title={loop
                        ? `${d.name} is captured from ${name}, so sending it back would echo forever. It's paused for this device. Use VB-Cable (see the banner at the top) to fix this.`
                        : on ? `Stop sending ${d.name} to ${name}` : `Send ${d.name} to ${name}`}
                      onClick={() => post("/api/routes", { source: d.id, output: oid, on: !on }).catch(fail)}
                      className={`flex max-w-full items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${focusRing} ${
                        loop ? "border-amber-600/60 bg-amber-500/10 text-amber-200"
                          : on ? "border-sky-500/60 bg-sky-500/15 text-sky-200" : "border-zinc-700 text-zinc-400 hover:border-zinc-500"
                      } ${out && !out.connected ? "opacity-50" : ""}`}
                    >
                      {loop ? <AlertTriangle className="size-3 shrink-0" /> : on ? <Check className="size-3 shrink-0" /> : <Plus className="size-3 shrink-0" />}
                      <span className="truncate">{name}</span>
                    </button>
                  );
                })}
              </div>
            )}
        </div>
      )}
    </motion.li>
  );
}

export function AvailableRow({ d, onError }: { d: Device; onError: (m: string) => void }) {
  const add = () => post(`/api/${d.direction === "input" ? "sources" : "outputs"}`, { id: d.id }).catch((e) => onError(e.message));
  return (
    <motion.li layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <button
        onClick={add}
        title={`Add ${d.name} to the setup`}
        className={`flex w-full items-center gap-3 rounded-xl border border-dashed border-zinc-800 px-4 py-2.5 text-left hover:border-zinc-600 hover:bg-zinc-900 ${focusRing}`}
      >
        <DeviceIcon d={d} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-zinc-300">{d.name}</p>
          <Subtitle d={d} />
        </div>
        <span className="flex items-center gap-1 text-xs text-sky-400"><Plus className="size-4" /> Add</span>
      </button>
    </motion.li>
  );
}
