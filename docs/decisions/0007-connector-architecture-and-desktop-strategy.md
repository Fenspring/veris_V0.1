# Decision 0007 — Connector Architecture, and Not Rewriting the Core in TypeScript

**Date:** 2026-08-19
**Status:** Accepted
**Driver:** Veris becomes a connection layer over the systems a hospital already
runs — LMS, policy management, regulatory sources — delivered as a desktop
application.

---

## The decision that had to be made first

The brief specifies a TypeScript-first desktop stack (Tauri, React, Tailwind).
Taken literally that implies rewriting an intelligence core that is 3,700 lines
of Python carrying 49 evaluation checks, a frozen gold set, and measured results
against three baselines.

**We are not rewriting it.** The reasoning:

- That core is the only *evidence* the product works. Rewriting it discards the
  measurements and replaces them with an unmeasured equivalent, in exchange for
  language uniformity — a trade of evidence for tidiness.
- Connectors talk HTTP, OAuth, CSV, SFTP and XML. TypeScript has no material
  advantage there, and vendor SDKs are avoidable by design (Decision 0004
  already forbids SDK coupling for models, for the same reason).
- One language means **one connector contract test suite**, not two.

What the brief actually requires is that the customer install nothing and that
adding a connector not require rewriting the application. Both are achievable
without a rewrite.

## Desktop architecture: Tauri shell over a packaged core

```
┌─────────────────────────── VERIS.app / VERIS.exe ───────────────────────────┐
│  Tauri shell (Rust)                                                          │
│    · window, auto-update, code signing, installer                            │
│    · OS credential storage — Keychain / DPAPI (never the app's own files)    │
│    · supervises the core process, binds it to 127.0.0.1 on a random port     │
│                                                                              │
│  Dashboard (static assets, already built)  ── HTTP ──▶  Veris core sidecar   │
│                                                          (PyInstaller binary) │
│                                                            · connectors       │
│                                                            · sync engine      │
│                                                            · knowledge graph  │
│                                                            · agents           │
└──────────────────────────────────────────────────────────────────────────────┘
```

The user installs one signed binary. No Python, Node, Docker or database.

Tauri over Electron: ~10× smaller installer, lower memory, and the Rust side
gives first-class access to OS credential stores — which §10 makes a hard
requirement, not a nicety.

**Honest status:** the shell is scaffolded but **not built or verified here.**
This container has Rust and Node but lacks the WebKitGTK/GTK development
libraries Tauri needs on Linux, and Windows and macOS binaries cannot be
produced from it at all. Signing, notarization and auto-update are configured
but unexercised. Everything below the shell — connectors, sync, graph, agents —
runs and is tested.

## Connector framework

One interface, implemented per vendor, with all vendor-specific behaviour
confined inside the connector:

```
authenticate() → test_connection() → discover() → sync() → health_check() → disconnect()
```

Two properties are load-bearing:

**The registry drives the UI.** A connector declares its category, the auth
mechanisms it supports, what it can read, and what it will never touch. The
Connection Center renders from that metadata, so adding a connector adds no
dashboard code. This is what §7 asks for and it is the difference between a
plugin architecture and a switch statement.

**Read-only by construction.** No connector exposes a write path. Veris reads,
normalizes, relates and recommends; it does not change the customer's systems
(§24). That is enforced by the interface having no method to do so, not by
convention.

## Two kinds of external record, deliberately separated

A distinction the brief implies but does not name, and getting it wrong would
corrupt the graph:

| | Example | Becomes |
|---|---|---|
| **Knowledge** — makes a normative claim | a policy, a standard, a requirement | a document with verified-span entities, exactly like an uploaded file |
| **Operational fact** — a state of the world | an employee, a course, a completion | a normalized record, linked to the graph but never a source of citations |

A policy can be cited. A completion record cannot: "12,842 people completed
this" is a fact about the world, not a statement any document makes. Keeping
them in separate stores means an evidence citation always resolves to text a
human can read, which is the property the whole trust model rests on.

## Mock connectors before credentials

Mock LMS, policy and regulatory connectors ship in the product and implement the
same interface as real ones, passing the same contract tests. This makes the
full experience demonstrable before any vendor credential exists — and means a
new real connector is proven against a suite that already has passing
implementations, rather than against nothing.

Mocks are labelled as mocks in the UI. Never presenting a mock as a live
integration is a hard rule (§6).

## What would cause this to be reconsidered

- If PyInstaller packaging proves unreliable across Windows/macOS signing, the
  sidecar becomes the weak point and a native rewrite of the *connector layer*
  (not the intelligence core) would be the fallback.
- If a major vendor ships an SDK with no usable HTTP surface, that connector —
  and only that connector — may need a different runtime.
