# SPK IPO endpoint: evidence and schema (Phase 1A)

This documents how `src/halka_arz_advisor/spk/client.py` arrived at the
concrete URL it calls, and the schema it validates against. No host or
path was guessed — everything below was read from official sources or
confirmed with a single documented request.

## 1. The OpenAPI document

Fetched `https://ws.spk.gov.tr/swagger/v2/swagger.json` (HTTP 200,
`application/json`, ~166 KB, OpenAPI version `3.0.1`).

Relevant facts pulled from it:

- **No `servers` entry.** Per the [OpenAPI 3.0 spec](https://spec.openapis.org/oas/v3.0.1#openapi-object),
  when `servers` is absent (or empty) the default is a single server
  with `url: "/"`, resolved **relative to the location the document
  itself was retrieved from**. The document was retrieved from
  `https://ws.spk.gov.tr/swagger/v2/swagger.json`, so the implied base
  URL is `https://ws.spk.gov.tr/`.
- **Path** `/BorclanmaAraclari/api/IlkHalkaArzVerileri` exists under
  `paths`, tagged `BorclanmaAraclari`, summary "İlk Halka Arz Verileri".
- Its `get` operation takes one query parameter, `yil` (`integer`,
  `int32`, description "Yil (yyyy)") — matches the brief exactly.
- Its 200 response (`application/json`, `text/json`, `text/plain`) is
  `type: array` of `#/components/schemas/IlkHalkaArzVerileriBilgi`.

## 2. The Swagger UI page

Fetched `https://ws.spk.gov.tr/help/index.html` (already recorded during
Phase 0 probing; HTTP 200, static Swagger UI shell). Its inline script
config contains:

```json
{"urls":[{"url":"/swagger/v2/swagger.json","name":"SPK Web Servisleri API v2"}], ...}
```

The spec is referenced by a **relative** path from this same page — i.e.
the docs UI and the OpenAPI document (and therefore the API it
describes) are served from the same host, `ws.spk.gov.tr`. This
corroborates step 1 independently of the OpenAPI spec's own default-server
rule.

## 3. A documented live request

```
GET https://ws.spk.gov.tr/BorclanmaAraclari/api/IlkHalkaArzVerileri?yil=2024
Accept: application/json
```

Response: `HTTP/1.1 200 OK`, `Content-Type: application/json; charset=utf-8`,
a JSON array of 33 objects, each with exactly the fields declared in
`IlkHalkaArzVerileriBilgi` (verified with a second request for a year with
no IPOs, `yil=1990`, which returned `[]` rather than an error — confirming
the endpoint distinguishes "no data" from a malformed request).

**Conclusion: base URL is `https://ws.spk.gov.tr`, endpoint path is
`/BorclanmaAraclari/api/IlkHalkaArzVerileri`.** No other host was tried.

## `IlkHalkaArzVerileriBilgi` schema

From `components.schemas.IlkHalkaArzVerileriBilgi` in the OpenAPI document.
The schema declares `additionalProperties: false`; the client rejects any
record with a field not listed here (`SpkSchemaError`). Only `ay` lacks
`"nullable": true` in the spec, so it's the one field the client treats as
required/non-null.

| Raw JSON key | `SpkIpoRecord` field | JSON type | Nullable |
|---|---|---|---|
| `ay` | `ay` | integer | No |
| `donem` | `donem` | string | Yes |
| `borsaKodu` | `borsa_kodu` | string | Yes |
| `sirketUnvani` | `sirket_unvani` | string | Yes |
| `halkaArzSekli` | `halka_arz_sekli` | string | Yes |
| `halkaArzOrani` | `halka_arz_orani` | number | Yes |
| `halkaArzFiyatiTl` | `halka_arz_fiyati_tl` | number | Yes |
| `ortakSatisBinTl` | `ortak_satis_bin_tl` | integer (int64) | Yes |
| `nakitSermayeArtisiBinTl` | `nakit_sermaye_artisi_bin_tl` | integer (int64) | Yes |
| `ekSatisTutariBinTl` | `ek_satis_tutari_bin_tl` | integer (int64) | Yes |
| `satisaHazirBekletilenPayTutariBinTl` | `satisa_hazir_bekletilen_pay_tutari_bin_tl` | integer (int64) | Yes |
| `satisaSunulanToplamTutarBinABDDolari` | `satisa_sunulan_toplam_tutar_bin_abd_dolari` | number | Yes |
| `satisaSunulanToplamTutarBinTl` | `satisa_sunulan_toplam_tutar_bin_tl` | integer (int64) | Yes |
| `mevcutSermayeBinTl` | `mevcut_sermaye_bin_tl` | number | Yes |
| `yeniSermayeBinTl` | `yeni_sermaye_bin_tl` | number | Yes |
| `satisaSunulanToplamTutarPiyasaDegeriBinTl` | `satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl` | number | Yes |
| `ilkIslemGorduguPazar` | `ilk_islem_gordugu_pazar` | string | Yes |
| `halkaArzaAracilikEdenKurum` | `halka_arza_aracilik_eden_kurum` | string | Yes |
| `borsadaIslemGormeTarihi` | `borsada_islem_gorme_tarihi` | string (date-time) | Yes |

`SpkIpoRecord` also carries a `raw: dict` field with the untouched source
object for that row, on top of `SpkApiClient.fetch_ipo_raw()` preserving
the whole raw JSON array before any per-record normalization happens.

## Not done in this phase

- `spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu` (the IPO
  *application* list page) was inspected in Phase 0 and confirmed
  server-rendered, but no client was built for it here — Phase 1A is
  scoped to the OpenAPI-documented IPO data endpoint only.
- No other paths in the OpenAPI document were explored or called.
