"""Understanding Agent - Complete Implementation with RAG
Analyzes a JIRA story with banking-pattern context to build comprehensive understanding.

Aligned to USA banking compliance (OCC, FDIC, CFPB, FinCEN, OFAC, FFIEC).

Uses ComplianceService (DB-backed) and hardcoded banking-knowledge hints derived
from the discovery agent's pattern detection.
"""
from typing import Dict, Any, List
import structlog
from sqlalchemy import select
from src.agents.base_agent import BaseAgent
from src.services.compliance_service import ComplianceService
from src.services.compliance_rule_resolver import ComplianceRuleResolver

logger = structlog.get_logger()

# Maps banking_patterns keys from DiscoveryAgent to the compliance_rules.domain
# field. Patterns absent from this map don't resolve to obligations and are
# skipped — keeps the obligation-coverage writeback narrow rather than
# over-attributing (e.g. marking every FinCEN obligation covered by one
# wire test).
_PATTERN_TO_DOMAIN: Dict[str, str] = {
    "wire_transfer": "wire_transfer",
    "ach":           "ach",
    "sar":           "sar",
    "ctr":           "ctr",
    "bsa_aml":       "bsa_aml",
    "kyc_cdd":       "kyc_cdd",
    "ofac":          "ofac",
    "reg_e":         "reg_e",
    "hmda":          "hmda",
    "section_1071":  "section_1071",
    "loan":          "loan",
}


class UnderstandingAgent(BaseAgent):
    agent_type = "understanding"

    def __init__(self):
        super().__init__()
        # NEW: Use ComplianceService instead of hardcoded rules
        self.compliance_service = ComplianceService()
        # Hybrid pgvector + BM25 retrieval over compliance_rules. We borrow the
        # resolver's Titan v2 embed so there's a single embedding implementation.
        self.resolver = ComplianceRuleResolver()
    
    # REMOVED: Hardcoded RBI_RULES (now loaded from Finacle/RBI connectors)

    async def execute(self, context: Dict, input_data: Dict) -> Dict[str, Any]:
        """
        Understand the JIRA story with RAG context
        
        Args:
            context: Workflow context
            input_data: Contains jira_story, discovery results
            
        Returns:
            requirements: Extracted requirements
            test_scenarios_outline: High-level test scenarios
            banking_rules: Applied banking rules
            compliance_context: RBI compliance context
            rag_context: Banking-knowledge context hints
        """
        try:
            jira_story = input_data.get("jira_story", "")
            jira_key = input_data.get("jira_key", "")
            workflow_id = input_data.get("workflow_id")
            discovery = input_data.get("discovery", {})
            trigger_rule_id = input_data.get("rule_id")
            
            self.logger.info(
                "understanding_started",
                workflow_id=workflow_id,
                jira_key=jira_key
            )
            
            # 1. Build RAG context from banking-pattern hints
            rag_context = await self._retrieve_rag_context(jira_story, discovery)
            
            # 2. Extract requirements from story
            requirements = self._extract_requirements(jira_story, discovery)
            
            # 3. Apply banking rules
            banking_rules = self._apply_banking_rules(discovery["banking_patterns"])
            
            # 4. Build compliance context
            compliance_context = self._build_compliance_context(
                discovery.get("compliance_requirements", []),
                banking_rules
            )
            
            # 5. Outline test scenarios (high-level)
            test_scenarios_outline = self._outline_test_scenarios(
                requirements,
                banking_rules,
                compliance_context,
                rag_context
            )
            
            # 6. Identify edge cases
            edge_cases = self._identify_edge_cases(banking_rules, discovery)

            # 7. Resolve concrete obligation_ids the generated test will cover.
            #    Threaded into ExecutionAgent so it can flip
            #    obligations.has_test_coverage on PR-open success — REQ-010.
            obligation_ids, rule_ids = await self._resolve_obligation_ids(
                discovery, jira_story, trigger_rule_id
            )

            result = {
                "requirements": requirements,
                "test_scenarios_outline": test_scenarios_outline,
                "banking_rules": banking_rules,
                "compliance_context": compliance_context,
                "edge_cases": edge_cases,
                "rag_context": rag_context,
                "rag_used": len(rag_context) > 0,
                "obligation_ids": obligation_ids,
                "rule_ids": rule_ids,
            }

            self.logger.info(
                "understanding_completed",
                workflow_id=workflow_id,
                requirements_count=len(requirements),
                scenarios_count=len(test_scenarios_outline),
                rag_chunks=len(rag_context),
                obligation_ids_resolved=len(obligation_ids),
                rule_ids_resolved=len(rule_ids),
            )

            return result
            
        except Exception as e:
            self.logger.error(
                "understanding_failed",
                workflow_id=workflow_id,
                error=str(e),
                exc_info=True
            )
            raise
    
    async def _retrieve_rag_context(self, story_text: str, discovery: Dict) -> List[Dict]:
        """Return banking-pattern context hints for the discovered patterns."""
        return self._fallback_rag_context(discovery)

    async def _resolve_obligation_ids(
        self, discovery: Dict, jira_story: str = "", trigger_rule_id: str = None
    ) -> tuple[List[str], List[str]]:
        """Resolve concrete obligation_id values for the story.

        Two complementary retrieval paths, unioned:
          1. Domain-scoped: pattern → compliance_rules.domain → rules. Precise
             but blind to rules whose domain tag the pattern map doesn't name.
          2. Hybrid pgvector + BM25 semantic search over the story text, so
             rules that match by *meaning* (e.g. the FinCEN Part 1010
             obligations) surface even without a domain-map entry.

        Best-effort: DB / embed failures degrade (hybrid → BM25 → nothing) and
        empty results return ([], []) so the run still completes — the writeback
        in ExecutionAgent simply no-ops.
        """
        patterns = discovery.get("banking_patterns") or {}
        domains = {
            _PATTERN_TO_DOMAIN[p] for p in patterns
            if p in _PATTERN_TO_DOMAIN
        }

        rule_ids: List[str] = []

        # Path 1 — domain-scoped lookup.
        for domain in domains:
            try:
                rules = await self.compliance_service.get_rules_by_domain(domain)
            except Exception as exc:
                self.logger.warning(
                    "obligation_resolve_domain_failed",
                    domain=domain,
                    error=str(exc),
                )
                continue
            rule_ids.extend(r["rule_id"] for r in rules if r.get("rule_id"))

        # Path 2 — hybrid semantic + BM25 retrieval. Query is the story plus the
        # discovery summary and detected signals, for better recall than the
        # raw ticket text alone.
        hybrid_query = " ".join(filter(None, [
            jira_story,
            discovery.get("summary", ""),
            " ".join(p for p, on in patterns.items() if on),
            " ".join(discovery.get("compliance_requirements") or []),
        ]))
        rule_ids.extend(await self._hybrid_search_rule_ids(hybrid_query))

        # Always include the rule the CCO triggered the story on, so its
        # obligations are covered even when domain/semantic retrieval misses it
        # (e.g. Part 1010 rules with no embedding or domain tag). Without this,
        # coverage lands only on RAG-neighbour rules, not the clicked rule.
        if trigger_rule_id:
            rule_ids.append(trigger_rule_id)

        if not rule_ids:
            return [], []

        rule_ids = sorted(set(rule_ids))

        try:
            from src.services.database import get_db
            from src.models.database import Obligation
            async with get_db() as session:
                stmt = (
                    select(Obligation.obligation_id)
                    .where(Obligation.regulation_id.in_(rule_ids))
                    .where(Obligation.is_active.is_(True))
                )
                result = await session.execute(stmt)
                obligation_ids = [row[0] for row in result.all()]
        except Exception as exc:
            self.logger.warning(
                "obligation_resolve_query_failed",
                rule_count=len(rule_ids),
                error=str(exc),
            )
            return [], rule_ids

        return obligation_ids, rule_ids

    async def _hybrid_search_rule_ids(
        self, query_text: str, top_k: int = 12
    ) -> List[str]:
        """Return the rule_ids of the most relevant compliance_rules for a query.

        Hybrid score = 0.7 · pgvector cosine similarity (1024-dim Titan v2
        embedding) + 0.3 · ts_rank BM25 on the generated tsv column — the same
        weighting the compliance_rule_resolver uses. If Bedrock embedding is
        unavailable (local dev / LLM_MOCK_MODE / no AWS creds) it degrades to
        pure BM25. Any failure returns [] so obligation resolution still
        completes on the domain-scoped path alone.
        """
        query_text = (query_text or "").strip()
        if not query_text:
            return []
        # BM25 needs OR semantics: plainto_tsquery ANDs every lexeme, so a full
        # sentence matches no single rule. Build "a | b | c" from sanitized
        # tokens (the 'english' config drops stopwords); a sentinel keeps the
        # SQL uniform if nothing survives.
        import re as _re
        _toks = [t for t in _re.findall(r"[a-z0-9]+", query_text.lower()) if len(t) > 2]
        tsq = " | ".join(dict.fromkeys(_toks)) or "zzznomatchzzz"
        try:
            from src.services.database import get_db
            from sqlalchemy import text as sqla_text

            try:
                query_emb = await self.resolver._embed_query(query_text)
            except Exception:
                query_emb = None

            async with get_db() as session:
                if query_emb is not None:
                    # Include rows without an embedding too, as long as BM25
                    # matches — otherwise freshly-scraped rules whose Titan
                    # vector hasn't been backfilled yet (e.g. the Part 1010
                    # block) would be invisible to semantic retrieval. Embedded
                    # rows get the 0.7 cosine boost; unembedded ones fall back
                    # to the 0.3 BM25 term via COALESCE.
                    stmt = sqla_text("""
                        SELECT rule_id,
                                 0.7 * COALESCE(
                                     1 - (embedding <=> CAST(:vec AS vector)), 0
                                 )
                               + 0.3 * COALESCE(
                                     ts_rank(tsv, to_tsquery('english', :tsq)), 0
                                 ) AS score
                        FROM compliance_rules
                        WHERE is_active = TRUE
                          AND (embedding IS NOT NULL
                               OR tsv @@ to_tsquery('english', :tsq))
                        ORDER BY score DESC
                        LIMIT :k
                    """)
                    rows = (await session.execute(
                        stmt, {"vec": str(query_emb), "tsq": tsq, "k": top_k}
                    )).fetchall()
                    mode = "hybrid"
                else:
                    stmt = sqla_text("""
                        SELECT rule_id,
                               ts_rank(tsv, to_tsquery('english', :tsq)) AS score
                        FROM compliance_rules
                        WHERE is_active = TRUE
                          AND tsv @@ to_tsquery('english', :tsq)
                        ORDER BY score DESC
                        LIMIT :k
                    """)
                    rows = (await session.execute(
                        stmt, {"tsq": tsq, "k": top_k}
                    )).fetchall()
                    mode = "bm25_degraded"

            rule_ids = [r.rule_id for r in rows if r.rule_id]
            self.logger.info(
                "hybrid_rule_search",
                mode=mode,
                query_len=len(query_text),
                hits=len(rule_ids),
            )
            return rule_ids

        except Exception as exc:
            self.logger.warning("hybrid_rule_search_failed", error=str(exc))
            return []

    @staticmethod
    def _fallback_rag_context(discovery: Dict) -> List[Dict]:
        """Banking-knowledge context hints keyed off discovered patterns."""
        chunks: List[Dict] = []
        patterns = discovery.get("banking_patterns", {})

        if "wire_transfer" in patterns:
            chunks.append({
                "type": "banking_knowledge",
                "topic": "wire_transfer_compliance",
                "content": "BSA/AML requires CTR filing for cash transactions >$10,000. "
                           "OFAC screening required before wire release. "
                           "Travel Rule applies to transfers >$3,000.",
            })
        if "ofac" in patterns:
            chunks.append({
                "type": "banking_knowledge",
                "topic": "ofac_screening",
                "content": "OFAC SDN list must be checked before any funds transfer. "
                           "Fuzzy name matching required. Blocked transactions must be reported within 10 days.",
            })
        if "sar" in patterns or "bsa_aml" in patterns:
            chunks.append({
                "type": "banking_knowledge",
                "topic": "sar_filing",
                "content": "SAR required for suspicious activity >$5,000 (or $0 for insider abuse). "
                           "Filing deadline: 30 days from detection. No tipping off the subject.",
            })
        if "ctr" in patterns:
            chunks.append({
                "type": "banking_knowledge",
                "topic": "ctr_reporting",
                "content": "CTR required for cash transactions >$10,000. "
                           "Structuring detection for multiple related transactions. "
                           "Filed with FinCEN within 15 days.",
            })
        if "reg_e" in patterns:
            chunks.append({
                "type": "compliance_knowledge",
                "regulation": "Regulation_E",
                "content": "Consumer has 60 days to report unauthorized EFT. "
                           "Bank must provide provisional credit within 10 business days. "
                           "Investigation must complete within 45 days (90 for new accounts).",
            })
        if "limit" in patterns:
            chunks.append({
                "type": "banking_knowledge",
                "topic": "transaction_limits",
                "content": "Wire transfers >$10,000 require CTR filing. "
                           "SAR filing at $5,000 threshold for suspicious activity.",
            })
        if discovery.get("compliance_requirements"):
            chunks.append({
                "type": "compliance_knowledge",
                "regulation": "BSA_AML_KYC",
                "content": "BSA/AML compliance requires CIP, CDD, ongoing monitoring, "
                           "and SAR/CTR filing per FinCEN regulations.",
            })

        return chunks
    
    def _extract_requirements(self, story_text: str, discovery: Dict) -> List[Dict]:
        """Extract functional and non-functional requirements"""
        requirements = []
        
        patterns = discovery.get("banking_patterns", {})
        
        # Functional requirements
        if "transfer" in patterns or "wire_transfer" in patterns:
            requirements.append({
                "type": "functional",
                "requirement": "Transfer money between accounts",
                "acceptance_criteria": [
                    "Source account has sufficient balance",
                    "Beneficiary account is valid",
                    "OFAC screening passes before release",
                    "Transaction succeeds and balance updated",
                    "Audit trail recorded",
                ]
            })
        
        if "wire_transfer" in patterns:
            requirements.append({
                "type": "functional",
                "requirement": "Wire transfer compliance checks",
                "acceptance_criteria": [
                    "CTR filed for cash transactions > $10,000",
                    "Dual approval required for wires > $10,000",
                    "Travel Rule data included for transfers > $3,000",
                    "OFAC SDN screening before wire release",
                ]
            })
        
        if "limit" in patterns or "ctr" in patterns:
            requirements.append({
                "type": "functional",
                "requirement": "Validate transaction thresholds",
                "acceptance_criteria": [
                    "Per-transaction limit enforced",
                    "CTR $10,000 threshold detected",
                    "Structuring detection for related transactions",
                    "Reject if cumulative limit exceeded",
                ]
            })
        
        # Non-functional requirements (US compliance)
        if "wire_transfer" in patterns or "sar" in patterns or "bsa_aml" in patterns:
            requirements.append({
                "type": "compliance",
                "requirement": "BSA/AML wire transfer compliance",
                "acceptance_criteria": [
                    "CTR filing for cash transactions > $10,000",
                    "SAR filing for suspicious activity > $5,000",
                    "OFAC SDN screening before transfer",
                    "Audit trail maintained for 5 years",
                    "No tipping off SAR subjects",
                ]
            })
        
        if "ofac" in patterns:
            requirements.append({
                "type": "compliance",
                "requirement": "OFAC sanctions screening",
                "acceptance_criteria": [
                    "Real-time SDN list screening",
                    "Fuzzy name matching with configurable threshold",
                    "Blocked transactions reported within 10 days",
                    "False positive review workflow",
                ]
            })
        
        if "reg_e" in patterns:
            requirements.append({
                "type": "compliance",
                "requirement": "Regulation E error resolution",
                "acceptance_criteria": [
                    "60-day reporting window for consumers",
                    "10-business-day provisional credit",
                    "45-day investigation (90 for new accounts)",
                    "Written notice of investigation results",
                ]
            })
        
        if "section_1071" in patterns or "hmda" in patterns:
            requirements.append({
                "type": "compliance",
                "requirement": "CFPB Section 1071 / HMDA compliance",
                "acceptance_criteria": [
                    "Demographic data collection at application",
                    "Firewall between demographic data and underwriting",
                    "LAR reporting data completeness",
                ]
            })
        
        return requirements
    
    def _apply_banking_rules(self, patterns: Dict) -> Dict[str, Any]:
        """Apply relevant banking rules from ComplianceService"""
        applicable_rules = {}
        
        if "wire_transfer" in patterns or "transfer" in patterns:
            applicable_rules["wire_transfer"] = self.compliance_service.get_rule("wire_transfer_rules") or {
                "ctr_threshold": 10000,
                "dual_approval_threshold": 10000,
                "travel_rule_threshold": 3000,
                "currency": "USD",
                "ofac_screening_required": True,
            }
        
        if "sar" in patterns or "bsa_aml" in patterns:
            applicable_rules["sar"] = self.compliance_service.get_rule("sar_thresholds") or {
                "suspicious_threshold": 5000,
                "insider_threshold": 0,
                "filing_deadline_days": 30,
                "no_tipping_off": True,
            }
        
        if "ctr" in patterns:
            applicable_rules["ctr"] = self.compliance_service.get_rule("ctr_rules") or {
                "cash_threshold": 10000,
                "filing_deadline_days": 15,
                "structuring_detection": True,
            }
        
        if "ofac" in patterns:
            applicable_rules["ofac"] = self.compliance_service.get_rule("ofac_screening") or {
                "sdn_screening": True,
                "fuzzy_match_threshold": 85,
                "blocking_required": True,
                "reporting_deadline_days": 10,
            }
        
        if "kyc_cdd" in patterns:
            applicable_rules["kyc_cdd"] = self.compliance_service.get_rule("kyc_cdd_rules") or {
                "cip_required": True,
                "cdd_beneficial_ownership": True,
                "edd_high_risk": True,
            }
        
        if "reg_e" in patterns:
            applicable_rules["reg_e"] = self.compliance_service.get_rule("reg_e_rules") or {
                "reporting_window_days": 60,
                "provisional_credit_days": 10,
                "investigation_days": 45,
                "new_account_investigation_days": 90,
            }
        
        if "section_1071" in patterns or "hmda" in patterns:
            applicable_rules["section_1071"] = self.compliance_service.get_rule("cfpb_1071_rules") or {
                "demographic_collection": True,
                "firewall_required": True,
                "lar_reporting": True,
            }
        
        return applicable_rules
    
    def _build_compliance_context(self, compliance_requirements: List, banking_rules: Dict) -> Dict:
        """Build comprehensive compliance context"""
        context = {
            "regulations": [],
            "audit_requirements": [],
            "data_handling": []
        }
        
        # Audit requirements
        context["audit_requirements"] = [
            "Record all transactions with timestamp",
            "Store user ID, IP address, device info",
            "Maintain immutable audit log",
            "Retain BSA records for 5 years (31 CFR 1010.430)",
            "SOX compliance for financial reporting",
        ]
        
        # Data handling
        context["data_handling"] = [
            "Encrypt data in transit (TLS 1.3)",
            "Encrypt data at rest (AES-256)",
            "GLBA Safeguards Rule — protect consumer NPI",
            "Mask SSN/TIN in logs and UI (show last 4 only)",
            "PCI-DSS for card data",
        ]
        
        # Regulation-specific
        if "wire_transfer" in banking_rules:
            context["regulations"].append({
                "name": "BSA_AML_Wire_Transfer",
                "regulator": "FinCEN / OCC",
                "requirements": [
                    "$10,000 CTR filing threshold",
                    "OFAC SDN screening before release",
                    "Travel Rule for transfers > $3,000",
                    "Dual approval for wires > $10,000",
                ]
            })
        
        if "sar" in banking_rules:
            context["regulations"].append({
                "name": "SAR_Filing_Requirements",
                "regulator": "FinCEN",
                "requirements": [
                    "SAR for suspicious activity > $5,000",
                    "SAR for insider abuse at any amount",
                    "30-day filing deadline from detection",
                    "No tipping off the subject",
                ]
            })
        
        if "ofac" in banking_rules:
            context["regulations"].append({
                "name": "OFAC_Sanctions",
                "regulator": "OFAC / Treasury",
                "requirements": [
                    "Real-time SDN list screening",
                    "Fuzzy name matching (85% threshold)",
                    "Block and report within 10 days",
                ]
            })
        
        if "reg_e" in banking_rules:
            context["regulations"].append({
                "name": "Regulation_E",
                "regulator": "CFPB",
                "requirements": [
                    "60-day reporting window for consumers",
                    "10-business-day provisional credit",
                    "45-day investigation (90 for new accounts)",
                ]
            })
        
        if "section_1071" in banking_rules:
            context["regulations"].append({
                "name": "CFPB_Section_1071",
                "regulator": "CFPB",
                "requirements": [
                    "Collect demographic data at application",
                    "Firewall between demographics and underwriting",
                    "Annual LAR data submission",
                ]
            })
        
        return context
    
    def _outline_test_scenarios(
        self, 
        requirements: List, 
        banking_rules: Dict,
        compliance_context: Dict,
        rag_context: List
    ) -> List[Dict]:
        """Create high-level outline of test scenarios"""
        scenarios = []
        
        # Happy path
        scenarios.append({
            "category": "happy_path",
            "name": "successful_transaction",
            "priority": "P0",
            "covers": ["basic_functionality", "successful_flow"]
        })
        
        # Wire transfer / BSA-AML scenarios
        if "wire_transfer" in banking_rules:
            scenarios.extend([
                {
                    "category": "compliance",
                    "name": "wire_transfer_below_10k",
                    "priority": "P0",
                    "covers": ["wire_under_threshold", "no_ctr_required"]
                },
                {
                    "category": "compliance",
                    "name": "wire_transfer_above_10k_ctr",
                    "priority": "P0",
                    "covers": ["ctr_filing", "dual_approval"]
                },
                {
                    "category": "compliance",
                    "name": "wire_transfer_ofac_blocked",
                    "priority": "P0",
                    "covers": ["ofac_screening", "blocked_transaction"]
                },
                {
                    "category": "compliance",
                    "name": "wire_travel_rule_3000",
                    "priority": "P0",
                    "covers": ["travel_rule", "originator_info"]
                },
            ])
        
        # SAR scenarios
        if "sar" in banking_rules:
            scenarios.extend([
                {
                    "category": "compliance",
                    "name": "sar_suspicious_above_5k",
                    "priority": "P0",
                    "covers": ["sar_threshold", "sar_filing"]
                },
                {
                    "category": "compliance",
                    "name": "sar_no_tipping_off",
                    "priority": "P0",
                    "covers": ["sar_confidentiality", "no_disclosure"]
                },
            ])
        
        # OFAC scenarios
        if "ofac" in banking_rules:
            scenarios.extend([
                {
                    "category": "compliance",
                    "name": "ofac_sdn_exact_match",
                    "priority": "P0",
                    "covers": ["ofac_screening", "exact_match_block"]
                },
                {
                    "category": "compliance",
                    "name": "ofac_fuzzy_match_review",
                    "priority": "P0",
                    "covers": ["ofac_screening", "fuzzy_match", "manual_review"]
                },
            ])
        
        # Reg E scenarios
        if "reg_e" in banking_rules:
            scenarios.extend([
                {
                    "category": "compliance",
                    "name": "reg_e_provisional_credit",
                    "priority": "P0",
                    "covers": ["reg_e", "provisional_credit_10_days"]
                },
                {
                    "category": "compliance",
                    "name": "reg_e_late_dispute",
                    "priority": "P1",
                    "covers": ["reg_e", "60_day_window_expired"]
                },
            ])
        
        # Edge cases
        scenarios.extend([
            {
                "category": "edge_cases",
                "name": "boundary_values",
                "priority": "P1",
                "covers": ["min_amount", "max_amount", "zero_amount"]
            },
            {
                "category": "edge_cases",
                "name": "null_and_empty_values",
                "priority": "P1",
                "covers": ["null_handling", "input_validation"]
            }
        ])
        
        # Error handling
        scenarios.extend([
            {
                "category": "error_handling",
                "name": "insufficient_balance",
                "priority": "P0",
                "covers": ["balance_check", "error_messages"]
            },
            {
                "category": "error_handling",
                "name": "network_failure",
                "priority": "P1",
                "covers": ["timeout_handling", "retry_logic"]
            }
        ])
        
        # Use RAG context to add learned scenarios
        for rag_chunk in rag_context:
            if rag_chunk.get("type") == "past_story" and "learnings" in rag_chunk.get("content", "").lower():
                scenarios.append({
                    "category": "compliance",
                    "name": "rag_learned_scenario",
                    "priority": "P1",
                    "covers": ["rag_learning"],
                    "source": "rag_learning"
                })
        
        return scenarios
    
    def _identify_edge_cases(self, banking_rules: Dict, discovery: Dict) -> List[Dict]:
        """Identify edge cases based on banking rules"""
        edge_cases = []
        
        # Wire transfer boundary cases
        if "wire_transfer" in banking_rules:
            edge_cases.extend([
                {"type": "boundary", "case": "amount = $10,000", "description": "Exactly at CTR threshold"},
                {"type": "boundary", "case": "amount = $10,001", "description": "Just above CTR threshold"},
                {"type": "boundary", "case": "amount = $9,999", "description": "Just below CTR threshold"},
                {"type": "boundary", "case": "amount = $3,000", "description": "At Travel Rule threshold"},
                {"type": "boundary", "case": "amount = $0.01", "description": "Minimum possible amount"},
                {"type": "boundary", "case": "amount = $0", "description": "Zero amount (should fail)"},
                {"type": "boundary", "case": "amount = -$100", "description": "Negative amount (should fail)"},
            ])
        
        # SAR threshold cases
        if "sar" in banking_rules:
            edge_cases.extend([
                {"type": "boundary", "case": "suspicious amount = $5,000", "description": "Exactly at SAR threshold"},
                {"type": "boundary", "case": "suspicious amount = $4,999", "description": "Just below SAR threshold"},
                {"type": "boundary", "case": "insider amount = $0", "description": "Insider abuse — no minimum"},
            ])
        
        # OFAC fuzzy matching edge cases
        if "ofac" in banking_rules:
            edge_cases.extend([
                {"type": "matching", "case": "name = exact SDN match", "description": "Exact match — must block"},
                {"type": "matching", "case": "name = SDN spelling variant", "description": "Fuzzy match at 85% — review"},
                {"type": "matching", "case": "name = common name", "description": "Below threshold — false positive"},
            ])
        
        # Reg E timing edge cases
        if "reg_e" in banking_rules:
            edge_cases.extend([
                {"type": "timing", "case": "dispute at day 60", "description": "Last day of reporting window"},
                {"type": "timing", "case": "dispute at day 61", "description": "Past reporting window (reject)"},
                {"type": "timing", "case": "provisional credit day 10", "description": "Deadline for provisional credit"},
            ])
        
        # Structuring detection
        if "wire_transfer" in banking_rules or "ctr" in banking_rules:
            edge_cases.extend([
                {"type": "structuring", "case": "3 x $3,500 same day", "description": "Aggregate > $10,000 — structuring"},
                {"type": "structuring", "case": "$9,900 + $200 next day", "description": "Split across days — pattern"},
            ])
        
        return edge_cases
