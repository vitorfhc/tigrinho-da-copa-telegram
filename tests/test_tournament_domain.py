"""Pure bolãozinho math + parsing (Feature 7 / §22). 100%-covered, deterministic, no I/O."""

from __future__ import annotations

import pytest

from tigrinho.domain.tournament import (
    PrizeSplit,
    compute_outcome,
    determine_winners,
    parse_create_args,
    parse_price_to_cents,
    pot_cents,
    prize_cents,
    split_prize,
)


def test_pot_and_prize_basic() -> None:
    assert pot_cents(10, 1000) == 10000
    assert prize_cents(10, 1000) == 9000  # winner does not win own stake


def test_prize_lone_entrant_is_zero() -> None:
    assert prize_cents(1, 1000) == 0
    assert prize_cents(0, 1000) == 0


@pytest.mark.parametrize(
    ("prize", "n", "per", "rem"),
    [
        (9000, 1, 9000, 0),
        (9000, 2, 4500, 0),
        (9000, 3, 3000, 0),
        (1000, 3, 333, 1),
        (9000, 7, 1285, 5),
        (0, 3, 0, 0),
    ],
)
def test_split_prize(prize: int, n: int, per: int, rem: int) -> None:
    assert split_prize(prize, n) == PrizeSplit(per, rem)


def test_split_prize_rejects_zero_winners() -> None:
    with pytest.raises(ValueError):
        split_prize(9000, 0)


def test_determine_winners_single() -> None:
    w = determine_winners({1: 14, 2: 12, 3: 9})
    assert w is not None
    assert w.score == 14
    assert w.telegram_ids == (1,)


def test_determine_winners_tie_sorted() -> None:
    w = determine_winners({3: 12, 1: 12, 2: 5})
    assert w is not None
    assert w.score == 12
    assert w.telegram_ids == (1, 3)


def test_determine_winners_all_zero_all_win() -> None:
    w = determine_winners({1: 0, 2: 0})
    assert w is not None
    assert w.telegram_ids == (1, 2)
    assert w.score == 0


def test_determine_winners_empty_is_none() -> None:
    assert determine_winners({}) is None


def test_compute_outcome_single_winner() -> None:
    o = compute_outcome({1: 14, 2: 12}, 1000)
    assert o.has_result is True
    assert o.pot_cents == 2000
    assert o.prize_cents == 1000
    assert o.winner_ids == (1,)
    assert o.winning_score == 14
    assert o.per_winner_cents == 1000
    assert o.remainder_cents == 0


def test_compute_outcome_tie_splits_prize() -> None:
    o = compute_outcome({1: 12, 2: 12, 3: 5}, 1000)
    assert o.pot_cents == 3000
    assert o.prize_cents == 2000
    assert o.winner_ids == (1, 2)
    assert o.per_winner_cents == 1000
    assert o.remainder_cents == 0


def test_compute_outcome_lone_entrant_prize_zero() -> None:
    o = compute_outcome({1: 7}, 1000)
    assert o.has_result is True
    assert o.pot_cents == 1000
    assert o.prize_cents == 0
    assert o.winner_ids == (1,)
    assert o.per_winner_cents == 0


def test_compute_outcome_no_entrants() -> None:
    o = compute_outcome({}, 1000)
    assert o.has_result is False
    assert o.pot_cents == 0
    assert o.winner_ids == ()


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        ("10", 1000),
        ("10,50", 1050),
        ("10.50", 1050),
        (" 7 ", 700),
        ("0,99", 99),
        ("100", 10000),
        ("3,5", 350),
    ],
)
def test_parse_price_ok(raw: str, cents: int) -> None:
    assert parse_price_to_cents(raw) == cents


@pytest.mark.parametrize("raw", ["", "abc", "0", "0,00", "-5", "1,234", "1.2.3", "10,5,0", "R$10"])
def test_parse_price_bad(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_price_to_cents(raw)


def test_parse_create_args_ok() -> None:
    assert parse_create_args("Oitavas de final | 10,50") == ("Oitavas de final", 1050)


@pytest.mark.parametrize("arg", ["semprice", "a | b | c", "| 10", "Nome |", "  | 10", "Nome | abc"])
def test_parse_create_args_bad(arg: str) -> None:
    with pytest.raises(ValueError):
        parse_create_args(arg)
