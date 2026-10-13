import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from enum import Enum
from unittest.mock import Mock, MagicMock, patch
import uuid


# ============================================================================
# Domain Models
# ============================================================================

class TransactionStatus(Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    HELD_FOR_REVIEW = "held_for_review"
    REJECTED = "rejected"
    RELEASED = "released"


class OFACMatchType(Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    NO_MATCH = "no_match"


@dataclass
class Account:
    account_id: str
    balance: Decimal
    routing_number: str
    account_number: str
    customer_name: str
    status: str = "active"


@dataclass
class OFACScreeningResult:
    match_type: OFACMatchType
    match_score: int
    matched_entry: Optional[str]
    screening_timestamp: datetime


@dataclass
class TransactionRequest:
    transaction_id: str
    from_account: str
    to_account: str
    routing_number: str
    amount: Decimal
    customer_name: str
    user_id: str
    ip_address: str
    device_info: str
    timestamp: datetime


@dataclass
class TransactionResult:
    transaction_id: str
    status: TransactionStatus
    message: str
    balance_after: Optional[Decimal]
    audit_id: str
    timestamp: datetime
    blocking_report_id: Optional[str] = None
    review_id: Optional[str] = None


@dataclass
class AuditRecord:
    audit_id: str
    transaction_id: str
    user_id: str
    ip_address: str
    device_info: str
    action: str
    status: str
    timestamp: datetime
    encrypted: bool = True
    pii_masked: bool = True


@dataclass
class BlockingReport:
    report_id: str
    transaction_id: str
    customer_name: str
    matched_sdn_entry: str
    filing_deadline: datetime
    filed_at: Optional[datetime]
    status: str


@dataclass
class ComplianceReview:
    review_id: str
    transaction_id: str
    match_score: int
    reviewer_id: Optional[str]
    decision: Optional[str]
    decision_timestamp: Optional[datetime]
    false_positive_documented: bool = False


# ============================================================================
# Constants (U.S. Banking Compliance Thresholds)
# ============================================================================

CTR_THRESHOLD = Decimal("10000")
SAR_THRESHOLD = Decimal("5000")
TRAVEL_RULE_THRESHOLD = Decimal("3000")
WIRE_DUAL_APPROVAL_THRESHOLD = Decimal("10000")
OFAC_FUZZY_THRESHOLD = 85
BENEFICIAL_OWNERSHIP_PCT = 20
OFAC_BLOCKING_REPORT_DEADLINE_DAYS = 10
BSA_RECORD_RETENTION_YEARS = 5


# ============================================================================
# Service Interfaces (Mocked)
# ============================================================================

class OFACService:
    def screen_customer(self, customer_name: str) -> OFACScreeningResult:
        raise NotImplementedError

    def get_sdn_list_version(self) -> str:
        raise NotImplementedError


class RuleResolver:
    def get_ofac_fuzzy_threshold(self) -> int:
        return OFAC_FUZZY_THRESHOLD

    def get_blocking_report_deadline_days(self) -> int:
        return OFAC_BLOCKING_REPORT_DEADLINE_DAYS


class AuditLogger:
    def log_transaction(self, record: AuditRecord) -> str:
        raise NotImplementedError

    def get_audit_record(self, audit_id: str) -> Optional[AuditRecord]:
        raise NotImplementedError


class AccountRepository:
    def get_account(self, account_id: str) -> Optional[Account]:
        raise NotImplementedError

    def update_balance(self, account_id: str, new_balance: Decimal) -> bool:
        raise NotImplementedError

    def validate_routing_account(self, routing: str, account: str) -> bool:
        raise NotImplementedError


class ComplianceRepository:
    def file_blocking_report(self, report: BlockingReport) -> str:
        raise NotImplementedError

    def create_review(self, review: ComplianceReview) -> str:
        raise NotImplementedError

    def update_review_decision(
        self, review_id: str, decision: str, reviewer_id: str
    ) -> ComplianceReview:
        raise NotImplementedError

    def document_false_positive(self, review_id: str) -> bool:
        raise NotImplementedError


# ============================================================================
# Transaction Service (System Under Test)
# ============================================================================

class TransactionService:
    def __init__(
        self,
        ofac_service: OFACService,
        rule_resolver: RuleResolver,
        audit_logger: AuditLogger,
        account_repo: AccountRepository,
        compliance_repo: ComplianceRepository,
    ):
        self.ofac_service = ofac_service
        self.rule_resolver = rule_resolver
        self.audit_logger = audit_logger
        self.account_repo = account_repo
        self.compliance_repo = compliance_repo

    def process_transaction(self, request: TransactionRequest) -> TransactionResult:
        transaction_id = request.transaction_id
        timestamp = datetime.now(timezone.utc)

        # Validate amount
        if request.amount <= Decimal("0"):
            message = (
                "Amount must be greater than 0"
                if request.amount == Decimal("0")
                else "Invalid amount"
            )
            audit_id = self._log_audit(request, "rejected", message)
            return TransactionResult(
                transaction_id=transaction_id,
                status=TransactionStatus.REJECTED,
                message=message,
                balance_after=None,
                audit_id=audit_id,
                timestamp=timestamp,
            )

        # Validate beneficiary account
        if not self.account_repo.validate_routing_account(
            request.routing_number, request.to_account
        ):
            message = "Invalid beneficiary account"
            audit_id = self._log_audit(request, "rejected", message)
            return TransactionResult(
                transaction_id=transaction_id,
                status=TransactionStatus.REJECTED,
                message=message,
                balance_after=None,
                audit_id=audit_id,
                timestamp=timestamp,
            )

        # Get account and check balance
        account = self.account_repo.get_account(request.from_account)
        if not account:
            message = "Account not found"
            audit_id = self._log_audit(request, "rejected", message)
            return TransactionResult(
                transaction_id=transaction_id,
                status=TransactionStatus.REJECTED,
                message=message,
                balance_after=None,
                audit_id=audit_id,
                timestamp=timestamp,
            )

        if account.balance < request.amount:
            message = "Insufficient balance"
            audit_id = self._log_audit(request, "rejected", message)
            return TransactionResult(
                transaction_id=transaction_id,
                status=TransactionStatus.REJECTED,
                message=message,
                balance_after=account.balance,
                audit_id=audit_id,
                timestamp=timestamp,
            )

        # OFAC screening
        screening_result = self.ofac_service.screen_customer(request.customer_name)

        # Exact match - block transaction
        if screening_result.match_type == OFACMatchType.EXACT:
            audit_id = self._log_audit(request, "blocked", "OFAC exact match")
            blocking_report = self._file_blocking_report(
                transaction_id, request.customer_name, screening_result.matched_entry
            )
            return TransactionResult(
                transaction_id=transaction_id,
                status=TransactionStatus.BLOCKED,
                message="Transaction blocked due to OFAC match",
                balance_after=account.balance,
                audit_id=audit_id,
                timestamp=timestamp,
                blocking_report_id=blocking_report.report_id,
            )

        # Fuzzy match above threshold - hold for review
        fuzzy_threshold = self.rule_resolver.get_ofac_fuzzy_threshold()
        if (
            screening_result.match_type == OFACMatchType.FUZZY
            and screening_result.match_score >= fuzzy_threshold
        ):
            audit_id = self._log_audit(request, "held_for_review", "OFAC fuzzy match")
            review = self._create_compliance_review(
                transaction_id, screening_result.match_score
            )
            return TransactionResult(
                transaction_id=transaction_id,
                status=TransactionStatus.HELD_FOR_REVIEW,
                message="Transaction held for manual compliance review",
                balance_after=account.balance,
                audit_id=audit_id,
                timestamp=timestamp,
                review_id=review.review_id,
            )

        # Process successful transaction
        new_balance = account.balance - request.amount
        self.account_repo.update_balance(request.from_account, new_balance)
        audit_id = self._log_audit(request, "success", "Transaction completed")

        return TransactionResult(
            transaction_id=transaction_id,
            status=TransactionStatus.SUCCESS,
            message="Transaction completed successfully",
            balance_after=new_balance,
            audit_id=audit_id,
            timestamp=timestamp,
        )

    def release_transaction_after_review(
        self, review_id: str, reviewer_id: str, is_false_positive: bool
    ) -> TransactionResult:
        review = self.compliance_repo.update_review_decision(
            review_id, "approved", reviewer_id
        )
        if is_false_positive:
            self.compliance_repo.document_false_positive(review_id)

        timestamp = datetime.now(timezone.utc)
        audit_id = str(uuid.uuid4())

        return TransactionResult(
            transaction_id=review.transaction_id,
            status=TransactionStatus.RELEASED,
            message="Transaction released after compliance review",
            balance_after=None,
            audit_id=audit_id,
            timestamp=timestamp,
        )

    def _log_audit(
        self, request: TransactionRequest, status: str, message: str
    ) -> str:
        audit_record = AuditRecord(
            audit_id=str(uuid.uuid4()),
            transaction_id=request.transaction_id,
            user_id=request.user_id,
            ip_address=request.ip_address,
            device_info=request.device_info,
            action="transaction",
            status=status,
            timestamp=datetime.now(timezone.utc),
            encrypted=True,
            pii_masked=True,
        )
        return self.audit_logger.log_transaction(audit_record)

    def _file_blocking_report(
        self, transaction_id: str, customer_name: str, matched_entry: str
    ) -> BlockingReport:
        deadline_days = self.rule_resolver.get_blocking_report_deadline_days()
        report = BlockingReport(
            report_id=str(uuid.uuid4()),
            transaction_id=transaction_id,
            customer_name=customer_name,
            matched_sdn_entry=matched_entry,
            filing_deadline=datetime.now(timezone.utc)
            + timedelta(days=deadline_days),
            filed_at=datetime.now(timezone.utc),
            status="filed",
        )
        self.compliance_repo.file_blocking_report(report)
        return report

    def _create_compliance_review(
        self, transaction_id: str, match_score: int
    ) -> ComplianceReview:
        review = ComplianceReview(
            review_id=str(uuid.uuid4()),
            transaction_id=transaction_id,
            match_score=match_score,
            reviewer_id=None,
            decision=None,
            decision_timestamp=None,
            false_positive_documented=False,
        )
        self.compliance_repo.create_review(review)
        return review


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture
def mock_ofac_service() -> Mock:
    return Mock(spec=OFACService)


@pytest.fixture
def mock_rule_resolver() -> Mock:
    resolver = Mock(spec=RuleResolver)
    resolver.get_ofac_fuzzy_threshold.return_value = OFAC_FUZZY_THRESHOLD
    resolver.get_blocking_report_deadline_days.return_value = (
        OFAC_BLOCKING_REPORT_DEADLINE_DAYS
    )
    return resolver


@pytest.fixture
def mock_audit_logger() -> Mock:
    logger = Mock(spec=AuditLogger)
    logger.log_transaction.return_value = str(uuid.uuid4())
    return logger


@pytest.fixture
def mock_account_repo() -> Mock:
    return Mock(spec=AccountRepository)


@pytest.fixture
def mock_compliance_repo() -> Mock:
    return Mock(spec=ComplianceRepository)


@pytest.fixture
def transaction_service(
    mock_ofac_service: Mock,
    mock_rule_resolver: Mock,
    mock_audit_logger: Mock,
    mock_account_repo: Mock,
    mock_compliance_repo: Mock,
) -> TransactionService:
    return TransactionService(
        ofac_service=mock_ofac_service,
        rule_resolver=mock_rule_resolver,
        audit_logger=mock_audit_logger,
        account_repo=mock_account_repo,
        compliance_repo=mock_compliance_repo,
    )


@pytest.fixture
def valid_account() -> Account:
    return Account(
        account_id="ACC123456",
        balance=Decimal("10000.00"),
        routing_number="021000021",
        account_number="1234567890",
        customer_name="John Doe",
        status="active",
    )


@pytest.fixture
def valid_transaction_request(valid_account: Account) -> TransactionRequest:
    return TransactionRequest(
        transaction_id=str(uuid.uuid4()),
        from_account=valid_account.account_id,
        to_account="9876543210",
        routing_number="021000022",
        amount=Decimal("500.00"),
        customer_name=valid_account.customer_name,
        user_id="USER001",
        ip_address="192.168.1.100",
        device_info="Mozilla/5.0",
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================================
# Test Cases
# ============================================================================


class TestSuccessfulTransaction:
    def test_successful_transaction(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        mock_audit_logger: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Scenario: successful_transaction
        Given User has sufficient balance and valid account
        When User initiates valid transaction
        Then Transaction succeeds, balance updated, audit trail recorded
        """
        # Arrange
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
        mock_account_repo.update_balance.return_value = True
        mock_ofac_service.screen_customer.return_value = OFACScreeningResult(
            match_type=OFACMatchType.NO_MATCH,
            match_score=0,
            matched_entry=None,
            screening_timestamp=datetime.now(timezone.utc),
        )
        audit_id = str(uuid.uuid4())
        mock_audit_logger.log_transaction.return_value = audit_id

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.SUCCESS
        assert result.message == "Transaction completed successfully"
        assert result.balance_after == Decimal("9500.00")
        assert result.audit_id == audit_id
        mock_account_repo.update_balance.assert_called_once_with(
            valid_account.account_id, Decimal("9500.00")
        )
        mock_audit_logger.log_transaction.assert_called_once()
        audit_call_args = mock_audit_logger.log_transaction.call_args[0][0]
        assert audit_call_args.user_id == "USER001"
        assert audit_call_args.ip_address == "192.168.1.100"
        assert audit_call_args.encrypted is True
        assert audit_call_args.pii_masked is True


class TestOFACSDNExactMatchBlocked:
    def test_ofac_sdn_exact_match_blocked(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        mock_compliance_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Scenario: ofac_sdn_exact_match_blocked
        Given Customer name exactly matches OFAC SDN entry
        When Wire transfer or account opening attempted
        Then Transaction blocked, blocking report filed within 10 days
        """
        # Arrange
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
        mock_ofac_service.screen_customer.return_value = OFACScreeningResult(
            match_type=OFACMatchType.EXACT,
            match_score=100,
            matched_entry="JOHN DOE (SDN #12345)",
            screening_timestamp=datetime.now(timezone.utc),
        )

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.BLOCKED
        assert "OFAC match" in result.message
        assert result.blocking_report_id is not None
        assert result.balance_after == valid_account.balance
        mock_compliance_repo.file_blocking_report.assert_called_once()
        blocking_report = mock_compliance_repo.file_blocking_report.call_args[0][0]
        assert blocking_report.customer_name == valid_account.customer_name
        assert blocking_report.matched_sdn_entry == "JOHN DOE (SDN #12345)"
        assert blocking_report.status == "filed"
        deadline_delta = (
            blocking_report.filing_deadline - blocking_report.filed_at
        ).days
        assert deadline_delta == OFAC_BLOCKING_REPORT_DEADLINE_DAYS

    def test_ofac_exact_match_does_not_update_balance(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Verify that exact OFAC match does not deduct balance.
        """
        # Arrange
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
        mock_ofac_service.screen_customer.return_value = OFACScreeningResult(
            match_type=OFACMatchType.EXACT,
            match_score=100,
            matched_entry="JOHN DOE (SDN #12345)",
            screening_timestamp=datetime.now(timezone.utc),
        )

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        mock_account_repo.update_balance.assert_not_called()
        assert result.balance_after == valid_account.balance


class TestOFACFuzzyMatchReview:
    def test_ofac_fuzzy_match_review(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        mock_compliance_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Scenario: ofac_fuzzy_match_review
        Given Customer name is a close variant of SDN entry
        When Fuzzy match score >= 85%
        Then Transaction held for manual compliance review
        """
        # Arrange
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
        mock_ofac_service.screen_customer.return_value = OFACScreeningResult(
            match_type=OFACMatchType.FUZZY,
            match_score=85,
            matched_entry="Jon Doe (SDN #67890)",
            screening_timestamp=datetime.now(timezone.utc),
        )

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.HELD_FOR_REVIEW
        assert "manual compliance review" in result.message
        assert result.review_id is not None
        assert result.balance_after == valid_account.balance
        mock_compliance_repo.create_review.assert_called_once()
        review = mock_compliance_repo.create_review.call_args[0][0]
        assert review.match_score == 85
        assert review.decision is None
        assert review.reviewer_id is None

    def test_ofac_fuzzy_match_at_threshold_boundary(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        mock_compliance_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Boundary test: fuzzy match score exactly at 85% threshold.
        """
        # Arrange
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
        mock_ofac_service.screen_customer.return_value = OFACScreeningResult(
            match_type=OFACMatchType.FUZZY,
            match_score=OFAC_FUZZY_THRESHOLD,
            matched_entry="Jon Doe (SDN #67890)",
            screening_timestamp=datetime.now(timezone.utc),
        )

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.HELD_FOR_REVIEW
        mock_compliance_repo.create_review.assert_called_once()

    def test_ofac_fuzzy_match_below_threshold_passes(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        mock_compliance_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Boundary test: fuzzy match score below 85% threshold should pass.
        """
        # Arrange
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
        mock_ofac_service.screen_customer.return_value = OFACScreeningResult(
            match_type=OFACMatchType.FUZZY,
            match_score=84,
            matched_entry="Jon Doe (SDN #67890)",
            screening_timestamp=datetime.now(timezone.utc),
        )

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.SUCCESS
        mock_compliance_repo.create_review.assert_not_called()


class TestOFACFalsePositiveRelease:
    def test_ofac_false_positive_release(
        self,
        transaction_service: TransactionService,
        mock_compliance_repo: Mock,
    ):
        """
        Scenario: ofac_false_positive_release
        Given Compliance officer reviews fuzzy match
        When Determination is false positive
        Then Transaction released, false positive documented
        """
        # Arrange
        review_id = str(uuid.uuid4())
        transaction_id = str(uuid.uuid4())
        reviewer_id = "REVIEWER001"
        mock_compliance_repo.update_review_decision.return_value = ComplianceReview(
            review_id=review_id,
            transaction_id=transaction_id,
            match_score=87,
            reviewer_id=reviewer_id,
            decision="approved",
            decision_timestamp=datetime.now(timezone.utc),
            false_positive_documented=False,
        )
        mock_compliance_repo.document_false_positive.return_value = True

        # Act
        result = transaction_service.release_transaction_after_review(
            review_id=review_id, reviewer_id=reviewer_id, is_false_positive=True
        )

        # Assert
        assert result.status == TransactionStatus.RELEASED
        assert "released after compliance review" in result.message
        mock_compliance_repo.update_review_decision.assert_called_once_with(
            review_id, "approved", reviewer_id
        )
        mock_compliance_repo.document_false_positive.assert_called_once_with(review_id)

    def test_ofac_review_release_without_false_positive(
        self,
        transaction_service: TransactionService,
        mock_compliance_repo: Mock,
    ):
        """
        Test release after review when it's not a false positive.
        """
        # Arrange
        review_id = str(uuid.uuid4())
        transaction_id = str(uuid.uuid4())
        reviewer_id = "REVIEWER002"
        mock_compliance_repo.update_review_decision.return_value = ComplianceReview(
            review_id=review_id,
            transaction_id=transaction_id,
            match_score=86,
            reviewer_id=reviewer_id,
            decision="approved",
            decision_timestamp=datetime.now(timezone.utc),
            false_positive_documented=False,
        )

        # Act
        result = transaction_service.release_transaction_after_review(
            review_id=review_id, reviewer_id=reviewer_id, is_false_positive=False
        )

        # Assert
        assert result.status == TransactionStatus.RELEASED
        mock_compliance_repo.document_false_positive.assert_not_called()


class TestZeroAmountTransfer:
    def test_zero_amount_transfer(
        self,
        transaction_service: TransactionService,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Scenario: zero_amount_transfer
        Given User initiates transfer
        When User enters amount $0
        Then Transaction rejected 'Amount must be greater than 0'
        """
        # Arrange
        valid_transaction_request.amount = Decimal("0")

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.REJECTED
        assert result.message == "Amount must be greater than 0"
        assert result.balance_after is None


class TestNegativeAmountTransfer:
    def test_negative_amount_transfer(
        self,
        transaction_service: TransactionService,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Scenario: negative_amount_transfer
        Given User initiates transfer
        When User enters amount -$100
        Then Transaction rejected 'Invalid amount'
        """
        # Arrange
        valid_transaction_request.amount = Decimal("-100")

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.REJECTED
        assert result.message == "Invalid amount"
        assert result.balance_after is None

    def test_negative_amount_large_value(
        self,
        transaction_service: TransactionService,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Boundary test: large negative amount.
        """
        # Arrange
        valid_transaction_request.amount = Decimal("-999999.99")

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.REJECTED
        assert result.message == "Invalid amount"


class TestInsufficientBalance:
    def test_insufficient_balance(
        self,
        transaction_service: TransactionService,
        mock_account_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Scenario: insufficient_balance
        Given User has balance $1,000
        When User transfers $5,000
        Then Transaction rejected 'Insufficient balance'
        """
        # Arrange
        valid_account.balance = Decimal("1000.00")
        valid_transaction_request.amount = Decimal("5000.00")
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.REJECTED
        assert result.message == "Insufficient balance"
        assert result.balance_after == Decimal("1000.00")
        mock_account_repo.update_balance.assert_not_called()

    def test_insufficient_balance_exact_boundary(
        self,
        transaction_service: TransactionService,
        mock_account_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Boundary test: transfer amount exactly equals balance + 0.01.
        """
        # Arrange
        valid_account.balance = Decimal("1000.00")
        valid_transaction_request.amount = Decimal("1000.01")
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True

        # Act
        result = transaction_service.process_transaction(valid_transaction_request)

        # Assert
        assert result.status == TransactionStatus.REJECTED
        assert result.message == "Insufficient balance"

    def test_sufficient_balance_exact_match(
        self,
        transaction_service: TransactionService,
        mock_ofac_service: Mock,
        mock_account_repo: Mock,
        valid_account: Account,
        valid_transaction_request: TransactionRequest,
    ):
        """
        Boundary test: transfer amount exactly equals balance (should succeed).
        """
        # Arrange
        valid_account.balance = Decimal("1000.00")
        valid_transaction_request.amount = Decimal("1000.00")
        mock_account_repo.get_account.return_value = valid_account
        mock_account_repo.validate_routing_account.return_value = True
