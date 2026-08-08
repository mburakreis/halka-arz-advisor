"""The decision-feature requirements catalog.

Grounded directly in this project's existing data models — no feature
here assumes a field, document type, or external source that doesn't
already exist somewhere in :mod:`halka_arz_advisor.kap` /
:mod:`halka_arz_advisor.spk`, *except* where a feature is deliberately
cataloged as a known gap (its ``required_source_fields`` name a field
that has no corresponding extractor yet — the audit reports that
honestly as ``MISSING_FIELD``/``MISSING_DOCUMENT`` rather than pretending
it exists).

``required_source_fields`` entries use a namespace prefix identifying
which existing model they'd read from:

- ``kap_extraction.<name>`` — a field on
  :class:`halka_arz_advisor.kap.extraction.ExtractedFacts` (the 12 in
  ``FIELD_NAMES``) *or* a field that would belong there but has no
  extractor implemented yet (a genuine, named gap).
- ``spk_ipo_record.<name>`` — a field on
  :class:`halka_arz_advisor.spk.models.SpkIpoRecord` (SPK's completed-
  IPO record — only exists once the offering is done).
- ``spk_application.<name>`` — a field on
  :class:`halka_arz_advisor.spk.application_list.SpkIpoApplicationRecord`
  (an announced-but-not-yet-completed application).
- ``kap_document.<document_type>`` — not a field at all, a check for
  whether a readable disclosure of that
  :data:`~halka_arz_advisor.kap.classification.DocumentType` exists for
  the company (used by features that are about a document's presence,
  e.g. a third-party price-determination review).
- ``financial_series.<metric_name>`` — one metric in
  :data:`halka_arz_advisor.kap.financials.FINANCIAL_METRIC_NAMES`,
  resolved against every :class:`~halka_arz_advisor.kap.financials.FinancialObservation`
  collected for the company (any explicitly labelled period counts;
  see that module's docstring) — never a single scalar, since a metric
  is reported for several comparable periods at once.
- ``derived_financial.<feature_name>`` — one feature in
  :data:`halka_arz_advisor.kap.derived_financials.DERIVED_FINANCIAL_FEATURE_NAMES`,
  a value computed (not extracted) from ``financial_series``/``kap_extraction``
  data under strict comparison rules — resolves ``DERIVABLE`` when the
  formula could be computed, ``MISSING_FIELD``/``MISSING_DOCUMENT``
  when its inputs were missing or incompatible (see that module's
  docstring for exactly what "incompatible" means per feature).
- ``market_data.<name>`` — no corresponding model exists in this
  project at all (a genuine, currently out-of-scope data source, e.g.
  a peer/index feed) — always evaluates to ``MISSING_DOCUMENT``, *except*
  the handful of names that
  :func:`halka_arz_advisor.evds.features.build_market_context_snapshot`
  actually populates (see the market_context block below), which
  resolve from ``CompanyDecisionInputs.market_context`` instead.
- ``kap_sector.classification`` — not a field on any model; wires
  :attr:`halka_arz_advisor.decision.audit.CompanyDecisionInputs.sector`
  (:func:`halka_arz_advisor.kap.sector.classify_sector`, a deterministic,
  name-based classifier already used internally for
  ``SECTOR_INAPPLICABLE_METRICS`` gating) into the catalog directly —
  not a new extractor, just exposing an already-computed value.

A ``derived`` feature has no ``required_source_fields`` of its own; it
reads its ``derivation_dependencies`` (other ``feature_id``s) instead
— see :mod:`halka_arz_advisor.decision.audit` for how that's resolved.
"""

from __future__ import annotations

from .models import FeatureSpec

_PROSPECTUS_AND_ANNOUNCEMENT = ("approved_prospectus", "investor_sale_announcement")

FEATURE_CATALOG: tuple[FeatureSpec, ...] = (
    # ------------------------------------------------------------------
    # fundamental_quality
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="business_description",
        category="fundamental_quality",
        title="İşletme faaliyet tanımı",
        description="A concise description of what the company actually does, from the prospectus.",
        required_source_fields=("kap_extraction.business_description",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="key_risk_factors",
        category="fundamental_quality",
        title="Temel risk faktörleri",
        description="The prospectus's own stated risk factors.",
        required_source_fields=("kap_extraction.key_risk_items",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="use_of_proceeds_plan",
        category="fundamental_quality",
        title="Halka arz gelirinin kullanım planı",
        description="What management states the offering proceeds will be used for.",
        required_source_fields=("kap_extraction.use_of_proceeds",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="financial_statement_summary",
        category="fundamental_quality",
        title="Finansal tablo özeti (gelir/kâr eğilimi)",
        description=(
            "Revenue and net income across every explicitly labelled comparable period stated in the "
            "price determination report's own income statement table."
        ),
        required_source_fields=("financial_series.revenue", "financial_series.net_income"),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="revenue_growth_yoy",
        category="fundamental_quality",
        title="Yıllık gelir büyümesi",
        description=(
            "Year-over-year revenue growth between the two most recent comparable ANNUAL periods "
            "(same consolidation scope, currency, scale, and inflation-adjustment status)."
        ),
        required_source_fields=("derived_financial.revenue_growth_yoy",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="net_margin",
        category="fundamental_quality",
        title="Net kar marjı",
        description=(
            "Net income divided by revenue for the most recent period both are reported for, requiring an "
            "exact period, scope, currency, and scale match."
        ),
        required_source_fields=("derived_financial.net_margin",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="debt_to_equity",
        category="fundamental_quality",
        title="Borç/özkaynak oranı",
        description=(
            "financial_debt divided by equity for the most recent period both are reported for, requiring an "
            "exact period, scope, currency, and scale match."
        ),
        required_source_fields=("derived_financial.debt_to_equity",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="current_ratio",
        category="fundamental_quality",
        title="Cari oran",
        description=(
            "current_assets divided by current_liabilities for the most recent period both are reported for — "
            "not a meaningful concept for a company whose statement of financial position isn't presented in a "
            "classified current/non-current format (see halka_arz_advisor.kap.sector)."
        ),
        required_source_fields=("derived_financial.current_ratio",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="operating_cash_flow_to_net_income",
        category="fundamental_quality",
        title="Faaliyet nakit akışı / net kâr oranı",
        description=(
            "A rough earnings-quality signal: operating_cash_flow divided by net_income for the most recent "
            "period both are reported for."
        ),
        required_source_fields=("derived_financial.operating_cash_flow_to_net_income",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="interest_coverage",
        category="fundamental_quality",
        title="Faiz karşılama oranı",
        description=(
            "operating_profit divided by finance_expense for the most recent period both are reported for — "
            "not a meaningful concept for a sector with no standard operating-profit line (see "
            "halka_arz_advisor.kap.sector)."
        ),
        required_source_fields=("derived_financial.interest_coverage",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="related_party_transactions_disclosure",
        category="fundamental_quality",
        title="İlişkili taraf işlemleri açıklaması",
        description="Disclosed related-party transactions, if any.",
        required_source_fields=("kap_extraction.related_party_transactions",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="litigation_exposure_disclosure",
        category="fundamental_quality",
        title="Hukuki takip/dava durumu",
        description="Disclosed material litigation the company is party to.",
        required_source_fields=("kap_extraction.litigation_status",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    # ------------------------------------------------------------------
    # valuation
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="offering_price",
        category="valuation",
        title="Halka arz fiyatı",
        description="The fixed offering price and its currency.",
        required_source_fields=("kap_extraction.offering_price", "kap_extraction.currency"),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="capital_increase_ratio",
        category="valuation",
        title="Sermaye artırım oranı",
        description="The capital increase ratio (%) backing the newly issued shares.",
        required_source_fields=("kap_extraction.capital_increase_ratio",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="secondary_sale_ratio",
        category="valuation",
        title="Ortak satış oranı",
        description="The existing-shareholder secondary sale ratio (%), if any.",
        required_source_fields=("kap_extraction.secondary_sale_ratio",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="implied_offer_size_value",
        category="valuation",
        title="Zımni halka arz büyüklüğü",
        description="offering_price × total_offered_shares — the offering's total nominal value.",
        required_source_fields=(),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="derived",
        derivation_dependencies=("offering_price", "total_offered_shares"),
    ),
    FeatureSpec(
        feature_id="post_offer_market_value_of_offering",
        category="valuation",
        title="Halka arz edilen payların piyasa değeri (SPK)",
        description="SPK's own published market value of the offered shares, once the IPO is complete.",
        required_source_fields=("spk_ipo_record.satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl",),
        acceptable_sources=("spk_completed_ipo_record",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="earnings_multiple_at_offer",
        category="valuation",
        title="Halka arz F/K oranı",
        description="Price/earnings multiple, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_pe",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_post_money_market_cap",
        category="valuation",
        title="Fiyat tespit raporu piyasa değeri",
        description="The company's final post-discount market cap, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_post_money_market_cap",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_enterprise_value",
        category="valuation",
        title="Fiyat tespit raporu firma değeri",
        description="Enterprise value, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_enterprise_value",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_net_debt",
        category="valuation",
        title="Fiyat tespit raporu net borç",
        description="Net debt, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_net_debt",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="net_debt",
        category="valuation",
        title="Yeniden hesaplanan net borç",
        description=(
            "financial_debt minus cash_and_equivalents for the most recent period both are reported for — a "
            "cross-check against reported_net_debt, not a substitute for it."
        ),
        required_source_fields=("derived_financial.net_debt",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_ev_ebitda_multiple",
        category="valuation",
        title="Fiyat tespit raporu EV/EBITDA çarpanı",
        description="EV/EBITDA multiple, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_ev_ebitda",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_ps_multiple",
        category="valuation",
        title="Fiyat tespit raporu F/S çarpanı",
        description="Price/sales multiple, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_ps",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_pb_multiple",
        category="valuation",
        title="Fiyat tespit raporu PD/DD çarpanı",
        description="Price/book multiple, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.reported_pb",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="headline_discount_percentage",
        category="valuation",
        title="Halka arz iskonto oranı",
        description="The discount applied between the calculated fair value and the actual offering price, as explicitly stated in the price determination report.",
        required_source_fields=("kap_extraction.headline_discount_percentage",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="recalculated_pe",
        category="valuation",
        title="Yeniden hesaplanan F/K",
        description=(
            "reported_post_money_market_cap divided by the latest eligible full-year annual net income — "
            "a cross-check against the report's own stated P/E, not a substitute for it."
        ),
        required_source_fields=("derived_financial.recalculated_pe",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="reported_pe_difference_percentage",
        category="valuation",
        title="Rapor F/K farkı (%)",
        description="Percentage difference between recalculated_pe and the report's own explicitly stated reported_pe.",
        required_source_fields=("derived_financial.reported_pe_difference_percentage",),
        acceptable_sources=("price_determination_report",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    # ------------------------------------------------------------------
    # offering_structure
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="subscription_window",
        category="offering_structure",
        title="Talep toplama tarih aralığı",
        description="Subscription start and end dates.",
        required_source_fields=("kap_extraction.subscription_start_date", "kap_extraction.subscription_end_date"),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="distribution_method",
        category="offering_structure",
        title="Dağıtım yöntemi",
        description="How the offered shares will be allocated among subscribers.",
        required_source_fields=("kap_extraction.distribution_method",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="total_offered_shares",
        category="offering_structure",
        title="Toplam halka arz edilen pay tutarı",
        description="Total shares offered (capital increase + secondary sale combined).",
        required_source_fields=("kap_extraction.total_offered_shares",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="capital_increase_shares",
        category="offering_structure",
        title="Sermaye artırımı yoluyla ihraç edilen pay tutarı",
        description="Shares issued via capital increase specifically.",
        required_source_fields=("kap_extraction.capital_increase_shares",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="secondary_sale_shares",
        category="offering_structure",
        title="Ortak satışı yoluyla satılan pay tutarı",
        description="Shares sold by existing shareholders specifically, if any.",
        required_source_fields=("kap_extraction.secondary_sale_shares",),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="over_allotment_greenshoe_amount",
        category="offering_structure",
        title="Ek satış (yeşil ayakkabı) tutarı",
        description="The over-allotment/greenshoe amount, if used — only published on the completed SPK record today.",
        required_source_fields=("spk_ipo_record.ek_satis_tutari_bin_tl",),
        acceptable_sources=("spk_completed_ipo_record",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="lead_intermediary_institution",
        category="offering_structure",
        title="Halka arza aracılık eden kurum",
        description="The lead underwriter/intermediary institution — only published on the completed SPK record today.",
        required_source_fields=("spk_ipo_record.halka_arza_aracilik_eden_kurum",),
        acceptable_sources=("spk_completed_ipo_record",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="listing_market_segment",
        category="offering_structure",
        title="İlk işlem göreceği pazar",
        description="Which Borsa İstanbul market/segment the shares will list on.",
        required_source_fields=("spk_ipo_record.ilk_islem_gordugu_pazar",),
        acceptable_sources=("spk_completed_ipo_record",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    # ------------------------------------------------------------------
    # market_context
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="sector_classification",
        category="market_context",
        title="Sektör sınıflandırması",
        description="The issuer's industry/sector classification.",
        required_source_fields=("kap_sector.classification",),
        acceptable_sources=("approved_prospectus", "investor_sale_announcement", "spk_completed_ipo_record"),
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="peer_group_comparables",
        category="market_context",
        title="Emsal şirket karşılaştırması",
        description="Valuation/performance comparison against listed peers — needs a cross-company data source this project doesn't have.",
        required_source_fields=("market_data.peer_comparables",),
        acceptable_sources=("external_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="broader_index_level_at_offer",
        category="market_context",
        title="Halka arz döneminde BIST endeks seviyesi",
        description=(
            "Overall market conditions (index level/trend) during the subscription window — the latest "
            "cached BIST-100 index level, from the same evds.features.build_market_context_snapshot series "
            "that already powers bist100_return_20d/60d/120d below (not a separate, unimplemented feed)."
        ),
        required_source_fields=("market_data.bist_index_level",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="recent_comparable_ipo_performance",
        category="market_context",
        title="Yakın dönem benzer halka arzların performansı",
        description="How recently listed comparable IPOs have performed post-listing.",
        required_source_fields=("market_data.recent_ipo_performance",),
        acceptable_sources=("external_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="application_pipeline_status",
        category="market_context",
        title="Başvuru aşaması durumu",
        description="Whether an SPK IPO application has been filed for the company, and when.",
        required_source_fields=("spk_application.application_date",),
        acceptable_sources=("spk_application_record",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    # The nine EVDS-derived market-context features below (plus
    # broader_index_level_at_offer above, which shares the same
    # bist100_index series) are populated only when
    # halka_arz_advisor.decision.audit.CompanyDecisionInputs.market_context
    # carries a snapshot (see halka_arz_advisor.evds.features) — a
    # company-agnostic macro snapshot shared across every company in a
    # given run, not IPO-specific data. All ten are deliberately
    # non-mandatory and outside every scored category (fundamental_quality/
    # valuation/offering_structure) in scoring_config.py's expert_v0 —
    # this only extends the coverage *audit*, never total_score,
    # confidence_score, or the participation signal.
    FeatureSpec(
        feature_id="bist100_return_20d",
        category="market_context",
        title="BIST 100 20 iş günü getirisi",
        description="BIST 100 index return over the trailing 20 trading observations.",
        required_source_fields=("market_data.bist100_return_20d",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="bist100_return_60d",
        category="market_context",
        title="BIST 100 60 iş günü getirisi",
        description="BIST 100 index return over the trailing 60 trading observations.",
        required_source_fields=("market_data.bist100_return_60d",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="bist100_return_120d",
        category="market_context",
        title="BIST 100 120 iş günü getirisi",
        description="BIST 100 index return over the trailing 120 trading observations.",
        required_source_fields=("market_data.bist100_return_120d",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="bist100_volatility_20d",
        category="market_context",
        title="BIST 100 20 iş günü volatilitesi",
        description="Sample standard deviation of daily BIST 100 returns over the trailing 20 trading observations.",
        required_source_fields=("market_data.bist100_volatility_20d",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="bist100_max_drawdown_60d",
        category="market_context",
        title="BIST 100 60 iş günü en büyük düşüş",
        description="Largest peak-to-trough BIST 100 decline within the trailing 60 trading observations.",
        required_source_fields=("market_data.bist100_max_drawdown_60d",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="policy_rate",
        category="market_context",
        title="TCMB politika faizi",
        description="TCMB's current operative policy rate (1-week deposit-auction weighted average).",
        required_source_fields=("market_data.policy_rate",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="tlref_rate",
        category="market_context",
        title="BIST TLREF",
        description="BIST TLREF - Türk Lirası Gecelik Referans Faiz Oranı.",
        required_source_fields=("market_data.tlref_rate",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="cpi_yoy",
        category="market_context",
        title="Yıllık TÜFE değişimi",
        description="Year-over-year change of TÜİK's headline CPI general index, as published through EVDS.",
        required_source_fields=("market_data.cpi_yoy",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="policy_rate_minus_cpi",
        category="market_context",
        title="Politika faizi - Yıllık TÜFE farkı",
        description="Approximate real policy rate: policy_rate minus cpi_yoy, both in percentage points.",
        required_source_fields=("market_data.policy_rate_minus_cpi",),
        acceptable_sources=("evds_market_data_feed",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    # ------------------------------------------------------------------
    # allocation_efficiency
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="oversubscription_ratio_overall",
        category="allocation_efficiency",
        title="Genel talep/arz (katlama) oranı",
        description="Overall subscription multiple across all investor tranches combined.",
        required_source_fields=("kap_extraction.total_demand_multiple",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="retail_allocated_shares",
        category="allocation_efficiency",
        title="Bireysel yatırımcılara dağıtılan pay tutarı",
        description="Final nominal amount actually allocated to retail investors.",
        required_source_fields=("kap_extraction.retail_allocated_shares",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="institutional_allocated_shares",
        category="allocation_efficiency",
        title="Kurumsal yatırımcılara dağıtılan pay tutarı",
        description="Final nominal amount actually allocated to institutional investors.",
        required_source_fields=("kap_extraction.institutional_allocated_shares",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="allocation_by_investor_category",
        category="allocation_efficiency",
        title="Yatırımcı kategorisine göre dağıtım",
        description="Final share allocation broken down by investor category (retail/institutional/...).",
        required_source_fields=("kap_extraction.allocation_by_investor_category",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="demand_to_supply_ratio_by_tranche",
        category="allocation_efficiency",
        title="Dilim bazında talep/arz oranı",
        description="Subscription multiple broken down per allocation tranche.",
        required_source_fields=("kap_extraction.demand_to_supply_ratio_by_tranche",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="final_allocation_price",
        category="allocation_efficiency",
        title="Nihai dağıtım fiyatı",
        description="The price shares were actually allocated at — equal to offering_price for a fixed-price offering.",
        required_source_fields=(),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="derived",
        derivation_dependencies=("offering_price",),
    ),
    # ------------------------------------------------------------------
    # demand_sentiment
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="total_participant_count",
        category="demand_sentiment",
        title="Toplam yatırımcı sayısı",
        description="Total number of investors who placed demand across every tranche.",
        required_source_fields=("kap_extraction.total_participant_count",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="retail_participant_count",
        category="demand_sentiment",
        title="Bireysel yatırımcı sayısı",
        description="Number of retail investors who placed demand.",
        required_source_fields=("kap_extraction.retail_participant_count",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="retail_investor_demand_signal",
        category="demand_sentiment",
        title="Bireysel yatırımcı talep göstergesi",
        description="Retail-investor-specific subscription multiple/sentiment.",
        required_source_fields=("kap_extraction.retail_demand_multiple",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="institutional_investor_demand_signal",
        category="demand_sentiment",
        title="Kurumsal yatırımcı talep göstergesi",
        description="Institutional-investor-specific subscription multiple/sentiment.",
        required_source_fields=("kap_extraction.institutional_demand_multiple",),
        acceptable_sources=("ipo_results",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="analyst_or_broker_commentary_presence",
        category="demand_sentiment",
        title="Analist/aracı kurum değerlendirme raporu varlığı",
        description=(
            "Whether an independent third-party review of the price determination report was filed — "
            "recognized by classification but deliberately excluded from the fetch pipeline's target "
            "document types today (see halka_arz_advisor.kap.classification.target_document_types)."
        ),
        required_source_fields=("kap_document.price_determination_review",),
        acceptable_sources=("price_determination_review",),
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="post_ipo_price_performance_signal",
        category="demand_sentiment",
        title="Halka arz sonrası fiyat performansı",
        description="First-day/aftermarket trading performance relative to the offering price.",
        required_source_fields=("market_data.first_day_trading_performance",),
        acceptable_sources=("external_market_data_feed",),
        offer_timing="post_offer",
        is_mandatory=False,
        availability_kind="direct",
    ),
    # ------------------------------------------------------------------
    # data_confidence
    # ------------------------------------------------------------------
    FeatureSpec(
        feature_id="document_completeness",
        category="data_confidence",
        title="Belge eksiksizliği",
        description="Whether both a readable approved prospectus and investor sale announcement were found.",
        required_source_fields=("kap_document.approved_prospectus", "kap_document.investor_sale_announcement"),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=True,
        availability_kind="direct",
    ),
    FeatureSpec(
        feature_id="cross_document_field_corroboration",
        category="data_confidence",
        title="Belgeler arası veri doğrulaması",
        description="Whether the core pre-offer fields are each confirmed by more than one document, not just one.",
        required_source_fields=(),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="derived",
        derivation_dependencies=(
            "offering_price",
            "distribution_method",
            "subscription_window",
            "total_offered_shares",
            "capital_increase_ratio",
        ),
    ),
    FeatureSpec(
        feature_id="ocr_reliance",
        category="data_confidence",
        title="OCR'a dayanma durumu",
        description="Whether any core pre-offer field's value came from OCR text rather than a digital PDF text layer.",
        required_source_fields=(),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="derived",
        derivation_dependencies=(
            "offering_price",
            "distribution_method",
            "subscription_window",
            "total_offered_shares",
            "capital_increase_ratio",
        ),
    ),
    FeatureSpec(
        feature_id="single_source_field_flag",
        category="data_confidence",
        title="Tek kaynaklı alan işareti",
        description="Whether any core pre-offer field rests on exactly one observation (no corroborating document at all).",
        required_source_fields=(),
        acceptable_sources=_PROSPECTUS_AND_ANNOUNCEMENT,
        offer_timing="pre_offer",
        is_mandatory=False,
        availability_kind="derived",
        derivation_dependencies=(
            "offering_price",
            "distribution_method",
            "subscription_window",
            "total_offered_shares",
            "capital_increase_ratio",
        ),
    ),
)

_BY_ID: dict[str, FeatureSpec] = {spec.feature_id: spec for spec in FEATURE_CATALOG}

if len(_BY_ID) != len(FEATURE_CATALOG):
    seen: set[str] = set()
    duplicates = sorted({s.feature_id for s in FEATURE_CATALOG if s.feature_id in seen or seen.add(s.feature_id)})
    raise AssertionError(f"duplicate feature_id(s) in FEATURE_CATALOG: {duplicates}")


def get_feature(feature_id: str) -> FeatureSpec:
    try:
        return _BY_ID[feature_id]
    except KeyError:
        raise KeyError(f"no such feature_id in FEATURE_CATALOG: {feature_id!r}") from None


def features_by_category(category: str) -> tuple[FeatureSpec, ...]:
    return tuple(spec for spec in FEATURE_CATALOG if spec.category == category)
