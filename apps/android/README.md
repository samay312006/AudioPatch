# AudioPatch for Android

The phone side of [AudioPatch](../../README.md). It works together with the
[Windows app](../desktop) over Bluetooth.

[⬇ Download the APK](https://github.com/samay312006/AudioPatch/releases/latest) ·
[Install](#download-and-install) · [What Android allows](#router-and-the-one-headset-limit)

> **Status:** *Wireless output* and *Send to PC* work and are tested on a real phone (Nothing Phone (4a) Pro,
> Android 16). *Router* is in development; see [why](#router-and-the-one-headset-limit).

## Modes

| Mode | What it does | Status |
|---|---|---|
| **Wireless output** | Plays your PC's sound on this phone, on its speaker or headphones connected to it. The PC treats the phone as one more output, with its own volume and lip-sync delay. | ✅ Working |
| **Send to PC** | Plays this phone's sound through your PC: the PC becomes a Bluetooth speaker for the phone, and AudioPatch sends the sound to the PC's outputs. Works with every app, including Spotify and Netflix. The app shows whether it's connected and how to set it up; the connection is made from the PC. | ✅ Working |
| **Router** | Plays this phone's own audio on several devices at once. For now the tab points to *Send to PC*, which does this through your PC. | 🚧 In development |

### Wireless output

1. Pair the phone with your PC once (Bluetooth settings on either device).
2. In the app: **Wireless output → Turn on wireless output**. The app waits for your PC.
3. On the PC, in AudioPatch's **Phones** panel, click **Use as output** next to this phone, then **+ Add** it under Outputs.

The card shows the connection, a level meter, the buffer size and any dropouts. Wireless output keeps running with
the screen off (there's a notification with a **Stop** button) and turns itself back on when you reopen the app if
it was on before. The PC reconnects to it automatically.

Expect roughly **300 ms** of extra delay (the buffer that rides out Bluetooth's bursty delivery) plus your headphones'
own lag. Use **Calibrate sync** on the PC to line the phone up with the PC's other outputs.

### Send to PC

1. Pair the phone with your PC once.
2. On the PC, in AudioPatch's **Phones** panel, click **Play its audio here** next to this phone.
3. Play anything on the phone. On the PC, make sure **System audio** is a source.

The app's **Send to PC** screen shows *"This phone's audio is playing on <PC>"* while it's connected.

## Download and install

1. On the phone, download **`AudioPatch-android.apk`** from the [latest release](https://github.com/samay312006/AudioPatch/releases/latest).
2. Open it. Android asks you to allow installs from your browser or file manager ("Install unknown apps"): allow it, then install.
3. Open AudioPatch and tap **Allow** when it asks for **Nearby devices** (Bluetooth) and **Notifications**.

The app isn't on Google Play. Updates are signed with the same key, so newer releases install over older ones.
If you installed a development build from source before, uninstall it first (the signatures differ); the PC will
then see the phone as a new device, so add it again there.

## Requirements

- Android 10 or later.
- Bluetooth, and a Windows PC running AudioPatch 0.2 or later.
- Permissions: **Nearby devices** (Bluetooth), and **Notifications** for the playback notification.

## Router and the one-headset limit

The goal of *Router* is playing the phone's audio on several devices at once, e.g. two people's Bluetooth
headphones. Here's what Android allows, measured on the test phone and confirmed in Android's source
(full research with sources: [docs/multi-headphone-research.md](../../docs/multi-headphone-research.md)):

- **Classic Bluetooth headphones: one at a time.** Android can keep several connected, but only one plays media.
  That's enforced in Android's Bluetooth service, its Bluetooth stack and its audio framework; no app can change it
  without root. Asking Android to play to the speaker as well was overridden to the headset in our test.
- **Two at once needs LE Audio.** Android 16's *Audio sharing* (Auracast) plays to several LE Audio earbuds, but only
  the system can start it, and classic headphones can't join.
- **Speaker + one headset works, with a catch.** Audio tagged as an *alarm* is copied to the speaker and the active
  headset (confirmed on the test phone), but both get the same signal, so the speaker plays ahead of the headset and
  the two can't be lined up.
- **What works today:** *Send to PC*. The PC receives the phone's audio and AudioPatch plays it on all the PC's
  headphones and speakers at once, each with its own volume and lip-sync delay. A USB-C Bluetooth transmitter that
  pairs two headphones is the phone-only alternative.

## Build and install from source

Requirements: Android SDK (Android Studio installs it), JDK 17+, and a phone with
[USB debugging](https://developer.android.com/studio/debug/dev-options) enabled.

```bash
cd apps/android
./gradlew assembleDebug          # Windows: gradlew.bat assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Or open `apps/android` in Android Studio and press Run.

A release build (`./gradlew assembleRelease`) is signed only if your `~/.gradle/gradle.properties` defines
`AUDIOPATCH_KEYSTORE`, `AUDIOPATCH_KEYSTORE_PASSWORD`, `AUDIOPATCH_KEY_ALIAS` and `AUDIOPATCH_KEY_PASSWORD`;
signing keys never live in the repo. Debug builds include two test hooks (test tones through the router engine)
that are disabled in release builds.

Tests (run on your computer, no phone needed):

```bash
./gradlew testDebugUnitTest
```

They cover the jitter buffer and drift correction (including a simulated bursty Bluetooth link with clock drift),
backlog handling, adaptive buffering, and the link framing (byte-for-byte compatible with the desktop app).

## How it works

- **Link:** the app opens a Bluetooth RFCOMM service and the PC connects to it (on the laptop this was developed
  on, a phone dialling the PC timed out, while the PC reaching the phone was quick). Audio arrives as 20 ms Opus
  frames at 128 kbit/s. Full protocol and measurements: [docs/bluetooth-link.md](../../docs/bluetooth-link.md).
- **Decoding:** Android's built-in Opus decoder (MediaCodec).
- **Buffering:** Bluetooth delivers no lost packets but in bursts, with gaps of up to ~150 ms. A 300 ms jitter buffer
  rides that out; each dropout raises it by 25 ms. Latency otherwise stays fixed, so a sync calibration keeps holding.
- **Clock drift:** the PC's and phone's clocks differ slightly; the app resamples by at most ±0.3% (inaudible) to
  keep the buffer on target. Same controller as the desktop engine.
- **Diagnostics:** link statistics are logged every 5 s: `adb logcat -s AudioPatch`.

## Code layout

```
app/src/main/java/com/audiopatch/
  MainActivity.kt        UI shell, permissions, mode switcher
  AudioService.kt        foreground service that keeps audio running
  AppState.kt, Identity.kt
  modes/Receiver.kt      Wireless output: RFCOMM server, decode, playback, link stats
  modes/Router.kt        router engine (capture + per-output tracks); in development
  modes/AlarmRouteTest.kt  debug experiment: speaker + headset via alarm routing
  audio/JitterBuffer.kt  ring buffer + jitter/drift controller
  audio/OpusDecoder.kt   MediaCodec Opus decoder
  link/BtProtocol.kt     link framing (mirrors apps/desktop/backend/bt/protocol.py)
  ui/                    Compose screens and components
app/src/test/            JVM unit tests
```
