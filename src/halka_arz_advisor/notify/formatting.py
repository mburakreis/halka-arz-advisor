"""Plain, human-readable notification text — no templating engine needed."""

from __future__ import annotations

from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord


def format_ipo_notification(record: SpkIpoRecord) -> str:
    """"Yeni halka arz": company (+ ticker), price, ratio, listing date."""
    company = record.sirket_unvani or "(şirket adı belirtilmemiş)"
    if record.borsa_kodu:
        company = f"{company} ({record.borsa_kodu})"

    lines = ["Yeni halka arz:", company]
    if record.halka_arz_fiyati_tl is not None:
        lines.append(f"Fiyat: {record.halka_arz_fiyati_tl} TL")
    if record.halka_arz_orani is not None:
        lines.append(f"Halka arz oranı: %{record.halka_arz_orani}")
    if record.borsada_islem_gorme_tarihi is not None:
        lines.append(f"İşlem tarihi: {record.borsada_islem_gorme_tarihi.date().isoformat()}")
    return "\n".join(lines)


def format_application_notification(record: SpkIpoApplicationRecord) -> str:
    """"Yeni halka arz başvurusu": company, application date."""
    return (
        "Yeni halka arz başvurusu:\n"
        f"{record.company_name}\n"
        f"Başvuru tarihi: {record.application_date.isoformat()}"
    )
