# ARCHITECTURE ALIGNMENT REVIEW
## Critical Changes: Dual Architecture → Hybrid Model

**Date:** March 28, 2026  
**Issue:** Current dual architecture contradicts winning hybrid model strategy

---

## 🚨 **CRITICAL PROBLEMS IN CURRENT ARCHITECTURE**

### **Problem 1: 6-Agent Pipeline Location ❌**

**Current Architecture (Dual System):**
```
VS Code → Fortress Backend → 6-Agent Pipeline → Compliance DB
                ↑
         Customer data flows here!
```

**This BREAKS the hybrid model promise:**
- Customer JIRA data flows to Fortress servers
- Customer transaction data would flow to Fortress servers
- Banks will reject this due to data privacy concerns

**Fixed Architecture (Hybrid Model):**
```
Customer Infrastructure:
├─ 6-Agent Pipeline (Docker container)
├─ Customer JIRA data
├─ Customer transaction data
└─ Calls Fortress API (gets compliance rules only)

Fortress Infrastructure:
└─ Compliance Database + API (NO customer data)
```

**Key Difference:** Customer data NEVER leaves customer infrastructure ✅

---

### **Problem 2: Missing Production Use Cases ❌**

**Current Architecture Focus:**
- ✅ JIRA test generation (via VS Code plugin)
- ❌ Real-time wire transfer validation (MISSING!)
- ❌ OFAC sanctions screening (MISSING!)
- ❌ Loan application validation (MISSING!)

**This is a $10K/month revenue problem:**
- JIRA test generation alone: $5K/month pilot
- Real-time compliance enforcement: $15K/month production

**Fixed Architecture needs:**
- VS Code plugin (developer workflow)
- **+ Production agents** (banking system integration)
- **+ API validation endpoints** (real-time transaction checks)

---

### **Problem 3: System 2 Definition Too Narrow ❌**

**Current Definition:**
> "SYSTEM 2: CLIENT SYSTEM (VS Code Plugin)"

**This limits the product to:**
- Developer tools only
- Test generation only
- No production deployment

**Fixed Definition:**
> "PIPELINE 2: CUSTOMER PRODUCTION INFRASTRUCTURE"
> - VS Code plugin (test generation)
> - Docker agents (real-time validation)
> - Banking system integrations (wire transfers, OFAC, loans)

---

## ✅ **CORRECTED ARCHITECTURE**

### **NEW: Two-Pipeline Hybrid Architecture**

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PIPELINE 1: FORTRESS LABS TRAINING & STORAGE (Cloud)                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  What Fortress Hosts:                                                   │
│  ├─ Regulation scrapers (18 sources)                                    │
│  ├─ Compliance database (PostgreSQL + Weaviate + Redis)                 │
│  ├─ API Gateway (serves compliance rules)                               │
│  └─ Scheduled updates (daily OFAC, weekly OCC)                          │
│                                                                          │
│  What Flows OUT: Compliance rules only (public regulatory data)         │
│  What Flows IN: Nothing (no customer data!)                             │
│                                                                          │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │
                    HTTPS API (Read-Only)
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PIPELINE 2: CUSTOMER PRODUCTION INFRASTRUCTURE (On-Premises/VPC)       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  A) DEVELOPER WORKFLOW (VS Code Plugin)                          │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │                                                                   │  │
│  │  Developer → VS Code → Fortress Agent (local) → Fortress API     │  │
│  │              ↓                   ↓                                │  │
│  │           JIRA Story      6-Agent Pipeline                        │  │
│  │                                  ↓                                │  │
│  │                           pytest test code                        │  │
│  │                                                                   │  │
│  │  Data Flow:                                                       │  │
│  │  - JIRA story text → Agent (stays local)                         │  │
│  │  - Agent → Fortress API (gets OCC rules)                         │  │
│  │  - Agent → pytest code (saved locally)                           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  B) PRODUCTION WORKFLOW (Banking System Integration)             │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │                                                                   │  │
│  │  Core Banking → Fortress Agent (Docker) → Fortress API           │  │
│  │       ↓                    ↓                                      │  │
│  │  Wire Transfer      6-Agent Pipeline                              │  │
│  │  $15,000                   ↓                                      │  │
│  │                     Block/Allow Decision                          │  │
│  │                                                                   │  │
│  │  Data Flow:                                                       │  │
│  │  - Transaction data → Agent (stays in customer DB)               │  │
│  │  - Agent → Fortress API (gets wire transfer rules)               │  │
│  │  - Agent → Block transaction (compliance violation)              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  C) OFAC SCREENING WORKFLOW                                      │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │                                                                   │  │
│  │  Transaction → Fortress Agent → Fortress API                     │  │
│  │       ↓                           ↓                               │  │
│  │  Beneficiary Name           OFAC SDN List (8000+ entries)        │  │
│  │                                   ↓                               │  │
│  │                            Match Detection → Block + SAR          │  │
│  │                                                                   │  │
│  │  Data Flow:                                                       │  │
│  │  - Beneficiary name → Agent (stays local)                        │  │
│  │  - Agent → Fortress API (gets OFAC list)                         │  │
│  │  - Agent → Screens locally, blocks if match                      │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  What Customer Hosts:                                                   │
│  ├─ 6-Agent Pipeline (Docker container)                                 │
│  ├─ Customer database (PostgreSQL for results)                          │
│  ├─ JIRA connector (optional)                                           │
│  ├─ Core banking integration                                            │
│  └─ VS Code plugin (optional)                                           │
│                                                                          │
│  What Stays Local: ALL customer data (JIRA, transactions, PII)          │
│  What Leaves: Nothing (zero customer data to Fortress)                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 **SPECIFIC CHANGES REQUIRED**

### **Change 1: Move 6-Agent Pipeline**

**FROM (Current):**
```
┌──────────────────────────────────────────────────────────────────────────┐
│  BACKEND CONNECTION (HTTP/WebSocket)                                     │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  6-AGENT PIPELINE (FastAPI Backend)  ← Fortress hosts this          │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │  Understanding Agent → Risk Agent → AML Agent → ...                 │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

**TO (Hybrid):**
```
┌──────────────────────────────────────────────────────────────────────────┐
│  CUSTOMER INFRASTRUCTURE (Docker/K8s)                                    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  6-AGENT PIPELINE (Customer Docker Container)  ← Customer hosts!    │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │  Understanding Agent → Risk Agent → AML Agent → ...                 │ │
│  │         ↓ Calls Fortress API                                        │ │
│  │  GET /api/v1/rules?geography=usa&regulator=occ                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### **Change 2: Add Production Banking Components**

**NEW Components Needed:**

```typescript
// NEW: Wire Transfer Validator
export class WireTransferValidator {
  constructor(
    private fortressAPI: FortressAPIClient,
    private bankingDB: BankDatabaseConnection
  ) {}
  
  async validateTransfer(transfer: WireTransfer): Promise<ValidationResult> {
    // 1. Get compliance rules from Fortress API
    const rules = await this.fortressAPI.getRules({
      geography: 'usa',
      regulator: 'occ',
      domain: 'wire_transfer'
    });
    
    // 2. Check against rules (LOCAL - customer data stays here)
    const violations = this.checkRules(transfer, rules);
    
    // 3. Return decision
    if (violations.length > 0) {
      return {
        action: 'block',
        reason: violations[0].recommendation,
        regulation: violations[0].regulation
      };
    }
    
    return { action: 'allow' };
  }
  
  private checkRules(transfer: WireTransfer, rules: Rule[]): Violation[] {
    // THIS RUNS LOCALLY - customer data never sent to Fortress
    const violations = [];
    
    for (const rule of rules) {
      if (rule.type === 'dual_approval') {
        if (transfer.amount >= rule.threshold && 
            transfer.approvals.length < 2) {
          violations.push({
            regulation: 'OCC BSA/AML',
            recommendation: 'Require second approval'
          });
        }
      }
    }
    
    return violations;
  }
}
```

```typescript
// NEW: OFAC Screener
export class OFACScreener {
  constructor(private fortressAPI: FortressAPIClient) {}
  
  async screenParty(partyName: string): Promise<ScreeningResult> {
    // 1. Get OFAC SDN list from Fortress API (public data)
    const sdnList = await this.fortressAPI.getOFACList();
    
    // 2. Screen locally (customer data never sent to Fortress)
    const matches = this.fuzzyMatch(partyName, sdnList);
    
    if (matches.length > 0) {
      return {
        action: 'block',
        matches: matches,
        sarRequired: true
      };
    }
    
    return { action: 'clear' };
  }
  
  private fuzzyMatch(name: string, sdnList: SDNEntry[]): Match[] {
    // LOCAL fuzzy matching - customer name never leaves infrastructure
    return sdnList.filter(entry => 
      similarity(name, entry.name) > 0.85
    ).map(entry => ({
      matchedEntity: entry.name,
      sdnNumber: entry.number,
      matchScore: similarity(name, entry.name)
    }));
  }
}
```

---

### **Change 3: Fortress API Endpoints (Read-Only)**

**Fortress API Should ONLY Provide:**

```python
# api/compliance_rules.py (Fortress Labs API)

@app.get("/api/v1/rules")
async def get_rules(
    geography: str,
    regulator: str,
    domain: str
):
    """
    Returns: Public compliance rules only
    
    Example Response:
    {
      "rules": [
        {
          "rule_id": "OCC-BSA-001",
          "rule_text": "Wire transfers >= $10,000 require dual approval",
          "threshold_amount": 10000.00,
          "test_template": "def test_wire_dual_approval()..."
        }
      ]
    }
    
    Note: NO CUSTOMER DATA in request or response!
    """
    return db.query(ComplianceRule).filter(
        geography=geography,
        regulator=regulator,
        domain=domain
    ).all()

@app.get("/api/v1/ofac/sdn-list")
async def get_ofac_sdn_list():
    """
    Returns: Public OFAC SDN list (8000+ entries)
    
    Example Response:
    {
      "entries": [
        {
          "name": "AL-QAHTANI, Mohammed Ahmed",
          "sdn_number": "12345",
          "programs": ["SDGT", "IRAN"]
        }
      ],
      "last_updated": "2026-03-28T10:00:00Z"
    }
    
    Note: This is PUBLIC data from OFAC website!
    """
    return db.query(OFACEntry).all()

# ❌ NEVER ADD THIS ENDPOINT (violates hybrid model):
# @app.post("/api/v1/validate-transaction")
# async def validate_transaction(transaction_data):
#     # This would require customer sending transaction data to Fortress!
#     # VIOLATION of data privacy promise!
#     pass
```

---

### **Change 4: System Naming**

**OLD:**
- System 1: Data Platform
- System 2: Client System (VS Code Plugin)

**NEW:**
- **Pipeline 1:** Training & Storage (Fortress Labs)
- **Pipeline 2:** Production Infrastructure (Customer)
  - **Workflow A:** Developer Tools (VS Code plugin)
  - **Workflow B:** Real-Time Validation (Banking integration)
  - **Workflow C:** OFAC Screening
  - **Workflow D:** Loan Application Validation

---

## 📊 **DATA FLOW COMPARISON**

### **WRONG (Current Architecture):**

```
User Action (VS Code) 
  → JIRA Story Text
  → Fortress Backend (CUSTOMER DATA SENT TO FORTRESS! ❌)
  → 6-Agent Pipeline (runs on Fortress servers)
  → Compliance Rules
  → pytest Code
  → Returns to VS Code
```

**Problem:** JIRA story text (customer IP) sent to Fortress servers

---

### **CORRECT (Hybrid Architecture):**

```
User Action (VS Code)
  → JIRA Story Text (stays in VS Code)
  → Fortress Agent (LOCAL Docker container)
    ├─ Step 1: Parse JIRA (local processing)
    ├─ Step 2: Call Fortress API → GET compliance rules (public data)
    ├─ Step 3: Generate test (local processing)
    └─ Step 4: Save pytest code (local file system)
```

**Key:** Customer data NEVER sent to Fortress ✅

---

### **Production Example:**

```
Wire Transfer Submitted ($15,000)
  → Core Banking System
  → Fortress Agent (LOCAL Docker container)
    ├─ Step 1: Extract amount, approvals (local)
    ├─ Step 2: Call Fortress API → GET wire transfer rules
    │            Response: {"threshold": 10000, "required_approvals": 2}
    ├─ Step 3: Check locally: $15K >= $10K? Yes. Approvals = 1? Yes.
    │            Violation: Need 2 approvals, only have 1
    └─ Step 4: Block transaction (local decision)
              Log to customer PostgreSQL (local)
```

**Key:** Transaction data ($15K, account numbers) NEVER sent to Fortress ✅

---

## ✅ **UPDATED ARCHITECTURE DIAGRAM**

### **Correct Two-Pipeline Hybrid Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  PIPELINE 1: FORTRESS LABS (AWS Cloud)                                  │
│  ══════════════════════════════════════════════════════════════════════ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ INGESTION LAYER                                                    │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │ • 18 Regulation Scrapers (OCC, FDIC, FinCEN, CFPB, Fed, NCUA)     │ │
│  │ • 5 Live APIs (OFAC, FFIEC)                                        │ │
│  │ • Scheduled: Daily (OFAC), Weekly (OCC), Monthly (CFPB)            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              ↓                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ PROCESSING LAYER                                                   │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │ • PDF/HTML/Excel Parsers                                           │ │
│  │ • Claude Sonnet 4 (rule extraction)                                │ │
│  │ • Change detection (vs previous version)                           │ │
│  │ • Version control                                                  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              ↓                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ STORAGE LAYER                                                      │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │ • PostgreSQL: 300+ compliance requirements                         │ │
│  │ • Weaviate: Regulation embeddings                                  │ │
│  │ • Redis: Cache (hot rules)                                         │ │
│  │ • S3: Raw PDFs, audit trail                                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              ↓                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ API LAYER (Read-Only)                                              │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │ • GET /api/v1/rules?geography=usa&regulator=occ                    │ │
│  │ • GET /api/v1/ofac/sdn-list                                        │ │
│  │ • GET /api/v1/rules/changes?since=2026-01-01                       │ │
│  │ • WS  /ws/rules/updates (real-time notifications)                  │ │
│  │                                                                     │ │
│  │ ⚠️ NO CUSTOMER DATA ACCEPTED (read-only public compliance rules)   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
                   HTTPS (Read-Only API)
                   Returns: Compliance rules only
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  PIPELINE 2: CUSTOMER INFRASTRUCTURE (On-Premises / Private VPC)        │
│  ══════════════════════════════════════════════════════════════════════ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ FORTRESS AGENT (Docker Container)                                  │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │                                                                     │ │
│  │  6-Agent Pipeline:                                                  │ │
│  │  ├─ Agent 1 (Discovery): Parse JIRA story                          │ │
│  │  ├─ Agent 2 (Understanding): Call Fortress API → Get rules         │ │
│  │  ├─ Agent 3 (Framework): Generate BDD framework                    │ │
│  │  ├─ Agent 4 (Generation): Generate pytest code                     │ │
│  │  ├─ Agent 5 (Validation): Validate syntax                          │ │
│  │  └─ Agent 6 (Execution): Post to JIRA / Validate transaction       │ │
│  │                                                                     │ │
│  │  Fortress API Client:                                               │ │
│  │  • Circuit breaker (fail-open if API down)                         │ │
│  │  • Local cache (Redis - 1 hour TTL)                                │ │
│  │  • Retry logic (3 attempts)                                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              ↓                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ CUSTOMER INTEGRATIONS                                              │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │                                                                     │ │
│  │  A) Developer Workflow:                                             │ │
│  │     VS Code Plugin → Fortress Agent → pytest test code             │ │
│  │                                                                     │ │
│  │  B) Production Banking:                                             │ │
│  │     Core Banking → Fortress Agent → Block/Allow decision           │ │
│  │                                                                     │ │
│  │  C) OFAC Screening:                                                 │ │
│  │     Transaction → Fortress Agent → OFAC check → Block if match     │ │
│  │                                                                     │ │
│  │  D) Loan Validation:                                                │ │
│  │     Loan App → Fortress Agent → Section 1071 check → Reject if     │ │
│  │                                   missing demographic fields        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              ↓                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ CUSTOMER DATABASE (PostgreSQL)                                     │ │
│  │ ─────────────────────────────────────────────────────────────────  │ │
│  │ • generated_tests (pytest code from JIRA)                          │ │
│  │ • transaction_validations (compliance checks)                      │ │
│  │ • ofac_screenings (sanctions screening results)                    │ │
│  │ • manual_review_queue (flagged transactions)                       │ │
│  │ • daily_metrics (compliance performance)                           │ │
│  │                                                                     │ │
│  │ ⚠️ ALL CUSTOMER DATA STAYS HERE (never sent to Fortress)           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🎯 **SUMMARY OF REQUIRED CHANGES**

### **Architecture Document Changes:**

1. **Rename Systems:**
   - "System 1: Data Platform" → **"Pipeline 1: Training & Storage"**
   - "System 2: Client System" → **"Pipeline 2: Customer Production Infrastructure"**

2. **Move 6-Agent Pipeline:**
   - FROM: Fortress backend servers
   - TO: Customer Docker container

3. **Add Production Components:**
   - Wire transfer validator
   - OFAC screener
   - Loan application validator
   - Real-time banking integrations

4. **Clarify Data Privacy:**
   - Add explicit callouts: "Customer data NEVER leaves customer infrastructure"
   - Update API to be read-only (no customer data accepted)
   - Add fail-open circuit breaker

5. **Update Deployment Model:**
   - Customer deploys via Docker Compose / Kubernetes
   - Fortress provides Docker image
   - Customer configures API credentials

### **Key Messaging Changes:**

**OLD Messaging:**
> "Fortress Labs provides a VS Code plugin that connects to our backend"

**NEW Messaging:**
> "Fortress Labs provides a Docker container you deploy in YOUR infrastructure.
> Your transaction data stays in your database.
> Only compliance rules flow from our API (public regulatory data)."

---

## ✅ **VERIFICATION CHECKLIST**

Before implementing, verify:

- [ ] 6-Agent pipeline runs in customer Docker container (not Fortress backend)
- [ ] Customer JIRA data never sent to Fortress API
- [ ] Customer transaction data never sent to Fortress API
- [ ] Fortress API is read-only (returns compliance rules only)
- [ ] Customer can run offline (with cached rules, fail-open)
- [ ] OFAC screening happens locally (customer data stays local)
- [ ] Wire transfer validation happens locally (customer data stays local)
- [ ] All customer data stored in customer PostgreSQL (not Fortress DB)

---

## 🚀 **IMPLEMENTATION PRIORITY**

### **Phase 1 (MVP): Fix Architecture First**
1. Move 6-agent pipeline to customer Docker container
2. Make Fortress API read-only
3. Add circuit breaker (fail-open)
4. Update deployment docs

### **Phase 2: Add Production Use Cases**
1. Wire transfer validator component
2. OFAC screener component
3. Customer database schema
4. Integration examples

### **Phase 3: Polish Developer Experience**
1. VS Code plugin (connects to local agent)
2. Web UI (alternative to VS Code)
3. Better documentation
4. Video tutorials

---

**CRITICAL:** Without these changes, banks will reject Fortress Labs due to data privacy concerns, killing the entire business model. The hybrid architecture is the KEY differentiator!

---

**Document Version:** 1.0  
**Date:** March 28, 2026  
**Next Steps:** Update dual architecture document with these corrections
