"""Pinned, verified EVDS series codes for the five market-context
inputs this project uses — never guessed.

Every code below was confirmed live against the real EVDS REST service
(``https://evds3.tcmb.gov.tr/igmevdsms-dis/``, an authenticated account)
on 2026-08-07, by browsing TCMB's own "Tüm Seriler" (All Series) catalog
and cross-checking the resulting series code against a real, current
data pull — not copied from a guess or an unverified third party:

- ``TP.MK.F.BILESIK`` — found via EVDS's own site search for "BIST 100",
  landing on category 3002 (Piyasa Verileri) > datagroup ``bie_mkbrgn``
  ("Borsa İstanbul Endeksi ve Günlük İşlem Hacmi"). Confirmed live: a
  same-datagroup table pull showed column ``TP_MK_F_BILESIK`` with a
  real 06-08-2026 close of 13,798.82.
- ``TP.MK.ISL.HC`` — the *same* datagroup (``bie_mkbrgn``) as the index
  itself, found by filtering that group's own series list for "İşlem"
  (confirmed as "Toplam İşlem Hacmi" / total trading volume). Column
  ``TP_MK_ISL_HC`` in the same live pull showed 264,591,164.39 (TL) for
  06-08-2026 — i.e. this project's "same dataset" requirement is met
  literally, not just by convention.
- ``TP.BISTTLREF.ORAN`` — found in datagroup ``bie_bisttlref`` ("Türk
  Lirası Gecelik Referans Faiz Oranı (BIST-TLREF)"); ``.ORAN`` (the rate
  itself, source Borsa İstanbul) rather than ``.KAPANIS`` (the
  cumulative TLREF *index* value) — confirmed live at 39.99% on
  06-08-2026.
- ``TP.PY.P06.1HI`` — TCMB's own realized weighted-average rate on its
  1-week deposit-purchase auction (datagroup ``bie_pyintbnk``), the
  instrument TCMB's Monetary Policy Committee has operated its
  announced policy rate through since 2023. Found after ruling out two
  plausible-looking but wrong candidates: ``TP.BISPOLFAIZ.TUR``
  (a BIS-sourced, monthly, multi-country comparison series — wrong
  source institution and frequency) and ``TP.PY.P01.1H`` (a standing
  quotation that live-tested as a stale/inactive 0.0 for every recent
  date). ``TP.PY.P06.1HI`` live-tested at a real, stable ~40.1% across
  the last two weeks of July/early August 2026 — consistent with an
  administered rate that only moves on MPC decision dates.
- ``TP.TUKFIY2025.GENEL`` — TÜİK's headline CPI general index, current
  (2025=100) base year, found via EVDS's own "Başlıca Göstergeler" (Key
  Indicators) page, which charts this exact series for both the
  month-over-month and year-over-year CPI panels. Datagroup
  ``bie_tukfiy2025`` ("Tüketici Fiyat Endeksi (2025=100)") — chosen
  over the superficially similar ``bie_oktug2025`` ("Özel Kapsamlı TÜFE
  Göstergeleri" / core-CPI subcomponents), which is a different,
  narrower dataset. Live-tested index levels for 2025-07..2026-07 (e.g.
  100.42 -> 132.31) give a real year-over-year figure of ~31.8%, in the
  plausible range for Turkey in 2026.

Deliberately data, not code: bumping :data:`EVDS_REGISTRY_VERSION` and
appending/replacing an entry is the only change needed if TCMB revises
a series code (this has already happened once, historically — CPI
rebased from 2003=100 to 2025=100).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvdsFrequency = Literal["daily", "monthly"]

# Bump whenever a series code, source, or unit below changes — folded
# into cache file paths (see evds.cache) so a registry change never
# silently mixes observations pinned under a stale code's assumptions
# with ones fetched under a corrected registry.
EVDS_REGISTRY_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class EvdsSeriesSpec:
    """One pinned EVDS series this project fetches and caches."""

    key: str  # this project's own stable name for the series, e.g. "bist100_index"
    series_code: str  # the exact EVDS "TP...." code
    description: str
    source_institution: str
    frequency: EvdsFrequency
    unit: str
    expected_update_cadence: str
    datagroup_code: str  # EVDS's own grouping — informational, for traceability only


EVDS_SERIES_REGISTRY: tuple[EvdsSeriesSpec, ...] = (
    EvdsSeriesSpec(
        key="bist100_index",
        series_code="TP.MK.F.BILESIK",
        description="BİST 100 Endeksi (XU100), Kapanış Fiyatlarına Göre (Ocak 1986=0,01)",
        source_institution="Borsa İstanbul",
        frequency="daily",
        unit="index_points",
        expected_update_cadence="one new observation per Borsa İstanbul trading day",
        datagroup_code="bie_mkbrgn",
    ),
    EvdsSeriesSpec(
        key="bist100_volume",
        series_code="TP.MK.ISL.HC",
        description="Borsa İstanbul Toplam İşlem Hacmi (TL) — same datagroup as bist100_index",
        source_institution="Borsa İstanbul",
        frequency="daily",
        unit="try",
        expected_update_cadence="one new observation per Borsa İstanbul trading day",
        datagroup_code="bie_mkbrgn",
    ),
    EvdsSeriesSpec(
        key="policy_rate",
        series_code="TP.PY.P06.1HI",
        description=(
            "1 Haftalık Depo Alım İhalesi - Gerçekleşen Basit Faiz Oranı Ağırlıklı Ortalaması (%) "
            "— TCMB's operative policy-rate instrument since 2023"
        ),
        source_institution="TCMB",
        frequency="daily",
        unit="percent",
        expected_update_cadence="one new observation per TCMB business day (value itself changes only on MPC decision dates)",
        datagroup_code="bie_pyintbnk",
    ),
    EvdsSeriesSpec(
        key="tlref_rate",
        series_code="TP.BISTTLREF.ORAN",
        description="BIST TLREF - Türk Lirası Gecelik Referans Faiz Oranı (%) (ISIN: TRIXIST00015)",
        source_institution="Borsa İstanbul",
        frequency="daily",
        unit="percent",
        expected_update_cadence="one new observation per Borsa İstanbul business day",
        datagroup_code="bie_bisttlref",
    ),
    EvdsSeriesSpec(
        key="cpi_index",
        series_code="TP.TUKFIY2025.GENEL",
        description="Tüketici Fiyat Endeksi (TÜFE) Genel Endeks (2025=100)",
        source_institution="TÜİK",
        frequency="monthly",
        unit="index_points",
        expected_update_cadence="one new observation per calendar month, published by TÜİK in the first days of the following month",
        datagroup_code="bie_tukfiy2025",
    ),
)

_BY_KEY: dict[str, EvdsSeriesSpec] = {spec.key: spec for spec in EVDS_SERIES_REGISTRY}

if len(_BY_KEY) != len(EVDS_SERIES_REGISTRY):
    raise AssertionError("duplicate key(s) in EVDS_SERIES_REGISTRY")


def get_series_spec(key: str) -> EvdsSeriesSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"no such EVDS series key in EVDS_SERIES_REGISTRY: {key!r}") from None


def daily_series_keys() -> tuple[str, ...]:
    """Keys fetchable together in one batched EVDS request — see
    :mod:`halka_arz_advisor.evds.client`'s module docstring for why
    ``cpi_index`` (monthly) is deliberately excluded and fetched on its
    own."""
    return tuple(spec.key for spec in EVDS_SERIES_REGISTRY if spec.frequency == "daily")


def monthly_series_keys() -> tuple[str, ...]:
    return tuple(spec.key for spec in EVDS_SERIES_REGISTRY if spec.frequency == "monthly")
