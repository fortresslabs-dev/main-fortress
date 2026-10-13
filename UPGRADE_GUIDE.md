# UPGRADE GUIDE: V2 → V3 (Plugin System)
## How to Update Your Backend with Plugin Architecture

**Date:** March 9, 2026  
**From:** TEXT MIND Banking Backend V2  
**To:** Fortress Labs Backend V3 (with Plugin System)

---

## 🎯 **WHAT'S NEW IN V3**

✅ **Plugin System** - Geography-aware connector loading (USA/India)  
✅ **JIRA Integration** - Requirements source (500-line connector)  
✅ **Compliance Service** - Replaces hardcoded RBI rules  
✅ **USA Customer Support** - Wells Fargo configuration  
✅ **Claude Sonnet** - Replaces OpenAI GPT-4  
✅ **Auto-Discovery** - Connectors loaded automatically  
✅ **Configuration Files** - YAML-based (USA/India/Global)  

---

## 📦 **PACKAGE CONTENTS**

```
fortress-labs-backend-v3-merged.tar.gz
├── src/
│   ├── agents/ (UPDATED)
│   │   ├── understanding.py          ⚠️ UPDATED - Uses PluginManager
│   │   ├── generation.py             ⚠️ UPDATED - Uses Claude Sonnet
│   │   └── [5 other agents]          ✅ Unchanged
│   │
│   ├── connectors/ (NEW)
│   │   ├── base.py                   ✅ NEW - Base connector classes
│   │   ├── registry.py               ✅ NEW - Auto-discovery
│   │   ├── manager.py                ✅ NEW - Geography loader
│   │   ├── regulatory/
│   │   │   └── rbi/connector.py      ✅ NEW - RBI connector
│   │   └── requirements/
│   │       └── jira/connector.py     ✅ NEW - JIRA connector
│   │
│   ├── services/
│   │   ├── compliance_service.py     ✅ NEW - Dynamic compliance rules
│   │   ├── bedrock_service.py        ⚠️ UPDATED - Better error handling
│   │   └── [5 other services]        ✅ Unchanged
│   │
│   ├── api/                           ✅ Unchanged
│   ├── models/                        ✅ Unchanged
│   └── mcp_servers/                   ✅ Unchanged
│
├── config/ (NEW)
│   └── connectors/
│       ├── global.yaml
│       ├── india.yaml
│       ├── usa.yaml
│       └── customer_overrides/
│           ├── hdfc_bank.yaml
│           └── wells_fargo.yaml
│
├── scripts/ (NEW)
│   └── connectors/
│       └── test_jira.py
│
├── alembic/                           ✅ Unchanged
├── docker-compose.yml                 ✅ Unchanged
├── requirements.txt                   ⚠️ UPDATED - New dependencies
└── .env.example                       ⚠️ UPDATED - New variables
```

---

## 🚀 **QUICK START (5 MINUTES)**

### **Option A: Fresh Install (Recommended)**

```bash
# 1. Extract package
tar -xzf fortress-labs-backend-v3-merged.tar.gz
cd fortress-labs-backend-v3-merged

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
nano .env  # Edit with your credentials

# 5. Run database migrations
alembic upgrade head

# 6. Start services (Docker)
docker-compose up -d

# 7. Test JIRA connector (optional)
python scripts/connectors/test_jira.py

# 8. Start backend
uvicorn src.main:app --reload
```

### **Option B: Update Existing Installation**

```bash
# 1. Backup your current code
cp -r your-backend-v2 your-backend-v2-backup

# 2. Extract new files
tar -xzf fortress-labs-backend-v3-merged.tar.gz

# 3. Copy plugin system into your existing code
cp -r fortress-labs-backend-v3-merged/src/connectors your-backend-v2/src/
cp -r fortress-labs-backend-v3-merged/config your-backend-v2/
cp -r fortress-labs-backend-v3-merged/scripts your-backend-v2/

# 4. Update agent files
cp fortress-labs-backend-v3-merged/src/agents/understanding.py your-backend-v2/src/agents/
cp fortress-labs-backend-v3-merged/src/agents/generation.py your-backend-v2/src/agents/

# 5. Update services
cp fortress-labs-backend-v3-merged/src/services/compliance_service.py your-backend-v2/src/services/
cp fortress-labs-backend-v3-merged/src/services/bedrock_service.py your-backend-v2/src/services/

# 6. Update requirements
pip install jira structlog pyyaml requests beautifulsoup4 pdfplumber

# 7. Update .env
echo "CUSTOMER_ID=wells_fargo" >> .env
echo "REQUIREMENTS_JIRA_SERVER=https://your-company.atlassian.net" >> .env
echo "REQUIREMENTS_JIRA_EMAIL=your-email@company.com" >> .env
echo "REQUIREMENTS_JIRA_API_TOKEN=your-token" >> .env

# 8. Restart backend
uvicorn src.main:app --reload
```

---

## 🔧 **ENVIRONMENT VARIABLES**

### **NEW Variables (Add to .env):**

```bash
# Customer Configuration
CUSTOMER_ID=wells_fargo  # or hdfc_bank for India
GEOGRAPHY=usa  # or india (optional - auto-detected from CUSTOMER_ID)

# JIRA Integration (NEW)
REQUIREMENTS_JIRA_SERVER=https://your-company.atlassian.net
REQUIREMENTS_JIRA_EMAIL=your-email@company.com
REQUIREMENTS_JIRA_API_TOKEN=your-api-token-here

# Core Banking (for USA customers using FLEXCUBE)
CORE_BANKING_FLEXCUBE_HOST=flexcube-uat.bank.internal
CORE_BANKING_FLEXCUBE_PORT=1521
CORE_BANKING_FLEXCUBE_SERVICE=FLEXCUBE
CORE_BANKING_FLEXCUBE_READONLY_USER=fortress_readonly
CORE_BANKING_FLEXCUBE_READONLY_PASSWORD=xxxxx

# Core Banking (for India customers using Finacle)
CORE_BANKING_FINACLE_HOST=finacle-uat.bank.internal
CORE_BANKING_FINACLE_PORT=1521
CORE_BANKING_FINACLE_SERVICE=FINACLE
CORE_BANKING_FINACLE_READONLY_USER=fortress_readonly
CORE_BANKING_FINACLE_READONLY_PASSWORD=xxxxx
```

### **REMOVED Variables:**

```bash
# NO LONGER NEEDED:
# GPT_API_KEY=xxxxx  ❌ REMOVED (now using Claude Bedrock)
```

### **Existing Variables (Keep These):**

```bash
# AWS Bedrock
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret

# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/fortress_labs

# Redis
REDIS_URL=redis://localhost:6379/0

# Weaviate
WEAVIATE_URL=http://localhost:8080
```

---

## ✅ **VERIFICATION CHECKLIST**

### **Step 1: Check Plugin System**

```bash
python -c "from src.connectors.manager import PluginManager; \
           m = PluginManager(customer_id='wells_fargo'); \
           print(f'Geography: {m.get_geography()}'); \
           print(f'Connectors: {m._get_enabled_connectors()}');"

# Expected output:
# Geography: usa
# Connectors: ['core_banking.flexcube', 'regulatory.occ', 'requirements.jira', ...]
```

### **Step 2: Test JIRA Connector**

```bash
python scripts/connectors/test_jira.py

# Expected output:
# ✅ Connected to JIRA successfully
# ✅ Authenticated as: [Your Name]
# ✅ Story fetched: BANK-123
# ✅ JIRA connector is working!
```

### **Step 3: Test Updated Agents**

```bash
python -c "from src.agents.understanding import UnderstandingAgent; \
           agent = UnderstandingAgent(); \
           print('✅ Understanding Agent initialized with plugin system')"

python -c "from src.agents.generation import GenerationAgent; \
           agent = GenerationAgent(); \
           print('✅ Generation Agent initialized with Bedrock')"
```

### **Step 4: Test End-to-End**

```bash
# Start backend
uvicorn src.main:app --reload

# In another terminal, test agent pipeline
curl -X POST http://localhost:8000/api/agents/execute \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "understanding",
    "input_data": {
      "jira_story": "Implement wire transfer with dual approval"
    }
  }'

# Should return: Understanding analysis with compliance rules applied
```

---

## 🐛 **TROUBLESHOOTING**

### **Issue 1: ImportError: No module named 'jira'**

```bash
# Solution:
pip install jira structlog
```

### **Issue 2: JIRA connector authentication failed**

```bash
# Solution:
# 1. Get API token: https://id.atlassian.com/manage-profile/security/api-tokens
# 2. Update .env:
REQUIREMENTS_JIRA_SERVER=https://your-company.atlassian.net
REQUIREMENTS_JIRA_EMAIL=your-email@company.com
REQUIREMENTS_JIRA_API_TOKEN=paste-token-here
```

### **Issue 3: Plugin manager can't find connectors**

```bash
# Solution:
# Make sure folder structure is correct:
ls src/connectors/base.py
ls src/connectors/registry.py
ls src/connectors/manager.py

# If files missing, re-extract package
```

### **Issue 4: Oracle driver not found (cx_Oracle)**

```bash
# Solution (only if using FLEXCUBE/Finacle):
# 1. Install Oracle Instant Client
# 2. Install cx_Oracle:
pip install cx_Oracle

# If you're NOT using Oracle databases, comment out in requirements.txt:
# cx-Oracle==8.3.0  # Optional
```

---

## 📋 **WHAT CHANGED - DETAILED**

### **Updated Files (5 files):**

#### **1. src/agents/understanding.py**

**Before:**
```python
RBI_RULES = {
    "upi_limits": {"per_transaction": 100000}
}

class UnderstandingAgent:
    def _apply_banking_rules(self, patterns):
        return self.RBI_RULES.get("upi_limits")
```

**After:**
```python
from src.connectors.manager import PluginManager

class UnderstandingAgent:
    def __init__(self):
        self.plugin_manager = PluginManager(
            customer_id=os.getenv("CUSTOMER_ID")
        )
    
    def _apply_banking_rules(self, patterns):
        rules = self.plugin_manager.get_compliance_rules()
        return rules.get("upi_limits")
```

#### **2. src/agents/generation.py**

**Before:**
```python
from openai import OpenAI

class GenerationAgent:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("GPT_API_KEY"))
    
    async def generate(self, prompt):
        return self.client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[...]
        )
```

**After:**
```python
from src.services.bedrock_service import BedrockService

class GenerationAgent:
    def __init__(self):
        self.bedrock = BedrockService()
    
    async def generate(self, prompt):
        return await self.bedrock.generate_code(
            prompt=prompt,
            model="anthropic.claude-sonnet-4-20250514"
        )
```

#### **3. requirements.txt**

Added:
- jira==3.8.0
- structlog==24.1.0
- pyyaml==6.0.1
- requests, beautifulsoup4, pdfplumber (for scrapers)

#### **4. .env.example**

Added:
- CUSTOMER_ID
- REQUIREMENTS_JIRA_*
- CORE_BANKING_FLEXCUBE_*
- CORE_BANKING_FINACLE_*

Removed:
- GPT_API_KEY

---

## 🎯 **MIGRATION TIMELINE**

### **Immediate (Today):**
- ✅ Extract package
- ✅ Install dependencies
- ✅ Update .env
- ✅ Test JIRA connector

### **This Week:**
- ✅ Add JIRA credentials
- ✅ Test end-to-end workflow
- ✅ Deploy to staging

### **Next Week:**
- ✅ Get bank UAT database access (FLEXCUBE/Finacle)
- ✅ Add additional connectors (OCC/FDIC scrapers)
- ✅ Production deployment

---

## ✅ **SUCCESS CRITERIA**

After upgrade, you should be able to:

1. ✅ **Load connectors by geography:**
   ```python
   manager = PluginManager(customer_id="wells_fargo")
   assert manager.get_geography() == "usa"
   ```

2. ✅ **Fetch JIRA stories:**
   ```python
   jira = manager.get_connector("requirements.jira")
   story = jira.fetch_story("BANK-123")
   assert story['compliance_related'] == True
   ```

3. ✅ **Generate tests with compliance rules:**
   ```python
   understanding_agent = UnderstandingAgent()
   result = await understanding_agent.execute(
       input_data={"jira_story": "..."}
   )
   assert "banking_rules" in result
   ```

4. ✅ **Use Claude Sonnet (not OpenAI):**
   ```python
   generation_agent = GenerationAgent()
   tests = await generation_agent.generate_tests(...)
   # Uses Bedrock, not OpenAI
   ```

---

## 🆘 **NEED HELP?**

- **Documentation:** See BACKEND_CODE_COMPARISON.md
- **JIRA Setup:** See JIRA_CONNECTOR_QUICK_START.md
- **Plugin System:** See CONNECTOR_IMPLEMENTATION_CHECKLIST.md
- **Data Sources:** See DATA_SOURCE_COMPLETE_STRATEGY.md

---

**You're ready to upgrade! The plugin system is backward-compatible - all your V2 code still works!** 🚀
