# UI inspiration: the RAGBAZ kanban sketch

A reference sketch of a kanban board lives at
<https://kanban.ragbaz.cc/>. By default it renders **mockup data** — a built-in
demo dataset — so the whole interface can be explored with no backend. It is a
sibling of this suite (the `ragbaz` identity is v-BAZ's default guest login), and
it is the most fully-realized picture we have of where Rebekah's human interface
is heading. This document records what it demonstrates, what is worth adopting,
and — importantly — the boundaries of that adoption, so the plan in
[`HUMAN-INTERFACE-PLAN.md`](HUMAN-INTERFACE-PLAN.md) can borrow its ideas without
inheriting shapes that would break Rebekah's console invariants.

It is a design reference, not a dependency. We adapt *concepts and contracts*
from it; we do not import its build.

## What it is

A single-page app (Vite + React module bundle, `Archivo` / `Space Grotesk` /
`JetBrains Mono` type) that presents a kanban board with a deep feature set:

- **Board of resizable panes.** Columns (`kb-pane`) with a title, a live count,
  an empty state ("Drop cards here"), and a resizable splitter between them. The
  layout is user-defined and saveable ("Save layout to your account", "Layout
  name", "The board has only one row"), not a fixed lane set.
- **Rich cards.** Each card carries an id, title, body, tags, a priority
  (`critical` / `high` / `low`), a progress meter, an assignee, and a
  dependencies affordance (`kb-card__deps`). Drag-and-drop between panes.
- **Card zoom / task detail.** Opening a card (`kb-zoom__*`) shows its facts, a
  priority and position, tags, a progress meter, a **blocked / missing**
  indicator, and — the part most relevant to us — a **links list** that
  correlates the card to other identifiers (`kb-zoom__links`,
  `kb-zoom__linktitle`, `kb-zoom__linkmeta`).
- **Dependency tree** with cycle detection ("Dependency Tree", "Depends on",
  "Dependency cycle detection").
- **Timeline / schedule** view ("Scheduled tasks", "No tasks carry dates", a
  today marker), i.e. a Gantt-style projection of dated tasks.
- **Activity ticker** scrolling along the bottom, pausable.
- **Keyboard-first navigation**: a keyboard legend, "Keyboard-only board
  navigation", "Skip to board", explicit shortcuts.
- **Theming**: named themes (Solarized Light, Gruvbox Dark, Tokyo Night, Nord
  Frost, an earthy "concrete/clay"), font and size pickers, heading case/align,
  surface textures ("Blueprint grid", "Concrete flecks", "Film grain", "Linen
  weave"), backdrop/border blur, and per-element color swatches.
- **Offline-first sync** with a four-state indicator (`kb-sync--ok`,
  `--saving`, `--error`, plus "Local only" / "Remote sync off"): the board works
  entirely local, and *optionally* mirrors to an API.
- **Passkey-first accounts**: WebAuthn registration/login, add-a-passkey,
  step-up re-verification, recovery codes, and invite codes.

## The contracts it implies

The bundle talks to a small, legible HTTP surface. These are the shapes worth
stealing, because they line up with Rebekah's gateway rather than fighting it.

### Data API — `/api/v1` with ETag / If-Match

The board reads and writes task/view state under an `/api/v1` prefix using
**optimistic concurrency**: responses carry an `ETag`, and mutations send
`If-Match` so a stale writer is rejected rather than clobbering a concurrent
edit ("Mirror this view to the API", "Remote view sync", "View id").

This is the same versioned namespace Rebekah's gateway already serves
(`/api/v1/session|system|attention`). Adopting ETag/If-Match on any *mutating*
`/api/v1` route Rebekah later adds (e.g. moving a WeftMark card, saving a console
layout) gives us safe concurrent editing for free and matches this reference.

### Auth — WebAuthn/passkey ceremonies

A clean, self-describing passkey contract (note the options/verify split that
every WebAuthn ceremony needs):

| Purpose | Endpoints |
| --- | --- |
| Register a new account | `POST /api/auth/register/options` → `/register/verify` |
| Sign in | `POST /api/auth/login/options` → `/login/verify` |
| Add a passkey to an account | `POST /api/auth/passkeys/options` → `/passkeys/verify` |
| Step-up (re-verify for a sensitive action) | `POST /api/auth/stepup/options` → `/stepup/verify` |
| Recovery code | `POST /api/auth/recover` |
| Sign out | `POST /api/auth/logout` |
| Who am I / capabilities | `GET /api/me` ("What your account can do") |
| Invites | `POST /api/invites`, `POST /api/invites/redeem` |

This directly answers the earlier "login via passkeys" question: it is a
worked reference for the ceremony shape and the step-up pattern, sitting beside
Rebekah's existing password/token/OIDC schemes rather than replacing them. The
`/api/me` capabilities response is the same idea as Rebekah's
`/api/v1/session` (principal + role profile + capabilities).

## What to adopt, mapped to the plan

Ranked by how directly it advances the current
[first slice](HUMAN-INTERFACE-PLAN.md#14-first-implementation-slice):

1. **Card / Change-Set detail with a links list** → plan §14.6 ("Change Set
   detail linking WeftMark, OpenCode, and Sylvae identifiers") and §11.
   The `kb-zoom__links` model — a card that opens to a panel correlating it to
   related identifiers, with a blocked/missing indicator — is exactly the detail
   view Rebekah needs. Rebekah already aggregates attention items with an `id`,
   `kind`, `lane`, and `source`; the detail view extends that to a per-card set
   of linked WeftMark/OpenCode/Sylvae ids. **This is the recommended next
   console slice.**
2. **Four-state sync/health indicator** → already landed for service health
   (Ready / Needs attention / Installing / Offline). The sketch's
   `--ok/--saving/--error/local` sync chip is the same grammar; reuse it verbatim
   when Rebekah's console gains *mutating* calls, so "saving…/saved/error/local"
   reads consistently with the health tiles.
3. **Optimistic concurrency (ETag / If-Match)** → the contract to use the day any
   `/api/v1` route mutates state. Cheap to honor in `nix/gateway.py`; prevents
   HITL/multi-operator write races.
4. **Keyboard-first navigation + legend** → plan §14.9. The console already uses
   `role="tab"`/`tabpanel` with arrow keys; a discoverable keyboard legend and a
   "Skip to board" link are small, high-value additions.
5. **Passkey auth ceremony** → the deferred passkey login, when it is picked back
   up. Use the `/api/auth/{register,login,passkeys,stepup}/{options,verify}` +
   `/api/auth/recover` shape and the `/api/me` capabilities response. (OIDC and
   passkeys both remain postponed for now — recorded here so the contract is
   ready when they are not.)
6. **Theming tokens** → optional polish. The named palettes (Solarized, Gruvbox,
   Tokyo Night, Nord) are just values and can seed a Rebekah light/dark theme set
   if we ever expose one; they carry no code.
7. **Dependency tree / timeline** → later views (plan §7, §11). Out of scope for
   the first slice, but the demo shows they compose from the same task/card data.

## Adoption boundaries (do not regress)

The sketch is a CDN-fonted React SPA. Rebekah's console
([`nix/ui/index.html`](../nix/ui/index.html)) is deliberately the opposite, and
those choices are security invariants (see
[`CLAUDE.md`](../CLAUDE.md) §7), not stylistic ones:

- **No framework, no CDN, one static same-origin file.** Do **not** import the
  React bundle or its build. Rebekah's console ships as a single file the gateway
  serves under a strict CSP with `connect-src 'self'` and no third-party origins.
  Adopt the *ideas and contracts*; re-implement the pieces we want in the
  existing vanilla-JS console.
- **Untrusted data renders via `textContent`/`createElement`, never
  `innerHTML`.** Any linked-identifier or card-detail view we borrow must keep
  that rule — card titles, ids, and link metadata come from backends.
- **The gateway stays fail-closed and single-entry.** New `/api/v1` routes are
  authenticated, allow-listed, and never leak backend detail; "offline-first /
  local only" from the sketch maps to Rebekah's existing graceful `stale`
  degradation, not to a second unauthenticated data path.
- **Fonts and textures are cosmetic.** If we adopt a theme, self-host or inline
  it; do not add a Google Fonts (or any external) dependency to satisfy a look.

## Provenance

`kanban.ragbaz.cc` is a sibling sketch under the same `ragbaz` identity as this
suite. It is treated here as first-party design reference material. Nothing from
its bundle is copied into this repository; this document captures the concepts
and the HTTP contracts, which is what the plan consumes.
