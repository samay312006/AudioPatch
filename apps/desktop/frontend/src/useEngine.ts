import { useEffect, useState, useSyncExternalStore } from "react";
import type { Calibration, Device, EngineStatus, Routing, SystemInfo } from "./types";

const EMPTY: Routing = { sources: [], outputs: [], routes: {}, names: {}, params: {} };

/** Levels arrive ~30x/s; keep them outside React state so only meters re-render. */
const levels = {
  value: {} as Record<string, number>,
  subs: new Set<() => void>(),
  set(v: Record<string, number>) {
    this.value = v;
    this.subs.forEach((f) => f());
  },
};

export function useLevel(id: string): number {
  return useSyncExternalStore(
    (cb) => { levels.subs.add(cb); return () => levels.subs.delete(cb); },
    () => levels.value[id] ?? 0,
  );
}

/** Live connection to the backend: devices, routing, and engine state arrive over the WebSocket. */
export function useEngine() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [routing, setRouting] = useState<Routing>(EMPTY);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [engine, setEngine] = useState<EngineStatus>({ mode: "stable", devices: {}, feedback: [] });
  const [online, setOnline] = useState(false);
  const [calibration, setCalibration] = useState<Calibration | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    const connect = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.onopen = () => setOnline(true);
      ws.onclose = () => {
        setOnline(false);
        levels.set({});
        retry = window.setTimeout(connect, 1000);
      };
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "levels") levels.set(msg.levels);
        else if (msg.type === "devices") setDevices(msg.devices);
        else if (msg.type === "routing") setRouting(msg.routing);
        else if (msg.type === "system") setSystem(msg.system);
        else if (msg.type === "engine") setEngine(msg.engine);
        else if (msg.type === "calibration") setCalibration(msg.calibration);
      };
    };
    connect();
    return () => {
      window.clearTimeout(retry);
      if (ws) { ws.onclose = null; ws.close(); }
    };
  }, []);

  return { devices, routing, system, engine, online, calibration, setCalibration };
}

/** POST a JSON body; throws with the server's message on failure. */
export async function post(path: string, body: unknown): Promise<void> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().then((j) => j.detail).catch(() => r.statusText);
    throw new Error(typeof detail === "string" ? detail : r.statusText);
  }
}

/**
 * Windows' own volume (0..1) for each output in the setup. With VB-Cable as the
 * default device, the keyboard volume keys no longer reach real devices, so a
 * speaker left at 2% would play almost nothing. Polled: it changes outside the app.
 */
export function useWindowsVolumes(outputs: string[]) {
  const [vols, setVols] = useState<Record<string, number>>({});
  const key = outputs.join(",");
  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/windows-volume").then((r) => r.json())
      .then((v) => { if (alive) setVols(v); }).catch(() => {});
    load();
    const t = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, [key]);
  const set = async (id: string, volume: number) => {
    const r = await fetch("/api/windows-volume", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, volume }),
    });
    if (r.ok) setVols(await r.json());
    else throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
  };
  return { vols, set };
}
