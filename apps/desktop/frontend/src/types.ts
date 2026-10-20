export type DeviceKind = "bluetooth" | "usb" | "hdmi" | "wired" | "builtin" | "virtual" | "loopback" | "phone" | "unknown";

export interface Device {
  id: string;
  name: string;
  direction: "input" | "output";
  kind: DeviceKind;
  connected: boolean;
  channels: number;
  samplerate: number;
  latency_ms: number;
  is_default: boolean;
  extra?: { captures?: string; addr?: string; model?: string };
}

export interface SystemInfo {
  os: "windows" | "macos" | "linux";
  os_version: string;
  loopback_available: boolean;
  blackhole_installed: boolean | null;
  default_output: string | null;
  virtual_cable: { installed: boolean; output_id: string | null; is_default: boolean };
  bluetooth?: { running: boolean; error: string | null; name: string };
}

export interface DeviceParams {
  volume?: number;
  muted?: boolean;
  mono?: boolean;
  delay_ms?: number;
  measured_ms?: number;
}

export interface Routing {
  sources: string[];
  outputs: string[];
  routes: Record<string, string[]>;
  names: Record<string, string>;
  params: Record<string, DeviceParams>;
}

export interface EngineDevice {
  running: boolean;
  error?: string | null;
  latency_ms?: number | null;
  xruns?: number;
  underruns?: number;
}

export interface EngineStatus {
  mode: "stable" | "low";
  devices: Record<string, EngineDevice>;
  feedback: { source: string; output: string }[];
}

export interface CalibrationOutputResult {
  name: string;
  ok: boolean;
  latency_ms: number | null;
  spread_ms: number | null;
  heard: number;
  clicks: number;
}

export type Calibration =
  | { state: "running"; output: string; index: number; total: number; click: number; clicks: number }
  | { state: "done"; mode: "mic" | "tap"; outputs: Record<string, CalibrationOutputResult>; delays: Record<string, number>; clamped: string[] }
  | { state: "cancelled" }
  | { state: "error"; error: string };

/** A paired phone and the state of both phone features (GET /api/bt/phones). */
export interface Phone {
  id: string;
  name: string;
  address: string;
  output: { connected: boolean; connecting: boolean; device_id: string | null };
  audio: { available: boolean; id: string | null; connected: boolean };
}
