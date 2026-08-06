"""Typed model for SPK's ``IlkHalkaArzVerileriBilgi`` schema.

Field list, types, and nullability below were read directly from the
official OpenAPI document (https://ws.spk.gov.tr/swagger/v2/swagger.json,
``components.schemas.IlkHalkaArzVerileriBilgi``), which declares
``additionalProperties: false``. We enforce that same strictness here:
an unexpected field or a value of the wrong type is a schema error, not
something to coerce or drop silently.

``ay`` (month) is the only property in that schema without
``"nullable": true``, so it is treated as required/non-null here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .exceptions import SpkSchemaError

# (raw JSON key, model field name, expected Python type, nullable)
FIELD_SPECS: tuple[tuple[str, str, type, bool], ...] = (
    ("ay", "ay", int, False),
    ("donem", "donem", str, True),
    ("borsaKodu", "borsa_kodu", str, True),
    ("sirketUnvani", "sirket_unvani", str, True),
    ("halkaArzSekli", "halka_arz_sekli", str, True),
    ("halkaArzOrani", "halka_arz_orani", float, True),
    ("halkaArzFiyatiTl", "halka_arz_fiyati_tl", float, True),
    ("ortakSatisBinTl", "ortak_satis_bin_tl", int, True),
    ("nakitSermayeArtisiBinTl", "nakit_sermaye_artisi_bin_tl", int, True),
    ("ekSatisTutariBinTl", "ek_satis_tutari_bin_tl", int, True),
    ("satisaHazirBekletilenPayTutariBinTl", "satisa_hazir_bekletilen_pay_tutari_bin_tl", int, True),
    ("satisaSunulanToplamTutarBinABDDolari", "satisa_sunulan_toplam_tutar_bin_abd_dolari", float, True),
    ("satisaSunulanToplamTutarBinTl", "satisa_sunulan_toplam_tutar_bin_tl", int, True),
    ("mevcutSermayeBinTl", "mevcut_sermaye_bin_tl", float, True),
    ("yeniSermayeBinTl", "yeni_sermaye_bin_tl", float, True),
    ("satisaSunulanToplamTutarPiyasaDegeriBinTl", "satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl", float, True),
    ("ilkIslemGorduguPazar", "ilk_islem_gordugu_pazar", str, True),
    ("halkaArzaAracilikEdenKurum", "halka_arza_aracilik_eden_kurum", str, True),
    ("borsadaIslemGormeTarihi", "borsada_islem_gorme_tarihi", datetime, True),
)

_KNOWN_RAW_KEYS = frozenset(spec[0] for spec in FIELD_SPECS)


@dataclass(frozen=True, slots=True)
class SpkIpoRecord:
    """One normalized row of ``IlkHalkaArzVerileriBilgi``."""

    ay: int
    donem: str | None
    borsa_kodu: str | None
    sirket_unvani: str | None
    halka_arz_sekli: str | None
    halka_arz_orani: float | None
    halka_arz_fiyati_tl: float | None
    ortak_satis_bin_tl: int | None
    nakit_sermaye_artisi_bin_tl: int | None
    ek_satis_tutari_bin_tl: int | None
    satisa_hazir_bekletilen_pay_tutari_bin_tl: int | None
    satisa_sunulan_toplam_tutar_bin_abd_dolari: float | None
    satisa_sunulan_toplam_tutar_bin_tl: int | None
    mevcut_sermaye_bin_tl: float | None
    yeni_sermaye_bin_tl: float | None
    satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl: float | None
    ilk_islem_gordugu_pazar: str | None
    halka_arza_aracilik_eden_kurum: str | None
    borsada_islem_gorme_tarihi: datetime | None
    raw: dict = field(repr=False)


def _coerce_field(raw: dict, raw_key: str, expected_type: type, *, nullable: bool, index: int):
    present = raw_key in raw
    value = raw.get(raw_key)

    if not present or value is None:
        if nullable:
            return None
        reason = "missing" if not present else "null"
        raise SpkSchemaError(
            f"record at index {index}: '{raw_key}' is required (non-nullable) but was {reason}"
        )

    if expected_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SpkSchemaError(
                f"record at index {index}: expected int for '{raw_key}', got "
                f"{type(value).__name__}: {value!r}"
            )
        return value

    if expected_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SpkSchemaError(
                f"record at index {index}: expected number for '{raw_key}', got "
                f"{type(value).__name__}: {value!r}"
            )
        return float(value)

    if expected_type is str:
        if not isinstance(value, str):
            raise SpkSchemaError(
                f"record at index {index}: expected string for '{raw_key}', got "
                f"{type(value).__name__}: {value!r}"
            )
        return value

    if expected_type is datetime:
        if not isinstance(value, str):
            raise SpkSchemaError(
                f"record at index {index}: expected a date-time string for '{raw_key}', "
                f"got {type(value).__name__}: {value!r}"
            )
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise SpkSchemaError(
                f"record at index {index}: could not parse '{raw_key}' as an ISO date-time: {value!r}"
            ) from exc

    raise AssertionError(f"unhandled expected type in FIELD_SPECS: {expected_type!r}")  # pragma: no cover


def parse_ipo_record(raw: dict, *, index: int) -> SpkIpoRecord:
    """Normalize one raw ``IlkHalkaArzVerileriBilgi`` object into an :class:`SpkIpoRecord`.

    Raises :class:`SpkSchemaError` on any field with the wrong type, a
    missing non-nullable field, or a field the documented schema doesn't
    declare — never silently drops or zero-fills a bad value.
    """
    if not isinstance(raw, dict):
        raise SpkSchemaError(f"record at index {index} is not a JSON object: {type(raw).__name__}")

    unexpected_keys = set(raw.keys()) - _KNOWN_RAW_KEYS
    if unexpected_keys:
        raise SpkSchemaError(
            f"record at index {index} has field(s) not in the documented "
            f"IlkHalkaArzVerileriBilgi schema: {sorted(unexpected_keys)}"
        )

    values = {
        model_field: _coerce_field(raw, raw_key, expected_type, nullable=nullable, index=index)
        for raw_key, model_field, expected_type, nullable in FIELD_SPECS
    }
    return SpkIpoRecord(raw=raw, **values)
