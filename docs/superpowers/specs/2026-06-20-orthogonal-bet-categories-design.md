# Design: Orthogonal bet categories (de-coupling the bet set)

**Date:** 2026-06-20
**Status:** Approved (design), hardened after adversarial multi-agent review — pending final spec
review, then implementation plan
**Touches:** `COMPLETION.md` (§8.1 **and** §8.2, §9.3), `README.md`, `domain/bets.py`,
`domain/scoring.py`, `domain/settlement.py`, `providers/base.py` + the API-Football mapper,
`db/models.py` + one **additive** Alembic migration (half-time-score columns only),
`bot/bets_handlers.py`, `bot/callbacks.py`, `bot/keyboards.py`, `domain/text_pt.py`
(`/ajuda`, `CATEGORY_LABELS`, `CATEGORY_ORDER`, `describe_bet_value`, the bettor-count denominator),
the `/palpite` AI surface (`ai/schemas.py`, `ai/prompt.py`, `palpite_service.py`), `cli.py`,
`PROGRESS.md`, and the category-coupled tests.

> **Review note (2026-06-20).** This spec was reviewed by a 43-agent adversarial workflow (5
> dimensions, every finding verified against the code). 29 findings were confirmed/partial. The two
> material outcomes: (1) the originally-proposed third market `WHICH_HALF` was **dropped** — review
> proved `SECOND` is a known, persistent ~45% favourite (19-World-Cup base rate: ~43% of goals in the
> 1st half vs ~54% in the 2nd; PMC6815073), i.e. a blind farm, violating the no-easy-EV rule; (2) a
> large set of correctness/surface gaps (half-time-score plumbing, the manual-override path, the
> rollout boundary, `/palpite`, and every full-category-set reader) were folded in below.

## Problem

The five current markets are **too coupled** — deciding one decides the others. They all read the
**same underlying variable**: the 90′ scoreline (`FIRST_TEAM` also peeks at goal *order*). Three are
*deterministic functions* of `EXACT_SCORE`.

Worked example — a player who predicts **1×0 home**:

| Market | Forced value once you say "1×0 home" |
|---|---|
| `EXACT_SCORE` | 1×0 |
| `WINNER` | HOME — **forced** |
| `BTTS` | ONLY_HOME — **forced** |
| `OVER_UNDER` | UNDER (1 ≤ 2) — **forced** |
| `FIRST_TEAM` | HOME — all-but-forced |

So forming **one** scoreline opinion answers **4 of 5** markets. They are one question asked at five
resolutions, not five separate questions.

The math is unavoidable: **any market that is a function of the final score correlates with every
other one.** Real independence requires markets on *different axes* of the match.

## Design principle

> `EXACT_SCORE` already owns every **aggregate-score** question (winner, margin, total, BTTS,
> over/under are all coarsenings of it). That is *why* those four were redundant. So every satellite
> must instead ask a question the scoreline leaves open — and it must be **balanced** (no blindly
> farmable favourite).

Constraints agreed during brainstorming, and how they pruned the field:

- **No easy expected value (the hard rule).** No market may have a *blindly* farmable side: a binary
  must be a near-coinflip, and a 3-way must have no option a player picks every game without thought.
  This rejected "red card yes/no" (~25/75) and "penalty yes/no" (~30/70). On review it also rejected
  **`WHICH_HALF`** — "more goals in the 1st or 2nd half" has a *known, structural* favourite
  (`SECOND` ~45%, stable across 19 World Cups), so it is a blind farm, not a coinflip.
- **No cards / penalties.** The only easy-to-grade discipline/set-piece markets are also the ones
  with a farmable favourite.
- **Pure-luck filler investigated and rejected on grounding.** "Which team kicks off" is the most
  orthogonal idea possible but **API-Football v3 has no coin-toss / kick-off data** (events are only
  Goal/Card/Substitution/VAR; the fixture object exposes no such field — verified 2026-06-20). The
  only gradeable luck fillers were either coupled to the score (total-goals odd/even) or void-prone
  and quirky (first-goal-minute odd/even).
- **Quality over quantity.** Rather than pad the set with a market that breaks a constraint, the set
  is **two markets that each satisfy every constraint**. Two clean bets beat three where the third is
  a blind farm. (Decision confirmed by the user after review surfaced the `WHICH_HALF` defect.)

## The new set

| # | `BetCategory` | Selection | Axis | Points* |
|---|---|---|---|---|
| 1 | `EXACT_SCORE` *(unchanged)* | `{home:int, away:int}` | Final scoreline | **5** |
| 2 | `HALF_TIME_RESULT` *(new — replaces `WINNER`)* | `{sel: HOME\|DRAW\|AWAY}` at the break | In-game time-slice | **2** |

*\*Tentative; calibrated against real WC base rates before locking. See Balance & points below.*

**Removed and why:**
- `WINNER` → coarsening of `EXACT_SCORE` (1×0 forces HOME). Replaced by `HALF_TIME_RESULT`, which
  keeps the familiar "who's on top?" feel but on a *different time-slice* the final score can't pin.
- `OVER_UNDER` → coarsening of the total, a coarsening of `EXACT_SCORE`. (Currently **1 pt** — the
  lone sub-2 outlier in the old 5/2/2/2/1 table, priced low because it had a farmable favourite.)
- `BTTS` → a function of the score (1×0 ⇒ ONLY_HOME). Pure aggregate.
- `FIRST_TEAM` → partially coupled (a clean-sheet win forces it), the most score-correlated of the
  old satellites, and it voids on 0-0.
- `WHICH_HALF` *(considered, rejected on review)* → blindly farmable `SECOND` side (see above).

## Grading rule (PURE, `domain/scoring.py`)

`HALF_TIME_RESULT` grades on the **half-time score** `(ht_home, ht_away)` of normal time:

- `HOME` if `ht_home > ht_away`; `AWAY` if `ht_away > ht_home`; `DRAW` if equal.
- **No knockout special-case.** A half-time score can always be level, so `DRAW` is always a valid
  option (unlike `WINNER`, which had to hide `DRAW` in knockouts — simpler UI and grading). In a
  knockout decided by extra time / penalties, this still grades the **regulation** half-time sign,
  consistent with the project rule that all score-based markets read the 90′/regulation match.
- **Void-free for any match that was actually played** (every played match has a half-time state).
  **Exception — missing half-time score:** API-Football v3 does **not** guarantee `score.halftime`
  is populated for `status=FT`; it is `null` for awarded/walkover fixtures (which the mapper maps
  `AWD`/`WO` → `FINISHED`) and can be absent on backfill gaps. **Rule:** a missing half-time score
  **voids `HALF_TIME_RESULT` for that fixture only** (mirroring how `FIRST_TEAM` voids on 0-0) — it
  must **not** fail the whole settlement, because the same fixture may carry `EXACT_SCORE` bets that
  grade fine. So "void-free" is honest for normally-played matches; a walkover is the one void case.

This replaces the misleading earlier claim that the market "never voids".

## De-coupling proof

Re-run the `1×0 home` example against the new set:

| Market | Constrained by "1×0 home"? |
|---|---|
| `EXACT_SCORE` | the bet itself |
| `HALF_TIME_RESULT` | **open** — the break could be 0-0 (`DRAW`) or 1-0 (`HOME`); only `AWAY` is excluded |

`HALF_TIME_RESULT` is a *different random variable* than the final score — the half-time leader is
not a function of the 90′ result. It is only **partially** constrained: a team shut out across the
whole match cannot have led at the break, so a clean-sheet final removes one of the three options.
For any score with goals on both sides, or higher-scoring games, all three options stay live. This is
a large improvement over the old set, where `1×0` forced four markets outright.

## Balance & points

`HALF_TIME_RESULT` passes the no-easy-EV rule, unlike the rejected markets:

- **Base rate (to state in `COMPLETION.md` §8.1 when points are locked):** at WC level the half-time
  result is `DRAW` ~40-46% (the modal leg), with `HOME` and `AWAY` ~27-28% each. The modal `DRAW` is
  a **minority** outcome — "always pick DRAW" is *wrong* >55% of the time, so it is a losing farm,
  the opposite of the ~70-75% free-money side that disqualified the red-card/penalty binaries. Naming
  the correct `HOME`/`AWAY` requires a genuine read. This is the same profile as the `WINNER` 3-way
  the project already ships and accepts as fair (`COMPLETION.md` §8.1, chosen-leg p≈0.48).
- The "~45%" guideline is a *no-obvious-favourite* heuristic, not a hard disqualifying ceiling; a
  ~45% **minority** leg in a 3-way is acceptable (and is exactly why `WHICH_HALF`'s ~45% leg — which
  has a *known* correct side you pick blind — is different and was cut).
- **Points 5/2.** Monotonic with rarity (`EXACT_SCORE` modal ~10-11% → 5; `HALF_TIME_RESULT` modal
  ~45% → 2). `HALF_TIME_RESULT` mirrors the old 3-way `WINNER`, which §8.1 priced at **2**, so 2 is
  the §8.1-consistent value. (Review confirmed 5/2 does **not** violate §8.1's "no category dominates
  on expected points" rule — that rule forbids a market being *easier-and-worth-more than a harder
  peer*, not modal-EV parity with `EXACT_SCORE`, which the old locked table never had either.) Record
  the computed base rates + this rationale in §8.1 when locking, as the 2026-06-16 decision did.

## New data dependency: the half-time score

Today `MatchResult` (`providers/base.py`) and `GradingContext` (`domain/scoring.py`) carry only the
90′ score (`home_goals_90`/`away_goals_90`, from `score.fulltime`), and the API-Football mapper
parses **only** `score.fulltime` (it does not currently consume `score.halftime`). `HALF_TIME_RESULT`
needs the **half-time score**, exposed by API-Football v3 as `score.halftime`. Plumbing:

1. **Provider.** Add `home_goals_ht: int | None` / `away_goals_ht: int | None` to `MatchResult`
   (default `None`, like `live_home_goals`); the API-Football mapper populates them from
   `score.halftime` (new parse, alongside the existing `score.fulltime` extraction).
2. **GradingContext (optional + lazy fail-fast).** Add `home_goals_ht: int | None` /
   `away_goals_ht: int | None` to `GradingContext` (**optional**, *not* required `int`).
   `build_context` (`domain/settlement.py`) populates them from `MatchResult` **without raising on
   `None`**. The existing eager 90′ fail-fast stays (every category needs the final score). HT is
   validated **lazily**: only when a `HALF_TIME_RESULT` bet is graded and HT is absent does that bet
   **void** (per the rule above). Add a per-side consistency guard — `home_goals_ht > home_goals_90`
   or `away_goals_ht > away_goals_90` is corrupt data → raise `ValueError` (mirroring the existing
   missing-90′-score guard), so a half-time count can never exceed full time and silently mis-grade.
3. **Persist on the `games` table.** Add nullable `home_goals_ht` / `away_goals_ht` columns
   (additive Alembic migration — honours the append-only-migrations guardrail); `settle_fixture`
   writes them alongside `home_goals_90`/`away_goals_90`. This lets the manual-override re-grade
   (below) and any from-stored-state re-settle reproduce the grade without re-hitting the provider.
4. No new endpoint or extra request — `score.halftime` rides along on the result already fetched.

> The "derive per-half from the `goals` tuple" alternative is **settlement-only**: the live-feed
> `MatchResult` from `get_live_results` carries `goals=()`, so HT must come from `score.halftime`, not
> from goal minutes, on any live path.

## Manual override path (`set-result`)

`cli.py`'s `set-result` (the documented admin manual-score override) hand-builds a `MatchResult` with
only the 90′ score and feeds it through `settle_fixture` → `build_context`. Because `build_context`
runs **before** the per-bet loop, an *unconditional* HT fail-fast would crash **every** override
(even legacy games that have no `HALF_TIME_RESULT` bets). This is why the fail-fast above is **lazy**.
Additionally:

- Extend `set-result` with optional `--ht-home` / `--ht-away` flags (Typer options are correct here;
  the keyboard-wizard-first rule applies only to Telegram commands, not the admin CLI). When given,
  populate `MatchResult.home_goals_ht`/`away_goals_ht` and persist them.
- When HT is not supplied on an override of a new-era game that has `HALF_TIME_RESULT` bets, those
  bets **void** (and the admin is told), rather than crashing the re-grade.

## Rollout — append-only, existing competitions untouched

The bot is **live mid-tournament** (WC 2026, today 2026-06-20) with bets already placed and graded
under the old categories. Rule: **already-running competitions keep the old set; only competitions
that start after this deploys use the new set.** Mechanism:

- **`BetCategory` is append-only.** Add `HALF_TIME_RESULT`; **do not remove**
  `WINNER`/`FIRST_TEAM`/`BTTS`/`OVER_UNDER`. Their payload models, grading branches, points, and
  `callbacks` codes stay, so every previously-stored bet keeps grading and rendering correctly
  (settlement loops per stored bet; this suffices). Appending an enum member stored as TEXT needs
  **no migration** — the only migration in this change is the additive half-time-score columns.
- **Separate *offerable* from *gradeable*.** Introduce `OFFERABLE_CATEGORIES` selected **per game**
  by a config cutoff: `offerable_for(game) = NEW [EXACT_SCORE, HALF_TIME_RESULT]` if
  `game.kickoff_utc >= config.new_categories_from_utc`, else `OLD [EXACT_SCORE, FIRST_TEAM, BTTS,
  WINNER, OVER_UNDER]`. **No `Game` creation timestamp is needed** (the model has none — `created_at`
  does not exist on `Game`), and **no migration** for the gate. Set `new_categories_from_utc` to a
  datetime *after the current competition's last fixture* (e.g. after the WC 2026 final), so every
  in-flight fixture stays on the old five and the new set activates only for a future competition.
- **Every full-category-set reader must become per-game-regime,** not a module constant. In
  particular `domain/text_pt.py::TOTAL_CATEGORIES = len(BetCategory)` is now wrong twice over (the
  append-only enum makes it grow, *and* it must differ per game): the "X/N palpitaram" denominator,
  the reminder/announce/reveal paths, and their tests must compute `len(offerable_for(game))` (5 for
  legacy games, 2 for new-set games).
- **Invariant for the edit/re-bet flow.** `OFFERABLE_CATEGORIES` gates **only** the new-bet category
  picker (`keyboards.category_keyboard`). All other category-aware surfaces — `parse_payload`/
  `serialize_payload`, `describe_bet`/`describe_bet_value` rendering, the wizard's existing-bets
  render + in-place upsert/edit, and grading — operate on the **full** `BetCategory` set, so a player
  can still see and edit an old-category bet on a legacy game after deploy.

## Impacted surfaces (for the implementation plan)

**Domain & grading**
- `domain/bets.py` — add `HalfTimeResultPayload` + `HalfTimeSel{HOME,DRAW,AWAY}`; extend the
  `Payload` union, `parse_payload`, `serialize_payload`. Keep all existing payload models.
- `domain/scoring.py` — add `POINTS[HALF_TIME_RESULT] = 2`; add the HT grading branch (reading the
  new optional HT fields, with the void-on-missing-HT and per-side consistency rules). Hold ~100%
  line+branch coverage on the new branch.
- `domain/settlement.py` — thread HT into `GradingContext` (optional, lazy fail-fast, consistency
  guard); a finished fixture *with* a `HALF_TIME_RESULT` bet but no HT score voids that bet.
- `providers/base.py` + API-Football mapper — add HT fields to `MatchResult`; mapper parses
  `score.halftime`.

**Persistence**
- `db/models.py` + Alembic — nullable `games.home_goals_ht` / `home_goals_ht` columns (additive);
  `settlement_service.py` writes them on settle.

**Bot wizard**
- `bot/keyboards.py` — `category_keyboard` must iterate `offerable_for(game)` (today it iterates
  `CATEGORY_ORDER` from `domain/text_pt.py`), not the full enum. Add the `HALF_TIME_RESULT` selector
  keyboard (real team names for HOME/AWAY + "Empate"; **`DRAW` shown** — no knockout hiding).
- `bot/bets_handlers.py` — refactor `_step_payload`'s category dispatch from the open-ended
  `if/elif/.../else: # FIRST_TEAM` fallthrough into an exhaustive `match category:` with one `case`
  per `BetCategory` + `assert_never`, mirroring `parse_payload`. (Today the `else` mis-routes any
  unhandled category to the first-team keyboard.)
- `bot/callbacks.py` — add a `HalfTimeResultInput` dataclass, **both** directions of a
  `_HALF_TIME_TO_CODE` / `_CODE_TO_HALF_TIME` selection dict (a missing direction KeyErrors on a real
  user tap), and a `_CATEGORY_TO_CODE` entry for `HALF_TIME_RESULT`; keep `callback_data` ≤ 64 bytes.

**Rendering / text (`domain/text_pt.py`)**
- Add `CATEGORY_LABELS[HALF_TIME_RESULT]` and `CATEGORY_ORDER` entry (consumed by `poll_job.py`,
  `reconcile_job.py`, `category_button_label`, `points_table_text`, `closed_bets_text`).
- Add a `describe_bet_value` branch for `HALF_TIME_RESULT` (else results/correction posts KeyError).
- Make the bettor-count denominator per-game-regime (`TOTAL_CATEGORIES` → `len(offerable_for(game))`).
- `/ajuda` — rewrite the categories section (maintenance rule: `/ajuda` **and** `COMPLETION.md` in
  the same change). The results message must drop the `FIRST_TEAM`-specific line for new-era games.

**AI `/palpite`** (entirely omitted from the first draft — category-coupled, will break)
- `ai/schemas.py` (`GamePalpite` fields + `payloads()`), `ai/prompt.py` (`_SYSTEM_INSTRUCTION`
  grading rules + JSON template), `ai/gemini.py` (response schema if it pins fields),
  `palpite_service.py`, `bot/palpite_handlers.py`. Make `/palpite` **regime-aware**: predict the
  game's *offerable* set — for legacy games still the old five (no behaviour change during the live
  WC), for new-era games `EXACT_SCORE` + `HALF_TIME_RESULT` (add a `half_time_result` field; the
  prompt gains the HT grading rule and drops the removed-category instructions for new games).

**CLI**
- `cli.py` — `set-result` `--ht-home`/`--ht-away` (above); check `bets`/`games`/`board` dumps render
  the new category via the shared `CATEGORY_LABELS`/`describe_bet_value` (so they inherit the fix).

**Docs & tests**
- `README.md` — bet-categories line + §1 summary + first-scorer mention; also fix the already-stale
  "first team to score (3)" to its re-priced value while there.
- `COMPLETION.md` — §8.1 (table, grading, points, decision record) **and** §8.2 ("keyboard of the 5
  categories", BTTS detail, payload collectors) and §9.3 ("how many of the 5 …") so the count/old-set
  language is corrected (maintenance rule).
- `PROGRESS.md` — note the change.
- Tests — table-driven grading for `HALF_TIME_RESULT` (HOME/DRAW/AWAY, HT-draw, **missing-HT void**,
  the per-side consistency `ValueError` so `domain/settlement.py` keeps ~100% coverage); payload
  round-trip; wizard/keyboard/callbacks decode tests; update the `/5`→regime denominator literals in
  `tests/test_reminder_job.py` and `tests/test_text_pt.py`; AI-schema tests; a settlement test
  proving an old-category bet still grades (append-only invariant).

## Tentative pt-BR labels

- `EXACT_SCORE` — "Placar exato"
- `HALF_TIME_RESULT` — "Quem está na frente no 1º tempo" (botões: nomes reais dos times + "Empate")

## Open questions (resolve in the plan, not blocking the design)

1. **Cutoff value.** Confirm `config.new_categories_from_utc` is set after the current competition's
   last fixture (so the live WC stays entirely on the old set). A per-`Game` regime column is an
   alternative if finer-grained control is ever needed, but it would add a (non-additive) backfill.
2. **Points calibration.** Confirm 5/2 against a quick WC base-rate check and write the numbers into
   §8.1; no change is expected (5/2 already passes the §8.1 methodology per review).
