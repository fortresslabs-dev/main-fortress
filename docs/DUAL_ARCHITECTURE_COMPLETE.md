# FORTRESS LABS - DUAL ARCHITECTURE
## System 1: Data Platform | System 2: Client System

**Date:** March 11, 2026  
**Purpose:** Separate data ingestion/processing from client-side agent execution

---

## 🏗️ **ARCHITECTURE OVERVIEW**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         FORTRESS LABS PLATFORM                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────┐    ┌──────────────────────────────┐   │
│  │   SYSTEM 1: DATA PLATFORM   │    │  SYSTEM 2: CLIENT SYSTEM     │   │
│  │   (Backend/Cloud)           │    │  (VS Code Plugin)            │   │
│  │                             │    │                               │   │
│  │  • Regulation Scraping      │◄───┤  • 6-Agent Pipeline          │   │
│  │  • Data Processing          │    │  • User Interface            │   │
│  │  • Compliance Database      │    │  • Test Generation           │   │
│  │  • Auto-Update Rules        │───►│  • Real-time Feedback        │   │
│  │  • API Server               │    │  • Cost Tracking             │   │
│  │                             │    │                               │   │
│  └────────────────────────────┘    └──────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key Principle:** **Separation of Concerns**
- **System 1** = Data acquisition, processing, and storage (backend)
- **System 2** = User interaction and agent execution (client)

---

## 🔷 **SYSTEM 1: DATA PLATFORM (Backend/Cloud)**

### **Purpose:**
Continuously ingest regulatory data from government sources, process it, store in compliance database, and serve via API to client systems.

### **Architecture Diagram:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        DATA PLATFORM ARCHITECTURE                        │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────┐
│  DATA SOURCES    │
│  (External)      │
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  INGESTION LAYER (Python Scrapers + APIs)                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ Govt Website │  │  Live APIs   │  │ Bank DB      │  │ Manual     │ │
│  │  Scrapers    │  │  Connectors  │  │  Connectors  │  │ Upload     │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                 │                 │                 │         │
│         │  18 scrapers    │  5 APIs         │  12 DB queries  │  PDFs  │
│         │  (OCC, FDIC,    │  (OFAC, FFIEC)  │  (FLEXCUBE)     │        │
│         │   FinCEN...)    │                 │                 │        │
│         └─────────┬───────┴─────────────────┴─────────────────┘        │
└───────────────────┼──────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PROCESSING LAYER (ETL Pipeline)                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  PDF Parser  │  │ HTML Parser  │  │ Excel Parser │  │  NLP       │ │
│  │              │  │              │  │              │  │ Extraction │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                 │                 │                 │         │
│         └─────────┬───────┴─────────────────┴─────────────────┘        │
│                   │                                                     │
│  ┌────────────────▼─────────────────┐                                  │
│  │  Rule Extraction Engine          │                                  │
│  │  • Extract compliance rules      │                                  │
│  │  • Detect changes vs previous    │                                  │
│  │  • Version control               │                                  │
│  │  • Classify by domain/geography  │                                  │
│  └────────────────┬─────────────────┘                                  │
└───────────────────┼──────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STORAGE LAYER                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────┐  ┌────────────────────────┐               │
│  │  PostgreSQL             │  │  Weaviate Vector DB    │               │
│  │  (Relational)           │  │  (Semantic Search)     │               │
│  ├────────────────────────┤  ├────────────────────────┤               │
│  │ • Compliance rules      │  │ • Regulation text      │               │
│  │ • Test case metadata    │  │ • Embeddings           │               │
│  │ • Audit trail           │  │ • Similarity search    │               │
│  │ • Version history       │  │                        │               │
│  └────────────────────────┘  └────────────────────────┘               │
│                                                                          │
│  ┌────────────────────────┐  ┌────────────────────────┐               │
│  │  Redis Cache            │  │  S3 / Blob Storage     │               │
│  │  (Hot Data)             │  │  (Raw Files)           │               │
│  ├────────────────────────┤  ├────────────────────────┤               │
│  │ • Frequent rules        │  │ • PDF documents        │               │
│  │ • API responses         │  │ • Excel scenarios      │               │
│  │ • Session data          │  │ • Backup archives      │               │
│  └────────────────────────┘  └────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  API LAYER (FastAPI)                                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  REST API Endpoints                                              │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │  GET  /api/v1/rules?geography=usa&regulator=occ                  │  │
│  │  GET  /api/v1/rules/{rule_id}                                    │  │
│  │  GET  /api/v1/rules/changes?since=2026-01-01                     │  │
│  │  POST /api/v1/search (semantic search)                           │  │
│  │  GET  /api/v1/test-cases?domain=fair_lending                     │  │
│  │  GET  /api/v1/health                                             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  WebSocket (Real-time Updates)                                   │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │  WS   /ws/rules/updates                                          │  │
│  │  WS   /ws/scraper/status                                         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  AUTOMATION LAYER (Scheduled Jobs)                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  Daily       │  │  Weekly      │  │  Monthly     │  │  Event     │ │
│  │  Scraper     │  │  Scraper     │  │  Scraper     │  │  Driven    │ │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├────────────┤ │
│  │ • OFAC SDN   │  │ • FinCEN     │  │ • DFAST      │  │ • Fed Reg  │ │
│  │ • OCC bull.  │  │ • CFPB       │  │   scenarios  │  │   monitor  │ │
│  │ • FDIC news  │  │ • FRB SR     │  │              │  │            │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘ │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Change Detection & Notification                                 │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │  • Compare new vs old rules                                      │  │
│  │  • Classify change severity (breaking/non-breaking)              │  │
│  │  • Send alerts to clients (webhook/email/Slack)                  │  │
│  │  • Update version history                                        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
                 CLIENTS
        (VS Code, Web UI, Mobile)
```

---

### **DATA PLATFORM - COMPONENT DETAILS**

#### **1. Ingestion Layer**

**Govt Website Scrapers (18 sources):**
```python
# scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

scrapers = {
    'occ_bsa': {
        'class': OCCScraper,
        'schedule': 'daily',
        'priority': 'high'
    },
    'fincen_sar': {
        'class': FinCENScraper,
        'schedule': 'weekly',
        'priority': 'high'
    },
    'fdic_manual': {
        'class': FDICScraper,
        'schedule': 'monthly',
        'priority': 'medium'
    }
    # ... 15 more scrapers
}

scheduler = BackgroundScheduler()

for scraper_id, config in scrapers.items():
    scheduler.add_job(
        run_scraper,
        trigger='cron',
        args=[config['class']],
        **get_cron_schedule(config['schedule'])
    )
```

**Live API Connectors (5 sources):**
```python
# api_connectors.py
class OFACAPIConnector:
    """Real-time OFAC SDN list updates"""
    
    def sync_sdn_list(self):
        """Called every 4 hours"""
        response = requests.get(
            "https://ofac.treasury.gov/api/sanctions/sdn"
        )
        
        # Compare with DB
        new_entries = self.detect_new_entries(response.json())
        
        if new_entries:
            # Alert clients immediately
            self.send_alert(new_entries)
```

**Bank DB Connectors (12 queries):**
```python
# bank_connectors.py
class FLEXCUBEConnector:
    """Wells Fargo UAT database connector"""
    
    def sync_transaction_limits(self):
        """Sync wire transfer limits daily"""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT threshold_amount, approval_levels
            FROM dual_approval_rules
            WHERE active_flag = 'Y'
        """)
        
        # Update compliance database
        self.update_rules_db(cursor.fetchall())
```

---

#### **2. Processing Layer**

**Rule Extraction Engine:**
```python
# rule_extractor.py
class RuleExtractor:
    """Extract structured rules from unstructured documents"""
    
    def extract_from_pdf(self, pdf_content):
        """Extract rules from OCC/FDIC PDF"""
        
        # 1. Parse PDF
        text = pdfplumber.extract_text(pdf_content)
        
        # 2. Detect sections
        sections = self.detect_sections(text)
        
        # 3. Extract rules using Claude
        rules = []
        for section in sections:
            rule = self.extract_rule_with_llm(section)
            rules.append(rule)
        
        return rules
    
    def extract_rule_with_llm(self, text):
        """Use Claude to extract structured rule"""
        prompt = f"""
        Extract compliance rule from this text:
        
        {text}
        
        Return JSON:
        {{
            "rule_id": "unique_id",
            "rule_name": "descriptive name",
            "rule_type": "limit|requirement|procedure",
            "rule_value": "specific threshold or requirement",
            "effective_date": "YYYY-MM-DD",
            "regulator": "OCC|FDIC|FinCEN|etc",
            "geography": "USA|India",
            "test_case_template": "pytest test template"
        }}
        """
        
        response = await bedrock.generate(prompt)
        return json.loads(response)
```

**Change Detection:**
```python
# change_detector.py
class ChangeDetector:
    """Detect changes in regulations vs previous version"""
    
    def detect_changes(self, new_rules, previous_rules):
        """Compare new vs old rules"""
        
        changes = {
            'added': [],
            'removed': [],
            'modified': [],
            'severity': None
        }
        
        for new_rule in new_rules:
            old_rule = self.find_matching_rule(
                new_rule, previous_rules
            )
            
            if not old_rule:
                changes['added'].append(new_rule)
            elif self.has_changed(new_rule, old_rule):
                changes['modified'].append({
                    'old': old_rule,
                    'new': new_rule,
                    'diff': self.compute_diff(old_rule, new_rule)
                })
        
        # Classify severity
        changes['severity'] = self.classify_severity(changes)
        
        return changes
    
    def classify_severity(self, changes):
        """Classify change severity"""
        
        # Breaking changes (require code update)
        if any(c['diff']['type'] == 'value_change' 
               for c in changes['modified']):
            return 'breaking'
        
        # Non-breaking (informational)
        return 'non-breaking'
```

---

#### **3. Storage Layer**

**PostgreSQL Schema:**
```sql
-- Compliance rules table
CREATE TABLE compliance_rules (
    rule_id VARCHAR(100) PRIMARY KEY,
    rule_name VARCHAR(500),
    rule_type VARCHAR(50),
    rule_value JSONB,
    effective_date DATE,
    regulator VARCHAR(50),
    geography VARCHAR(20),
    version INTEGER,
    test_case_template TEXT,
    source_url TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_geography ON compliance_rules(geography);
CREATE INDEX idx_regulator ON compliance_rules(regulator);
CREATE INDEX idx_effective_date ON compliance_rules(effective_date);

-- Version history
CREATE TABLE rule_versions (
    version_id SERIAL PRIMARY KEY,
    rule_id VARCHAR(100) REFERENCES compliance_rules(rule_id),
    change_type VARCHAR(20),  -- added|modified|removed
    old_value JSONB,
    new_value JSONB,
    changed_at TIMESTAMP DEFAULT NOW(),
    changed_by VARCHAR(100)  -- system|manual|api
);

-- Scraper audit trail
CREATE TABLE scraper_runs (
    run_id SERIAL PRIMARY KEY,
    scraper_name VARCHAR(100),
    status VARCHAR(20),  -- success|failed|partial
    rules_added INTEGER,
    rules_updated INTEGER,
    errors JSONB,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

**Weaviate Schema:**
```python
# weaviate_schema.py
schema = {
    "class": "ComplianceRegulation",
    "description": "Full text of regulations for semantic search",
    "vectorizer": "text2vec-transformers",
    "properties": [
        {
            "name": "regulation_text",
            "dataType": ["text"],
            "description": "Full regulation text"
        },
        {
            "name": "regulation_id",
            "dataType": ["string"]
        },
        {
            "name": "regulator",
            "dataType": ["string"]
        },
        {
            "name": "geography",
            "dataType": ["string"]
        },
        {
            "name": "effective_date",
            "dataType": ["date"]
        }
    ]
}
```

---

#### **4. API Layer**

**FastAPI Endpoints:**
```python
# api/rules.py
from fastapi import FastAPI, Query
from typing import Optional, List

app = FastAPI()

@app.get("/api/v1/rules")
async def get_rules(
    geography: Optional[str] = Query(None, regex="^(usa|india)$"),
    regulator: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    effective_after: Optional[str] = Query(None)
):
    """Get compliance rules filtered by criteria"""
    
    query = db.query(ComplianceRule)
    
    if geography:
        query = query.filter(ComplianceRule.geography == geography)
    
    if regulator:
        query = query.filter(ComplianceRule.regulator == regulator)
    
    if domain:
        query = query.filter(ComplianceRule.rule_type.like(f"%{domain}%"))
    
    if effective_after:
        query = query.filter(
            ComplianceRule.effective_date >= effective_after
        )
    
    rules = query.all()
    
    return {
        "count": len(rules),
        "rules": [rule.to_dict() for rule in rules]
    }

@app.get("/api/v1/rules/changes")
async def get_rule_changes(
    since: str = Query(..., description="ISO date"),
    severity: Optional[str] = Query(None, regex="^(breaking|non-breaking)$")
):
    """Get rule changes since date"""
    
    versions = db.query(RuleVersion).filter(
        RuleVersion.changed_at >= since
    )
    
    if severity:
        # Classify each version
        versions = [v for v in versions 
                   if classify_severity(v) == severity]
    
    return {
        "count": len(versions),
        "changes": [v.to_dict() for v in versions]
    }

@app.post("/api/v1/search")
async def semantic_search(
    query: str,
    geography: Optional[str] = None,
    limit: int = 10
):
    """Semantic search across regulations"""
    
    # Weaviate semantic search
    result = weaviate_client.query.get(
        "ComplianceRegulation",
        ["regulation_text", "regulation_id", "regulator"]
    ).with_near_text({
        "concepts": [query]
    }).with_limit(limit)
    
    if geography:
        result = result.with_where({
            "path": ["geography"],
            "operator": "Equal",
            "valueString": geography
        })
    
    return result.do()

@app.websocket("/ws/rules/updates")
async def rule_updates_websocket(websocket: WebSocket):
    """Real-time rule updates via WebSocket"""
    await websocket.accept()
    
    async for update in rule_update_stream():
        await websocket.send_json(update)
```

---

#### **5. Automation Layer**

**Scheduler Configuration:**
```yaml
# scheduler_config.yaml
scrapers:
  - name: occ_bsa_procedures
    class: OCCScraper
    schedule:
      type: cron
      hour: 2
      minute: 0
    priority: high
    retry: 3
    timeout: 300
    
  - name: ofac_sdn_sync
    class: OFACAPIConnector
    schedule:
      type: interval
      hours: 4
    priority: critical
    retry: 5
    timeout: 60
    
  - name: fincen_advisories
    class: FinCENScraper
    schedule:
      type: cron
      day_of_week: mon
      hour: 3
      minute: 0
    priority: high

notifications:
  - type: breaking_change
    channels: [slack, email, webhook]
    recipients: [compliance_team, engineering_team]
    
  - type: scraper_failure
    channels: [slack, pagerduty]
    recipients: [ops_team]
```

---

## 🔷 **SYSTEM 2: CLIENT SYSTEM (VS Code Plugin)**

### **Purpose:**
Provide developers and compliance officers with a VS Code extension to interact with 6-agent pipeline, generate compliance tests, and get real-time feedback.

### **Architecture Diagram:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    VS CODE PLUGIN ARCHITECTURE                           │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│  VS CODE EXTENSION HOST                                                  │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  USER INTERFACE LAYER (TypeScript + React)                         │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │                                                                     │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────┐│ │
│  │  │  Sidebar     │  │  Context     │  │  Status Bar  │  │ Quick  ││ │
│  │  │  Panel       │  │  Menu        │  │  Indicator   │  │ Picks  ││ │
│  │  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├────────┤│ │
│  │  │ • Test List  │  │ • Generate   │  │ • Geography  │  │ • JIRA ││ │
│  │  │ • Progress   │  │   Test       │  │   (USA/IND)  │  │ Story  ││ │
│  │  │ • Results    │  │ • Run Tests  │  │ • Cost       │  │ Select ││ │
│  │  │ • Compliance │  │ • Settings   │  │ • Status     │  │        ││ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────┘│ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                │                                         │
│                                ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  EXTENSION CORE (TypeScript)                                       │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │                                                                     │ │
│  │  ┌──────────────────────┐  ┌──────────────────────┐               │ │
│  │  │  Command Handler      │  │  Event Listener      │               │ │
│  │  ├──────────────────────┤  ├──────────────────────┤               │ │
│  │  │ • generateTest()      │  │ • onDocumentSave()   │               │ │
│  │  │ • runTests()          │  │ • onJiraSync()       │               │ │
│  │  │ • syncJira()          │  │ • onRuleUpdate()     │               │ │
│  │  │ • viewCompliance()    │  │                      │               │ │
│  │  └──────────────────────┘  └──────────────────────┘               │ │
│  │                                                                     │ │
│  │  ┌──────────────────────┐  ┌──────────────────────┐               │ │
│  │  │  State Manager        │  │  Configuration       │               │ │
│  │  ├──────────────────────┤  ├──────────────────────┤               │ │
│  │  │ • Current geography   │  │ • API endpoint       │               │ │
│  │  │ • Active agents       │  │ • Customer ID        │               │ │
│  │  │ • Test results cache  │  │ • JIRA credentials   │               │ │
│  │  └──────────────────────┘  └──────────────────────┘               │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                │                                         │
│                                ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  API CLIENT LAYER                                                  │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │                                                                     │ │
│  │  ┌──────────────────────┐  ┌──────────────────────┐               │ │
│  │  │  Backend API Client   │  │  WebSocket Client    │               │ │
│  │  ├──────────────────────┤  ├──────────────────────┤               │ │
│  │  │ • POST /generate      │  │ • Real-time updates  │               │ │
│  │  │ • GET /rules          │  │ • Progress events    │               │ │
│  │  │ • GET /test-cases     │  │ • Rule changes       │               │ │
│  │  └──────────────────────┘  └──────────────────────┘               │ │
│  │                                                                     │ │
│  │  ┌──────────────────────┐  ┌──────────────────────┐               │ │
│  │  │  JIRA Client          │  │  Auth Provider       │               │ │
│  │  ├──────────────────────┤  ├──────────────────────┤               │ │
│  │  │ • Fetch stories       │  │ • API token manager  │               │ │
│  │  │ • Add comments        │  │ • Credential vault   │               │ │
│  │  │ • Transition status   │  │                      │               │ │
│  │  └──────────────────────┘  └──────────────────────┘               │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  BACKEND CONNECTION (HTTP/WebSocket)                                     │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  6-AGENT PIPELINE (FastAPI Backend)                                │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │                                                                     │ │
│  │  Understanding Agent → Risk Agent → AML Agent →                    │ │
│  │  Fair Lending Agent → Stress Test Agent → Policy QA Agent          │ │
│  │                                                                     │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │ │
│  │  │  Compliance  │  │  Bedrock     │  │  Weaviate    │            │ │
│  │  │  Rules DB    │  │  (Claude)    │  │  (Vector DB) │            │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### **VS CODE PLUGIN - COMPONENT DETAILS**

#### **1. User Interface**

**Sidebar Panel (extension/sidebar.tsx):**
```typescript
// sidebar.tsx
import * as React from 'react';

export const SidebarPanel: React.FC = () => {
  const [tests, setTests] = useState<Test[]>([]);
  const [geography, setGeography] = useState<'usa' | 'india'>('usa');
  const [costToday, setCostToday] = useState(0);
  
  return (
    <div className="sidebar">
      {/* Geography Selector */}
      <GeographySelector 
        value={geography}
        onChange={setGeography}
      />
      
      {/* Cost Tracker */}
      <CostTracker dailyCost={costToday} />
      
      {/* Test List */}
      <TestList tests={tests} />
      
      {/* Compliance Status */}
      <ComplianceStatus geography={geography} />
      
      {/* Generate Button */}
      <Button onClick={handleGenerate}>
        Generate Tests from JIRA
      </Button>
    </div>
  );
};
```

**Context Menu (extension/commands.ts):**
```typescript
// commands.ts
export function activate(context: vscode.ExtensionContext) {
  
  // Right-click → Generate Test
  const generateCommand = vscode.commands.registerCommand(
    'fortress-labs.generateTest',
    async () => {
      const jiraKey = await vscode.window.showInputBox({
        prompt: 'Enter JIRA story key (e.g., BANK-123)'
      });
      
      if (jiraKey) {
        await generateTestFromJira(jiraKey);
      }
    }
  );
  
  // Right-click → Run Compliance Check
  const complianceCommand = vscode.commands.registerCommand(
    'fortress-labs.checkCompliance',
    async () => {
      const document = vscode.window.activeTextEditor?.document;
      if (document) {
        await runComplianceCheck(document);
      }
    }
  );
  
  context.subscriptions.push(generateCommand, complianceCommand);
}
```

---

#### **2. Extension Core**

**Command Handler:**
```typescript
// commandHandler.ts
export class CommandHandler {
  constructor(
    private apiClient: BackendAPIClient,
    private jiraClient: JiraClient
  ) {}
  
  async generateTest(jiraKey: string): Promise<void> {
    // Show progress
    await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: `Generating test for ${jiraKey}...`,
      cancellable: false
    }, async (progress) => {
      
      // Step 1: Fetch JIRA story
      progress.report({ increment: 10, message: 'Fetching JIRA story...' });
      const story = await this.jiraClient.fetchStory(jiraKey);
      
      // Step 2: Run Understanding Agent
      progress.report({ increment: 20, message: 'Analyzing requirements...' });
      const understanding = await this.apiClient.runAgent(
        'understanding',
        { jira_story: story }
      );
      
      // Step 3: Run Generation Agent
      progress.report({ increment: 40, message: 'Generating tests...' });
      const tests = await this.apiClient.runAgent(
        'generation',
        { understanding }
      );
      
      // Step 4: Create test file
      progress.report({ increment: 20, message: 'Creating file...' });
      await this.createTestFile(tests, jiraKey);
      
      // Step 5: Show result
      progress.report({ increment: 10, message: 'Done!' });
      vscode.window.showInformationMessage(
        `✅ Test generated for ${jiraKey}`
      );
    });
  }
  
  async createTestFile(tests: string, jiraKey: string): Promise<void> {
    const fileName = `test_${jiraKey.toLowerCase().replace('-', '_')}.py`;
    const filePath = vscode.Uri.file(
      path.join(vscode.workspace.rootPath!, 'tests', fileName)
    );
    
    await vscode.workspace.fs.writeFile(
      filePath,
      Buffer.from(tests, 'utf8')
    );
    
    // Open file
    const document = await vscode.workspace.openTextDocument(filePath);
    await vscode.window.showTextDocument(document);
  }
}
```

**State Manager:**
```typescript
// stateManager.ts
export class StateManager {
  private state: ExtensionState = {
    geography: 'usa',
    customerId: null,
    jiraConnected: false,
    backendConnected: false,
    tests: [],
    costTracking: {
      today: 0,
      month: 0
    }
  };
  
  private listeners: StateListener[] = [];
  
  setState(update: Partial<ExtensionState>): void {
    this.state = { ...this.state, ...update };
    this.notifyListeners();
  }
  
  getState(): ExtensionState {
    return this.state;
  }
  
  subscribe(listener: StateListener): void {
    this.listeners.push(listener);
  }
  
  private notifyListeners(): void {
    this.listeners.forEach(listener => listener(this.state));
  }
}
```

---

#### **3. API Client Layer**

**Backend API Client:**
```typescript
// backendAPIClient.ts
export class BackendAPIClient {
  constructor(
    private baseURL: string,
    private apiKey: string
  ) {}
  
  async runAgent(
    agentType: string,
    inputData: any
  ): Promise<any> {
    const response = await fetch(
      `${this.baseURL}/api/v1/agents/execute`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.apiKey}`
        },
        body: JSON.stringify({
          agent_type: agentType,
          input_data: inputData
        })
      }
    );
    
    if (!response.ok) {
      throw new Error(`Agent execution failed: ${response.statusText}`);
    }
    
    return await response.json();
  }
  
  async getRules(
    geography: string,
    regulator?: string
  ): Promise<ComplianceRule[]> {
    const params = new URLSearchParams({ geography });
    if (regulator) params.append('regulator', regulator);
    
    const response = await fetch(
      `${this.baseURL}/api/v1/rules?${params}`
    );
    
    const data = await response.json();
    return data.rules;
  }
  
  subscribeToUpdates(
    callback: (update: RuleUpdate) => void
  ): WebSocket {
    const ws = new WebSocket(
      `${this.baseURL.replace('http', 'ws')}/ws/rules/updates`
    );
    
    ws.onmessage = (event) => {
      const update = JSON.parse(event.data);
      callback(update);
    };
    
    return ws;
  }
}
```

---

**CONTINUED IN NEXT FILE DUE TO LENGTH...**

Would you like me to continue with:
1. Website verification guide
2. Research roadmap planning
3. Deployment architecture
4. Implementation timeline

Or shall I create all of these in one comprehensive delivery?

