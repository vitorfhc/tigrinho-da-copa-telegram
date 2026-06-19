# Splitwise Auto-Registration for Bolãozinho Results — Design Spec

**Date:** 2026-06-19
**Status:** Draft for user review (pre-planning)
**Feature:** Feature 8 — Splitwise integration (becomes COMPLETION.md §23)

> **Naming.** As with §22, the UI says **"bolãozinho"** (pt-BR) and the internal code keeps English
> identifiers (`tournament*` tables/models). "Splitwise" is the external expense-sharing service
> (<https://splitwise.com>) whose REST API the bot calls.

> **Grounding (MANDATORY before coding — §2/§11).** The Splitwise REST API surface below was verified
> against the official docs on 2026-06-19: <https://dev.splitwise.com/> (endpoints), the auth guide
> <https://splitwise.readthedocs.io/en/latest/user/authenticate.html>, and the community Python SDK
> <https://github.com/namaggarwal/splitwise>. **Re-verify exact field names, the `add_user_to_group`
> signature, and status codes against the live docs when writing `providers/splitwise.py`**, and record
> the doc URL in a comment next to the integration. If live docs disagree with this spec, live docs win
> and this spec + COMPLETION.md must be updated.

---

## 1. Summary

A bolãozinho (§22) is a **real-money side-pot** where the bot is *bookkeeping only — it never moves
money*. Splitwise is the same kind of bookkeeping: a shared "who-owes-whom" ledger. This feature mirrors
a finished bolãozinho's result into a Splitwise **group** so the friends' running balances update
automatically, with **zero money movement** — fully consistent with §22's philosophy.

When a bolãozinho finishes, the bot creates one Splitwise **expense** that encodes the net settlement:
each loser owes their entry stake; the winner(s) are credited. Players link a Splitwise **email** to the
bot once (a wizard flow); a finished bolãozinho is registered only when every entrant is linked.

The feature is **optional and dormant** unless both a Splitwise API key (`.env`) and a target group id
(`config.yaml`) are configured — exactly like the `/palpite` / `GEMINI_API_KEY` gate.

## 2. Product decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Player ↔ Splitwise identity | **Email.** New `players.splitwise_email`; expenses created `by email` (`users__N__email` + `first_name`). Splitwise matches/invites by email, so no numeric Splitwise user-id lookup is needed. |
| 2 | Destination | **One shared Splitwise group** (`config.yaml splitwise_group_id`). Members are added to the group at link time (best-effort `add_user_to_group`). |
| 3 | Link timing | **Required before joining** — but only for **AUTO-mode** bolãozinhos (feature-era; see §6). `/entrar` blocks until the player has linked. Guarantees every AUTO entrant is registrable, so its expense always balances. |
| 4 | Linking UX | **Keyboard-wizard first** (CLAUDE.md rule): argless `/vincular_splitwise` (or the 🔗 join-card button) opens a wizard that prompts for the email. The email is the only unavoidable typed value. |
| 5 | Ledger model | **One expense per bolãozinho**, "losers fund winners": `cost = (n−k)×entry`; each loser `owes entry` / `paid 0`; each winner `paid cost//k` (odd cents to first winners) / `owes 0`. Single winner = exactly the announced prize. Pure, 100%-covered. |
| 6 | When a bolãozinho registers | **AUTO** (feature-era): auto-registers at settle. **MANUAL** (old/in-flight): the bot **notifies the admin** when it becomes fully linked and the **admin manually triggers** registration. **EXCLUDED**: never touched. |
| 7 | Corrections (re-grade flips result) | **`update_expense` in place**, capped at 2 (mirrors `CORRECTION_POST_CAP`); beyond the cap → admin DM only. AUTO mode only. |
| 8 | Already-settled / closed at deploy | The migration marks every **FINISHED/CANCELLED** bolãozinho `EXCLUDED` (covers the ones already settled in Splitwise by hand). Bot never registers or notifies for them. |
| 9 | Currency | New `config.yaml splitwise_currency_code` (ISO 4217, default `BRL`). The display symbol stays `tournament_currency` (`R$`); only the API call uses the ISO code. |
| 10 | Feature gate | Active iff `SPLITWISE_API_KEY` (`.env`) **and** `splitwise_group_id` (`config.yaml`) are both set; validated at startup via `get_current_user`. Otherwise fully dormant. |

## 3. Configuration & secrets (§4)

- **`.env`** (new secret, disjoint from config): `SPLITWISE_API_KEY` — a Splitwise personal API key
  (Bearer auth). Optional. Add to `.env.example` (placeholder) and document in `CLAUDE.local.md` that
  **prod `.env` now carries a 4th secret**.
- **`config.yaml`** (new, non-secret):
  - `splitwise_group_id: int | None` (default `None`)
  - `splitwise_base_url: str` (default `https://secure.splitwise.com/api/v3.0`)
  - `splitwise_currency_code: str` (default `BRL`)
- **Startup validation (fail-fast, §3):** when the feature is enabled, call `get_current_user` once at
  startup; a 401/invalid key or unreachable host logs a clear error and **disables the feature for the
  run** (the bot still boots — Splitwise is non-critical, unlike the Telegram token). Add to
  `config.example.yaml`.

## 4. Data model & migration (one append-only migration, §22.6 style)

`players`:
- `splitwise_email: Mapped[str | None]` (nullable `String`).

`tournaments`:
- `splitwise_mode: Mapped[SplitwiseMode]` — enum `{AUTO, MANUAL, EXCLUDED}`.
- `splitwise_expense_id: Mapped[int | None]` — set once the bot registers (idempotency + corrections).
- `splitwise_synced_signature: Mapped[str | None]` — last `signature_of(outcome)` pushed to Splitwise
  (so we only `update_expense` when the result actually flips).
- `splitwise_admin_notified_at: Mapped[datetime | None]` — fire-once marker for the MANUAL "ready to
  register" admin DM.

**Migration data-fix (the transition rule):** in the same append-only migration, for **existing** rows:
- every `FINISHED` or `CANCELLED` tournament → `splitwise_mode = EXCLUDED` (ignored forever — this is
  what protects the bolãozinhos already settled in Splitwise by hand);
- every `OPEN` or `DRAFT` tournament → `splitwise_mode = MANUAL` (trackable for later manual
  registration).

New rows default to `MANUAL`; `open_tournament` promotes to `AUTO` when the feature is enabled at open
time (see §6). Migrations are append-only and never edit prior migrations (guardrail).

## 5. The three Splitwise modes (per bolãozinho)

| Mode | Set when | Join guard | Registration | Corrections |
|------|----------|-----------|--------------|-------------|
| `AUTO` | `open_tournament` runs **with the feature enabled** | `/entrar` requires a linked email | **Automatic** at settle (`resolve_and_post`); retried by the sweep on transient failure | Auto `update_expense`, capped |
| `MANUAL` | opened while feature **off**, or an existing OPEN/DRAFT at deploy | none (frictionless join, old behavior) | **Admin-triggered only.** Bot DMs the admin once when the roster is fully linked; admin runs the trigger command | none (admin re-runs the trigger) |
| `EXCLUDED` | existing FINISHED/CANCELLED at deploy, or admin sets it | none | **Never** | never |

Mode is **per-bolãozinho and lifelong** — a bolãozinho behaves consistently for its whole life, which is
why old OPEN bolãozinhos keep frictionless joining instead of suddenly blocking mid-tournament.

## 6. Email linking (wizard) + join guard

- **`/vincular_splitwise`** (player-facing, DM; group → deep-link button into DM, mirroring `/entrar`):
  **argless**. Opens a wizard: bot prompts "Envie seu e-mail do Splitwise"; the player's next text
  message is captured (stateful step) and validated (RFC-lite format check). On success:
  1. store `players.splitwise_email` (global — one link covers every bolãozinho);
  2. best-effort `add_user_to_group(splitwise_group_id, email, first_name=display_name)` so the player is
     a group member before any expense references them (sidesteps the undocumented "create_expense by
     email auto-invites?" question);
  3. confirm in pt-BR. A 🔗 button also lives on the **AUTO** join card when the player has no email.
- **Re-link / update:** running `/vincular_splitwise` again overwrites the stored email (no unlink
  command — out of scope).
- **Join guard** (`tournament_service.join`): if the bolãozinho's mode is `AUTO` and the player has no
  `splitwise_email`, raise `TournamentError("Vincule seu Splitwise antes de entrar")` and surface the 🔗
  button. `MANUAL`/`EXCLUDED` bolãozinhos and the feature-disabled case are unaffected
  (fully backward-compatible).
- **Telegram reachability caveat** (same as §22.3 open-broadcast): the bot can only DM players who have
  pressed Start. Nudges to never-Started players silently fail; they fall to the group nudge / fallback.

## 7. Pure ledger math (`domain/splitwise_ledger.py`, 100% line+branch coverage)

Pure, deterministic, no I/O — mirrors `domain/tournament.py`. Operates on **integer cents**.

```python
@dataclass(frozen=True, slots=True)
class LedgerShare:
    paid_cents: int
    owed_cents: int

def build_ledger(
    entrant_ids: Sequence[int],     # the participants included in this expense
    winner_ids: Sequence[int],      # subset of entrant_ids (from the result)
    entry_price_cents: int,
) -> dict[int, LedgerShare]:
    ...
```

Rules:
- `losers = entrant_ids − winner_ids`; `n = len(entrant_ids)`, `k = len(winner_ids)`.
- `cost = len(losers) × entry_price_cents`.
- Each loser: `paid 0, owed entry`. Each winner: `owed 0, paid cost//k` with the `cost % k` leftover
  cents distributed one-per-winner to the first winners (ascending id) — so `Σpaid == Σowed == cost`
  **exactly** (Splitwise rejects unbalanced expenses).
- **Single winner (k=1):** the `n−1` losers each owe `entry`, the winner is owed `(n−1)×entry` =
  **exactly the announced prize**.
- **Ties (k>1):** winners split the **losers' stakes** equally; this is the exact zero-sum settlement.
  It may differ by a few cents from the §22 display-only "prize ÷ k" abstraction, which intentionally is
  not cash-conserving on ties. **Documented in §23 of COMPLETION.md.**
- **`cost == 0`** (lone entrant, or a full tie where there are no losers) → **register nothing** (no
  money changes hands). The caller treats an empty/zero ledger as "skip".

A separate pure helper converts cents → the API's decimal string for the configured currency
(`f"{cents/100:.2f}"` for 2-decimal currencies).

## 8. Splitwise API client (`tigrinho/providers/splitwise.py`, async httpx)

Thin, strongly-typed wrapper. Auth: `Authorization: Bearer <SPLITWISE_API_KEY>`. Base URL from config.
Methods (verify exact params against live docs before coding):
- `get_current_user() -> SplitwiseUser` — startup validation.
- `add_user_to_group(group_id, *, email, first_name) -> None` — best-effort group membership at link
  time. Tolerate "already a member".
- `create_expense(*, group_id, cost, currency_code, description, shares) -> int` — `POST /create_expense`
  with `users__N__email/first_name/paid_share/owed_share`. Returns the new expense id. Raises
  `SplitwiseError` if the response body's `errors` object is non-empty (the API returns HTTP 200 with an
  `errors` map on failure — must be checked, not just the status code).
- `update_expense(expense_id, *, cost, description, shares) -> None` — `POST /update_expense/{id}`; send
  only changed fields.

`SplitwiseError` carries the parsed error messages for logging / admin DMs. All calls are `async`
(httpx) — never block the PTB event loop (§3 network=async rule).

## 9. Registration service & triggers (`tigrinho/splitwise_service.py`)

Telegram-agnostic orchestration; **never commits** (caller owns the unit of work, §22 convention). The
network half lives in the bot layer (it needs the async client + AppContext).

`build_registration(session, tournament_id) -> SplitwiseRegistration | None` (pure-ish; DB reads only):
- Reads entrants + emails, winners, `entry_price_cents`, mode, `splitwise_expense_id`,
  `splitwise_synced_signature`.
- Returns `None` (skip) when: feature disabled · mode `EXCLUDED` · no scorable result · `cost == 0` ·
  signature already synced · **any entrant unlinked** (the all-linked precondition).
- Otherwise returns a `SplitwiseRegistration { tournament_id, expense_id|None, shares_by_email, cost,
  description, signature }` describing a **create** (no `expense_id`) or **update** (existing id, changed
  signature).

The bot-layer caller (in `bot/tournament_announce.py`) executes the registration with the async client,
then persists `splitwise_expense_id` + `splitwise_synced_signature` in a fresh session and commits.
Best-effort: any `SplitwiseError`/network failure → log + admin DM (§14); never crashes the bot.

**Triggers:**
1. **Settle (AUTO):** `resolve_and_post` (already runs after every game state change) — after posting the
   winner announcement, for `AUTO` bolãozinhos it runs the registration (create, or update on a
   correction). The expense description: `🏆 Bolãozinho '<name>' — <winner(s)>`.
2. **Sweep (AUTO retry + MANUAL notify):** `bolaozinho_sweep` (periodic) —
   - **AUTO**: retry any finished AUTO bolãozinho whose `splitwise_synced_signature` ≠ current signature
     (covers transient API failures). *(Note: unlike the §22.4 "no provider calls" rule — which is about
     the budget-limited API-Football provider — the sweep MAY now make Splitwise calls; Splitwise is not
     the football provider and is best-effort.)*
   - **MANUAL**: detect a finished, non-excluded, not-yet-registered, not-yet-notified bolãozinho whose
     roster is **now fully linked** → DM the admin **once** ("Bolão #X (nome) está totalmente vinculado.
     Use /bolaozinho_splitwise pra registrar.") and set `splitwise_admin_notified_at`. **No Splitwise
     call** — registration stays admin-triggered.
3. **Manual trigger (MANUAL):** the admin command in §11.

## 10. Corrections (AUTO only)

Mirrors the existing §22.4 group-correction machinery. When a re-grade flips an already-registered AUTO
bolãozinho's result, the current `signature_of(outcome)` differs from `splitwise_synced_signature`:
- `update_expense(splitwise_expense_id, …)` with the corrected shares, capped at **2** auto-corrections
  per bolãozinho (a `splitwise_corrections` counter in `AppContext`, parallel to
  `tournament_corrections`); beyond the cap → admin DM only ("recalculado de novo; ajuste o Splitwise via
  /bolaozinho").
- A no-result/cancel after a registration is **not** auto-undone (rare; DM the admin). Deleting an
  expense is out of scope (the admin can delete in the Splitwise app).

## 11. Commands

**Telegram (wizard-first, per CLAUDE.md):**
- `/vincular_splitwise` — player linking wizard (§6). Added to `/ajuda` + the private/group command
  lists.
- `/bolaozinho_splitwise` — **admin-only** manual-trigger wizard. Argless → a **picker** of bolãozinhos
  ready for manual registration (`MANUAL` mode, `FINISHED`, not excluded, fully linked, not yet
  registered). Tap one → the bot creates the Splitwise expense and confirms. This is the command the
  admin uses after receiving the "ready to register" DM. New `callbacks.TournamentOp` opcode (≤64-byte
  `callback_data`, e.g. `bw:<id>`).

**Admin CLI (`python -m tigrinho.cli bolaozinho …`, §22.7 — not subject to the wizard rule):**
- `register-splitwise <id> [--force] [--yes]` — manually register/refresh a bolãozinho's expense.
  `--force` (destructive → needs `--yes`) registers among **linked entrants only** for a never-linker:
  it **refuses if any winner is unlinked** (cannot credit a payout to someone off Splitwise) and drops
  unlinked **losers** with a loud warning (deliberate, admin-acknowledged ledger distortion).
- `splitwise-exclude <id> [--yes]` — set a bolãozinho `EXCLUDED` (e.g. one already settled by hand that
  the migration didn't catch). Destructive-ish → `--yes`.
- `nudge-splitwise [--yes]` — one-shot, idempotent: best-effort DM every **unlinked** entrant of every
  non-excluded `MANUAL`/`AUTO` OPEN bolãozinho with the 🔗 link prompt. Run once after deploy; safe to
  re-run.
- `splitwise-status [id]` — read-only: show each bolãozinho's mode, expense id, and linked/total
  entrants (operator visibility).

## 12. Transition / deploy runbook (the in-flight problem)

1. Apply the migration → existing FINISHED/CANCELLED become `EXCLUDED` (incl. the already-by-hand-settled
   ones); existing OPEN/DRAFT become `MANUAL`.
2. Set `SPLITWISE_API_KEY` (`.env`) + `splitwise_group_id`/`splitwise_currency_code` (`config.yaml`) on
   prod (scp — not carried by `git pull`).
3. Run `cli bolaozinho nudge-splitwise` once → unlinked entrants of in-flight bolãozinhos get the 🔗 DM.
4. As stragglers link, MANUAL bolãozinhos that have finished and become fully linked → the bot DMs the
   admin once → admin runs `/bolaozinho_splitwise` (or `cli … register-splitwise <id>`) to register.
5. New bolãozinhos opened from now on are `AUTO`: everyone is forced to link at join, so they
   auto-register at settle.

Passive nudge: the partial-placar / reminder posts for `MANUAL`/`AUTO` bolãozinhos with unlinked entrants
may carry a one-line "🔗 vincule seu Splitwise" hint (best-effort, no new ping spam — plain text).

## 13. Edge cases

- **Lone entrant / full tie (no losers):** `cost == 0` → skip (no expense).
- **Unlinked entrant in an AUTO bolãozinho at settle:** should never happen (join guard), but defensively
  → skip + admin DM (treated like MANUAL fallback).
- **Player links after a MANUAL bolãozinho already EXCLUDED:** never re-flagged (EXCLUDED is terminal
  unless admin changes it).
- **Re-running `nudge-splitwise`:** idempotent (just re-DMs unlinked entrants; harmless).
- **Splitwise API/network down:** create/update fail best-effort → admin DM; AUTO retried next
  sweep/settle; MANUAL stays pending until the admin re-triggers.
- **Email no longer matches a real Splitwise account:** Splitwise invites the email; the expense still
  records correctly against the invited placeholder user. No special handling.
- **Feature toggled off after some AUTO registrations:** further registrations/corrections are skipped;
  existing expenses are left as-is.

## 14. Error handling (§14)

All Splitwise interactions are **best-effort and non-critical**: failures log (`structlog`) and DM the
admin, never raise into the poll/sweep/handler flow. The Telegram winner announcement (§22.4) is always
posted regardless of Splitwise outcome. Startup `get_current_user` failure disables the feature for the
run rather than crashing (Splitwise ≠ Telegram token criticality).

## 15. Testing & gates (all four gates green; domain ~100%)

- **`domain/splitwise_ledger.py`:** 100% line+branch — single winner, k-way tie (incl. odd-cent
  remainder distribution), lone entrant (cost 0), full tie (cost 0), `--force` subset, balance assertions
  (`Σpaid == Σowed == cost`).
- **`splitwise_service.build_registration`:** skip-when-disabled / EXCLUDED / no-result / cost-0 /
  unlinked-present / already-synced; create vs update (signature flip); MANUAL never auto-registers.
- **`providers/splitwise.py`:** mocked httpx — create/update/add-user/get-current-user happy paths, the
  `errors`-in-200-body failure path, auth header.
- **Handlers/CLI:** link wizard (prompt → store → add-to-group), join guard blocks unlinked on AUTO,
  `/bolaozinho_splitwise` picker + register, CLI `register-splitwise`/`--force`/`exclude`/`nudge`/
  `status`, sweep MANUAL-notify (fire-once) + AUTO-retry, corrections cap.
- **Migration:** existing FINISHED/CANCELLED → EXCLUDED, OPEN/DRAFT → MANUAL.

## 16. Docs to update (§11 maintenance rule — same change)

- **COMPLETION.md:** new **§23** (this feature) + `config.yaml` table rows (`splitwise_group_id`,
  `splitwise_base_url`, `splitwise_currency_code`) + secret note (`SPLITWISE_API_KEY`) + a change-log
  entry; note the tie-vs-prize divergence.
- **`/ajuda`:** add `/vincular_splitwise` (+ the AUTO-join linking requirement) and `/bolaozinho_splitwise`.
- **README**, **PROGRESS.md** (add Feature 8 to the §18 milestone list + tick items).
- **`CLAUDE.local.md`:** prod `.env` now carries a **4th** secret (`SPLITWISE_API_KEY`); add
  `splitwise_group_id`/`splitwise_currency_code` to the list of `config.yaml` values to scp.
- **CLAUDE.md:** wizard convention — **already added** in this branch.

## 17. Module layout

```
tigrinho/domain/splitwise_ledger.py     # pure ledger math (100% covered)
tigrinho/providers/splitwise.py         # async httpx client + SplitwiseError
tigrinho/splitwise_service.py           # build_registration / mode logic (no commit, no network)
tigrinho/bot/tournament_announce.py     # +execute registration after winner post (AUTO)
tigrinho/bot/tournament_handlers.py     # /vincular_splitwise wizard, join guard, /bolaozinho_splitwise
tigrinho/jobs (sweep)                    # +AUTO retry, +MANUAL ready-notify
tigrinho/cli.py                          # register-splitwise / splitwise-exclude / nudge / status
tigrinho/db/models.py                    # +columns, SplitwiseMode enum
tigrinho/db/migrations/versions/*.py     # one append-only migration (+data-fix)
tigrinho/config.py                       # +3 config fields, +1 secret, startup validation
```

## 18. Out of scope (YAGNI)

- OAuth flow (API key is sufficient for a single bot account).
- Per-bolãozinho Splitwise groups (one shared group only).
- Splitwise → bot sync / webhooks (one-way push only).
- Expense **deletion** / undo on cancel (admin handles in the Splitwise app).
- Splitwise categories, receipts, comments, currency conversion.
- An unlink command.

## 19. Decisions flagged for review

- **Manual-trigger surface:** spec puts the trigger as a **Telegram wizard** (`/bolaozinho_splitwise`,
  consistent with the wizard rule + convenient from the admin DM) **and** a CLI `register-splitwise`. If
  you'd rather it be CLI-only, drop the Telegram command.
- **Sweep makes Splitwise calls** (AUTO retry). If you want the sweep to stay network-call-free for
  Splitwise, AUTO retry can move to "next game-state-change only" (slower recovery from transient
  failures).
- **Passive nudge line** on placar/reminder posts — included as best-effort; easy to cut if it feels
  noisy.
- **Tie ledger** intentionally diverges from the display-only "prize ÷ k" (it is the exact zero-sum
  settlement). Confirm this is acceptable.
