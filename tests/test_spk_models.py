from datetime import datetime

import pytest

from halka_arz_advisor.spk.exceptions import SpkSchemaError
from halka_arz_advisor.spk.models import parse_ipo_record


def test_parses_full_record(fixture_json):
    sample = fixture_json("spk_ipo_2024_sample.json")
    record = parse_ipo_record(sample[0], index=0)

    assert record.ay == 2
    assert record.donem == "2024 / 2"
    assert record.borsa_kodu == "PATEK"
    assert record.sirket_unvani == "PASİFİK TEKNOLOJİ A.Ş."
    assert record.halka_arz_orani == pytest.approx(20.15)
    assert record.halka_arz_fiyati_tl == pytest.approx(35.0)
    assert record.ortak_satis_bin_tl == 13000
    assert record.satisa_sunulan_toplam_tutar_bin_abd_dolari == pytest.approx(30757.513621)
    assert record.borsada_islem_gorme_tarihi == datetime(2024, 2, 13, 0, 0, 0)
    assert record.raw == sample[0]


def test_nullable_fields_accept_null():
    raw = {
        "ay": 5,
        "donem": None,
        "borsaKodu": None,
        "sirketUnvani": None,
        "halkaArzSekli": None,
        "halkaArzOrani": None,
        "halkaArzFiyatiTl": None,
        "ortakSatisBinTl": None,
        "nakitSermayeArtisiBinTl": None,
        "ekSatisTutariBinTl": None,
        "satisaHazirBekletilenPayTutariBinTl": None,
        "satisaSunulanToplamTutarBinABDDolari": None,
        "satisaSunulanToplamTutarBinTl": None,
        "mevcutSermayeBinTl": None,
        "yeniSermayeBinTl": None,
        "satisaSunulanToplamTutarPiyasaDegeriBinTl": None,
        "ilkIslemGorduguPazar": None,
        "halkaArzaAracilikEdenKurum": None,
        "borsadaIslemGormeTarihi": None,
    }
    record = parse_ipo_record(raw, index=0)
    assert record.ay == 5
    assert record.donem is None
    assert record.borsada_islem_gorme_tarihi is None


def test_missing_required_ay_raises():
    raw = {"donem": "2024 / 2"}
    with pytest.raises(SpkSchemaError, match="'ay' is required"):
        parse_ipo_record(raw, index=3)


def test_null_required_ay_raises():
    raw = {"ay": None}
    with pytest.raises(SpkSchemaError, match="'ay' is required"):
        parse_ipo_record(raw, index=0)


def test_wrong_type_string_field_raises_not_coerced():
    raw = {"ay": 1, "sirketUnvani": 12345}
    with pytest.raises(SpkSchemaError, match="expected string for 'sirketUnvani'"):
        parse_ipo_record(raw, index=0)


def test_bool_rejected_for_int_field():
    raw = {"ay": True}
    with pytest.raises(SpkSchemaError, match="expected int for 'ay'"):
        parse_ipo_record(raw, index=0)


def test_int_value_accepted_for_float_field():
    raw = {"ay": 1, "halkaArzFiyatiTl": 35}
    record = parse_ipo_record(raw, index=0)
    assert record.halka_arz_fiyati_tl == 35.0
    assert isinstance(record.halka_arz_fiyati_tl, float)


def test_malformed_number_string_not_coerced_to_zero():
    raw = {"ay": 1, "halkaArzOrani": "not-a-number"}
    with pytest.raises(SpkSchemaError, match="expected number for 'halkaArzOrani'"):
        parse_ipo_record(raw, index=0)


def test_invalid_date_string_raises():
    raw = {"ay": 1, "borsadaIslemGormeTarihi": "not-a-date"}
    with pytest.raises(SpkSchemaError, match="could not parse 'borsadaIslemGormeTarihi'"):
        parse_ipo_record(raw, index=0)


def test_unexpected_extra_field_raises():
    raw = {"ay": 1, "sirketUnvani": "X A.Ş.", "someNewField": "surprise"}
    with pytest.raises(SpkSchemaError, match="someNewField"):
        parse_ipo_record(raw, index=0)


def test_non_object_record_raises():
    with pytest.raises(SpkSchemaError, match="is not a JSON object"):
        parse_ipo_record(["not", "a", "dict"], index=0)
