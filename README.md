# webrtc-android-h265

Builds Google's open-source [WebRTC](https://webrtc.googlesource.com/src) for Android
with H.265 (HEVC) RTP support enabled (`rtc_use_h265=true`), as an `.aar`.

The build is the whole repository: `.github/workflows/build.yml` fetches the WebRTC
branch that belongs to a Chrome milestone, builds it with GitHub Actions and attaches
the result to a release. Nothing else lives here.

H.265 is decoded and encoded by the device's own MediaCodec hardware; this build only
adds the RTP packetization that WebRTC needs to carry it.

WebRTC is © The WebRTC project authors, BSD-3-Clause; see its `LICENSE` and
`PATENTS` files, which are included in every release.
