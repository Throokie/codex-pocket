#!/usr/bin/env bash
set -euo pipefail
# android-json is test-only; Android provides org.json in the app runtime.
: "${POCKET_JSON_JAR:?Set POCKET_JSON_JAR to a test-only org.json-compatible JAR (e.g. android-json)}"
test_root="$(cd "$(dirname "$0")" && pwd)"
source_root="$test_root/../app/src/main/java/io/github/codexpocket/app"
test_out="${POCKET_TEST_OUT:-$(mktemp -d)}"
mkdir -p "$test_out"
printf '%s\n' 'package io.github.codexpocket.app; final class BuildConfig {static final boolean DEBUG=true;}' > "$test_out/BuildConfig.java"
javac -cp "$POCKET_JSON_JAR" -d "$test_out" "$test_out/BuildConfig.java" \
 "$source_root/StrictJson.java" "$source_root/ConfigLinks.java" "$source_root/AdbFiles.java" "$source_root/AdbImportPolicy.java" "$source_root/OriginRules.java" "$source_root/IdentityProof.java" "$source_root/RoutePolicy.java" "$source_root/RoutingGrantPolicy.java" \
 "$source_root/CoalescingTask.java" "$source_root/StreamFailurePolicy.java" "$source_root/HttpFailurePolicy.java" \
 "$source_root/PinnedTls.java" "$source_root/DiscoveryProof.java" "$source_root/VerifiedCandidates.java" "$source_root/EndpointRules.java" "$source_root/DiscoveryManifest.java" \
 "$test_root/ConfigLinksTest.java" "$test_root/AdbFilesTest.java" "$test_root/AdbImportPolicyTest.java" "$test_root/NetworkSecurityTest.java" "$test_root/CoalescingTaskTest.java" "$test_root/StreamFailurePolicyTest.java" "$test_root/HttpFailurePolicyTest.java" "$test_root/DiscoverySecurityTest.java" "$test_root/DiscoveryManifestTest.java"
for suite in ConfigLinksTest AdbImportPolicyTest NetworkSecurityTest CoalescingTaskTest StreamFailurePolicyTest HttpFailurePolicyTest DiscoveryManifestTest; do
 java -cp "$test_out:$POCKET_JSON_JAR" "io.github.codexpocket.app.$suite"
done
java -cp "$test_out:$POCKET_JSON_JAR" io.github.codexpocket.app.DiscoverySecurityTest "$test_root/fixtures"

java -cp "$test_out:$POCKET_JSON_JAR" io.github.codexpocket.app.AdbFilesTest "$test_out"
