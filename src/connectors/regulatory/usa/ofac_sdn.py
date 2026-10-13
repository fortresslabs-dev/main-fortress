"""
OFAC SDN Connector - Scrapes OFAC Specially Designated Nationals list
Uses the SDN_ADVANCED.XML API from sanctionslistservice.ofac.treas.gov which
includes all alias names, addresses, and ID numbers — required for compliant screening.

Source: https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML
Update frequency: Daily (typically 2-4 PM ET)
Priority: P0 CRITICAL — real-time screening required
"""
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Any, Optional
import requests
import structlog

from src.connectors.base import RegulatoryConnector
from src.services.ecfr_client import ECFRError, ECFRSection, fetch_citation

logger = structlog.get_logger(__name__)

_XML_NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML"

_DEFAULT_URLS = {
    "sdn_xml": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML",
    "sanctions_programs": "https://ofac.treasury.gov/sanctions-programs-and-country-information",
}
_HEADERS = {"User-Agent": "TextMind-Compliance/1.0"}


def _tag(el: ET.Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _tx(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def _find(el: ET.Element, local: str) -> Optional[ET.Element]:
    return el.find(f"{{{_XML_NS}}}{local}")


def _findall(el: ET.Element, local: str) -> List[ET.Element]:
    return el.findall(f"{{{_XML_NS}}}{local}")


class OfacSdnConnector(RegulatoryConnector):
    """OFAC SDN list connector — parses SDN_ADVANCED.XML for all alias names."""

    connector_name = "ofac_sdn"
    geography = "usa"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        cfg_urls = self.config.get("urls", {})
        self.urls = {k: cfg_urls.get(k, v) for k, v in _DEFAULT_URLS.items()}
        self._raw_entries: List[Dict[str, Any]] = []
        self._regulations: List[Dict[str, Any]] = []
        self._requirements: List[Dict[str, Any]] = []
        self._kpis: List[Dict[str, Any]] = []
        self._test_scenarios: List[Dict[str, Any]] = []
        self._ecfr_chapter_v: Optional[ECFRSection] = None

    def connect(self) -> bool:
        self.connected = True
        logger.info("ofac_connector_ready")
        return True

    def disconnect(self):
        self.connected = False

    def test_connection(self) -> bool:
        try:
            resp = requests.head(self.urls["sdn_xml"], timeout=10, headers=_HEADERS, allow_redirects=True)
            ok = resp.status_code == 200
            logger.info("ofac_connection_test", reachable=ok)
            return ok
        except requests.RequestException as e:
            logger.error("ofac_connection_test_failed", error=str(e))
            return False

    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        logger.info("ofac_extraction_started")

        self._download_and_parse_xml()
        # Fetch 31 CFR Chapter V once per run — nine LAW rows share this
        # citation. On failure, ``_ecfr_chapter_v`` stays None and the
        # per-row _apply_ecfr helper routes those rows to advisory rather
        # than fabricating LAW authority (§11.3 hard-gate contract).
        self._ecfr_chapter_v = self._fetch_chapter_v()
        self._build_regulation()
        self._build_requirements()
        self._build_kpis()
        self._build_test_scenarios()

        result = {
            "sdn_entries": self._raw_entries,
            "regulations": self._regulations,
            "requirements": self._requirements,
            "kpis": self._kpis,
            "test_scenarios": self._test_scenarios,
            "scraped_documents": [],
        }

        try:
            result["scraped_documents"] = self._scrape_live()
        except Exception as exc:
            logger.warning("ofac_live_scrape_failed_using_static", error=str(exc))

        logger.info(
            "ofac_extraction_completed",
            sdn_count=len(self._raw_entries),
            regulations=len(self._regulations),
            requirements=len(self._requirements),
            kpis=len(self._kpis),
            test_scenarios=len(self._test_scenarios),
            scraped_docs=len(result["scraped_documents"]),
        )
        return result

    # ------------------------------------------------------------------ #
    # XML parsing
    # ------------------------------------------------------------------ #

    # PartySubTypeID attribute values → entity type string
    # Derived from ReferenceValueSets/PartySubTypeValues in the XML:
    #   1 = Vessel (PartyTypeID=4), 2 = Aircraft (PartyTypeID=4),
    #   3 = Entity (PartyTypeID=2), 4 = Individual (PartyTypeID=1)
    _PARTY_SUBTYPE_MAP = {"1": "vessel", "2": "aircraft", "3": "entity", "4": "individual"}

    def _download_and_parse_xml(self):
        url = self.urls["sdn_xml"]
        logger.info("ofac_downloading_xml", url=url)

        resp = requests.get(url, timeout=120, headers=_HEADERS)
        resp.raise_for_status()

        # Stash raw bytes + source headers so the scheduler can feed them
        # to ofac_sdn_loader.record_list_version() (writes the freshness
        # anchor row used by the P2 ofac_attestation export). Kept on the
        # instance because the connector returns a parsed dict; the bytes
        # would otherwise be lost. GC'd when the connector instance is
        # released at end of the scrape cycle.
        self._last_xml_bytes = resp.content
        self._last_xml_source_url = url
        self._last_xml_last_modified = resp.headers.get("Last-Modified")

        # Archive the raw XML to S3 before parsing — gives us a replayable
        # audit trail and lets the change-detection step do byte-level diffs
        # against yesterday. Failures here are non-fatal; the parse still runs.
        self._archive_xml_to_s3(resp.content, resp.headers.get("Last-Modified"))

        root = ET.fromstring(resp.content)

        publish_el = _find(root, "DateOfIssue")
        publish_date = _tx(publish_el) if publish_el is not None else None

        locations = self._parse_locations(root)
        id_docs = self._parse_id_docs(root)

        # Build ProfileID → programs list once (O(n)) to avoid O(n²) scan per party
        profile_programs: Dict[str, List[str]] = {}
        se_container = _find(root, "SanctionsEntries")
        if se_container is not None:
            for se in _findall(se_container, "SanctionsEntry"):
                pid = se.get("ProfileID", "")
                if not pid:
                    continue
                for measure in _findall(se, "SanctionsMeasure"):
                    comment_el = _find(measure, "Comment")
                    prog = (comment_el.text or "").strip() if comment_el is not None else ""
                    if prog and prog not in profile_programs.get(pid, []):
                        profile_programs.setdefault(pid, []).append(prog)

        parties_el = _find(root, "DistinctParties")
        if parties_el is None:
            logger.error("ofac_xml_no_distinct_parties")
            return

        entries: List[Dict[str, Any]] = []

        for party in _findall(parties_el, "DistinctParty"):
            profile = _find(party, "Profile")
            if profile is None:
                continue

            # ID and type are XML attributes, not child elements
            profile_id = profile.get("ID", "")
            subtype_id = profile.get("PartySubTypeID", "")
            entity_type = self._PARTY_SUBTYPE_MAP.get(subtype_id, "entity")

            identity = _find(profile, "Identity")
            if identity is None:
                continue

            # Collect all alias names; AliasTypeID 1403 = primary Name record
            aliases: List[str] = []
            primary_name = ""
            for alias_el in _findall(identity, "Alias"):
                is_primary = alias_el.get("Primary", "false").lower() == "true"
                alias_type = alias_el.get("AliasTypeID", "")
                for dn in _findall(alias_el, "DocumentedName"):
                    parts = _findall(dn, "DocumentedNamePart")
                    name = " ".join(
                        _tx(_find(p, "NamePartValue")) for p in parts
                        if _tx(_find(p, "NamePartValue"))
                    )
                    if not name:
                        continue
                    # AliasTypeID 1403 = canonical Name (not AKA/FKA/NKA)
                    if (is_primary or alias_type == "1403") and not primary_name:
                        primary_name = name
                    if name not in aliases:
                        aliases.append(name)

            if not primary_name and aliases:
                primary_name = aliases[0]
            if not primary_name:
                continue

            # Features: DOB, POB, nationality — FeatureTypeID is an attribute on <Feature>
            features: Dict[str, str] = {}
            for feat in _findall(profile, "Feature"):
                ftype_id = feat.get("FeatureTypeID", "")
                for fver in _findall(feat, "FeatureVersion"):
                    detail = _tx(_find(fver, "VersionDetail"))
                    loc_ref = _find(fver, "VersionLocation")
                    if detail:
                        features[ftype_id] = detail
                    elif loc_ref is not None:
                        loc_id = loc_ref.get("LocationID", "")
                        loc = locations.get(loc_id, {})
                        features[ftype_id] = loc.get("description", "")

            # ID docs: keyed by IdentityID (attribute on Identity element)
            identity_id = identity.get("ID", "")
            id_numbers: List[Dict] = id_docs.get(identity_id, [])

            # Addresses: pulled from Profile Feature VersionLocation elements
            addresses: List[Dict] = []
            for feat in _findall(profile, "Feature"):
                for fver in _findall(feat, "FeatureVersion"):
                    loc_ref = _find(fver, "VersionLocation")
                    if loc_ref is not None:
                        loc_id = loc_ref.get("LocationID", "")
                        if loc_id and loc_id in locations and locations[loc_id].get("description"):
                            addr = locations[loc_id]
                            if addr not in addresses:
                                addresses.append(addr)

            programs = profile_programs.get(profile_id, [])

            # FeatureTypeID 9 = Nationality, 8 = Citizenship — values vary by dataset
            country = features.get("8", "") or features.get("9", "") or features.get("1", "")

            entries.append({
                "sdn_number": profile_id,
                "entity_name": primary_name,
                "entity_type": entity_type,
                "programs": programs,
                "aliases": aliases,
                "addresses": addresses,
                "id_numbers": id_numbers,
                "remarks": features.get("11", ""),
                "country": country,
                "nationality": features.get("9", ""),
                "source_updated_at": publish_date,
                "last_updated": datetime.utcnow().isoformat(),
            })

        self._raw_entries = entries
        logger.info("ofac_xml_parsed", count=len(entries), publish_date=publish_date)

    def _archive_xml_to_s3(self, body: bytes, last_modified: Optional[str]) -> None:
        """Gzip-archive SDN_ADVANCED.XML to S3 (KMS-encrypted).

        Path: scraper-chunks/sdn_xml_raw/YYYY/MM/DD/sdn_advanced_{ts}.xml.gz
        Skipped silently if S3_BUCKET is unset (local dev).
        """
        import os, gzip, io
        bucket = os.getenv("S3_BUCKET", "")
        if not bucket:
            return
        try:
            import boto3
            ts = datetime.utcnow()
            key = (
                f"scraper-chunks/sdn_xml_raw/{ts:%Y/%m/%d}/"
                f"sdn_advanced_{ts:%Y%m%dT%H%M%SZ}.xml.gz"
            )
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(body)
            boto3.client("s3").put_object(
                Bucket=bucket,
                Key=key,
                Body=buf.getvalue(),
                ContentType="application/gzip",
                ContentEncoding="gzip",
                ServerSideEncryption="aws:kms",
                Metadata={"last-modified": last_modified or "unknown"},
            )
            logger.info("ofac_xml_archived", bucket=bucket, key=key, bytes=len(body))
        except Exception as exc:
            # Audit archival is non-fatal — parsing must always proceed
            logger.warning("ofac_xml_archive_failed", error=str(exc))

    def _parse_locations(self, root: ET.Element) -> Dict[str, Dict]:
        locations_el = _find(root, "Locations")
        if locations_el is None:
            return {}
        result: Dict[str, Dict] = {}
        for loc in _findall(locations_el, "Location"):
            loc_id = loc.get("ID", "")
            if not loc_id:
                continue
            parts: Dict[str, str] = {}
            for part in _findall(loc, "LocationPart"):
                # LocPartTypeID is an attribute on LocationPart
                ptype = part.get("LocPartTypeID", "")
                for pval in _findall(part, "LocationPartValue"):
                    val = _tx(_find(pval, "Value"))
                    if val:
                        parts[ptype] = val
            # Map LocPartTypeID numbers to semantic names:
            # 1450=ADDRESS1, 1451=CITY, 1452=STATE/PROVINCE, 1453=POSTAL CODE, 1=COUNTRY
            result[loc_id] = {
                "country": parts.get("1", ""),
                "city": parts.get("1451", ""),
                "state": parts.get("1452", ""),
                "postal_code": parts.get("1453", ""),
                "address": parts.get("1450", ""),
                "description": ", ".join(v for v in [
                    parts.get("1450", ""), parts.get("1451", ""),
                    parts.get("1452", ""), parts.get("1", ""),
                ] if v),
            }
        return result

    def _parse_id_docs(self, root: ET.Element) -> Dict[str, List[Dict]]:
        """Returns dict keyed by IdentityID → list of id doc dicts."""
        docs_el = _find(root, "IDRegDocuments")
        if docs_el is None:
            return {}
        result: Dict[str, List[Dict]] = {}
        for doc in _findall(docs_el, "IDRegDocument"):
            identity_id = doc.get("IdentityID", "")
            if not identity_id:
                continue
            entry = {
                "id_type": doc.get("IDRegDocTypeID", ""),
                "id_number": _tx(_find(doc, "IDRegistrationNo")),
                "country": doc.get("IssuedBy-CountryID", "") or doc.get("IssuingCountryID", ""),
                "expiration_date": _tx(_find(doc, "ExpirationDate")),
            }
            result.setdefault(identity_id, []).append(entry)
        return result

    @staticmethod
    def _normalise_type(raw: str) -> str:
        raw_lower = raw.lower().strip()
        if "individual" in raw_lower:
            return "individual"
        if "vessel" in raw_lower:
            return "vessel"
        if "aircraft" in raw_lower:
            return "aircraft"
        return "entity"

    # ------------------------------------------------------------------ #
    # eCFR verification for 31 CFR Chapter V
    # ------------------------------------------------------------------ #
    # Nine LAW rows in this connector cite "31 C.F.R. Chapter V". The
    # ecfr_client's chapter parser handles the URL / DIV walk; we call
    # it once per connector run and share the result across every row
    # that needs the stamp. On ECFRError we leave the row unverified —
    # the authority gate routes those to advisory rather than
    # fabricating a LAW citation.

    def _fetch_chapter_v(self) -> Optional[ECFRSection]:
        try:
            session = requests.Session()
            fetched = fetch_citation(31, "Chapter V", session=session)
            logger.info(
                "ofac_ecfr_ok",
                citation=fetched.citation,
                content_hash=fetched.content_hash[:12],
            )
            return fetched
        except ECFRError as exc:
            logger.warning("ofac_ecfr_chapter_v_failed", error=str(exc))
            return None

    @staticmethod
    def _apply_ecfr(row: Dict[str, Any], fetched: Optional[ECFRSection]) -> None:
        """Stamp verification fields on a LAW row citing 31 CFR Chapter V.

        Called only for rows carrying ``authority_type == 'LAW'`` and a
        Chapter V citation. GUIDANCE / INTERNAL_CONTROL rows are left
        alone — the class-boundary CHECK constraint from migration 046
        forbids ``citation_verified = TRUE`` on non-LAW rows anyway.
        """
        if fetched is None:
            row["citation_verified"] = False
            row.setdefault(
                "authority_source",
                "eCFR fetch failed for 31 CFR Chapter V — see logs. "
                "Row emitted without provenance; authority gate will "
                "route to advisory.",
            )
            return
        row["source_url"] = fetched.source_url
        row["source_content_hash"] = fetched.content_hash
        row["scraped_at"] = fetched.fetched_at.isoformat()
        row["citation_verified"] = True
        row["citation_verified_at"] = fetched.fetched_at.isoformat()
        row["citation_verified_source"] = fetched.verifier_source

    # ------------------------------------------------------------------ #
    # Structured compliance data builders
    # ------------------------------------------------------------------ #

    def _build_regulation(self):
        row = {
            "regulation_id": "OFAC-SDN-001",
            "regulation_name": "SDN List Screening Requirement",
            # 31 CFR Chapter V is the umbrella for OFAC sanctions
            # programs. Individual programs live under specific parts
            # (e.g., 31 CFR 594 SDGT, 31 CFR 560 Iran), but Chapter V
            # is the accurate top-level cite for "must screen against
            # SDN".
            "cfr_citation": "31 C.F.R. Chapter V",
            "authority_type": "LAW",
            "category": "Sanctions",
            "subcategory": "OFAC",
            "regulator": "OFAC",
            "geography": "usa",
            "effective_date": None,
            "exam_frequency": "continuous",
            "source_url": self.urls["sdn_xml"],
            "last_updated": datetime.utcnow().isoformat(),
        }
        self._apply_ecfr(row, self._ecfr_chapter_v)
        self._regulations = [row]

    def _build_requirements(self):
        requirements = [
            {
                "requirement_id": "OFAC-SCREEN-001",
                "regulation_id": "OFAC-SDN-001",
                "cfr_citation": "31 C.F.R. Chapter V",
                "authority_type": "LAW",
                "requirement_text": "All wire transfer beneficiaries must be screened against SDN list before processing",
                "rule_type": "screening",
                "threshold_amount": None,
                "threshold_currency": "USD",
                "test_requirement": "Verify system screens every outgoing wire against SDN before execution",
                "parameters": {
                    "screening_timing": "real-time_before_execution",
                    "screening_method": "fuzzy_name_matching",
                    # Aligned with runtime threshold in
                    # src/services/validators.py:OFAC_SIMILARITY_THRESHOLD
                    "match_threshold": 85,
                    "match_algorithm": "pg_trgm_hybrid",
                    "action_on_match": "block_transaction_pending_manual_review",
                },
            },
            {
                "requirement_id": "OFAC-SCREEN-002",
                "regulation_id": "OFAC-SDN-001",
                "cfr_citation": "31 C.F.R. Chapter V",
                "authority_type": "LAW",
                "requirement_text": "New customer accounts must be screened against SDN list during onboarding",
                "rule_type": "screening",
                "threshold_amount": None,
                "threshold_currency": "USD",
                "test_requirement": "Verify new account creation screens name against SDN",
                "parameters": {
                    "screening_timing": "during_onboarding",
                    "action_on_match": "block_account_creation",
                },
            },
            # 50% Rule is OFAC GUIDANCE (FAQ 401 / FAQ 1521), not a
            # codified CFR provision. Keep the screening effect but do NOT
            # claim it as codified law.
            {
                "requirement_id": "OFAC-50PERCENT-001",
                "regulation_id": "OFAC-SDN-001",
                "cfr_citation": None,
                "authority_type": "GUIDANCE",
                "authority_source": "OFAC 50 Percent Rule — Revised Guidance (Aug 13, 2014); OFAC FAQ 401/1521",
                "requirement_text": "Entities 50% or more owned in the aggregate (directly or indirectly) by one or more SDN persons are themselves treated as blocked, per OFAC guidance.",
                "rule_type": "ownership",
                "threshold_amount": 50.0,
                "threshold_currency": None,
                "test_requirement": "Verify 50% aggregate ownership rule is applied to entity screening (per OFAC guidance, not a CFR section)",
                "parameters": {
                    "ownership_threshold": 50.0,
                    "ownership_calculation": "aggregate_all_sdn_owners",
                    "action": "apply_same_blocking_restrictions",
                },
            },
            # C-6 — reclassified from LAW to INTERNAL_CONTROL. OFAC
            # imposes no 24-hour refresh SLA; sanctions are effective
            # immediately on publication and liability is strict.
            {
                "requirement_id": "OFAC-SDN-DAILY-001",
                "regulation_id": "OFAC-SDN-001",
                "cfr_citation": None,
                "authority_type": "INTERNAL_CONTROL",
                "authority_source": "Fortress platform SLA — NOT an OFAC requirement",
                "requirement_text": (
                    "Fortress ingests the SDN list within 24 hours of OFAC publication. "
                    "NOTE: OFAC imposes no refresh deadline; sanctions are effective "
                    "immediately on publication and liability is strict. This 24-hour "
                    "figure is a Fortress platform SLA, not a legal obligation, and must "
                    "not be presented to an examiner as one."
                ),
                "rule_type": "data_freshness",
                "threshold_amount": None,
                "threshold_currency": None,
                "test_requirement": "Verify SDN list refresh runs daily and last_updated < 24h (Fortress platform-health metric)",
                "parameters": {
                    "update_frequency": "daily",
                    "max_staleness_hours": 24,
                    "is_platform_sla": True,
                },
            },
        ]
        # Stamp verification only on LAW rows citing Chapter V. GUIDANCE
        # (OFAC-50PERCENT-001) and INTERNAL_CONTROL (OFAC-SDN-DAILY-001)
        # rows are excluded — migration 046's ck_cr_verified_implies_law
        # constraint forbids citation_verified on non-LAW rows.
        for row in requirements:
            if (
                row.get("authority_type") == "LAW"
                and row.get("cfr_citation") == "31 C.F.R. Chapter V"
            ):
                self._apply_ecfr(row, self._ecfr_chapter_v)
        self._requirements = requirements

    def _build_kpis(self):
        self._kpis = [
            # Renamed from "OFAC Screening Coverage" → "SDN Screening
            # Coverage" until CONS_ADVANCED.XML (non-SDN consolidated
            # lists) is ingested. We currently only screen against the
            # SDN list, not the full OFAC screening surface.
            {
                "kpi_id": "OFAC-KPI-001",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SCREEN-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. Chapter V",
                "kpi_name": "SDN Screening Coverage",
                "kpi_formula": "(Wires screened against SDN / Total wires) * 100",
                "target_value": 100.0,
                "target_unit": "percent",
                "monitoring_frequency": "real-time",
                "alert_threshold": 99.0,
                "criticality": "critical",
            },
            {
                "kpi_id": "OFAC-KPI-002",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SCREEN-001",
                "authority_type": "LAW",
                "citation": "31 C.F.R. Chapter V",
                "kpi_name": "SDN Screening False Positive Rate",
                "kpi_formula": "(False positive matches / Total matches) * 100",
                "target_value": 5.0,
                "target_unit": "percent",
                "monitoring_frequency": "weekly",
                "alert_threshold": 10.0,
                "criticality": "medium",
            },
            # Platform-health metric, NOT a compliance KPI. Parented to the
            # internal-control freshness rule so downstream artifacts do
            # not present this as a regulatory requirement.
            {
                "kpi_id": "OFAC-KPI-003",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SDN-DAILY-001",
                "authority_type": "INTERNAL_CONTROL",
                "authority_source": "Fortress platform SLA",
                "kpi_name": "SDN List Freshness (Platform SLA)",
                "kpi_formula": "Hours since last SDN list update",
                "target_value": 24.0,
                "target_unit": "hours",
                "monitoring_frequency": "daily",
                "alert_threshold": 48.0,
                "criticality": "high",
            },
        ]

    def _build_test_scenarios(self):
        self._test_scenarios = [
            {
                "scenario_id": "TC-007",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SCREEN-001",
                "regulator": "OFAC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. Chapter V",
                "scenario_name": "Wire transfer to SDN-listed individual blocked",
                "priority": "critical",
                "given": "John Doe is on OFAC SDN list (UID 12345)",
                "when_step": "Bank initiates wire transfer $50,000 to 'John Doe'",
                "then_step": "Transaction BLOCKED, compliance alert raised, audit log created",
            },
            {
                "scenario_id": "TC-008",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SCREEN-001",
                "regulator": "OFAC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. Chapter V",
                "scenario_name": "Fuzzy name matching - 92% similarity triggers review",
                "priority": "high",
                "given": "SDN list contains 'John Doe'",
                "when_step": "Wire transfer to 'Jon Doe' (92% match)",
                "then_step": "Transaction HELD for manual review, similarity score logged",
            },
            {
                "scenario_id": "TC-009",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SCREEN-001",
                "regulator": "OFAC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. Chapter V",
                "scenario_name": "Non-SDN name passes screening",
                "priority": "high",
                "given": "Alice Smith is NOT on SDN list",
                "when_step": "Wire transfer $25,000 to 'Alice Smith'",
                "then_step": "Transaction APPROVED, screening timestamp logged",
            },
            {
                "scenario_id": "TC-010",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-50PERCENT-001",
                "regulator": "OFAC",
                "authority_type": "GUIDANCE",
                "authority_source": "OFAC 50 Percent Rule guidance",
                "scenario_name": "50% ownership rule applied",
                "priority": "high",
                "given": "Entity 'ABC Corp' is 60% owned by SDN-listed individual",
                "when_step": "Wire transfer to 'ABC Corp'",
                "then_step": "Transaction BLOCKED per OFAC 50% Rule guidance",
            },
            # Platform-health test (not a legal obligation). Retained
            # because operators still need the staleness alert; simply
            # re-labeled so it no longer masquerades as a compliance TC.
            {
                "scenario_id": "TC-011",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SDN-DAILY-001",
                "regulator": "OFAC",
                "authority_type": "INTERNAL_CONTROL",
                "authority_source": "Fortress platform SLA",
                "scenario_name": "SDN list staleness alert (platform-health)",
                "priority": "medium",
                "given": "SDN list was last updated 25 hours ago",
                "when_step": "System checks SDN freshness",
                "then_step": "Alert raised: 'SDN list stale — exceeds 24h Fortress SLA (not an OFAC requirement)'",
            },
            {
                "scenario_id": "TC-ALIAS-001",
                "regulation_id": "OFAC-SDN-001",
                "parent_requirement_id": "OFAC-SCREEN-001",
                "regulator": "OFAC",
                "authority_type": "LAW",
                "citation": "31 C.F.R. Chapter V",
                "scenario_name": "Alias name match triggers block",
                "priority": "critical",
                "given": "SDN entity has primary name 'RIHS' and alias 'HAND OF MERCY'",
                "when_step": "Wire transfer to 'Hand of Mercy Foundation'",
                "then_step": "Transaction BLOCKED via alias match, all known aliases screened",
            },
        ]

    # ------------------------------------------------------------------ #
    # Live scrape (supplemental)
    # ------------------------------------------------------------------ #

    def _scrape_live(self) -> List[Dict]:
        from src.connectors.regulatory.usa._scraper_utils import (
            make_session,
            scrape_html_page,
        )
        session = make_session()
        return scrape_html_page(
            session,
            self.urls["sanctions_programs"],
            source="OFAC",
            category="Sanctions Programs",
            content_selectors=[".usa-prose", "article"],
        )

    def check_updates(self) -> Dict[str, Any]:
        try:
            resp = requests.head(self.urls["sdn_xml"], timeout=30, headers=_HEADERS)
            last_modified = resp.headers.get("Last-Modified")
            logger.info("ofac_update_check", last_modified=last_modified)
            return {"last_updated": last_modified, "needs_refresh": True}
        except Exception as exc:
            logger.warning("ofac_update_check_failed", error=str(exc))
            return {"last_updated": None, "needs_refresh": False}
