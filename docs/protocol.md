# AudioPatch Link protocol (v1)

How the desktop app and the Android app talk over Wi-Fi. Implemented in
[`apps/desktop/backend/link/`](../apps/desktop/backend/link) and
[`apps/android/.../link/`](../apps/android/app/src/main/java/com/audiopatch/link).
Keep this file, `protocol.py` and `LinkProtocol.kt` in sync.

## Roles

| Phone role | Meaning | Audio direction | Appears on the PC as |
|---|---|---|---|
| `output` | "Wireless output" mode: the phone plays the PC's sound | PC → phone | an **output** device |
| `source` | "Source" mode: the phone's sound plays on the PC's devices | phone → PC | an **input** (source) |

Connecting only makes the phone *appear* in the dashboard. No audio flows until
the user adds the phone there, like any other device. The PC tells an output
phone when audio starts and stops (`state` message).

## Ports

| Port | Transport | Purpose |
|---|---|---|
| 47800 | UDP | discovery |
| 47801 | TCP | control (newline-delimited JSON) |
| 47802 | UDP | audio from phones (source role) |

Output phones receive audio on a UDP port of their own choosing (sent in `hello`).

## 1. Discovery

The phone broadcasts to `255.255.255.255:47800` (and its subnet broadcast address):

```json
{"t":"probe","v":1}
```

Every PC running AudioPatch replies, unicast to the sender:

```json
{"t":"here","v":1,"name":"DESKTOP-ABC","id":"3f2a…","control_port":47801}
```

Networks with "client isolation" (some public/guest Wi-Fi) block this; the app
then offers manual entry of the PC's IP address (shown in the dashboard header).

## 2. Control (TCP 47801)

One JSON object per line, UTF-8, max 4096 bytes per line. Either side closes the
connection to end the session.

Phone → PC, first message within 5 s:

```json
{"t":"hello","v":1,"role":"output","id":"b6c1…","name":"Nothing Phone (4a) Pro","model":"A069P","udp_port":50123}
```

- `id`: stable per app install (letters, digits, `-`; max 64). The PC uses it to
  remember the phone's settings across reconnects. Reconnecting with the same
  `id` and `role` replaces the previous session.
- `udp_port`: required for `output`, where the phone listens for audio.

PC → phone:

```json
{"t":"welcome","v":1,"name":"DESKTOP-ABC","stream_id":123456789,"audio_port":47802}
{"t":"state","streaming":false}
```

`state` is sent again whenever the user adds/removes the (output) phone in the
dashboard: `true` means audio packets are on their way.

Keep-alive: the phone sends `{"t":"ping"}` every second; the PC answers
`{"t":"pong"}`. Either side drops the session after 5 s of silence.
`{"t":"bye"}` ends it politely. A rejected hello gets `{"t":"error","error":"…"}`.

## 3. Audio (UDP)

Each datagram is one packet: a 16-byte little-endian header followed by
interleaved signed 16-bit little-endian PCM at 48 000 Hz.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `APL1` |
| 4 | 4 | `stream_id` (u32, from `welcome`) |
| 8 | 4 | `seq` (u32, +1 per packet, wraps) |
| 12 | 2 | `frames` (u16) |
| 14 | 1 | `channels` (1 or 2) |
| 15 | 1 | `format` (1 = s16le) |
| 16 | frames × channels × 2 | samples |

Packets carry 240 frames (5 ms): 976 bytes with stereo, so they never fragment.
That's ~1.6 Mbit/s per stream.

- **PC → output phone:** sent to the phone's IP and `udp_port`, paced by the PC's
  clock. The phone keeps a jitter buffer (~80 ms) and corrects clock drift by
  resampling by up to ±0.3%, like the desktop does.
- **Source phone → PC:** sent to the PC's `audio_port` with the session's
  `stream_id`. Packets with an unknown `stream_id` are ignored. The PC adds 60 ms
  of extra buffering for Wi-Fi jitter.

Lost packets are not retransmitted (late audio is useless); the receiver plays
silence for a missing packet.

## Latency

Wi-Fi adds roughly 10-40 ms plus the jitter buffer, and a phone's Bluetooth
headphones add their own 100-300 ms. The desktop's **Calibrate sync** wizard
measures the whole chain acoustically (it hears the phone's headphones through
the PC's microphone), so phone outputs line up with local ones the same way.

## Security

The link is meant for a trusted home network. It is unencrypted and
unauthenticated; the PC only accepts devices into a setup when the user adds
them in the dashboard, limits sessions (16) and message sizes, and validates
every packet. The dashboard and its API still listen on `127.0.0.1` only.
