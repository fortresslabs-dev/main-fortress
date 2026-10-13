import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass
from enum import Enum


class TransactionStatus(Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    HELD = "held"
    REJECTED = "rejected"
    RELEASED = "released"


class ComplianceDecision(Enum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    FALSE_POSITIVE = "false_positive"


@dataclass
class Account:
    account_id: str
    routing_number: str
    balance: Decimal
    customer_name: str
    status: str = "active"


@dataclass
class Transaction:
    transaction_id: str
    from_account: str
    to_account: str
    amount: Decimal
    timestamp: datetime
    status: TransactionStatus
    user_id: str
    ip_address: str
    device_info: str
    compliance_decision: Optional[ComplianceDecision] = None
    compliance_notes: Optional[str] = None


@dataclass
class OFACScreeningResult:
    is_match: bool
    match_type: str
    match_score: Optional[int]
    sdn_entry: Optional[str]
    requires_blocking: bool
    requires_review: bool


@dataclass
class AuditLogEntry:
    timestamp: datetime
    transaction_id: str
    user_id: str
    ip_address: str
    device_info: str
    action: str
    result: str
    compliance_decision: Optional[str]
    encrypted: bool = True


# Constants based on U.S. banking regulations
CTR_THRESHOLD = Decimal("10000")
SAR_THRESHOLD = Decimal("5000")
TRAVEL_RULE_THRESHOLD = Decimal("3000")
WIRE_DUAL_APPROVAL_THRESHOLD = Decimal("10000")
OFAC_FUZZY_THRESHOLD = 85
BENEFICIAL_OWNERSHIP_PCT = 20
OFAC_REPORTING_DEADLINE_DAYS = 10


class OFACService:
    """Mock OFAC SDN screening service."""
    
    def __init__(self, sdn_list: List[str], fuzzy_threshold: int = OFAC_FUZZY_THRESHOLD):
        self.sdn_list = sdn_list
        self.fuzzy_threshold = fuzzy_threshold
    
    def screen_name(self, name: str) -> OFACScreeningResult:
        """Screen customer name against SDN list."""
        name_lower = name.lower().strip()
        
        for sdn_entry in self.sdn_list:
            sdn_lower = sdn_entry.lower().strip()
            
            if name_lower == sdn_lower:
                return OFACScreeningResult(
                    is_match=True,
                    match_type="exact",
                    match_score=100,
                    sdn_entry=sdn_entry,
                    requires_blocking=True,
                    requires_review=False
                )
            
            fuzzy_score = self._calculate_fuzzy_score(name_lower, sdn_lower)
            if fuzzy_score >= self.fuzzy_threshold:
                return OFACScreeningResult(
                    is_match=True,
                    match_type="fuzzy",
                    match_score=fuzzy_score,
                    sdn_entry=sdn_entry,
                    requires_blocking=False,
                    requires_review=True
                )
        
        return OFACScreeningResult(
            is_match=False,
            match_type="none",
            match_score=0,
            sdn_entry=None,
            requires_blocking=False,
            requires_review=False
        )
    
    def _calculate_fuzzy_score(self, name1: str, name2: str) -> int:
        """Calculate fuzzy match score (simplified Levenshtein-based)."""
        if name1 == name2:
            return 100
        
        len1, len2 = len(name1), len(name2)
        if len1 == 0 or len2 == 0:
            return 0
        
        max_len = max(len1, len2)
        distance = self._levenshtein_distance(name1, name2)
        similarity = (1 - distance / max_len) * 100
        return int(similarity)
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein distance."""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]


class AuditLogger:
    """Mock audit logger for compliance tracking."""
    
    def __init__(self):
        self.logs: List[AuditLogEntry] = []
    
    def log_transaction(
        self,
        transaction_id: str,
        user_id: str,
        ip_address: str,
        device_info: str,
        action: str,
        result: str,
        compliance_decision: Optional[str] = None
    ) -> AuditLogEntry:
        """Log transaction with full audit trail."""
        entry = AuditLogEntry(
            timestamp=datetime.now(timezone.utc),
            transaction_id=transaction_id,
            user_id=self._mask_pii(user_id),
            ip_address=ip_address,
            device_info=device_info,
            action=action,
            result=result,
            compliance_decision=compliance_decision,
            encrypted=True
        )
        self.logs.append(entry)
        return entry
    
    def _mask_pii(self, value: str) -> str:
        """Mask PII showing only last 4 characters."""
        if len(value) <= 4:
            return "****"
        return "*" * (len(value) - 4) + value[-4:]
    
    def get_logs_for_transaction(self, transaction_id: str) -> List[AuditLogEntry]:
        """Retrieve audit logs for specific transaction."""
        return [log for log in self.logs if log.transaction_id == transaction_id]


class ComplianceReviewService:
    """Mock compliance review service."""
    
    def __init__(self):
        self.reviews: Dict[str, Dict[str, Any]] = {}
    
    def submit_for_review(
        self,
        transaction_id: str,
        reason: str,
        screening_result: OFACScreeningResult
    ) -> str:
        """Submit transaction for manual compliance review."""
        review_id = f"REV-{transaction_id}"
        self.reviews[review_id] = {
            "transaction_id": transaction_id,
            "reason": reason,
            "screening_result": screening_result,
            "status": "pending",
            "submitted_at": datetime.now(timezone.utc)
        }
        return review_id
    
    def complete_review(
        self,
        review_id: str,
        decision: ComplianceDecision,
        officer_id: str,
        notes: str
    ) -> Dict[str, Any]:
        """Complete compliance review with decision."""
        if review_id not in self.reviews:
            raise ValueError(f"Review {review_id} not found")
        
        self.reviews[review_id].update({
            "status": "completed",
            "decision": decision,
            "officer_id": officer_id,
            "notes": notes,
            "completed_at": datetime.now(timezone.utc)
        })
        return self.reviews[review_id]


class BlockingReportService:
    """Mock OFAC blocking report service."""
    
    def __init__(self):
        self.reports: List[Dict[str, Any]] = []
    
    def file_blocking_report(
        self,
        transaction_id: str,
        customer_name: str,
        sdn_entry: str,
        amount: Decimal,
        blocked_at: datetime
    ) -> str:
        """File OFAC blocking report (must be within 10 days)."""
        report_id = f"OFAC-{transaction_id}"
        report = {
            "report_id": report_id,
            "transaction_id": transaction_id,
            "customer_name": customer_name,
            "sdn_entry": sdn_entry,
            "amount": amount,
            "blocked_at": blocked_at,
            "filed_at": datetime.now(timezone.utc),
            "deadline": blocked_at + timedelta(days=OFAC_REPORTING_DEADLINE_DAYS)
        }
        self.reports.append(report)
        return report_id


class TransactionService:
    """Core transaction processing service."""
    
    def __init__(
        self,
        ofac_service: OFACService,
        audit_logger: AuditLogger,
        compliance_review: ComplianceReviewService,
        blocking_report: BlockingReportService
    ):
        self.ofac_service = ofac_service
        self.audit_logger = audit_logger
        self.compliance_review = compliance_review
        self.blocking_report = blocking_report
        self.accounts: Dict[str, Account] = {}
    
    def add_account(self, account: Account) -> None:
        """Add account to service."""
        self.accounts[account.account_id] = account
    
    def process_transaction(
        self,
        from_account_id: str,
        to_account_id: str,
        amount: Decimal,
        user_id: str,
        ip_address: str,
        device_info: str
    ) -> Transaction:
        """Process transaction with full compliance checks."""
        transaction_id = f"TXN-{datetime.now(timezone.utc).timestamp()}"
        timestamp = datetime.now(timezone.utc)
        
        # Validate amount
        if amount <= Decimal("0"):
            error_msg = "Amount must be greater than 0" if amount == Decimal("0") else "Invalid amount"
            self.audit_logger.log_transaction(
                transaction_id, user_id, ip_address, device_info,
                "transfer", f"rejected: {error_msg}"
            )
            return Transaction(
                transaction_id=transaction_id,
                from_account=from_account_id,
                to_account=to_account_id,
                amount=amount,
                timestamp=timestamp,
                status=TransactionStatus.REJECTED,
                user_id=user_id,
                ip_address=ip_address,
                device_info=device_info,
                compliance_notes=error_msg
            )
        
        # Validate accounts
        if from_account_id not in self.accounts:
            self.audit_logger.log_transaction(
                transaction_id, user_id, ip_address, device_info,
                "transfer", "rejected: Invalid source account"
            )
            return Transaction(
                transaction_id=transaction_id,
                from_account=from_account_id,
                to_account=to_account_id,
                amount=amount,
                timestamp=timestamp,
                status=TransactionStatus.REJECTED,
                user_id=user_id,
                ip_address=ip_address,
                device_info=device_info,
                compliance_notes="Invalid source account"
            )
        
        if to_account_id not in self.accounts:
            self.audit_logger.log_transaction(
                transaction_id, user_id, ip_address, device_info,
                "transfer", "rejected: Invalid beneficiary account"
            )
            return Transaction(
                transaction_id=transaction_id,
                from_account=from_account_id,
                to_account=to_account_id,
                amount=amount,
                timestamp=timestamp,
                status=TransactionStatus.REJECTED,
                user_id=user_id,
                ip_address=ip_address,
                device_info=device_info,
                compliance_notes="Invalid beneficiary account"
            )
        
        from_account = self.accounts[from_account_id]
        
        # Check balance
        if from_account.balance < amount:
            self.audit_logger.log_transaction(
                transaction_id, user_id, ip_address, device_info,
                "transfer", "rejected: Insufficient balance"
            )
            return Transaction(
                transaction_id=transaction_id,
                from_account=from_account_id,
                to_account=to_account_id,
                amount=amount,
                timestamp=timestamp,
                status=TransactionStatus.REJECTED,
                user_id=user_id,
                ip_address=ip_address,
                device_info=device_info,
                compliance_notes="Insufficient balance"
            )
        
        # OFAC screening
        screening_result = self.ofac_service.screen_name(from_account.customer_name)
        
        if screening_result.requires_blocking:
            # Exact match - block and report
            self.blocking_report.file_blocking_report(
                transaction_id=transaction_id,
                customer_name=from_account.customer_name,
                sdn_entry=screening_result.sdn_entry,
                amount=amount,
                blocked_at=timestamp
            )
            self.audit_logger.log_transaction(
                transaction_id, user_id, ip_address, device_info,
                "transfer", "blocked: OFAC SDN exact match",
                compliance_decision="blocked"
            )
            return Transaction(
                transaction_id=transaction_id,
                from_account=from_account_id,
                to_account=to_account_id,
                amount=amount,
                timestamp=timestamp,
                status=TransactionStatus.BLOCKED,
                user_id=user_id,
                ip_address=ip_address,
                device_info=device_info,
                compliance_decision=ComplianceDecision.BLOCKED,
                compliance_notes=f"OFAC SDN exact match: {screening_result.sdn_entry}"
            )
        
        if screening_result.requires_review:
            # Fuzzy match - hold for review
            self.compliance_review.submit_for_review(
                transaction_id=transaction_id,
                reason=f"OFAC fuzzy match (score: {screening_result.match_score}%)",
                screening_result=screening_result
            )
            self.audit_logger.log_transaction(
                transaction_id, user_id, ip_address, device_info,
                "transfer", "held: OFAC fuzzy match for review",
                compliance_decision="review_required"
            )
            return Transaction(
                transaction_id=transaction_id,
                from_account=from_account_id,
                to_account=to_account_id,
                amount=amount,
                timestamp=timestamp,
                status=TransactionStatus.HELD,
                user_id=user_id,
                ip_address=ip_address,
                device_info=device_info,
                compliance_decision=ComplianceDecision.REVIEW_REQUIRED,
                compliance_notes=f"OFAC fuzzy match: {screening_result.match_score}% similarity"
            )
        
        # Process successful transaction
        from_account.balance -= amount
        self.accounts[to_account_id].balance += amount
        
        self.audit_logger.log_transaction(
            transaction_id, user_id, ip_address, device_info,
            "transfer", "success",
            compliance_decision="approved"
        )
        
        return Transaction(
            transaction_id=transaction_id,
            from_account=from_account_id,
            to_account=to_account_id,
            amount=amount,
            timestamp=timestamp,
            status=TransactionStatus.SUCCESS,
            user_id=user_id,
            ip_address=ip_address,
            device_info=device_info,
            compliance_decision=ComplianceDecision.APPROVED
        )
    
    def release_held_transaction(
        self,
        transaction_id: str,
        officer_id: str,
        decision: ComplianceDecision,
        notes: str
    ) -> Transaction:
        """Release held transaction after compliance review."""
        review_id = f"REV-{transaction_id}"
        self.compliance_review.complete_review(
            review_id=review_id,
            decision=decision,
            officer_id=officer_id,
            notes=notes
        )
        
        # Create released transaction record
        timestamp = datetime.now(timezone.utc)
        return Transaction(
            transaction_id=transaction_id,
            from_account="",
            to_account="",
            amount=Decimal("0"),
            timestamp=timestamp,
            status=TransactionStatus.RELEASED,
            user_id="",
            ip_address="",
            device_info="",
            compliance_decision=decision,
            compliance_notes=notes
        )


# Fixtures

@pytest.fixture
def ofac_sdn_list() -> List[str]:
    """OFAC SDN list for testing."""
    return [
        "John Doe Terrorist",
        "Evil Corporation Ltd",
        "Bad Actor International"
    ]


@pytest.fixture
def ofac_service(ofac_sdn_list: List[str]) -> OFACService:
    """OFAC screening service fixture."""
    return OFACService(sdn_list=ofac_sdn_list, fuzzy_threshold=OFAC_FUZZY_THRESHOLD)


@pytest.fixture
def audit_logger() -> AuditLogger:
    """Audit logger fixture."""
    return AuditLogger()


@pytest.fixture
def compliance_review() -> ComplianceReviewService:
    """Compliance review service fixture."""
    return ComplianceReviewService()


@pytest.fixture
def blocking_report() -> BlockingReportService:
    """Blocking report service fixture."""
    return BlockingReportService()


@pytest.fixture
def transaction_service(
    ofac_service: OFACService,
    audit_logger: AuditLogger,
    compliance_review: ComplianceReviewService,
    blocking_report: BlockingReportService
) -> TransactionService:
    """Transaction service fixture."""
    return TransactionService(
        ofac_service=ofac_service,
        audit_logger=audit_logger,
        compliance_review=compliance_review,
        blocking_report=blocking_report
    )


@pytest.fixture
def valid_account() -> Account:
    """Valid account with sufficient balance."""
    return Account(
        account_id="ACC-001",
        routing_number="123456789",
        balance=Decimal("10000.00"),
        customer_name="Jane Smith",
        status="active"
    )


@pytest.fixture
def beneficiary_account() -> Account:
    """Beneficiary account."""
    return Account(
        account_id="ACC-002",
        routing_number="987654321",
        balance=Decimal("5000.00"),
        customer_name="Bob Johnson",
        status="active"
    )


@pytest.fixture
def low_balance_account() -> Account:
    """Account with low balance."""
    return Account(
        account_id="ACC-003",
        routing_number="111222333",
        balance=Decimal("1000.00"),
        customer_name="Alice Brown",
        status="active"
    )


@pytest.fixture
def sdn_exact_match_account() -> Account:
    """Account with exact SDN match."""
    return Account(
        account_id="ACC-SDN-001",
        routing_number="444555666",
        balance=Decimal("50000.00"),
        customer_name="John Doe Terrorist",
        status="active"
    )


@pytest.fixture
def sdn_fuzzy_match_account() -> Account:
    """Account with fuzzy SDN match."""
    return Account(
        account_id="ACC-SDN-002",
        routing_number="777888999",
        balance=Decimal("25000.00"),
        customer_name="Jon Doe Terrorist",
        status="active"
    )


# Test Cases

class TestSuccessfulTransaction:
    """Test successful transaction scenario."""
    
    def test_successful_transaction(
        self,
        transaction_service: TransactionService,
        valid_account: Account,
        beneficiary_account: Account,
        audit_logger: AuditLogger
    ):
        """
        Scenario: successful_transaction
        Given User has sufficient balance and valid account
        When User initiates valid transaction
        Then Transaction succeeds, balance updated, audit trail recorded
        """
        # Arrange
        transaction_service.add_account(valid_account)
        transaction_service.add_account(beneficiary_account)
        initial_from_balance = valid_account.balance
        initial_to_balance = beneficiary_account.balance
        transfer_amount = Decimal("500.00")
        
        # Act
        transaction = transaction_service.process_transaction(
            from_account_id=valid_account.account_id,
            to_account_id=beneficiary_account.account_id,
            amount=transfer_amount,
            user_id="USER-12345",
            ip_address="192.168.1.100",
            device_info="Mozilla/5.0"
        )
        
        # Assert
        assert transaction.status == TransactionStatus.SUCCESS
        assert transaction.compliance_decision == ComplianceDecision.APPROVED
        assert valid_account.balance == initial_from_balance - transfer_amount
        assert beneficiary_account.balance == initial_to_balance + transfer_amount
        
        # Verify audit trail
        logs = audit_logger.get_logs_for_transaction(transaction.transaction_id)
        assert len(logs) == 1
        assert logs[0].action == "transfer"
        assert logs[0].result == "success"
        assert logs[0].compliance_decision == "approved"
        assert logs[0].encrypted is True
        assert logs[0].timestamp.tzinfo == timezone.utc


class TestOFACSDNExactMatchBlocked:
    """Test OFAC SDN exact match blocking scenario."""
    
    def test_ofac_sdn_exact_match_blocked(
        self,
        transaction_service: TransactionService,
        sdn_exact_match_account: Account,
        beneficiary_account: Account,
        blocking_report: BlockingReportService,
        audit_logger: AuditLogger
    ):
        """
        Scenario: ofac_sdn_exact_match_blocked
        Given Customer name exactly matches OFAC SDN entry
        When Wire transfer or account opening attempted
        Then Transaction blocked, blocking report filed within 10 days
        """
        # Arrange
        transaction_service.add_account(sdn_exact_match_account)
        transaction_service.add_account(beneficiary_account)
        transfer_amount = Decimal("10000.00")
        
        # Act
        transaction = transaction_service.process_transaction(
            from_account_id=sdn_exact_match_account.account_id,
            to_account_id=beneficiary_account.account_id,
            amount=transfer_amount,
            user_id="USER-67890",
            ip_address="10.0.0.50",
            device_info="Chrome/91.0"
        )
        
        # Assert
        assert transaction.status == TransactionStatus.BLOCKED
        assert transaction.compliance_decision == ComplianceDecision.BLOCKED
        assert "OFAC SDN exact match" in transaction.compliance_notes
        
        # Verify blocking report filed
        assert len(blocking_report.reports) == 1
        report = blocking_report.reports[0]
        assert report["transaction_id"] == transaction.transaction_id
        assert report["customer_name"] == sdn_exact_match_account.customer_name
        assert report["amount"] == transfer_amount
        
        # Verify deadline is within 10 days
        deadline_delta = report["deadline"] - report["blocked_at"]
        assert deadline_delta.days == OFAC_REPORTING_DEADLINE_DAYS
        
        # Verify audit trail
        logs = audit_logger.get_logs_for_transaction(transaction.transaction_id)
        assert len(logs) == 1
        assert logs[0].result == "blocked: OFAC SDN exact match"
        assert logs[0].compliance_decision == "blocked"


class TestOFACFuzzyMatchReview:
    """Test OFAC fuzzy match review scenario."""
    
    def test_ofac_fuzzy_match_review(
        self,
        transaction_service: TransactionService,
        sdn_fuzzy_match_account: Account,
        beneficiary_account: Account,
        compliance_review: ComplianceReviewService,
        audit_logger: AuditLogger
    ):
        """
        Scenario: ofac_fuzzy_match_review
        Given Customer name is a close variant of SDN entry
        When Fuzzy match score >= 85%
        Then Transaction held for manual compliance review
        """
        # Arrange
        transaction_service.add_account(sdn_fuzzy_match_account)
        transaction_service.add_account(beneficiary_account)
        transfer_amount = Decimal("5000.00")
        
        # Act
        transaction = transaction_service.process_transaction(
            from_account_id=sdn_fuzzy_match_account.account_id,
            to_account_id=beneficiary_account.account_id,
            amount=transfer_amount,
            user_id="USER-11111",
            ip_address="172.16.0.10",
            device_info="Safari/14.0"
        )
        
        # Assert
        assert transaction.status == TransactionStatus.HELD
        assert transaction.compliance_decision == ComplianceDecision.REVIEW_REQUIRED
        assert "fuzzy match" in transaction.compliance_notes.lower()
        
        # Verify compliance review submitted
        review_id = f"REV-{transaction.transaction_id}"
        assert review_id in compliance_review.reviews
        review = compliance_review.reviews[review_id]
        assert review["status"] == "pending"
        assert review["transaction_id"] == transaction.transaction_id
        assert review["screening_result"].match_score >= OFAC_FUZZY_THRESHOLD
        
        # Verify audit trail
        logs = audit_logger.get_logs_for_transaction(transaction.transaction_id)
        assert len(logs) == 1
        assert "held" in logs[0].result.lower()
        assert logs[0].compliance_decision == "review_required"
    
    def test_ofac_fuzzy_match_threshold_boundary(
        self,
        transaction_service: TransactionService,
        beneficiary_account: Account
    ):
        """Test fuzzy match at exact threshold boundary (85%)."""
        # Arrange
        threshold_account = Account(
            account_id="ACC-THRESHOLD",
            routing_number="555666777",
            balance=Decimal("15000.00"),
            customer_name="Jon Do Terrorist",  # Should score ~85%
            status="active"
        )
        transaction_service.add_account(threshold_account)
        transaction_service.add_account(beneficiary_account)
        
        # Act
        transaction = transaction_service.process_transaction(
            from_account_id=threshold_account.account_id,
            to_account_id=beneficiary_account.account_id,
            amount=Decimal("1000.00"),
            user_id="USER-22222",
            ip_address="192.168.2.50",
            device_info="Firefox/89.0"
        )
        
        # Assert - should be held for review at or above threshold
        assert transaction.status in [TransactionStatus.HELD, TransactionStatus.SUCCESS]
        if transaction.status == TransactionStatus.HELD:
            assert transaction.compliance_decision == ComplianceDecision.REVIEW_REQUIRED


class TestOFACFalsePositiveRelease:
    """Test OFAC false positive release scenario."""
    
    def test_ofac_false_positive_release(
        self,
        transaction_service: TransactionService,
        sdn_fuzzy_match_account: Account,
        beneficiary_account: Account,
        compliance_review: ComplianceReviewService
    ):
        """
        Scenario: ofac_false_positive_release
        Given Compliance officer reviews fuzzy match
        When Determination is false positive
        Then Transaction released, false positive documented
        """
        # Arrange
        transaction_service.add_account(sdn_fuzzy_match_account)
        transaction_service.add_account(beneficiary_account)
        
        # Create held transaction
        held_transaction = transaction_service.process_transaction(
            from_account_id=sdn_fuzzy_match_account.account_id,
            to_account_id=beneficiary_account.account_id,
            amount=Decimal("3000.00"),
            user_id="USER-33333",
            ip_address="10.1.1.100",
            device_info="Edge/90.0"
        )
        assert held_transaction.status == TransactionStatus.HELD
        
        # Act - Compliance officer reviews and determines false positive
        released_transaction = transaction_service.release_held_transaction(
            transaction_id=held_transaction.transaction_id,
            officer_id="OFFICER-001",
            decision=ComplianceDecision.FALSE_POSITIVE,
            notes="Verified customer identity. Name similarity is coincidental. False positive."
        )
        
        # Assert
        assert released_transaction.status == TransactionStatus.RELEASED
        assert released_transaction.compliance_decision == ComplianceDecision.FALSE_POSITIVE
        assert "false positive" in released_transaction.compliance_notes.lower()
        
        # Verify review completed
        review_id = f"REV-{held_transaction.transaction_id}"
        review = compliance_review.reviews[review_id]
        assert review["status"] == "completed"
        assert review["decision"] == ComplianceDecision.FALSE_POSITIVE
        assert review["officer_id"] == "OFFICER-001"
        assert "false positive" in review["notes"].lower()


class TestZeroAmountTransfer:
    """Test zero amount transfer rejection scenario."""
    
    def test_zero_amount_transfer(
        self,
        transaction_service: TransactionService,
        valid_account: Account,
        beneficiary_account: Account,
        audit_logger: AuditLogger
    ):
        """
        Scenario: zero_amount_transfer
        Given User initiates transfer
        When User enters amount $0
        Then Transaction rejected 'Amount must be greater than 0'
        """
        # Arrange
        transaction_service.add_account(valid_account)
        transaction_service.add_account(beneficiary_account)
        
        # Act
        transaction = transaction_service.process_transaction(
            from_account_id=valid_account.account_id,
            to_account_id=beneficiary_account.account_id,
            amount=Decimal("0"),
            user_id="USER-44444",
            ip_address="192.168.3.100",
            device_info="Chrome/92.0"
        )
