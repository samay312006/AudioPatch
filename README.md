<p align="center">
  <img src="docs/icon.png" width="96" alt="AudioPatch icon">
</p>

<h1 align="center">AudioPatch</h1>

<p align="center">
  Play your PC's sound on several headphones and speakers <b>at the same time</b>, wired and Bluetooth,
  with per-device volume and lip-sync correction. Phones can join over Bluetooth.
</p>

<p align="center">
  <a href="https://github.com/samay312006/AudioPatch/releases/latest"><b>⬇ Download for Windows and Android</b></a>
  ·
  <a href="apps/desktop#quick-start">Quick start</a>
  ·
  <a href="apps/android">Android app</a>
</p>

![AudioPatch dashboard](docs/phones.png)

---

## Why

Windows sends sound to **one** output device at a time. If two people want to watch a film on a laptop with
their own Bluetooth headphones, or you want the TV speakers *and* your headphones, you're stuck.

AudioPatch captures everything your PC plays and sends it to as many devices as you like, each with its own volume
and a delay so a slow Bluetooth headset and a fast wired one stay in sync with each other and with the video.
It measures each device's lag for you with your laptop's microphone.

## What's in this repo

| App | What it does | Status | Get it |
|---|---|---|---|
| **[Windows](apps/desktop)** | The router: system audio and mics to any number of outputs, routing matrix, volume, lip-sync delay, drift correction, sync calibration, hot-plug. Plus phones over Bluetooth. | ✅ Released | [Download](https://github.com/samay312006/AudioPatch/releases/latest) |
| **[Android](apps/android)** | **Wireless output**: the phone plays the PC's sound (on its speaker or its own headphones). **Send to PC**: the phone's sound plays through the PC. **Router**: phone audio on several devices at once. | ✅ Wireless output and Send to PC released · 🚧 Router in development ([why](apps/android#router-and-the-one-headset-limit)) | [Download APK](https://github.com/samay312006/AudioPatch/releases/latest) |

### Phones and the PC, over Bluetooth

```mermaid
flowchart LR
    PC[AudioPatch on Windows]
    PC -->|"Use as output<br/>(Opus over Bluetooth)"| A[Phone running the app<br/>plays on its speaker or headphones]
    B[Any paired phone] -->|"Play its audio here<br/>(standard Bluetooth audio)"| PC
    PC --> H[Headphones, speakers, TV...]
```

- **Use as output** needs the Android app. One more listener per phone, with its own volume and delay.
- **Play its audio here** needs nothing on the phone: the PC acts as a Bluetooth speaker for it and passes the
  sound on to all your outputs. It works with every app.

The protocol and the measurements behind its design are in [docs/bluetooth-link.md](docs/bluetooth-link.md).

## Quick start (Windows)

1. Download **`AudioPatch-windows-x64.zip`** from the [latest release](https://github.com/samay312006/AudioPatch/releases/latest) and unzip it.
2. Install the free [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) (once, about 3 MB).
3. Run **`AudioPatch.exe`**, click **Use VB-Cable as Windows output**, add **System audio** as a source and your
   headphones/speakers as outputs.

Full guide, troubleshooting and the local API: **[apps/desktop/README.md](apps/desktop/README.md)**.

## Quick start (Android)

1. Pair your phone with the PC once (Bluetooth settings).
2. Install **`AudioPatch-android.apk`** from the [latest release](https://github.com/samay312006/AudioPatch/releases/latest) (allow "install unknown apps" when asked).
3. **To hear the PC on your phone:** in the app, *Wireless output → Turn on wireless output*; on the PC, *Phones → Use as output*, then **+ Add** the phone under Outputs.
4. **To play the phone's audio through the PC:** on the PC, *Phones → Play its audio here*. Nothing needed in the app.

More: **[apps/android/README.md](apps/android/README.md)**.

## Repository layout

```
apps/
  desktop/     Windows app: Python audio engine + React dashboard (README inside)
  android/     Android app: Kotlin + Jetpack Compose (README inside)
docs/
  bluetooth-link.md   PC <-> phone protocol, shared by both apps
  multi-headphone-research.md   what Android allows for multi-headphone output, with sources
  *.png               screenshots and icon
```

Each app builds and tests on its own:

| | Run / build | Tests |
|---|---|---|
| Desktop | `cd apps/desktop` → `python run.py` / `python build_exe.py` | `python -m pytest` (73 tests) |
| Android | `cd apps/android` → `./gradlew assembleDebug` | `./gradlew testDebugUnitTest` (8 tests) |

## What it can't do

- **It can't remove Bluetooth lag**, only even it out by delaying the faster devices. A phone used as an output adds
  about 300 ms on top of its own headphones' lag.
- **Phones alone** play to only one classic Bluetooth headset at a time. That's built into Android (no app can change
  it without root); only LE Audio earbuds can share through Android's own *Audio sharing*. AudioPatch's way around it
  is the PC: *Send to PC* plays the phone's sound on all the PC's headphones at once. Details and sources:
  [docs/multi-headphone-research.md](docs/multi-headphone-research.md).
- **Protected audio** that bypasses Windows' mixer (exclusive mode) can't be captured.

## Credits

Desktop: FastAPI, sounddevice/PortAudio, PyAudioWPatch, pycaw, PyWinRT, libopus (via PyOgg), NumPy, React, Vite,
Tailwind CSS, Framer Motion, lucide. Android: Kotlin, Jetpack Compose, Android MediaCodec.
Virtual audio device: [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) (not bundled).
