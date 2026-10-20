import { Cable, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import type { SystemInfo } from "../types";
import { post } from "../useEngine";

/** Guides the one-time virtual-cable setup that lets AudioPatch own every output. */
export function SetupBanner({ system, onError }: { system: SystemInfo; onError: (m: string) => void }) {
  const [busy, setBusy] = useState(false);

  if (system.os === "macos") {
    if (system.blackhole_installed) return null;
    return (
      <Banner tone="amber" title="One-time setup: install BlackHole">
        System audio capture on macOS needs the free BlackHole driver. Install it with{" "}
        <code className="rounded bg-black/30 px-1">brew install blackhole-2ch</code>, choose "BlackHole 2ch" as your Mac's sound output, then restart AudioPatch.
      </Banner>
    );
  }
  if (system.os !== "windows") return null;

  const vc = system.virtual_cable;
  if (vc.is_default) {
    return (
      <p className="mb-6 flex items-center gap-2 text-sm text-emerald-300">
        <CheckCircle2 className="size-4" /> Apps play silently into VB-Cable, and AudioPatch sends the sound to your devices.
      </p>
    );
  }
  if (!vc.installed) {
    return (
      <Banner tone="amber" title="One-time setup: install VB-Cable (about 3 MB)">
        Right now apps like YouTube play straight to <b>{system.default_output ?? "your default device"}</b>, so AudioPatch can't
        delay or re-route that device. Install the free{" "}
        <a className="underline" href="https://vb-audio.com/Cable/" target="_blank" rel="noreferrer">VB-Audio Virtual Cable</a>{" "}
        (extract the zip, right-click <code className="rounded bg-black/30 px-1">VBCABLE_Setup_x64.exe</code> → Run as administrator → Install Driver),
        then come back here.
      </Banner>
    );
  }
  const use = async () => {
    setBusy(true);
    try { await post("/api/system/use-virtual-cable", {}); } catch (e) { onError((e as Error).message); }
    setBusy(false);
  };
  return (
    <Banner tone="sky" title="VB-Cable is installed">
      <span className="mr-3">
        Make it the Windows output so apps play into it, and AudioPatch sends the sound to your chosen devices.
        (Windows sound settings will show "CABLE Input" as the output; switch back any time.)
      </span>
      <button
        onClick={use}
        disabled={busy}
        className="mt-2 rounded-lg bg-sky-500 px-3 py-1.5 text-sm font-medium text-zinc-950 hover:bg-sky-400 focus-visible:outline-2 focus-visible:outline-sky-200 disabled:opacity-50"
      >
        {busy ? "Switching…" : "Use VB-Cable as Windows output"}
      </button>
    </Banner>
  );
}

function Banner({ tone, title, children }: { tone: "amber" | "sky"; title: string; children: React.ReactNode }) {
  const c = tone === "amber" ? "border-amber-700 bg-amber-950 text-amber-100" : "border-sky-700 bg-sky-950 text-sky-100";
  return (
    <div role="status" className={`mb-6 flex gap-3 rounded-lg border px-4 py-3 text-sm ${c}`}>
      <Cable className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div>
        <p className="mb-1 font-semibold">{title}</p>
        <div>{children}</div>
      </div>
    </div>
  );
}
