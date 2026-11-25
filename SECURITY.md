# Security policy

## Supported versions

Security fixes land on the latest `0.3.x` release. Older versions are not patched.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting:
[Security advisories](https://github.com/thejae/OzemPDF/security/advisories/new) (repository
**Security** tab → **Report a vulnerability**).

Please include the affected version, the platform, a description of the impact, and — where possible
— a minimal PDF or command sequence that reproduces the problem. You will get an acknowledgement
within a few days, and a decision on the fix and disclosure date. Credit is given in the release
notes unless you ask to stay anonymous.

## Threat model

OzemPDF is a local, offline tool: there is no server-side component, no telemetry, and no network
access beyond the loopback interface it serves the interface from. Its attack surface is:

- **Untrusted PDF input.** Documents are parsed with pikepdf/qpdf, fontTools, Pillow and rendered
  with PDFium (pypdfium2). A memory-safety bug in those libraries is reachable through a crafted
  document.
- **The loopback HTTP server** (`ozempdf --serve`). It binds to `127.0.0.1` only and is meant for the
  local browser; it is not authenticated and must never be exposed to a network.
- **The Electron shell.** It runs with the standard sandbox unless the compatibility launcher
  explicitly disables it; that launcher is an opt-in host workaround, not the default app.
- **Local file writes.** Outputs are written atomically next to the destination the user chose.

By design, OzemPDF does not attempt to remove open passwords or certificate encryption; reports that
it "should crack" a password are out of scope (see the README section on PDF protection).

## What to report

- Crashes or memory-safety issues caused by a malformed document where you believe the cause is in
  OzemPDF rather than an upstream library — report it and we will route it upstream.
- Any way to make the app write outside the destination directory, escape the loopback binding,
  execute code from a PDF, or persist a password or signing secret.
- Path traversal or symlink issues in the output write.
- Vulnerable dependencies.

## Scope exclusions

- Denial of service from a deliberately huge document on your own machine.
- The `--no-sandbox` compatibility launcher being less sandboxed than the default app.
- Hardening that is not reachable from untrusted input.
