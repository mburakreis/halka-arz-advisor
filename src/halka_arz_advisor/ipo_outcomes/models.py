"""Persistent record of one IPO's market outcome — deliberately a flat,
storage-shaped dataclass (unlike :mod:`halka_arz_advisor.ipo_outcomes.calculations`'s
``OutcomeValue``, which pairs a value with its own ``as_of_date``) so a
future backtest can load a whole ticker's history from disk without
reconstructing intermediate computation state.

This package (and this model) is deliberately never imported by
:mod:`halka_arz_advisor.decision` — building this record does not
change entry scoring, Gemini narration, Telegram output, or exit rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class IpoMarketOutcome:
    ticker: str
    company_name: str | None

    # The official offer price every return/drawdown field below is
    # anchored to (halka_arz_advisor.spk.models.SpkIpoRecord.halka_arz_fiyati_tl)
    # — persisted so a stored outcome is self-explanatory without
    # re-fetching the SPK record it was built from.
    offer_price: float | None

    # Trading-start resolution (see halka_arz_advisor.ipo_outcomes.trading_start).
    resolved_trading_start_date: date | None
    spk_trading_start_date: date | None
    kap_trading_start_announcement_dates: tuple[date, ...]
    trading_start_conflict: bool

    # How much of the resolved window actually had cached/fetched price data.
    price_observation_count: int
    last_price_observation_date: date | None

    first_day_return: float | None
    return_5d: float | None
    return_20d: float | None
    return_3m: float | None
    max_drawdown_5d: float | None
    max_drawdown_20d: float | None
    max_drawdown_3m: float | None

    bist_relative_first_day: float | None
    bist_relative_5d: float | None
    bist_relative_20d: float | None
    bist_relative_3m: float | None

    generated_at: datetime


def outcome_to_dict(outcome: IpoMarketOutcome) -> dict:
    return {
        "ticker": outcome.ticker,
        "company_name": outcome.company_name,
        "offer_price": outcome.offer_price,
        "resolved_trading_start_date": outcome.resolved_trading_start_date.isoformat()
        if outcome.resolved_trading_start_date
        else None,
        "spk_trading_start_date": outcome.spk_trading_start_date.isoformat() if outcome.spk_trading_start_date else None,
        "kap_trading_start_announcement_dates": [d.isoformat() for d in outcome.kap_trading_start_announcement_dates],
        "trading_start_conflict": outcome.trading_start_conflict,
        "price_observation_count": outcome.price_observation_count,
        "last_price_observation_date": outcome.last_price_observation_date.isoformat()
        if outcome.last_price_observation_date
        else None,
        "first_day_return": outcome.first_day_return,
        "return_5d": outcome.return_5d,
        "return_20d": outcome.return_20d,
        "return_3m": outcome.return_3m,
        "max_drawdown_5d": outcome.max_drawdown_5d,
        "max_drawdown_20d": outcome.max_drawdown_20d,
        "max_drawdown_3m": outcome.max_drawdown_3m,
        "bist_relative_first_day": outcome.bist_relative_first_day,
        "bist_relative_5d": outcome.bist_relative_5d,
        "bist_relative_20d": outcome.bist_relative_20d,
        "bist_relative_3m": outcome.bist_relative_3m,
        "generated_at": outcome.generated_at.isoformat(),
    }


def outcome_from_dict(data: dict) -> IpoMarketOutcome:
    def _date(key: str) -> date | None:
        raw = data.get(key)
        return date.fromisoformat(raw) if raw else None

    return IpoMarketOutcome(
        ticker=data["ticker"],
        company_name=data.get("company_name"),
        offer_price=data.get("offer_price"),
        resolved_trading_start_date=_date("resolved_trading_start_date"),
        spk_trading_start_date=_date("spk_trading_start_date"),
        kap_trading_start_announcement_dates=tuple(date.fromisoformat(d) for d in data.get("kap_trading_start_announcement_dates", [])),
        trading_start_conflict=bool(data["trading_start_conflict"]),
        price_observation_count=int(data["price_observation_count"]),
        last_price_observation_date=_date("last_price_observation_date"),
        first_day_return=data.get("first_day_return"),
        return_5d=data.get("return_5d"),
        return_20d=data.get("return_20d"),
        return_3m=data.get("return_3m"),
        max_drawdown_5d=data.get("max_drawdown_5d"),
        max_drawdown_20d=data.get("max_drawdown_20d"),
        max_drawdown_3m=data.get("max_drawdown_3m"),
        bist_relative_first_day=data.get("bist_relative_first_day"),
        bist_relative_5d=data.get("bist_relative_5d"),
        bist_relative_20d=data.get("bist_relative_20d"),
        bist_relative_3m=data.get("bist_relative_3m"),
        generated_at=datetime.fromisoformat(data["generated_at"]),
    )
