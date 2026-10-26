#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${JAVA_HOME:?Set JAVA_HOME to JDK 21}"
: "${ANDROID_HOME:?Set ANDROID_HOME to an Android SDK with platform 37 and build-tools 37}"
# This is a development key, never a production or user's Codex credential.
key_path="${POCKET_DEBUG_KEYSTORE:-$PWD/.local/debug.keystore}"
mkdir -p "$(dirname "$key_path")"
if [[ ! -f "$key_path" ]]; then
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$key_path" -storepass android -keypass android \
    -alias pocket-development -keyalg RSA -keysize 3072 -validity 3650 \
    -dname 'CN=Codex Pocket Development, O=Unofficial Client, C=XX'
  chmod 600 "$key_path"
fi
./gradlew --no-daemon -PpocketDebugStore="$key_path" :app:assembleDebug "$@"
