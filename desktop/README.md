# Veris Desktop

Tauri shell around the Veris core.

> **Status: scaffolded, not built.** This container has Rust and Node but lacks
> the WebKitGTK/GTK development libraries Tauri needs on Linux, and Windows and
> macOS binaries cannot be produced from it at all. Signing, notarization and
> auto-update are configured but unexercised. Everything below the shell —
> connectors, sync, graph, agents — runs and is tested. See
> `docs/decisions/0007`.

## What the shell is responsible for

| | |
|---|---|
| Window and installer | one signed binary; no Python, Node, Docker or database for the user |
| Credential storage | OS keychain via Rust, never the app's own files |
| Core supervision | starts the packaged core, binds it to `127.0.0.1` on a random port |
| Auto-update | signed updates from a release feed |

The dashboard is the same static bundle the server ships (`web/`), so there is
one UI, not two.

## Build (on a machine with the toolchain)

```bash
# 1. Package the core as a sidecar binary
pip install pyinstaller
pyinstaller --onefile --name veris-core \
            --add-data "web:web" --add-data "corpus:corpus" \
            veris/api.py
cp dist/veris-core desktop/src-tauri/binaries/veris-core-$(rustc -vV | sed -n 's/host: //p')

# 2. Build the app
cd desktop && npm install && npm run tauri build
```

Produces `.msi`/`.exe` on Windows and `.dmg`/`.app` on macOS.

## Why a sidecar rather than a rewrite

The intelligence core carries 49 evaluation checks, a frozen gold set and
measured results against baselines. Rewriting it in Rust or TypeScript to
satisfy a language preference would discard the only evidence the product works.
The customer still installs one binary and no development tools, which is what
the requirement actually asks for.

## Verification still owed

- [ ] Build on Windows and macOS
- [ ] Code signing and notarization
- [ ] Auto-update against a real release feed
- [ ] Sidecar startup, port binding, and shutdown under a supervisor
- [ ] Keychain round-trip on both platforms
