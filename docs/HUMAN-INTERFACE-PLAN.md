# Human-interface plan for the Rebekah suite

**Status:** proposed implementation plan  
**Date:** 2026-09-22  
**Scope:** v-BAZ, Rebekah, WeftMark, Sylvae, OpenCode/Ollama integration, and optional Ephor integration

## 1. Decision and purpose

The suite needs one understandable human journey, not five adjacent product UIs.

The intended experience is:

1. **v-BAZ** safely prepares the machine.
2. **Rebekah** becomes the single authenticated front door after installation.
3. **WeftMark** supplies the primary work and review model.
4. **Sylvae** appears as execution and evidence attached to that work.
5. **OpenCode** appears as agent sessions attached to that work.
6. **Ollama** appears as local inference capacity, not as an unrelated model administrator.
7. **Ephor** appears only after explicit opt-in and successful capability discovery.

Ephor is no longer a mandatory dependency. Its absence must be presented as **not installed** or **available to add**, never as a failed baseline service. No baseline navigation, health score, readiness decision, build, or onboarding flow may require Ephor.

This plan follows the suite distribution review in
[`docs/SUITE-DISTRIBUTION-REVIEW.md`](./SUITE-DISTRIBUTION-REVIEW.md), but
supersedes that review wherever it treated Ephor as mandatory.

## 2. Review basis and limits

This is a source-level interface review of:

- Rebekah's built-in static console and authenticated gateway;
- v-BAZ's guided PowerShell installer and configuration file;
- WeftMark's browser review surface and Textual TUI;
- Sylvae's server-rendered evidence and run pages;
- Ephor's governance console as an optional future module.

It is not a rendered visual audit. The applications were not running in a
shared test environment during this review, so exact layout, focus order,
screen-reader output, touch behavior, latency, and browser compatibility still
need interactive verification.

## 3. What should be preserved

### v-BAZ

Keep the existing strengths:

- dry-run as the default;
- disk identity shown as disk plus partition, not drive letter alone;
- explicit `ERASE` language;
- decision summary before action;
- a second destructive confirmation in the underlying installer;
- rejection of boot/system volumes;
- plain-text operation without a Windows GUI dependency.

### Rebekah

Keep:

- a single same-origin gateway;
- dependency-free, off-grid static UI;
- token and OIDC authentication;
- backend capability allow-listing;
- loopback-only services;
- service-specific identities;
- the WeftMark board as the primary coordination surface;
- advanced API access for diagnostics.

### WeftMark

Keep:

- “What needs attention?” as the leading question;
- a board derived from authoritative workflow data;
- readiness, evidence, scope, and attention signals;
- a detail dialog rather than forcing navigation for every inspection;
- standalone TUI and browser surfaces for environments without Rebekah.

### Sylvae

Keep:

- a simple server-rendered interface;
- durable evidence records;
- clear skill/backend/model inputs;
- local-first execution;
- explicit backend and recursion guards.

### Ephor

Keep, when installed:

- explicit human identity;
- held-action queue;
- approve and deny as distinct actions;
- audit-chain status;
- SLA visibility;
- AI/human actor distinction.

## 4. Primary UX problems

### 4.1 The suite lacks one mental model

The current surfaces expose service names first: Board, OpenCode, Ollama,
Services, API console, Sylvae evidence, and optionally Ephor. A new operator
must already understand the architecture before the interface becomes useful.

The primary navigation should reflect user goals:

- **Work**
- **Review**
- **Runs**
- **Models**
- **System**
- **Governance** — visible only when Ephor is installed and enabled
- **Advanced** — API console, raw records, diagnostics

Service ownership can remain visible as metadata, but it should not be the main
information architecture.

### 4.2 Installation and daily operation are disconnected

v-BAZ ends after handing control to the underlying installer. Rebekah begins as
a separate console. The operator does not get one continuous story from
“these partitions will change” to “the governed workspace is ready.”

The installer must emit a durable installation record that Rebekah can show on
first sign-in:

- selected disks and partition identities;
- operations requested;
- dry-run or real-install mode;
- online/offline source;
- Rebekah image digest;
- default model identity;
- completed, skipped, degraded, and failed stages;
- exact recovery action for any incomplete stage.

### 4.3 Binary health is insufficient

The bootstrap contract already distinguishes `starting`, `ready`,
`degraded`, and `failed`. The UI should use those states consistently.

A red/green tile does not tell a user whether the remedy is to wait, connect a
network, free disk space, pull a model, supply a credential, or inspect logs.

### 4.4 Powerful actions are too close to observational surfaces

The current Rebekah console includes an API console and model pull controls in
the same primary shell as the board. OpenCode access can drive agents. Model
pulls can consume substantial disk and network resources.

The default HITL guest surface should be observational and review-oriented.
Operator actions belong behind clear roles and consequence previews.

### 4.5 Standalone surfaces use different language

The same conceptual object can appear as a WeftMark Change Set, Sylvae run,
OpenCode session, Ephor entry, service health item, or raw API response. The
correlation spine exists in documentation but is not yet the visible spine of
the interface.

## 5. Target experience

## 5.1 Global shell

Rebekah should provide a responsive application shell with:

- product name and current workspace;
- global state: **Ready**, **Needs attention**, **Installing**, or **Offline**;
- goal-based primary navigation;
- a compact activity indicator for running agents, skills, model pulls, and
  installation work;
- an attention inbox;
- user/credential menu;
- contextual help;
- an always-available “System details” drawer.

Do not show credentials permanently in the header. Use a dedicated connection
screen or dialog:

1. explain whether the console is local, LAN TLS, or OIDC;
2. accept the bearer token or start OIDC;
3. verify it;
4. replace the credential input with an identity/session control.

Bearer tokens should remain memory-only by default. An explicit “remember for
this tab” option may use `sessionStorage`; never use `localStorage`, URL
parameters, logs, or analytics.

## 5.2 Role profiles

The gateway should return effective UI capabilities after authentication:

| Profile | Default access |
|---|---|
| Observer | board, evidence, run output, health summary |
| Reviewer | Observer plus review decisions and evidence inspection |
| Operator | Reviewer plus model management, service recovery, configuration |
| Administrator | Operator plus integration setup and advanced API tools |

This is an interface projection of server-enforced authorization, not a
client-side security boundary. Hidden controls must also be rejected by the
gateway when called directly.

## 5.3 Attention inbox

A single inbox should aggregate items requiring a human:

- Change Sets awaiting review;
- failed or unavailable evidence;
- Sylvae runs needing inspection;
- OpenCode sessions paused for input;
- model unavailable or insufficient disk;
- installation/recovery warnings;
- optional Ephor held actions.

Each item must say:

- what happened;
- why the person is seeing it;
- consequence of doing nothing;
- safest next action;
- authoritative source;
- related Change Set;
- timestamp and freshness.

The inbox must distinguish **action required**, **degraded but usable**, and
**informational**.

## 6. Surface-by-surface plan

## 6.1 v-BAZ guided installer

Retain PowerShell and make it a structured wizard rather than replacing it with
a graphical installer.

### Proposed steps

1. **Welcome and safety**
   - Alpha status.
   - Backup requirement.
   - QEMU/OVMF and tested-hardware disclosure.
   - Dry-run selected by default.

2. **Machine readiness**
   - UEFI/GPT, Secure Boot, BitLocker, Fast Startup, virtualization, memory,
     disk space, and network state.
   - One line per check: state, impact, remedy.
   - Separate blockers from warnings.

3. **Installation mode**
   - Online, offline bundle, or auto.
   - Explain what will be downloaded and approximate size.
   - Verify bundle manifest and checksums before partition choices.

4. **Host partition**
   - Table with disk number, partition number, letter, label, filesystem,
     capacity, used/free space, boot/system status, BitLocker status.
   - Recommendation text, never automatic destructive selection.
   - “Why this is selectable” and “Why this is blocked.”

5. **Guest storage**
   - Same identity table.
   - Prominent full-volume destruction statement.
   - Explain ZFS purpose and what disabling it changes.

6. **Platform choices**
   - Baseline suite enabled: Rebekah, WeftMark, Sylvae, OpenCode, Ollama.
   - Default model with download/storage estimate.
   - Ephor shown only as “Optional governance integration — install later” or
     as an explicit opt-in requiring repository/artifact access.
   - Advanced runtime choices collapsed by default.

7. **Network and access**
   - Ethernet/tethering/Wi-Fi explanation.
   - Console exposure: local only by default.
   - TLS/OIDC choices deferred unless the operator selects LAN access.

8. **Decision review**
   - A compact disk map.
   - Separate **Will erase**, **Will create**, **Will download**, and
     **Will preserve** sections.
   - Exact partition identities and image/model identities.
   - Export configuration and dry-run report.

9. **Dry-run result**
   - Pass, warning, blocker.
   - Direct links/commands for remediation.
   - “Edit choices,” “Save report,” and “Run installation.”

10. **Installation handoff**
    - Stage names and durable log location.
    - Expected reboot sequence.
    - Recovery instructions.

### Interaction requirements

- Support Back/Edit without restarting.
- Never rely on color alone; retain words such as `ERASE`, `BLOCKED`, and
  `SAFE`.
- Use numbered choices with single-key shortcuts.
- Echo the selected object after every destructive choice.
- Keep secrets masked and out of exported reports.
- Generate both human-readable and JSON dry-run reports.

## 6.2 First boot and recovery

Add a small machine-readable status journal written by the provisioner. Rebekah
should render it when available.

Stages:

- boot environment loaded;
- target partitions verified;
- Alpine installed;
- storage prepared;
- container runtime prepared;
- Rebekah image loaded/pulled;
- model staged/pulled;
- services started;
- console ready.

Every stage needs:

- stable identifier;
- state;
- start/end time;
- short human message;
- log reference;
- retryability;
- safe recovery command.

On the local console, print a short status summary and the authenticated-console
address. Do not display bearer tokens in a photographed or scrollback-prone
screen unless explicitly requested; prefer a root-only retrieval command.

## 6.3 Rebekah console

### Navigation

**Work**

- WeftMark board or compact list.
- Search and filters.
- Change Set drawer with correlated OpenCode sessions, Sylvae runs, evidence,
  and governance status.
- Create/start actions only for authorized roles.

**Review**

- Attention-first queue.
- Evidence completeness.
- Readiness explanation in plain language.
- Approve/request changes actions where the underlying authority supports them.

**Runs**

- Unified activity stream for OpenCode and Sylvae.
- Status, duration, backend/model, owner, cost/token data when available.
- Live progress only where the backend provides a real stream.
- Cancel only when cancellation is supported and server-enforced.

**Models**

- Installed models first.
- Compatibility with current RAM/disk.
- Pull size estimate, storage destination, progress, cancel/retry.
- “Used by OpenCode” and “Used by Sylvae” relationships.
- Online/offline provenance and checksum.

**System**

- Installation record.
- Service states.
- Storage capacity.
- Network mode.
- version/digest manifest.
- updates and diagnostics.
- recovery actions.

**Governance**

- Completely absent from primary navigation when Ephor is not installed.
- When available, show installation source and version before enabling it.
- Surface held actions, policy decisions, audit-chain health, and evidence links.
- Never treat “Ephor absent” as “approved.”

**Advanced**

- API console.
- Raw correlation envelope.
- logs and downloadable support bundle.
- capability and route inspection.

### Board behavior

Desktop may retain five lanes. Mobile should default to a single sorted list
with a lane filter; horizontal lane scrolling may remain as an optional view.

Cards should show only:

- title and stable ID;
- current lane;
- owner/claim;
- readiness;
- one strongest attention reason;
- counts for runs and evidence.

Opening a card reveals detail. Avoid compressing every service identifier into
the card face.

### Empty, loading, and error states

Every panel needs distinct states:

- not configured;
- not installed;
- loading;
- empty;
- offline;
- unauthorized;
- degraded;
- failed;
- stale data.

Each state requires a next action or an explanation that no action is needed.

## 6.4 WeftMark

WeftMark remains the authoritative interaction model for work and review.

### Improvements

- Define a stable UI projection endpoint containing Change Set summary,
  attention reasons, readiness explanation, task counts, evidence counts, and
  correlation links.
- Add stable deep links for Change Sets and filters.
- Preserve the detail dialog on wide screens; use a full-height sheet on mobile.
- Make readiness explainable: list exactly which evidence is missing, failed,
  stale, or passed.
- Add a chronological “story” view combining scope, task claims, runs, evidence,
  review, and promotion.
- Use the same labels and ordering in web, TUI, and Rebekah projections.
- Keep the standalone static projection viewer for portable/offline review.

The existing “What needs attention?” heading is the right model and should
influence the rest of the suite.

## 6.5 Sylvae

The current evidence log is intentionally simple. Preserve the server-rendered,
low-dependency implementation while improving decision support.

### Run form

Before submission show:

- skill name and source;
- declared tier;
- selected backend and model;
- local/remote classification;
- whether credentials, money, or interactive quota may be consumed;
- recursion restrictions;
- expected evidence destination;
- related Change Set.

Agent backends and paid remote backends need an explicit consequence notice.
Cheap local execution should stay fast.

### Run list and detail

Add:

- queued/running/succeeded/unavailable/failed/cancelled states;
- start time, duration, backend, model, and run ID;
- Change Set link;
- input/output disclosure controls;
- copy/download evidence;
- clear distinction between unavailable and failed;
- safe redaction for secrets and unusually large payloads.

In Rebekah, Sylvae should appear as the **Runs** view filtered to skill runs,
not as an iframe containing a second navigation shell.

## 6.6 OpenCode

Do not attempt to replace OpenCode's full product UI in Rebekah.

The suite-facing surface needs only:

- session list;
- session state;
- workspace/Change Set association;
- model/provider;
- last activity;
- waiting-for-human indicator;
- open-in-OpenCode action;
- stop action for authorized operators.

New session creation should ask for a Change Set or explicitly mark the session
unassociated. The gateway must keep OpenCode password handling internal.

## 6.7 Ollama

Treat Ollama as capacity supporting work:

- installed models;
- default model;
- consumers;
- disk usage;
- load state;
- pull/delete operations for operators;
- offline provenance;
- compatibility warnings.

Model deletion is destructive and needs a confirmation naming the exact model
and showing current consumers. Model pull needs size/progress/retry information.

## 6.8 Optional Ephor module

Ephor integration should use a capability lifecycle:

1. **Absent** — no navigation item; optional integration shown under System.
2. **Available to install** — repository/artifact access can be configured.
3. **Installing** — progress and verification.
4. **Installed, disabled** — version present but not enforcing.
5. **Shadow** — decisions recorded but not gating.
6. **Enforcing** — decisions affect governed actions.
7. **Degraded/failed** — fail-closed only for workflows explicitly configured
   to require Ephor; baseline work remains accurately described.

Installation must record source revision, artifact digest, license, tests, and
configuration. The UI must not silently switch from shadow to enforce.

Held-action review should show:

- requester and human reviewer identity;
- requested action and normalized arguments;
- affected resource;
- risk flags and matching policy;
- prior related decisions;
- deadline and default outcome;
- approve/deny/defer/escalate choices actually supported;
- mandatory rationale where policy requires it;
- resulting audit entry and chain hash.

Update user-facing naming to **Ephor**. Preserve KAGP only for compatibility
identifiers and explain it once: “Ephor governance engine (KAGP protocol).”

## 7. Shared design system

Create a small package of CSS custom properties and semantic tokens used by
Rebekah, WeftMark's web review, Sylvae's review server, and optional Ephor web
surfaces.

### Semantic tokens

- `background`, `surface`, `surface-raised`;
- `foreground`, `foreground-muted`;
- `border`, `focus`;
- `primary`, `secondary`;
- `success`, `warning`, `danger`, `info`;
- states: `starting`, `ready`, `degraded`, `failed`, `unavailable`;
- attention levels;
- spacing, radius, type scale, and touch target.

Use the RAGBAZ visual language: solarized/gruvbox-dark character, cyrelian-blue
primary, pale-orange-sand secondary, and the alchemical sun mark. The baseline
must work without downloading fonts; optional online variable fonts may enhance
it when network access exists.

Do not let branding obscure operational meaning. Destructive actions remain
conventionally red and success remains distinguishable from warning.

### Shared components

- application header;
- navigation tabs/rail;
- status badge;
- attention banner;
- data table;
- empty state;
- confirmation dialog;
- detail drawer/sheet;
- progress row;
- evidence badge;
- correlation links;
- copyable identifier;
- code/log viewer;
- toast and inline error;
- skeleton/loading state.

The components may remain framework-free. Share contracts and tokens even when
implementation languages differ.

## 8. Accessibility and human factors

Target WCAG 2.2 AA for browser surfaces.

Required work:

- correct `tablist`, `tab`, and `tabpanel` relationships;
- arrow-key tab navigation and roving `tabindex`;
- visible focus with adequate contrast;
- focus trapping/restoration in dialogs and sheets;
- live regions for background completion and errors;
- skip links and landmark structure;
- labels and descriptions for all fields;
- minimum 44×44 CSS-pixel touch targets on mobile;
- no status communicated by color alone;
- reduced-motion support;
- zoom/reflow at 200–400%;
- table alternatives or cards on narrow screens;
- logical heading order;
- terminal prompts with text symbols in addition to color.

High-consequence actions need recognition rather than recall: show the exact
resource, change, and consequence at the decision point.

## 9. Security and privacy in the interface

- Never put credentials in URLs.
- Never persist bearer tokens beyond the chosen tab without explicit consent.
- Clear authentication state on sign-out.
- Keep strict same-origin CSP and no-CDN operation.
- Redact tokens, authorization headers, environment secrets, and likely secret
  values from logs/support bundles.
- Show stale-data timestamps.
- Treat raw API access as advanced operator functionality.
- Require server authorization for every control; UI hiding is not security.
- Add CSRF protection if cookie-based OIDC sessions are introduced.
- Avoid rendering backend HTML.
- Limit and safely truncate large run inputs, outputs, and logs.
- Make optional telemetry visible and disabled by default for off-grid installs.

## 10. API and data-contract work

Add a versioned Rebekah UI aggregation contract rather than letting the browser
reverse-engineer each backend.

Suggested endpoints:

- `GET /api/v1/session` — identity and effective capabilities;
- `GET /api/v1/system` — suite manifest, installation state, storage/network;
- `GET /api/v1/attention` — normalized attention items;
- `GET /api/v1/activity` — correlated OpenCode/Sylvae/model/install activity;
- `GET /api/v1/change-sets` and `GET /api/v1/change-sets/{id}`;
- `GET /api/v1/models`;
- `GET /api/v1/integrations` — includes Ephor lifecycle when applicable.

Every response should include:

- schema version;
- source service;
- observed timestamp;
- stale-after value where relevant;
- stable IDs;
- actionable error codes;
- links to related resources.

Do not make Rebekah a second source of truth. It may aggregate and normalize,
but decisions and mutations must be delegated to the authoritative component.

## 11. Delivery phases

### Phase 0 — terminology and contracts

**Goal:** prevent UI work from hard-coding the wrong architecture.

- Mark Ephor optional in Rebekah documentation and capability data.
- Define user profiles and effective UI capabilities.
- Define common service and activity states.
- Define attention-item, correlation-link, and installation-journal schemas.
- Define canonical product names and legacy KAGP labels.

**Acceptance:** one contract document with JSON examples; existing behavior can
map into it without inventing false states.

### Phase 1 — installer decision support

**Goal:** make disk and installation choices understandable before any write.

- Convert the existing flow into explicit numbered steps.
- Improve partition tables and readiness checks.
- Add compact disk map and grouped consequence summary.
- Export human and JSON dry-run reports.
- Add editable/back navigation.
- Emit installation journal.

**Acceptance:** a user can identify exactly what is erased and preserved without
reading source code; dry-run changes nothing; automated snapshot tests cover
each prompt path.

### Phase 2 — Rebekah shell and first-run experience

**Goal:** establish the single front door.

- Replace permanent token field with connection flow.
- Add goal-based navigation and role-aware controls.
- Add global state and attention inbox.
- Render installation journal and recovery actions.
- Move API console under Advanced.
- Implement complete loading/empty/error/offline states.

**Acceptance:** observer, reviewer, and operator fixtures each see only relevant
controls; keyboard navigation completes every primary flow.

### Phase 3 — correlated daily work

**Goal:** make the Change Set the visible spine.

- Improve WeftMark UI projection and deep links.
- Add Change Set detail with sessions, runs, and evidence.
- Add unified Runs view.
- Add OpenCode session summary and handoff.
- Add Ollama capacity/model view.
- Add model-pull progress and consequences.

**Acceptance:** from any run/session/evidence record, a user can reach its
Change Set and return without losing context.

### Phase 4 — optional Ephor installation and governance

**Goal:** preserve complete integration without burdening the baseline.

- Add integration discovery/setup under System.
- Verify access, source revision/artifact, license, and health.
- Implement absent/installing/disabled/shadow/enforcing/degraded states.
- Add held-action review only when enabled.
- Prove no baseline screen or health score fails when Ephor is absent.

**Acceptance:** a clean public Rebekah build runs without Ephor; an authorized
opt-in installation attaches governance evidence and can be removed/disabled
without corrupting baseline state.

### Phase 5 — shared design, accessibility, and resilience

**Goal:** consistent quality across standalone and integrated surfaces.

- Adopt shared semantic tokens.
- Complete keyboard, screen-reader, contrast, reflow, and reduced-motion work.
- Test mobile portrait and landscape.
- Test offline/no-CDN operation.
- Test slow API, stale data, partial failure, and reconnect.
- Add downloadable redacted support bundle.

**Acceptance:** automated accessibility checks have no serious violations;
manual keyboard and screen-reader scripts pass; all primary screens work at
320 CSS pixels and 200% zoom.

## 12. Test plan

### Automated

- HTML validation.
- axe-core accessibility tests.
- keyboard-navigation component tests.
- color-contrast token tests.
- API contract/schema tests.
- screenshot regression at desktop, mobile portrait, and mobile landscape.
- offline browser test with all external requests blocked.
- role/capability matrix tests.
- redaction tests.
- installer transcript snapshots.
- destructive-action confirmation tests.
- optional-Ephor absence tests.

### Interactive

Run task-based sessions for:

1. first-time operator choosing partitions;
2. cautious operator performing dry-run and editing choices;
3. reviewer finding why a Change Set is not ready;
4. reviewer inspecting a Sylvae run;
5. operator pulling an offline-compatible model;
6. operator diagnosing degraded service;
7. HITL guest using mobile portrait;
8. administrator adding Ephor later;
9. administrator switching Ephor from disabled to shadow and then enforcing.

Measure completion, wrong turns, time to identify consequences, and whether the
user can explain what will happen before confirming.

### Environment matrix

- Windows PowerShell 5.1 and PowerShell 7;
- Chromium and Firefox;
- desktop, phone portrait, phone landscape;
- keyboard only;
- common screen reader on Windows plus a mobile screen reader;
- online, offline, high latency, and interrupted connection;
- Ephor absent and Ephor enabled;
- fresh install, degraded install, and recovery.

## 13. Repository ownership

| Deliverable | Owning repository |
|---|---|
| Installer wizard, dry-run report, installation journal writer | v-BAZ |
| Application shell, aggregation API, auth UX, system and integration views | Rebekah |
| Change Set projection, readiness explanation, deep links | WeftMark |
| Run metadata, evidence presentation, backend consequence metadata | Sylvae |
| Optional governance setup and held-action semantics | Ephor |
| Shared token specification and interface contract | Rebekah, consumed by others |

Rebekah coordinates the experience but must not absorb the domain logic of the
other repositories.

## 14. First implementation slice

The smallest slice that materially improves the suite is:

1. document Ephor as optional in Rebekah's runtime capability model;
2. add `/api/v1/session`, `/api/v1/system`, and `/api/v1/attention`;
3. replace the header token field with a connection dialog;
4. change navigation to Work, Review, Runs, Models, System, Advanced;
5. move the API console to Advanced;
6. add Change Set detail linking WeftMark, OpenCode, and Sylvae identifiers;
7. make the board switch to list view on mobile;
8. render four-state service health with remedies;
9. add keyboard-complete tabs/dialogs and live error/status regions;
10. add Ephor absence tests.

This slice reuses the current gateway, console, WeftMark projection, and service
APIs. It does not require a frontend framework, a CDN, or a new source of truth.

For the Change Set detail (item 6), the keyboard work (item 9), and — when they
are picked back up — passkey auth and mutating `/api/v1` routes with
optimistic concurrency, see the concepts and HTTP contracts distilled from the
RAGBAZ kanban sketch in
[`UI-INSPIRATION-RAGBAZ-KANBAN.md`](UI-INSPIRATION-RAGBAZ-KANBAN.md). It is a
design reference only; its adoption boundaries (no framework/CDN, `textContent`
rendering, fail-closed gateway) are recorded there.

### 14.1 First-slice status

All ten items above are implemented and guarded by `tests/gateway.sh`:

1. Ephor documented/presented as optional (runtime + `/api/v1/system` + console).
2. `/api/v1/session`, `/api/v1/system`, `/api/v1/attention` (plus
   `/api/v1/change-sets[/{id}]`) on `rebekah-gateway`.
3. Header token field replaced by a connection dialog that explains the
   local/LAN-TLS/OIDC posture; bearer tokens are memory-only unless "remember
   for this tab" is chosen (§9).
4. Goal-based nav: Work, Review, Runs, Models, System, Advanced.
5. API console lives under Advanced.
6. Change Set detail correlating WeftMark git/evidence/review/handoff/claims/
   tasks, with OpenCode/Sylvae link slots (see
   [`WEFTMARK-RUNTIME-LINKS-SCOPE.md`](WEFTMARK-RUNTIME-LINKS-SCOPE.md) for the
   upstream work that fills them).
7. Board switches to a single attention-first list with a lane filter on narrow
   screens.
8. Four-state service health (Ready / Needs attention / Installing / Offline)
   with remedies, from `/api/v1/system`.
9. Keyboard-complete tabs and dialogs (roving `tabindex`, arrow keys, focus
   restoration), a skip link, live status/error regions, reduced-motion, and
   44×44 touch targets.
10. Ephor-absence coverage in `tests/gateway.sh`.

The remaining human-interface work (Phase 3 correlated daily work, and the
mutating/optimistic-concurrency and passkey slices) is out of this first slice.
