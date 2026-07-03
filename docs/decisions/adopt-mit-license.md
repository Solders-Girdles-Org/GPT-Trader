# MIT license for the public repository

---
status: accepted
date: 2026-07-03
deciders: rj
supersedes:
superseded-by:
---

## Context

The repository is public on GitHub but carried no LICENSE file and no
`license` field in `pyproject.toml`, which leaves the code publicly visible
yet all-rights-reserved by default. The original project scaffold's README
claimed "MIT License. See LICENSE for details" against a LICENSE file that
never existed; PR #1156 removed that unbacked claim. The owner confirmed the
MIT claim was scaffold boilerplate, not a prior decision, so the license
question had never actually been decided.

## Options

- **Option A — MIT.** Permissive, conventional for a solo open project. The
  decisive property here: an explicit "AS IS" no-warranty disclaimer attached
  to publicly visible trading software.
- **Option B — Apache-2.0.** Same permissiveness plus a patent grant and
  contribution terms; little added value for a solo project with no patent
  surface.
- **Option C — Stay unlicensed.** Keeps all rights reserved and defers the
  choice, but leaves reuse ambiguity and no warranty disclaimer on public
  code.

## Decision

Option A: MIT, copyright "Solders-Girdles" (the repository's pseudonymous
GitHub identity). Chosen primarily for the explicit no-warranty disclaimer;
permissive reuse is acceptable to the owner.

## Consequences

- [LICENSE](../../LICENSE) (MIT) at the repository root.
- `license = "MIT"` (PEP 639 SPDX expression) in `pyproject.toml`; the build
  requirement moves to `setuptools>=77`, the first version supporting it.
- README regains a License section pointing at the LICENSE file.

## Safety boundary

This decision does not authorize any broker/API call, live execution, money
movement, or autonomy change.
