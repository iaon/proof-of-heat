# RTK - Rust Token Killer (Codex CLI)

**Usage**: Token-optimized CLI proxy for shell commands.

## Rule

These instructions apply only after the user explicitly enables RTK for the current session, as described in `AGENTS.md`.

While RTK is enabled, prefix shell commands with `rtk`. When the user disables RTK, run commands normally. New sessions start with RTK disabled.

Examples:

```bash
rtk git status
rtk cargo test
rtk npm run build
rtk pytest -q
```

## Meta Commands

```bash
rtk gain            # Token savings analytics
rtk gain --history  # Recent command savings history
rtk proxy <cmd>     # Run raw command without filtering
```

## Verification

```bash
rtk --version
rtk gain
which rtk
```
