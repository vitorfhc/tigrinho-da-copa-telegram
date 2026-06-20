# Design: Orthogonal bet categories (de-coupling the bet set)

**Date:** 2026-06-20
**Status:** Approved (design) — pending spec review, then implementation plan
**Touches:** `COMPLETION.md §8.1`, `domain/bets.py`, `domain/scoring.py`, `domain/settlement.py`,
`providers/base.py` (+ the API-Football mapper), `bot/bets_handlers.py`, `bot/callbacks.py`,
`bot/keyboards`, `domain/text_pt.py` (`/ajuda`), one Alembic migration.

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
resolutions, not five separate questions. This is the user's complaint ("one decides the other").

The math is unavoidable: **any market that is a function of the final score correlates with every
other one.** Real independence requires markets on *different axes* of the match.

## Design principle

> `EXACT_SCORE` already owns every **aggregate-score** question (winner, margin, total, BTTS,
> over/under are all coarsenings of it). That is *why* those four were redundant. So every satellite
> must instead be a **timing** question (*when* goals happen, not *how many*) — because timing is the
> one thing a scoreline guess leaves completely open.

Additional constraints agreed during brainstorming:

- **No easy expected value.** Every market must have *no obvious favourite option* — either a
  near-coinflip binary or a 3-way where no outcome clears ~45%. (This is why skewed binaries like
  "red card yes/no" ~25/75 and "penalty yes/no" ~30/70 were rejected: players just farm the likely
  side for free points.)
- **No cards / penalties.** Per the user, the discipline/set-piece axes are out — the only ones easy
  enough to grade are also the ones with a farmable favourite.
- **Quality over quantity.** A clean 3-market set beats a padded 4th that re-introduces overlap. The
  pure-luck "which team kicks off" idea was investigated and **rejected on grounding**: API-Football
  v3 has no coin-toss / kick-off-team data (events are only Goal/Card/Substitution/VAR; the fixture
  object exposes no such field). The only gradeable pure-luck fillers were either coupled to the
  score (total-goals odd/even) or void-prone and quirky (first-goal-minute odd/even), so we keep the
  set at three.

## The new set

| # | `BetCategory` | Selection | Axis | Points* |
|---|---|---|---|---|
| 1 | `EXACT_SCORE` *(unchanged)* | `{home:int, away:int}` | Final scoreline | **5** |
| 2 | `HALF_TIME_RESULT` *(new — replaces `WINNER`)* | `{sel: HOME\|DRAW\|AWAY}` at the break | In-game time-slice | **2** |
| 3 | `WHICH_HALF` *(new — replaces `OVER_UNDER`)* | `{sel: FIRST\|SECOND\|EQUAL}` | Tempo | **2** |

*\*Tentative. Points are centralised in `domain/scoring.py` and will be calibrated against real WC
base rates before locking, consistent with the §8.1 fairness methodology (monotonic with rarity, no
category dominating expected points).*

**Removed and why:**
- `WINNER` → coarsening of `EXACT_SCORE` (1×0 forces HOME). Replaced by `HALF_TIME_RESULT`, which
  keeps the familiar "who's on top?" feel but on a *different time-slice* the final score can't pin.
- `OVER_UNDER` → coarsening of the total, which is a coarsening of `EXACT_SCORE`.
- `BTTS` → a function of the score (1×0 ⇒ ONLY_HOME). Pure aggregate.
- `FIRST_TEAM` → partially coupled (a clean-sheet win forces it) and the most score-correlated of the
  satellites; also voids on 0-0. Its "when" flavour is better captured by the two new timing markets.

## Grading rules (PURE, `domain/scoring.py`)

All grading reads the **90′ regulation** match (consistent with every existing score-based market;
extra time / penalties are ignored).

- **`HALF_TIME_RESULT`** — from the half-time score `(ht_home, ht_away)`:
  - `HOME` if `ht_home > ht_away`; `AWAY` if `ht_away > ht_home`; `DRAW` if equal.
  - **No knockout special-case.** A half-time score can always be level, so `DRAW` is always a valid
    option (unlike `WINNER`, which had to hide `DRAW` in knockouts). Simpler UI and grading.
  - **Never voids** (every match has a half-time state).
- **`WHICH_HALF`** — compare goals scored in each half of normal time:
  - `first_half_goals = ht_home + ht_away`
  - `second_half_goals = (home_goals_90 + away_goals_90) − first_half_goals`
  - `FIRST` if `first_half_goals > second_half_goals`; `SECOND` if `second_half_goals >
    first_half_goals`; `EQUAL` if they tie (this includes a 0-0, graded `EQUAL`, **not** a void).
  - All goals count (own goals included) — this is about goal *count per half*, not the genuine
    scorer. The half-time score authoritatively attributes stoppage-time goals (e.g. 45+2 → 1st half).
  - **Never voids.**

Both new markets are **void-free** — a strict improvement over `FIRST_TEAM`, which dies on 0-0.

## De-coupling proof

Re-run the `1×0 home` example against the new set:

| Market | Constrained by "1×0 home"? |
|---|---|
| `EXACT_SCORE` | the bet itself |
| `HALF_TIME_RESULT` | **open** — break could be 0-0 (DRAW) or 1-0 (HOME); AWAY is the only excluded value |
| `WHICH_HALF` | **open** — the single goal could land in either half |

Each satellite now asks a question the scoreline leaves open. The two satellites are *lightly*
correlated with each other (an early-heavy match nudges both), but neither is a function of the
other, and neither is a function of `EXACT_SCORE`.

## New data dependency: the half-time score

Today `MatchResult` (`providers/base.py`) and `GradingContext` (`domain/scoring.py`) carry only the
90′ score (`home_goals_90`/`away_goals_90`, from `score.fulltime`). Both new markets need the
**half-time score**, which API-Football v3 exposes as `score.halftime` on the fixture object
(verified 2026-06-20). Plumbing:

1. Add `home_goals_ht: int | None` / `away_goals_ht: int | None` to `MatchResult`; populate them from
   `score.halftime` in the API-Football mapper.
2. Add `home_goals_ht: int` / `away_goals_ht: int` to `GradingContext`; populate in `build_context`
   (`domain/settlement.py`), failing fast if a finished fixture is missing a half-time score.
3. No new endpoint or extra request — `score.halftime` rides along on the result already fetched.

`WHICH_HALF` can alternatively derive per-half counts from the existing `goals` tuple by minute; the
half-time-score approach is preferred because it is authoritative for stoppage-time attribution. The
implementation plan picks one and tests the boundary cases.

## Rollout — append-only, existing competitions untouched

The bot is **live mid-tournament** (WC 2026, today 2026-06-20) with bets already placed and graded
under the old categories. The agreed rule: **already-created competitions keep the old set; only
competitions created after this deploys use the new set.** Mechanism:

- **`BetCategory` is append-only.** Add `HALF_TIME_RESULT` and `WHICH_HALF`; **do not remove**
  `WINNER`/`FIRST_TEAM`/`BTTS`/`OVER_UNDER`. Their payload models, grading branches, and points stay
  so every previously-stored bet keeps grading correctly (settlement loops per stored bet, so this is
  sufficient). No data migration of existing `bets` rows.
- **Separate the *offerable* set from the *gradeable* set.** Introduce an explicit
  `OFFERABLE_CATEGORIES` list (the wizard's category step reads this), distinct from the full
  `BetCategory` enum used for grading. After deploy, `OFFERABLE_CATEGORIES = [EXACT_SCORE,
  HALF_TIME_RESULT, WHICH_HALF]`; the four removed categories become *gradeable but no longer
  offerable*.
- **Boundary (one open question — see below).** The cleanest implementable boundary is **by game
  creation time**: games synced before the deploy cutoff keep offering the old set; games created
  after offer the new set. Because all current WC 2026 fixtures predate the cutoff, the live
  tournament finishes entirely on the old five bets and the new three activate for the next batch of
  fixtures — zero disruption to in-flight bets, the one-bet-per-category unique constraint, or the
  scoreboard.

The Alembic migration is additive only (no column drops, no row rewrites), honouring the
append-only-migrations guardrail.

## Impacted surfaces (for the implementation plan)

- `domain/bets.py` — add `HalfTimeResultPayload` + `HalfTimeSel{HOME,DRAW,AWAY}`, `WhichHalfPayload`
  + `WhichHalfSel{FIRST,SECOND,EQUAL}`; extend `Payload` union, `parse_payload`, `serialize_payload`.
  Keep all existing payload models.
- `domain/scoring.py` — add `POINTS` entries; add grading branches reading the new HT fields; keep
  existing branches. Hold ~100% line+branch coverage on the new logic.
- `providers/base.py` + API-Football mapper — add half-time fields to `MatchResult`.
- `domain/settlement.py` — thread HT score into `GradingContext`.
- `bot/callbacks.py` — assign compact opcodes for the two new categories; add their payload step
  collectors (3-button keyboards), keeping `callback_data` ≤ 64 bytes.
- `bot/bets_handlers.py` + keyboards — category step reads `OFFERABLE_CATEGORIES`; add the two new
  selector keyboards (real team names for HOME/AWAY on `HALF_TIME_RESULT`; FIRST/SECOND/EQUAL labels
  for `WHICH_HALF`).
- `domain/text_pt.py` — update `/ajuda` (the maintenance rule requires `/ajuda` **and**
  `COMPLETION.md` change in the same change as any bet-category change).
- `COMPLETION.md §8.1` — rewrite the category table, grading rules, and points; record this decision.
- `PROGRESS.md` — note the change.
- Tests — table-driven grading tests for both new markets (incl. 0-0 → `EQUAL`, HT-draw, stoppage
  attribution), wizard/keyboard tests, payload round-trip tests, and a settlement test proving an
  old-category bet still grades.

## Tentative pt-BR labels (for `/ajuda` + keyboards)

- `EXACT_SCORE` — "Placar exato"
- `HALF_TIME_RESULT` — "Quem está na frente no 1º tempo" (botões: nomes reais dos times + "Empate")
- `WHICH_HALF` — "Qual tempo tem mais gols" (botões: "1º tempo" / "2º tempo" / "Empate")

## Open questions (resolve in the plan, not blocking the design)

1. **Rollout boundary mechanism.** Recommended: gate `OFFERABLE_CATEGORIES` by **game creation time
   vs a deploy cutoff** (so all live WC fixtures stay on the old set). Confirm this matches the user's
   "tournaments already created stay as-is" intent, vs. an alternative (e.g. a per-bolãozinho flag or
   a global config switch flipped at the next stage).
2. **Points calibration.** Confirm 5/2/2 after a quick WC base-rate check, or adjust so no category
   dominates expected points.
