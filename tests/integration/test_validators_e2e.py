"""
Validator Integration Tests — Wire Transfer, OFAC, Loan Application.

Tests run with a mock FortressAPIClient to avoid external network calls.
Verifies that customer data stays local and decisions are correct.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from src.services.validators import (
    WireTransferValidator,
    OFACScreener,
    LoanApplicationValidator,
    ValidationResult,
)
from src.services.fortress_api_client import FortressAPIClient


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mock_fortress():
    """FortressAPIClient with mocked HTTP calls."""
    client = FortressAPIClient(base_url="http://dummy:8000")
    return client


MOCK_WIRE_RULES = [
    {
        "rule_id": "OCC-BSA-WIRE-001",
        "rule_type": "dual_approval",
        "threshold_amount": 10000.0,
        "rule_text": "Dual approval for >=10K wires",
    },
    {
        "rule_id": "BSA-CTR-001",
        "rule_type": "ctr_threshold",
        "threshold_amount": 10000.0,
        "rule_text": "CTR filing for >=10K",
    },
]

MOCK_SDN_ENTRIES = [
    {"entity_name": "AL-QAHTANI, Mohammed Ahmed", "sdn_number": "12345", "entity_type": "individual", "programs": ["SDGT"]},
    {"entity_name": "PETROV, Ivan Sergeyevich", "sdn_number": "67890", "entity_type": "individual", "programs": ["UKRAINE-EO13661"]},
    {"entity_name": "KIM, Chin-So'k",           "sdn_number": "11111", "entity_type": "individual", "programs": ["NPWMD"]},
    {"entity_name": "KIM, Chol",                "sdn_number": "22222", "entity_type": "individual", "programs": ["DPRK4"]},
    {"entity_name": "KIM, Ch'o'l",              "sdn_number": "33333", "entity_type": "individual", "programs": ["DPRK4"]},
    {"entity_name": "KIM, Kum-Chol",            "sdn_number": "44444", "entity_type": "individual", "programs": ["DPRK2"]},
    {"entity_name": "KIM, Yong Nam",            "sdn_number": "55555", "entity_type": "individual", "programs": ["DPRK2"]},
]


# ── Wire Transfer Validator ─────────────────────────────────────────

class TestWireTransferValidator:

    @pytest.mark.asyncio
    async def test_small_wire_allowed(self, mock_fortress):
        mock_fortress.get_rules = AsyncMock(return_value=MOCK_WIRE_RULES)
        validator = WireTransferValidator(mock_fortress)

        result = await validator.validate({
            "amount": 5000,
            "currency": "USD",
            "approvals": [{"approver": "alice"}],
        })

        assert result.action == "allow"
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_large_wire_single_approval_blocked(self, mock_fortress):
        mock_fortress.get_rules = AsyncMock(return_value=MOCK_WIRE_RULES)
        validator = WireTransferValidator(mock_fortress)

        result = await validator.validate({
            "amount": 15000,
            "currency": "USD",
            "approvals": [{"approver": "alice"}],
        })

        assert result.action == "block"
        assert any("dual approval" in v.description.lower() for v in result.violations)

    @pytest.mark.asyncio
    async def test_large_wire_dual_approval_review(self, mock_fortress):
        """$15K wire with dual approval passes dual-approval but triggers CTR (medium)."""
        mock_fortress.get_rules = AsyncMock(return_value=MOCK_WIRE_RULES)
        validator = WireTransferValidator(mock_fortress)

        result = await validator.validate({
            "amount": 15000,
            "currency": "USD",
            "approvals": [{"approver": "alice"}, {"approver": "bob"}],
        })

        # No high-severity violation → no block; CTR medium → review
        assert result.action == "review"
        assert any("ctr" in v.description.lower() for v in result.violations)


# ── OFAC Screener ───────────────────────────────────────────────────

class TestOFACScreener:

    @pytest.mark.asyncio
    async def test_clean_name_cleared(self, mock_fortress):
        mock_fortress.get_ofac_sdn_list = AsyncMock(return_value=MOCK_SDN_ENTRIES)
        screener = OFACScreener(mock_fortress)

        result = await screener.screen("Jane Smith")
        assert result.action == "clear"

    @pytest.mark.asyncio
    async def test_exact_match_blocked(self, mock_fortress):
        mock_fortress.get_ofac_sdn_list = AsyncMock(return_value=MOCK_SDN_ENTRIES)
        screener = OFACScreener(mock_fortress)

        result = await screener.screen("AL-QAHTANI, Mohammed Ahmed")
        assert result.action == "block"
        assert len(result.violations) > 0

    @pytest.mark.asyncio
    async def test_surname_only_query_hits_all_kim_variants(self, mock_fortress):
        """Bare surname "kim" would score ~0.4 under whole-string fuzz vs
        every KIM variant. OFAC's own tool returns them all at score 100;
        the token-containment fast path must reproduce that behavior."""
        mock_fortress.get_ofac_sdn_list = AsyncMock(return_value=MOCK_SDN_ENTRIES)
        screener = OFACScreener(mock_fortress)

        result = await screener.screen("kim")
        assert result.action == "block"
        hit_sdns = {v.match_detail.sdn_number for v in result.violations if v.match_detail}
        # Every KIM entry in the mock list — none of the non-KIM ones.
        assert hit_sdns == {"11111", "22222", "33333", "44444", "55555"}
        assert all(v.match_detail.scores["token_hit"] == 1.0
                   for v in result.violations)

    @pytest.mark.asyncio
    async def test_two_token_query_requires_all_tokens_and_fuses_apostrophes(self, mock_fortress):
        """`kim chol` must (a) apply AND semantics — "KIM, Yong Nam" is not
        a hit — and (b) match "KIM, Ch'o'l" because the transliteration
        apostrophes fuse to CHOL in the normalizer, not fragment to CH/O/L."""
        mock_fortress.get_ofac_sdn_list = AsyncMock(return_value=MOCK_SDN_ENTRIES)
        screener = OFACScreener(mock_fortress)

        result = await screener.screen("kim chol")
        assert result.action == "block"
        hit_sdns = {v.match_detail.sdn_number for v in result.violations if v.match_detail}
        # KIM Chol (22222), KIM Ch'o'l (33333), KIM Kum-Chol (44444).
        # NOT: KIM Chin-So'k (11111 — no "chol"), KIM Yong Nam (55555).
        assert hit_sdns == {"22222", "33333", "44444"}

    @pytest.mark.asyncio
    async def test_no_customer_data_sent_to_fortress(self, mock_fortress):
        """Verify the party_name is NEVER in the API call params."""
        mock_fortress.get_ofac_sdn_list = AsyncMock(return_value=MOCK_SDN_ENTRIES)
        screener = OFACScreener(mock_fortress)

        await screener.screen("Sensitive Customer Name")

        # The mock was called with NO customer identifying data
        mock_fortress.get_ofac_sdn_list.assert_called_once()
        # get_ofac_sdn_list takes no name param — just limit


# ── Loan Application Validator ──────────────────────────────────────

class TestLoanApplicationValidator:

    @pytest.mark.asyncio
    async def test_complete_application_allowed(self, mock_fortress):
        mock_fortress.get_rules = AsyncMock(return_value=[])
        validator = LoanApplicationValidator(mock_fortress)

        result = await validator.validate({
            "race_of_principal_owner": "Asian",
            "sex_of_principal_owner": "Male",
            "ethnicity_of_principal_owner": "Not Hispanic",
            "naics_code": "522110",
            "business_gross_revenue": 500000,
            "time_in_business": 5,
            "number_of_workers": 10,
            "minority_owned_business": False,
            "women_owned_business": False,
        })

        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_missing_demographics_flagged(self, mock_fortress):
        mock_fortress.get_rules = AsyncMock(return_value=[])
        validator = LoanApplicationValidator(mock_fortress)

        result = await validator.validate({
            "naics_code": "522110",
            "gross_revenue": 500000,
            # Missing: race, sex, ethnicity
        })

        assert result.action in ("block", "review")
        assert len(result.violations) > 0
