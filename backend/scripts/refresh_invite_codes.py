"""DISABLED (security fix, 2026-11).

This script deleted EVERY invite code before inserting new ones, used the non-cryptographic
`random` module and created weak codes plus an unlimited-use master code. Use scripts/generate_invites.py instead: it is
additive (never deletes), uses `secrets`, and defaults to a dry run.
"""
raise SystemExit("Disabled: use scripts/generate_invites.py (additive, secure, dry-run by default).")
