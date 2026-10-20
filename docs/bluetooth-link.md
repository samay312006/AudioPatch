# AudioPatch Bluetooth link (v1)

How the desktop app and the Android app work together over Bluetooth.
Implemented in [`apps/desktop/backend/bt/`](../apps/desktop/backend/bt) and
[`apps/android/.../link/BtProtocol.kt`](../apps/android/app/src/main/java/com/audiopatch/link/BtProtocol.kt).
Keep this file, `protocol.py` and `BtProtocol.kt` in sync.

There are two independent features. Both need the phone **paired** with the PC once
(Windows Settings → Bluetooth & devices).

| Feature | Audio direction | How |
|---|---|---|
| **Wireless output** | PC → phone | custom RFCOMM link carrying Opus, below |
| **Phone audio on the PC** ("Send to PC") | phone → PC | standard Bluetooth audio (A2DP): Windows acts as a Bluetooth speaker via `AudioPlaybackConnection`. No app protocol involved |

## Phone audio on the PC

The desktop app opens a `Windows.Media.Audio.AudioPlaybackConnection` to the paired phone.
The phone then sees the PC as a Bluetooth speaker and sends it all of its audio, from every app,
including ones that block in-app audio capture. Windows plays that audio into its default output,
which with AudioPatch's recommended setup is VB-Cable, so it arrives in AudioPatch as part of **System audio**
and can be routed to any outputs. Requires Windows 10 2004 or later.

## Wireless output

### Who connects to whom

The **phone listens** (an RFCOMM server socket advertised over SDP) and the **PC connects** to it.
On the laptop this was developed on, a phone paging the PC timed out (Android's 5.12 s page
timeout) unless a Bluetooth link already existed, while the PC paging the phone connected in
1-3 s every time.

- Service UUID: `6b1f1d2e-3c1a-4f7e-9a51-a0d10b0a7c11`
- The PC finds it with an uncached SDP query and retries once (the phone may still be releasing
  a previous session).
- The PC remembers phones it has connected to (`phones.json`) and retries every 10 s while a phone
  is part of the user's setup but disconnected.

### Framing

One RFCOMM connection carries a sequence of frames:

| Field | Size | |
|---|---|---|
| type | u8 | `1` = control, `2` = audio |
| length | u16 little-endian | payload length, at most 4096 |
| payload | *length* | |

**Control** payloads are UTF-8 JSON objects. **Audio** payloads are `seq` (u32 LE, +1 per frame)
followed by one Opus packet: 20 ms, 48 kHz, stereo, 128 kbit/s constrained VBR.

### Messages

Phone → PC, first frame, within 6 s of connecting:

```json
{"t":"hello","v":1,"role":"output","id":"b6c1…","name":"Nothing Phone (4a) Pro","model":"A069P"}
```

`id` is stable per app install (letters, digits, `-`, max 64), so the PC remembers the phone's
volume and delay. Reconnecting with the same `id` replaces the old session.

PC → phone:

```json
{"t":"welcome","v":1,"name":"LAPTOP-…","codec":"opus","rate":48000,"frame_ms":20}
{"t":"state","streaming":false}
```

`state` is resent whenever the user adds or removes the phone as an output in the dashboard;
`true` means audio frames follow.

Keep-alive: the phone sends `{"t":"ping"}` every second and the PC answers `{"t":"pong"}`.
The PC ends the session after 6 s without data; the phone notices a lost PC when Bluetooth
drops the connection, then goes back to listening. `{"t":"bye"}` ends it politely; a rejected
hello gets `{"t":"error","error":"…"}`.

### Timing, buffering, drift

- The PC paces frames with its own clock (one per 20 ms) and never queues more than 6 frames
  (~120 ms) per phone: if the link backs up it drops frames rather than letting latency grow.
- RFCOMM is reliable (no loss), but it is **bursty**. Measured on a real link: no lost packets
  and no decoder drops, but gaps of up to ~150 ms between deliveries, with the phone's own
  Bluetooth headphones sharing its radio.
- The phone keeps a **300 ms** jitter buffer. Each underrun raises it by 25 ms (max 500 ms);
  otherwise latency is fixed, so a sync calibration stays valid. Measured dropouts: 100 ms
  buffer ~1/s, 200 ms ~1/15 s, 300 ms adaptive ~1 per 3 minutes.
- Clock drift between PC and phone is corrected on the phone by resampling by at most ±0.3%
  (the same PI controller as the desktop mixer). If even the *lowest* fill over 2 s is more than
  100 ms over target (a backlog delivered in one burst), the phone skips the excess once.

### Latency and sync

The phone path adds roughly the 300 ms buffer plus the phone's headphones' own lag
(often 150-250 ms). The desktop's **Calibrate sync** measures the whole chain acoustically, and
delay sliders go up to 1000 ms, so phone outputs can be lined up with the PC's local devices.

### Security

Pairing is the trust boundary: only paired devices can connect, and a phone only receives audio
after the user adds it as an output in the dashboard. A phone can't be used as an output while
it's also sending its audio to the PC (that would loop forever); the dashboard refuses.
