# Mobile frontend

Bundled, dependency-free UI for Codex Pocket. `index.html` loads local ES modules and CSS. No external scripts, fonts, images, analytics or simulated sessions are included.

## Integration

`transport.js` implements the protocol in `../../protocol/API.md`. On Android it uses `window.Pocket` and never receives the bearer credential. Browser backup mode uses `fetch` and stores the bearer only in the current `sessionStorage`; use it on the same origin as the API unless the operator explicitly configures CORS and CSP.

Native callbacks:

- `pocketResponse(id, status, bodyJson)` completes `request`, `upload`, `media`.
- `pocketPairing(payloadJson)` opens identity confirmation for a scanned QR.
- `pocketEvent({type, revision?})` accepts `connected`, `update`, `disconnected`, `unauthorized`.
- `pocketSharedImage(dataUrl)` queues a validated local PNG/JPEG/WebP/GIF.
- `pocketNotification()` opens the inbox and refreshes authoritative state.
- `pocketConnectionChanged()` refreshes settings/connection after native permission changes.
- `pocketBack()` returns true when it handled back (viewer, sheet or secondary page), false at the main chat.

The theme alone is kept in `localStorage`. Bounded message snapshots, drafts/receipts in memory and current session data are not a second durable history store. Cached pending requests cannot be answered until a fresh server snapshot is received. A device revoke clears local private display state. Unknown delivery records are never automatically replayed; the server remains the authority on deduplication.

Only media IDs from the authenticated API are rendered. Arbitrary paths and remote image URLs in message text are not fetched. Markdown is escaped before a limited presentation pass. Request options come from the original server request; unknown/`desktop_only` types render read-only guidance.

## Verification

The portable public demo is in `docs/demo/` at the repository root. It uses a local mock bridge and deterministic fake data, and has separate capture/interaction checks; it is not an end-to-end desktop acceptance environment. See its README for commands. The release verification scope is in `docs/VALIDATION.md`.

Earlier development used private workstation Playwright/API harnesses that are not included here. Their results are historical observations, not a claim that every frontend integration check is reproducible from this clone. Python server and Java policy/parser tests are included and run in GitHub Actions.

Pairing and ordinary connection generations are distinct. Every API/media/upload result is bound to its origin and connection epoch; late responses cannot restore an old session or clear a newer one. Pair status must match the current claim before any credential is persisted. The native bridge provides the matching `cancelPairing()` / `finishPairing()` transaction boundary.

Inline images prefer the server-provided `thumbnail_id`, with original ID fallback for older adapters. They are fetched only near the visible conversation area. Thumbnail cache uses LRU with both a 12 MiB decoded-byte estimate and a 30-item bound; browser Blob sizes and native base64 estimates are recorded only after connection-epoch validation. Eviction revokes Blob URLs and drops DOM source references. Original images are fetched only when opened, are not added to the thumbnail cache, and release their temporary URL/reference when closed. A late image result cannot reopen a viewer the user has already closed.

## v0.2 same-service network settings

`transport.getNetworkStatus()`, `configureNetworks(config)` and `probeNetworks()` use the native bridge; they never expose bearer credentials to Javascript. `getConnection().url` remains the logical service origin. The physical `active_url` is displayed independently and does not advance connection epochs, clear drafts or restart pairing.

The settings panel supports up to six owner-entered origins with labels and kinds `lan`, `tailscale`, `private`, `public`, automatic selection or a verified manual target, add/edit/delete, explicit probes and native-reported availability/identity errors. Measured latency is shown only for a verified available endpoint with a finite non-negative result. The client does not infer Tailscale direct/DERP status. New addresses must first be registered in the computer's management interface before detection/activation.

Native status also supplies `multi_route`, `identity_verified`, `manual_url` and `development_http`. Legacy single-address pairings must rescan a new QR to enable multiple routes. The optional QR `server_identity` and `endpoints` are passed to native validation unchanged, never learned from an arbitrary network identity response. Browser backup mode explicitly does not implement automatic route selection.

A native `network` event only refreshes local route settings; it does not refetch server history or clear user input. Network-operation results are connection-bound so a new pairing discards a delayed old configuration response. Native remains responsible for origin identity verification, stable selection/hysteresis, HTTPS preference and its transport policy.

