# Android 0.4.0 verification

See [validation scope](../../docs/VALIDATION.md) for the final APK, local Java/AVD checks, real-device scope, and unverified cases. Screenshots of the isolated browser demo are not native device acceptance.

Pure-Java tests are in `tests/run-java-tests.sh`. They use production policy/parser classes plus the public, certificate-only TLS fixtures in `tests/fixtures/`; no runtime signing or device credentials are distributed.
