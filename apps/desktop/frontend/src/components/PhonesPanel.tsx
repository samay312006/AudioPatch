import { AnimatePresence, motion } from "framer-motion";
import { ArrowDownToLine, ArrowUpFromLine, Loader2, RefreshCw, Smartphone } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { Device, Phone, SystemInfo } from "../types";

const focusRing = "focus-visible:outline-2 focus-visible:outline-sky-400";

async function call(path: string, body?: unknown): Promise<Phone[]> {
  const r = await fetch(path, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : r.statusText);
  return j as Phone[];
}

/**
 * Phones paired with this PC over Bluetooth, with both phone features:
 *  - "Use as output": connect to the AudioPatch app on the phone (its Wireless
 *    output mode); the phone then appears under Outputs.
 *  - "Play its audio here": the PC becomes a Bluetooth speaker for the phone;
 *    its sound arrives through System audio.
 */
export function PhonesPanel({ system, devices, onError }: {
  system: SystemInfo | null; devices: Device[]; onError: (m: string) => void;
}) {
  const [phones, setPhones] = useState<Phone[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);  // "<phone id>:<feature>"
  const bt = system?.bluetooth;

  const [loadError, setLoadError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    call("/api/bt/phones")
      .then((p) => { setPhones(p); setLoadError(null); })
      // Keep what we know (don't claim "no phones") and try again shortly.
      .catch((e) => { setLoadError((e as Error).message); window.setTimeout(refresh, 3000); });
  }, []);

  // Refetch when the device list changes (a phone connected or dropped).
  const phoneDevices = devices.filter((d) => d.kind === "phone").map((d) => d.id).join(",");
  useEffect(() => { if (bt?.running) refresh(); }, [bt?.running, phoneDevices, refresh]);

  if (system && system.os !== "windows") return null;

  const act = async (key: string, path: string, body: unknown) => {
    setBusy(key);
    try { setPhones(await call(path, body)); } catch (e) { onError((e as Error).message); refresh(); }
    setBusy(null);
  };

  return (
    <section aria-label="Phones" className="mt-10">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
          Phones <span className="font-normal normal-case tracking-normal text-zinc-600">· Bluetooth</span>
        </h2>
        {bt?.running && (
          <button onClick={refresh} title="Refresh the list of paired phones"
            className={`grid size-8 place-items-center rounded-lg text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 ${focusRing}`}>
            <RefreshCw className="size-4" aria-label="Refresh" />
          </button>
        )}
      </div>

      {!bt?.running ? (
        <p className="rounded-xl border border-zinc-800 px-4 py-3 text-sm text-zinc-500">
          {bt?.error ?? "Bluetooth is starting…"}
        </p>
      ) : phones === null ? (
        <p className="flex items-center gap-2 px-1 text-sm text-zinc-500">
          <Loader2 className="size-4 animate-spin" />
          {loadError ? "Couldn't read Bluetooth devices yet, retrying…" : "Looking for paired phones…"}
        </p>
      ) : phones.length === 0 ? (
        <p className="rounded-xl border border-zinc-800 px-4 py-3 text-sm text-zinc-500">
          No phones paired with this PC. Pair one in Windows Settings → Bluetooth &amp; devices, then refresh.
        </p>
      ) : (
        <ul className="grid gap-2 md:grid-cols-2">
          <AnimatePresence initial={false}>
            {phones.map((p) => (
              <motion.li key={p.id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                className="rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-violet-500/15 text-violet-300">
                    <Smartphone className="size-5" aria-hidden />
                  </span>
                  <p className="min-w-0 flex-1 truncate font-medium text-zinc-100" title={p.name}>{p.name}</p>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <Feature
                    icon={<ArrowDownToLine className="size-4" aria-hidden />}
                    label="Use as output"
                    on={p.output.connected}
                    busy={busy === `${p.id}:out` || p.output.connecting}
                    tip={p.output.connected
                      ? "Connected. Add it under Outputs (above) to send it sound. Click to disconnect."
                      : `Plays this PC's sound on ${p.name}. Open AudioPatch on the phone and turn on Wireless output first.`}
                    onClick={() => act(`${p.id}:out`,
                      p.output.connected ? "/api/bt/phones/output/disconnect" : "/api/bt/phones/output", { id: p.id })}
                  />
                  <Feature
                    icon={<ArrowUpFromLine className="size-4" aria-hidden />}
                    label="Play its audio here"
                    on={p.audio.connected}
                    disabled={!p.audio.available}
                    busy={busy === `${p.id}:audio`}
                    tip={!p.audio.available
                      ? "This phone can't send audio to this PC"
                      : p.audio.connected
                        ? "The phone's sound is playing through this PC (as part of System audio). Click to stop."
                        : `This PC becomes a Bluetooth speaker for ${p.name}: everything it plays comes through System audio. Works with every app.`}
                    onClick={() => p.audio.id && act(`${p.id}:audio`,
                      p.audio.connected ? "/api/bt/phones/audio/disconnect" : "/api/bt/phones/audio", { id: p.audio.id })}
                  />
                </div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

function Feature({ icon, label, on, busy, disabled, tip, onClick }: {
  icon: React.ReactNode; label: string; on: boolean; busy: boolean; disabled?: boolean; tip: string; onClick: () => void;
}) {
  return (
    <button
      role="switch" aria-checked={on} title={tip} disabled={disabled || busy} onClick={onClick}
      className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs transition-colors disabled:opacity-40 ${focusRing} ${
        on ? "border-violet-400/60 bg-violet-500/15 text-violet-200" : "border-zinc-700 text-zinc-300 hover:border-zinc-500"
      }`}
    >
      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {busy ? "Connecting…" : on ? `${label} · on` : label}
    </button>
  );
}
