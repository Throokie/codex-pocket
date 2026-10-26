# Codex Pocket Android 0.4.0

An unofficial Android client. Package ID: `io.github.codexpocket.app`. It is not an OpenAI product.

The APK bundles the web UI from `../web`; it never loads a server-provided webpage. All authenticated requests and image reads run through native whitelisted HTTP code. The device bearer is AES-GCM encrypted with an Android Keystore key; it is never returned to JavaScript. Android backup is disabled. The app does not bypass the desktop's permission policy.

## Build

Requires JDK 21, Android SDK platform 37 and build-tools 37. Gradle 9.6.0 is pinned in the wrapper. ZXing Embedded 4.3.0 and ZXing Core 3.5.3 provide QR decoding without Google Play services. No package manager or global installation is required by the build script.

```sh
JAVA_HOME=/path/to/jdk21 ANDROID_HOME=/path/to/android-sdk ./build-apk.sh
```

`POCKET_DEBUG_KEYSTORE=/private/path/debug.keystore` optionally selects this app's development key. The script creates a distinct key if absent. The standard development password is `android`, not a production secret. Do not publish `.local`, keystores, Gradle caches, private deployment data, or signing material. Output: `app/build/outputs/apk/debug/app-debug.apk`.

Release manifests reject cleartext network connections. The development APK explicitly permits HTTP only for loopback/private numeric origins (RFC1918, Tailscale 100.64/10 and private IPv6) (including emulator host `10.0.2.2`); real internet deployment requires HTTPS. Redirects, credentials in URLs, non-root server URL paths and arbitrary remote API paths are rejected. There is no permissive TLS verifier.

## Native bridge

See `../../protocol/API.md` for the v1 request contract. The callback body is a JSON string. Implemented:

- `request(id,method,path,bodyJson) → pocketResponse(id,status,json)`.
- `preparePairing(payloadJson) → JSON {url,version}` or JSON error; the candidate origin does not replace the paired server until `/pair/status` returns approved.
- `cancelPairing()` invalidates the candidate pairing generation; late approved responses cannot restore credentials. `finishPairing()` acknowledges the currently approved result after the UI has accepted it. Until that acknowledgement, cancellation restores the prior valid device connection.
- `scanPairing() → pocketPairing(payloadJson)` after QR validation. User grants camera permission in ZXing's native screen.
- `getConnection() → JSON {paired,url,deviceName,backgroundEnabled,notificationsGranted,unofficial}` without a token.
- `upload(id,mime,base64)` and `media(id,mediaId) → {data_url}`.
- `subscribe()/unsubscribe() → pocketEvent({type:connected|update|disconnected|unauthorized,revision})`.
- `disconnect()` clears the device credential and connection. It does not delete desktop history.
- `setBackgroundEnabled(boolean)` and `showNotificationSettings()` control the visible optional background connection and system settings. Enabling background connection requests Android 13 notification permission. Denial does not turn it on.
- Android image shares call `pocketSharedImage(dataUrl)`; standard HTML image file inputs use the native document picker. Up to four shared images, each at most 15MiB, are accepted.

Background mode uses a visible foreground data-sync service, with generic notification content. Android Doze, OEM process limits, foreground-service time limits, offline networks and user force-stop can still suspend it. It is not push infrastructure and does not guarantee delivery when killed. The state snapshot on reconnect remains authoritative.

## Third-party notices

- Android SDK/runtime APIs: Android platform terms; SDK binaries are not included in source delivery.
- Gradle wrapper: Apache-2.0, https://github.com/gradle/gradle
- ZXing Embedded 4.3.0: Apache-2.0, https://github.com/journeyapps/zxing-android-embedded
- ZXing Core 3.5.3: Apache-2.0, https://github.com/zxing/zxing
- AndroidX AppCompat/Core/Arch dependencies: Apache-2.0, https://android.googlesource.com/platform/frameworks/support

This native shell implements the app UI and protocol client only; desktop approval/image capabilities are provided and reported by the configured server adapter.

## Native validation scope

The development APK has been built with the pinned Gradle/JDK/SDK, installed and launched on a new task-only Android 13/API33 x86_64 emulator, with the Chinese bundled pairing page visually checked. Native CDP smoke checks cover local origin, no bearer in JS, public HTTP/subpath rejection and path whitelist rejection. Delayed-response tests use a local mock service, not a production desktop: cancelled/disconnected/replaced pairing results cannot restore credentials; callback tokens are stripped; late API/SSE 401 from an old connection cannot clear a newer device credential.

The notification refresh scheduler uses `CoalescingTask`, with a deterministic Java concurrency test in `tests/CoalescingTaskTest.java`: 10000 events during a blocked read produce one executor worker and two reads, with no growing queue. This is notification scheduling validation, not a claim that a killed Android app always receives notifications. Real server/Desktop integration and physical-camera scan coverage are recorded separately by the project owner.

## Verified network routes (0.2.0)

One pairing can retain up to six user-selected origins. The pairing origin remains the logical connection URL; `active_url` is the chosen transport. Each candidate must prove the QR-pinned P-256 identity using a fresh nonce and an origin-bound signature before receiving the device bearer. The computer must register that exact origin first. Legacy QR codes remain single-origin and require re-pairing to enable this feature. Candidate lists never expand from health or identity responses.

Automatic mode excludes HTTP whenever any HTTPS candidate exists. Manual mode keeps the selected target even when it fails. Probes use three bounded concurrent workers, a 1.8-second connect/read timeout, EWMA, a switching cooldown, and two completed faster probe rounds before a healthy upgrade. Failures can immediately select an already verified alternative. Probe measurements include the full identity request with `Connection: close`; they are not pure network RTT.

GET can retry once on a different verified route. Writes and uploads select one route and never replay after an uncertain result. Route changes cancel obsolete GET reads, preserve in-flight write receipts and keep the device identity unchanged. A proxy's ordinary 401 does not revoke pairing; revocation requires the service's structured authentication error on the current route.

Foreground probing is every 30 seconds; the optional visible connection service uses 120 seconds while running. Network changes invalidate old proofs and trigger fresh verification. No active owner means no scheduled probes. Android may suspend background work.

Multiple URLs must reach the same instance or shared database. Identity signatures do not protect unencrypted HTTP from an active relay. Use system-trusted HTTPS for deployment; release builds reject HTTP. No redirect or permissive TLS verifier is accepted.

`getNetworkStatus()` returns mode, manual target, active route, verification/latency states and `multi_route`. `configureNetworks(id,json)` and `probeNetworks(id)` return the full status through `pocketResponse`. `network` events update local settings without changing the logical connection epoch.

Portable pure-Java counterexamples are in `tests/NetworkSecurityTest.java`; compile it with the actual `OriginRules`, `IdentityProof`, `RoutePolicy`, and `RoutingGrantPolicy` source classes. These test cryptographic binding, canonical origins, security downgrade prevention, hysteresis, manual failure and stale pairing generations.

## QR-authorized automatic discovery (0.3.0)

A new QR can bind the service identity plus a separate TLS SPKI fingerprint and authorize automatic discovery. Existing 0.1/0.2 pairings keep their prior trust policy and need a new QR to enable discovery. Private or Tailscale automatic routes use HTTPS with an exact leaf-key fingerprint and certificate-validity checks. Only that explicit pinned route can replace hostname verification. Public manual routes retain Android system CA and hostname validation. There is no redirect or fallback from a failed pin to system trust or HTTP.

Before the first claim, up to three concurrent proof requests select a reachable QR-listed address. These probes contain no ticket or bearer. The claim is sent once to the chosen transport; uncertain writes never automatically replay. The logical QR origin remains stable.

Discovery downloads a nonce-bound, identity-signed manifest without bearer credentials. It verifies the exact signed payload, the unchanged TLS pin, a nondecreasing revision and unchanged content at equal revisions. Only that signed list may add or remove computer-managed routes. Phone manual extras and a fixed-route preference remain; an obsolete fixed target stays unavailable until the user changes it. More than six combined routes is rejected without silently dropping extras.

Manifest synchronization shares bounded event-driven routing work, with a fifteen-second minimum attempt gap and normal foreground/background probe intervals. A new SSE discovery revision can request a coalesced refresh. It does not cause ordinary chat events to probe.

When known addresses fail, Android NSD can search `_codexpocket._tcp.` on the current LAN for twenty-five seconds, at most eight service hints, with a sixty-second discovery cooldown. A multicast lock is held only during this window and released on stop. TXT names and IDs are hints, never authentication. A resolved private address must pass the QR TLS pin and return a valid signed manifest before any new route can receive a bearer. Network/owner/credential generations reject late results. Discovery stops when there is no visible owner or optional visible connection service. No SSID, location, UDP subnet scan, Tailscale installation or native automatic business approval is used.

LAN isolation may block mDNS. Known Tailscale DNS addresses can still serve as seeds; the app cannot promise to find a new computer address across networks when every seed is offline. Scoped IPv6 link-local discovery hints are currently ignored; ordinary private IPv4 and unscoped ULA are supported.

Run portable Java security/counterexample tests with `POCKET_JSON_JAR=/path/to/android-json.jar tests/run-java-tests.sh`. The JSON JAR is test-only; the APK uses Android's built-in `org.json`. The fixture certificates contain public test keys only and no signing private key. See NATIVE-VALIDATION.md for build and actual-runtime coverage.

Pinned TLS policies use a synchronized sixteen-entry LRU keyed by the complete canonical origin and TLS SPKI fingerprint. A policy reuses its socket factory and stateless verifier so Android can reuse connections and TLS sessions. Different origins or fingerprints receive separate factories. Certificate validity and exact fingerprint checks remain in both the trust manager and per-connection verifier; default system trust settings are never changed. Cache tests establish object reuse and isolation, not a measured latency improvement.

## Protected ADB files (0.3.1)

The shell-only broadcast receiver `io.github.codexpocket.app/.AdbCommandReceiver` accepts action `io.github.codexpocket.app.ADB_COMMAND` and requires `android.permission.DUMP`. Commands are `init`, `status`, `import-pairing`, `import-image` and explicit maintenance `reset-history`. The app creates `getExternalFilesDir(null)/CodexPocket/{inbox,outbox,media}`; typical shared-storage path is `/sdcard/Android/data/io.github.codexpocket.app/files/CodexPocket`. No storage-wide permission is requested.

Imports use an ASCII basename in `inbox` and a `request_id` prefixed `e<history_epoch>-`, such as `e0-<uuid>`. `init` and `status` return the current integer epoch and ledger size. Results are atomically published to `outbox/result-<request_id>.json`; `outbox/status.json` contains only readiness, version, pairing boolean, queue count and ledger metadata. No ticket, token, origin, chat body, image or key body is exported. `media` is reserved for the user's own file operations and remains empty by default.

Pairing JSON is limited to 64 KiB; images to 12 MiB with matching PNG/JPEG/WebP/GIF magic and extension, dimensions at most 16384 per side and 40 million pixels. Paths, symlinks, invalid names and incompatible types are rejected. Data is validated and copied atomically into a private one-shot queue, then its private ledger is committed, and only then is the inbox source consumed. Validation or persistence failure preserves the original input. The exported Activity can reference a genuine private queue entry but cannot import a payload from intent extras.

The final `prepared` status means a pairing preview or image draft was placed in the local UI. It never means paired, approved, uploaded or sent. Pairing still needs the user's request and computer approval; image sending remains a separate user action. Existing valid device credentials are not overwritten by previewing another pairing file.

At most four private staged inputs are retained for two minutes. Successful UI preparation removes the private stage. Failed or interrupted delivery retains a bounded stage until cleanup; each subsequent command/take cleans expired stages and reports `expired` or `delivery_unknown` without automatically replaying uncertain UI work. A queued import can be retried with its original ID and filename even after its source was consumed. Reusing an ID with different content is rejected; a completed or error result never repeats the UI action. If expired/error, inspect the result and explicitly submit a fresh ID and original local input to retry.

The private import ledger retains up to 512 import IDs; status/init do not consume it. Explicit `reset-history` is permitted only with no queued/processing work and atomically increments the epoch while clearing terminal history and failed private stages. Old-epoch import IDs are then rejected, so reset cannot silently replay old imports. Retrying the same last reset ID does not reset twice. Reset does not touch pairing credentials, drafts or the shared media directory.

For cold-start shell commands use `am broadcast --include-stopped-packages --allow-background-activity-starts ...`. Init/status create/read directories without launching UI. Imports may open the local preview/draft screen; if Android prevents the launch, retry the same queued ID after allowing the Activity to open. Desktop CLI examples live in `../../docs/ADB_FILES.md` and `../../scripts/pocket-adb.py`.

## Reusable configuration links (0.4.0)

The app accepts `codexpocket://import#v1.<unpadded-base64url-UTF8-JSON>` through ZXing, paste and an Android VIEW link. The entire URI is limited to 2400 bytes. Public names and route labels use the server-compatible limits of 80 and 40 Unicode code points; cryptographic IDs and URI byte limits are unchanged. Scheme, authority, path, fragment, JSON types, duplicate keys, P-256 identity and TLS fingerprint are checked locally. Preview does not connect to the network or change an existing pairing. The UI can compare public signing/TLS fingerprints; a different identity requires explicit confirmation. Links arriving during pairing/import are rejected with a clear prompt rather than replacing pending work.

Only the user's Import action starts bounded proof requests against at most three QR-listed HTTPS/pinned seeds. A new device selects a reachable identity-proved seed, then sends one unauthenticated POST to `/config/<id>/pair` with a fresh challenge. The exact response payload must verify under the separate `codex-pocket-config-pair-v1` signing domain and match its nonce, server ID, config ID, integer expiry, revision and original signing/TLS pins. No bearer is sent to proof/config endpoints, and no uncertain POST is replayed. The returned short-lived pairing continues through the existing claim and computer-approval stages; the client never approves business actions automatically.

An existing pairing with the same signing and TLS pins uses a fresh signed `/discovery` manifest to update managed addresses. It preserves credentials, manual extras/preferences and the logical connection URL. Address updates do not clear drafts. Public manually registered HTTPS routes in signed full lists retain normal system CA/hostname verification; initial link seeds remain pinned HTTPS. A canceled/obsolete import cannot commit a late candidate or network update: imports bind an atomic credential snapshot, their own generation/scope and the route configuration generation.

The bridge exposes `previewConfigLink(raw)` and asynchronous `importConfigLink(id,raw)`. Incoming scan/VIEW links call `window.pocketConfigLink(raw)` only for preview. A successful import returns either `{already_paired:true,network_status}` or `{already_paired:false,pairing}`. The latter is a short-lived pairing payload, never an existing device token. A rotated configuration ID can return `config_revoked`; it does not remove an already approved device grant.
