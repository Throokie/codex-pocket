# Third-party notices

Codex Pocket's original source is MIT licensed. Dependencies retain their own licenses. This document accompanies both the source and the development APK; it does not relicense them.

## Android development APK

ZXing Embedded 4.3.0 (JourneyApps), ZXing Core 3.5.3 (ZXing authors), AndroidX (Android Open Source Project), Kotlin/Kotlinx (JetBrains and contributors), JetBrains annotations, Guava listenablefuture (Google), and JSpecify are used under Apache-2.0. The resolved runtime coordinates and their locally cached Maven license declarations are recorded in `third_party/android/runtime-dependencies.json`. This is an observed dependency report, not a Gradle lockfile or a reproducible-build guarantee.

Upstream license and NOTICE copies are in `third_party/android/`, including ZXing's NOTICE and applicable notices extracted from resolved artifacts. Build tools and SDKs are not bundled in the APK. A plain-text notice with the Apache license is also provided with the GitHub Release.

## Source distribution / build tools

The Gradle 9.6.0 wrapper is Apache-2.0; the Gradle LICENSE and NOTICE are preserved in `third_party/gradle/`. Android SDKs, JDKs and signing keystores are not distributed.

## Python server

The server is distributed as source, not as a frozen binary. Dependency versions and upstream links are in [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md); upstream license copies for direct dependencies and ifaddr are in `third_party/python/`. In particular python-zeroconf is an unmodified dynamically imported LGPL-2.1-or-later dependency; users may modify or replace it. Anyone making a frozen/server binary must review corresponding-source and replacement/relinking obligations for that distribution.

The project does not include Codex desktop code or its account credentials. OpenAI/Codex marks belong to their respective owners; this project is unofficial.
