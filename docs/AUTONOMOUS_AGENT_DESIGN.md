# AUTONOMOUS COMPLIANCE AGENT DESIGN
## Claude Code: Build Self-Improving Compliance Scraping System

**Mission:** Design and implement an autonomous agent that can read compliance requirements, scrape regulatory sources, implement scrapers, test automatically, and improve iteratively.

**Version:** v4 (Autonomous Agent Architecture)  
**Previous:** v3 (Manual 6-agent pipeline)  
**Improvement:** Self-improving agent that handles entire compliance workflow autonomously

---

## 🎯 **AGENT MISSION**

You are building an **Autonomous Compliance Agent** that:

1. ✅ **Reads** compliance source specifications (URLs, extraction requirements)
2. ✅ **Analyzes** document structure (PDF, HTML, CSV, XML)
3. ✅ **Implements** appropriate scraper code automatically
4. ✅ **Executes** scraper and validates data quality
5. ✅ **Tests** output against expected schema
6. ✅ **Iterates** and fixes errors autonomously
7. ✅ **Reports** progress and results

**Goal:** Transform 18 regulatory sources into database records with ZERO manual coding.

---

## 🏗️ **SYSTEM ARCHITECTURE**

### **High-Level Design:**

```
┌─────────────────────────────────────────────────────────────┐
│              AUTONOMOUS COMPLIANCE AGENT                     │
└─────────────────────────────────────────────────────────────┘

Input: Source Specification
┌──────────────────────────────┐
│ Source: OCC BSA/AML          │
│ URL: https://occ.gov/...pdf  │
│ Type: PDF                    │
│ Expected: 45 requirements    │
│ Fields: [requirement_text,   │
│          threshold_amount,   │
│          rule_type]          │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────┐
│              AGENT EXECUTION LOOP                        │
└──────────────────────────────────────────────────────────┘

Phase 1: ANALYSIS
├─ Download document
├─ Detect format (PDF/HTML/CSV/XML)
├─ Analyze structure (sections, tables, text blocks)
└─ Determine extraction strategy

Phase 2: IMPLEMENTATION
├─ Generate scraper code (Python)
├─ Implement extraction logic
├─ Add error handling
└─ Create validation checks

Phase 3: EXECUTION
├─ Run scraper
├─ Validate output format
├─ Check data quality
└─ Store in database

Phase 4: VERIFICATION
├─ Compare actual vs expected counts
├─ Validate field completeness
├─ Check data integrity
└─ Run test scenarios

Phase 5: ITERATION (if needed)
├─ Analyze failures
├─ Fix scraper code
├─ Re-run extraction
└─ Repeat until success

Output: Database Records
┌──────────────────────────────┐
│ ✅ 45 requirements stored    │
│ ✅ 8 test scenarios created  │
│ ✅ Data quality: 98%         │
│ ✅ All validations passed    │
└──────────────────────────────┘
```

---

## 📋 **AGENT SPECIFICATION**

### **Agent Class Structure:**

```python
class AutonomousComplianceAgent:
    """
    Self-improving agent that scrapes compliance sources autonomously
    
    Capabilities:
    - Document analysis (PDF, HTML, CSV, XML)
    - Code generation (scraper implementation)
    - Self-testing (validation and iteration)
    - Error recovery (auto-fix common issues)
    - Progress reporting (human oversight)
    """
    
    def __init__(self, source_spec: dict, bedrock_client, db_session):
        self.source = source_spec
        self.bedrock = bedrock_client
        self.db = db_session
        self.max_iterations = 5
        self.current_iteration = 0
        
    def execute(self) -> dict:
        """Main execution loop"""
        
        while self.current_iteration < self.max_iterations:
            try:
                # Phase 1: Analyze
                analysis = self.analyze_source()
                
                # Phase 2: Implement
                scraper_code = self.generate_scraper(analysis)
                
                # Phase 3: Execute
                results = self.run_scraper(scraper_code)
                
                # Phase 4: Verify
                validation = self.validate_results(results)
                
                if validation['success']:
                    return self.report_success(results, validation)
                else:
                    # Phase 5: Iterate
                    self.current_iteration += 1
                    self.fix_issues(validation['errors'])
                    
            except Exception as e:
                self.handle_error(e)
                self.current_iteration += 1
        
        return self.report_failure()
```

---

## 🔍 **PHASE 1: ANALYSIS**

### **Document Analysis Logic:**

```python
def analyze_source(self) -> dict:
    """
    Analyze compliance source and determine extraction strategy
    
    Returns:
        {
            'url': str,
            'format': 'pdf|html|csv|xml',
            'structure': {
                'has_toc': bool,
                'sections': list,
                'tables': list,
                'images': list
            },
            'extraction_strategy': str,
            'estimated_requirements': int
        }
    """
    
    # Step 1: Download document
    print(f"📥 Downloading: {self.source['url']}")
    content = self.download_document(self.source['url'])
    
    # Step 2: Detect format
    doc_format = self.detect_format(content)
    print(f"📄 Format detected: {doc_format}")
    
    # Step 3: Analyze structure
    if doc_format == 'pdf':
        structure = self.analyze_pdf_structure(content)
    elif doc_format == 'html':
        structure = self.analyze_html_structure(content)
    elif doc_format == 'csv':
        structure = self.analyze_csv_structure(content)
    elif doc_format == 'xml':
        structure = self.analyze_xml_structure(content)
    
    # Step 4: Determine extraction strategy
    strategy = self.choose_extraction_strategy(doc_format, structure)
    
    print(f"🎯 Extraction strategy: {strategy}")
    print(f"📊 Estimated requirements: {structure['estimated_count']}")
    
    return {
        'url': self.source['url'],
        'format': doc_format,
        'structure': structure,
        'extraction_strategy': strategy,
        'estimated_requirements': structure['estimated_count']
    }

def analyze_pdf_structure(self, pdf_content: bytes) -> dict:
    """Analyze PDF structure using pdfplumber"""
    
    import pdfplumber
    import io
    
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        # Extract table of contents
        toc = self.extract_pdf_toc(pdf)
        
        # Analyze first 10 pages to understand structure
        sample_pages = pdf.pages[:10]
        
        # Detect sections
        sections = []
        for page in sample_pages:
            text = page.extract_text()
            # Look for section headers (all caps, short lines)
            for line in text.split('\n'):
                if self.is_section_header(line):
                    sections.append(line.strip())
        
        # Detect tables
        tables = []
        for page in sample_pages:
            page_tables = page.extract_tables()
            if page_tables:
                tables.extend(page_tables)
        
        # Estimate requirement count
        total_pages = len(pdf.pages)
        requirements_per_page = 2  # Conservative estimate
        estimated_count = total_pages * requirements_per_page
        
        return {
            'has_toc': len(toc) > 0,
            'toc': toc,
            'sections': sections[:20],  # First 20 sections
            'tables_found': len(tables),
            'total_pages': total_pages,
            'estimated_count': estimated_count
        }

def choose_extraction_strategy(self, doc_format: str, structure: dict) -> str:
    """
    Determine best extraction strategy based on document analysis
    
    Strategies:
    - 'toc_based': Use table of contents to navigate
    - 'section_based': Extract by section headers
    - 'table_based': Extract tabular data
    - 'full_text_llm': Send full text to LLM
    - 'chunked_llm': Send chunks to LLM
    - 'direct_parse': Direct parsing (CSV, XML)
    """
    
    if doc_format == 'csv':
        return 'direct_parse'
    
    if doc_format == 'xml':
        return 'direct_parse'
    
    if doc_format == 'html':
        if structure['tables_found'] > 5:
            return 'table_based'
        else:
            return 'section_based'
    
    if doc_format == 'pdf':
        if structure['has_toc']:
            return 'toc_based'
        elif structure['tables_found'] > 10:
            return 'table_based'
        elif structure['total_pages'] < 50:
            return 'full_text_llm'
        else:
            return 'chunked_llm'
    
    return 'chunked_llm'  # Default
```

---

## 🛠️ **PHASE 2: IMPLEMENTATION**

### **Code Generation Logic:**

```python
def generate_scraper(self, analysis: dict) -> str:
    """
    Generate scraper code using Claude Sonnet
    
    Returns: Complete Python scraper code as string
    """
    
    # Build prompt for Claude
    prompt = f"""
You are a senior Python developer building a compliance data scraper.

SOURCE INFORMATION:
- URL: {analysis['url']}
- Format: {analysis['format']}
- Extraction Strategy: {analysis['extraction_strategy']}
- Expected Requirements: ~{analysis['estimated_requirements']}

DOCUMENT STRUCTURE:
{json.dumps(analysis['structure'], indent=2)}

TARGET SCHEMA:
- regulation_id: str (unique identifier)
- regulation_name: str (descriptive name)
- category: str (e.g., "BSA/AML", "Fair Lending")
- regulator: str (e.g., "OCC", "FinCEN")
- requirements: list of:
  - requirement_id: str
  - requirement_text: str (exact quote from document)
  - rule_type: str (e.g., "limit", "procedure", "reporting")
  - threshold_amount: float (if applicable)
  - threshold_currency: str (if applicable)
  - test_requirement: str (what to test)

TASK:
Generate complete Python scraper code that:
1. Downloads and parses the document
2. Extracts requirements using the specified strategy
3. Validates data quality
4. Returns structured data matching the schema
5. Includes comprehensive error handling
6. Has logging for debugging

REQUIREMENTS:
- Use appropriate libraries (pdfplumber, BeautifulSoup, pandas, etc.)
- Follow the extraction strategy: {analysis['extraction_strategy']}
- Include a main() function that returns results
- Add docstrings and comments
- Handle common errors (network, parsing, validation)

OUTPUT: Complete, runnable Python code (no explanations, just code)
"""
    
    print("🤖 Generating scraper code with Claude Sonnet...")
    
    response = self.bedrock.messages.create(
        model="anthropic.claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    scraper_code = response.content[0].text
    
    # Clean up code (remove markdown fences if present)
    scraper_code = self.clean_code(scraper_code)
    
    # Save code to file for inspection
    code_file = f"src/scrapers/usa/generated_{self.source['source_id']}_scraper.py"
    self.save_code(code_file, scraper_code)
    
    print(f"✅ Scraper code generated: {code_file}")
    
    return scraper_code

def clean_code(self, code: str) -> str:
    """Remove markdown code fences and extra whitespace"""
    
    # Remove ```python and ``` markers
    code = re.sub(r'^```python\n', '', code)
    code = re.sub(r'\n```$', '', code)
    code = code.strip()
    
    return code
```

---

## ⚙️ **PHASE 3: EXECUTION**

### **Scraper Execution Logic:**

```python
def run_scraper(self, scraper_code: str) -> dict:
    """
    Execute generated scraper code safely
    
    Returns:
        {
            'success': bool,
            'data': list,  # Extracted requirements
            'count': int,
            'errors': list,
            'execution_time': float
        }
    """
    
    import tempfile
    import subprocess
    import json
    from datetime import datetime
    
    print("🚀 Executing scraper...")
    
    start_time = datetime.now()
    
    try:
        # Save code to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(scraper_code)
            temp_file = f.name
        
        # Execute scraper in subprocess (isolation)
        result = subprocess.run(
            ['python', temp_file],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        # Parse output
        if result.returncode == 0:
            # Scraper succeeded
            output = result.stdout
            
            # Try to parse JSON output
            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                # Output is not JSON, try to extract data
                data = self.extract_data_from_output(output)
            
            execution_time = (datetime.now() - start_time).total_seconds()
            
            print(f"✅ Scraper completed in {execution_time:.2f}s")
            print(f"📊 Extracted {len(data.get('requirements', []))} requirements")
            
            return {
                'success': True,
                'data': data,
                'count': len(data.get('requirements', [])),
                'errors': [],
                'execution_time': execution_time
            }
        else:
            # Scraper failed
            error_msg = result.stderr
            
            print(f"❌ Scraper failed: {error_msg[:200]}")
            
            return {
                'success': False,
                'data': None,
                'count': 0,
                'errors': [error_msg],
                'execution_time': (datetime.now() - start_time).total_seconds()
            }
            
    except subprocess.TimeoutExpired:
        print("❌ Scraper timed out (5 minutes)")
        return {
            'success': False,
            'data': None,
            'count': 0,
            'errors': ['Execution timeout (5 minutes)'],
            'execution_time': 300
        }
    
    except Exception as e:
        print(f"❌ Execution error: {str(e)}")
        return {
            'success': False,
            'data': None,
            'count': 0,
            'errors': [str(e)],
            'execution_time': (datetime.now() - start_time).total_seconds()
        }
```

---

## ✅ **PHASE 4: VERIFICATION**

### **Data Validation Logic:**

```python
def validate_results(self, results: dict) -> dict:
    """
    Validate scraper results against expected schema and quality metrics
    
    Returns:
        {
            'success': bool,
            'validations': {
                'count_check': bool,
                'schema_check': bool,
                'quality_check': bool,
                'uniqueness_check': bool
            },
            'errors': list,
            'warnings': list,
            'quality_score': float  # 0-100
        }
    """
    
    if not results['success']:
        return {
            'success': False,
            'errors': results['errors'],
            'quality_score': 0
        }
    
    data = results['data']
    validations = {}
    errors = []
    warnings = []
    
    # 1. Count check
    expected_count = self.source.get('expected_count', 0)
    actual_count = results['count']
    
    if expected_count > 0:
        count_variance = abs(actual_count - expected_count) / expected_count
        validations['count_check'] = count_variance < 0.3  # Within 30%
        
        if not validations['count_check']:
            warnings.append(f"Count mismatch: expected ~{expected_count}, got {actual_count}")
    else:
        validations['count_check'] = actual_count > 0
    
    # 2. Schema check
    required_fields = ['regulation_id', 'regulation_name', 'requirements']
    requirement_fields = ['requirement_id', 'requirement_text', 'rule_type']
    
    schema_valid = True
    
    # Check top-level fields
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")
            schema_valid = False
    
    # Check requirement fields
    if 'requirements' in data:
        for i, req in enumerate(data['requirements'][:5]):  # Check first 5
            for field in requirement_fields:
                if field not in req:
                    errors.append(f"Requirement {i}: Missing field '{field}'")
                    schema_valid = False
    
    validations['schema_check'] = schema_valid
    
    # 3. Quality check
    quality_issues = []
    
    if 'requirements' in data:
        for i, req in enumerate(data['requirements']):
            # Check for empty text
            if not req.get('requirement_text') or len(req.get('requirement_text', '')) < 10:
                quality_issues.append(f"Requirement {i}: Text too short or empty")
            
            # Check for placeholder values
            if 'TBD' in str(req) or 'TODO' in str(req):
                quality_issues.append(f"Requirement {i}: Contains placeholder text")
    
    validations['quality_check'] = len(quality_issues) < actual_count * 0.1  # <10% issues
    
    if quality_issues:
        warnings.extend(quality_issues[:5])  # First 5 issues
    
    # 4. Uniqueness check
    if 'requirements' in data:
        requirement_ids = [r.get('requirement_id') for r in data['requirements']]
        unique_ids = set(requirement_ids)
        validations['uniqueness_check'] = len(unique_ids) == len(requirement_ids)
        
        if not validations['uniqueness_check']:
            duplicates = len(requirement_ids) - len(unique_ids)
            errors.append(f"Found {duplicates} duplicate requirement IDs")
    
    # Calculate quality score
    checks_passed = sum(validations.values())
    total_checks = len(validations)
    quality_score = (checks_passed / total_checks) * 100
    
    # Overall success
    success = all(validations.values()) and len(errors) == 0
    
    print(f"📊 Validation Results:")
    print(f"   Count: {'✅' if validations.get('count_check') else '❌'}")
    print(f"   Schema: {'✅' if validations.get('schema_check') else '❌'}")
    print(f"   Quality: {'✅' if validations.get('quality_check') else '❌'}")
    print(f"   Uniqueness: {'✅' if validations.get('uniqueness_check') else '❌'}")
    print(f"   Quality Score: {quality_score:.1f}%")
    
    return {
        'success': success,
        'validations': validations,
        'errors': errors,
        'warnings': warnings,
        'quality_score': quality_score
    }
```

---

## 🔄 **PHASE 5: ITERATION**

### **Self-Improvement Logic:**

```python
def fix_issues(self, errors: list):
    """
    Analyze errors and generate improved scraper code
    
    This method uses Claude to understand errors and fix the scraper
    """
    
    print(f"🔧 Iteration {self.current_iteration}: Analyzing {len(errors)} errors...")
    
    # Build error analysis prompt
    prompt = f"""
The scraper you generated encountered errors. Analyze and fix them.

ORIGINAL SOURCE SPEC:
{json.dumps(self.source, indent=2)}

ERRORS ENCOUNTERED:
{json.dumps(errors, indent=2)}

CURRENT SCRAPER CODE:
{self.last_generated_code}

TASK:
1. Analyze the errors
2. Identify root causes
3. Generate FIXED scraper code
4. Ensure all errors are addressed

Common issues to check:
- PDF parsing errors → try different extraction method
- Missing fields → adjust extraction logic
- Timeout → reduce document size or chunk processing
- Schema validation → ensure all required fields are populated

OUTPUT: Fixed Python scraper code (complete, runnable)
"""
    
    response = self.bedrock.messages.create(
        model="anthropic.claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    fixed_code = self.clean_code(response.content[0].text)
    
    # Save fixed code
    code_file = f"src/scrapers/usa/generated_{self.source['source_id']}_scraper_v{self.current_iteration}.py"
    self.save_code(code_file, fixed_code)
    
    self.last_generated_code = fixed_code
    
    print(f"✅ Generated fixed scraper (iteration {self.current_iteration})")
```

---

## 📊 **IMPLEMENTATION GUIDE FOR CLAUDE CODE**

### **Step-by-Step Execution:**

```markdown
STEP 1: Create Agent Framework
────────────────────────────────────────────────────────────

File: src/agents/autonomous_compliance_agent.py

Tasks:
1. Create AutonomousComplianceAgent class
2. Implement __init__ with bedrock client, db session
3. Add execute() main loop
4. Add logging and progress tracking

Verification:
- [ ] Agent class exists
- [ ] Can instantiate agent
- [ ] Logging works

STEP 2: Implement Analysis Phase
────────────────────────────────────────────────────────────

Methods to implement:
1. analyze_source() - Main analysis orchestrator
2. download_document() - HTTP download with retries
3. detect_format() - Detect PDF/HTML/CSV/XML
4. analyze_pdf_structure() - PDF analysis with pdfplumber
5. analyze_html_structure() - HTML analysis with BeautifulSoup
6. choose_extraction_strategy() - Strategy selection

Verification:
- [ ] Can download documents
- [ ] Format detection works (test with PDF, HTML, CSV)
- [ ] Structure analysis returns valid data
- [ ] Strategy selection makes sense

STEP 3: Implement Code Generation Phase
────────────────────────────────────────────────────────────

Methods to implement:
1. generate_scraper() - Main code generation
2. build_generation_prompt() - Create Claude prompt
3. clean_code() - Remove markdown fences
4. save_code() - Save to file

Verification:
- [ ] Claude API call succeeds
- [ ] Generated code is valid Python
- [ ] Code saved to correct location
- [ ] Code includes error handling

STEP 4: Implement Execution Phase
────────────────────────────────────────────────────────────

Methods to implement:
1. run_scraper() - Execute generated code
2. extract_data_from_output() - Parse scraper output
3. handle_execution_errors() - Error recovery

Verification:
- [ ] Scraper executes in subprocess
- [ ] Timeout works (5 minutes)
- [ ] Output parsing works
- [ ] Errors captured properly

STEP 5: Implement Validation Phase
────────────────────────────────────────────────────────────

Methods to implement:
1. validate_results() - Main validation
2. validate_schema() - Check required fields
3. validate_quality() - Data quality checks
4. validate_uniqueness() - Check for duplicates

Verification:
- [ ] All validation checks work
- [ ] Quality score calculation correct
- [ ] Errors/warnings reported properly

STEP 6: Implement Iteration Phase
────────────────────────────────────────────────────────────

Methods to implement:
1. fix_issues() - Error analysis and code fixing
2. analyze_errors() - Understand root causes
3. regenerate_code() - Create improved version

Verification:
- [ ] Error analysis works
- [ ] Fixed code generated
- [ ] Iteration counter increments
- [ ] Max iterations enforced

STEP 7: Testing
────────────────────────────────────────────────────────────

Test with OFAC SDN (easiest):
1. Run: agent.execute(ofac_source_spec)
2. Verify: CSV downloaded
3. Verify: Data parsed
4. Verify: 8000+ entries in database

Test with OCC BSA/AML (complex):
1. Run: agent.execute(occ_source_spec)
2. Verify: PDF downloaded
3. Verify: Requirements extracted
4. Verify: 45+ requirements in database

STEP 8: Integration
────────────────────────────────────────────────────────────

Create runner script:
File: scripts/run_autonomous_agent.py

```python
from src.agents.autonomous_compliance_agent import AutonomousComplianceAgent
from src.services.database import get_db

# Source specifications
sources = [
    {
        'source_id': 'ofac_sdn',
        'name': 'OFAC SDN List',
        'url': 'https://www.treasury.gov/ofac/downloads/sdn.csv',
        'format': 'csv',
        'expected_count': 8000
    },
    {
        'source_id': 'occ_bsa',
        'name': 'OCC BSA/AML Manual',
        'url': 'https://www.occ.gov/publications/.../bsa-manual.pdf',
        'format': 'pdf',
        'expected_count': 45
    }
]

# Run agent for each source
for source in sources:
    print(f"\n{'='*60}")
    print(f"Processing: {source['name']}")
    print(f"{'='*60}\n")
    
    agent = AutonomousComplianceAgent(
        source_spec=source,
        bedrock_client=bedrock,
        db_session=next(get_db())
    )
    
    result = agent.execute()
    
    if result['success']:
        print(f"✅ SUCCESS: {result['count']} records extracted")
    else:
        print(f"❌ FAILED: {result['errors']}")
```

Verification:
- [ ] Runner script works
- [ ] Can process multiple sources
- [ ] Progress reported clearly
- [ ] Results stored in database
```

---

## 🎯 **SOURCE SPECIFICATIONS**

### **Create Source Specs File:**

**File:** `config/compliance_sources.yaml`

```yaml
sources:
  - source_id: ofac_sdn
    name: OFAC SDN List
    regulator: OFAC
    category: Sanctions
    url: https://www.treasury.gov/ofac/downloads/sdn.csv
    format: csv
    update_frequency: daily
    expected_count: 8000
    priority: 1
    
  - source_id: occ_bsa
    name: OCC BSA/AML Examination Manual
    regulator: OCC
    category: BSA/AML
    url: https://www.occ.gov/publications-and-resources/publications/banker-education/files/pub-bsa-aml-examination-manual.pdf
    format: pdf
    update_frequency: monthly
    expected_count: 45
    priority: 1
    
  - source_id: fincen_sar
    name: FinCEN SAR Filing Requirements
    regulator: FinCEN
    category: AML
    url: https://www.fincen.gov/resources/filing-information
    format: html
    update_frequency: quarterly
    expected_count: 12
    priority: 1
    
  - source_id: cfpb_1071
    name: CFPB Section 1071
    regulator: CFPB
    category: Fair Lending
    url: https://www.consumerfinance.gov/rules-policy/final-rules/small-business-lending-data-collection-rule-1071/
    format: html
    update_frequency: as_needed
    expected_count: 88
    priority: 1
    compliance_deadline: 2026-07-01
    
  # ... 14 more sources
```

---

## 📈 **PROGRESS TRACKING**

### **Agent Dashboard:**

```python
class AgentDashboard:
    """Track agent progress across all sources"""
    
    def __init__(self):
        self.results = []
    
    def log_result(self, source_id: str, result: dict):
        """Log single source result"""
        self.results.append({
            'source_id': source_id,
            'success': result['success'],
            'count': result.get('count', 0),
            'quality_score': result.get('quality_score', 0),
            'iterations': result.get('iterations', 0),
            'execution_time': result.get('execution_time', 0)
        })
    
    def print_summary(self):
        """Print overall progress"""
        
        total = len(self.results)
        successful = sum(1 for r in self.results if r['success'])
        failed = total - successful
        
        total_records = sum(r['count'] for r in self.results)
        avg_quality = sum(r['quality_score'] for r in self.results) / total if total > 0 else 0
        
        print(f"\n{'='*60}")
        print(f"AGENT EXECUTION SUMMARY")
        print(f"{'='*60}")
        print(f"Sources Processed: {total}")
        print(f"✅ Successful: {successful}")
        print(f"❌ Failed: {failed}")
        print(f"📊 Total Records: {total_records:,}")
        print(f"⭐ Avg Quality: {avg_quality:.1f}%")
        print(f"{'='*60}\n")
        
        # Detailed results
        for result in self.results:
            status = '✅' if result['success'] else '❌'
            print(f"{status} {result['source_id']}: "
                  f"{result['count']} records, "
                  f"{result['quality_score']:.1f}% quality, "
                  f"{result['iterations']} iterations, "
                  f"{result['execution_time']:.1f}s")
```

---

## 🚀 **DEPLOYMENT INSTRUCTIONS**

### **For Claude Code to Execute:**

```markdown
DEPLOYMENT CHECKLIST
────────────────────────────────────────────────────────────

Prerequisites:
- [ ] Docker running (backend, postgres, weaviate, redis)
- [ ] AWS Bedrock credentials in .env
- [ ] Database migration completed (5 tables)
- [ ] Dependencies installed (pdfplumber, beautifulsoup4, pandas)

Execution Steps:

STEP 1: Verify Environment
────────────────────────────
cd ~/code_gen/textmind_banking_backend

# Test database connection
docker compose exec postgres psql -U fortress -d fortress_labs -c "SELECT 1;"

# Test Bedrock connection
docker compose exec backend python -c "
import boto3
bedrock = boto3.client('bedrock-runtime', region_name='us-west-2')
print('Bedrock connected ✅')
"

STEP 2: Install Agent Dependencies
────────────────────────────────────
docker compose exec backend pip install pdfplumber beautifulsoup4 lxml requests

STEP 3: Create Agent Files
────────────────────────────
# Create directory structure
mkdir -p src/agents
mkdir -p config

# Copy agent implementation (from this document)
# src/agents/autonomous_compliance_agent.py
# config/compliance_sources.yaml
# scripts/run_autonomous_agent.py

STEP 4: Test with OFAC (Quick Win)
────────────────────────────────────
docker compose exec backend python scripts/run_autonomous_agent.py --source ofac_sdn

# Expected output:
# ============================================================
# Processing: OFAC SDN List
# ============================================================
# 📥 Downloading: https://www.treasury.gov/ofac/downloads/sdn.csv
# 📄 Format detected: csv
# 🎯 Extraction strategy: direct_parse
# 🤖 Generating scraper code with Claude Sonnet...
# ✅ Scraper code generated: src/scrapers/usa/generated_ofac_sdn_scraper.py
# 🚀 Executing scraper...
# ✅ Scraper completed in 4.2s
# 📊 Extracted 8245 requirements
# ✅ SUCCESS: 8245 records extracted

STEP 5: Process All Sources
────────────────────────────────────
docker compose exec backend python scripts/run_autonomous_agent.py --all

# This will process all 18 sources sequentially
# Estimated time: 2-4 hours total

STEP 6: Verify Results
────────────────────────────────────
docker compose exec postgres psql -U fortress -d fortress_labs -c "
SELECT 
    category,
    COUNT(*) as requirement_count
FROM compliance_requirements cr
JOIN regulations r ON cr.regulation_id = r.regulation_id
GROUP BY category
ORDER BY requirement_count DESC;
"

# Expected output:
#   category    | requirement_count 
# --------------+-------------------
#  BSA/AML      |               156
#  Fair Lending |                92
#  Sanctions    |                 9
#  ...

STEP 7: Validation
────────────────────────────────────
docker compose exec backend python scripts/validate_all_compliance_data.py

# This runs comprehensive validation on all extracted data
```

---

## ✅ **SUCCESS CRITERIA**

**Agent is successful when:**

```yaml
phase_1_complete:
  - OFAC scraper auto-generated: true
  - 8000+ SDN entries stored: true
  - Quality score: >= 95%
  - Execution time: < 10 minutes

phase_2_complete:
  - OCC BSA scraper auto-generated: true
  - 45+ requirements stored: true
  - Quality score: >= 90%
  - Iterations needed: <= 3

all_sources_complete:
  - Total sources processed: 18
  - Success rate: >= 85% (15+ sources)
  - Total requirements: >= 300
  - Total test scenarios: >= 35
  - Average quality: >= 90%
  - Total time: < 4 hours
```

---

## 🎯 **CLAUDE CODE: YOUR ACTION PLAN**

**Start implementing NOW:**

```markdown
Day 1 (4 hours):
- [ ] Create AutonomousComplianceAgent class
- [ ] Implement Phase 1 (Analysis)
- [ ] Test with OFAC CSV (easiest)
- [ ] Verify: Can download and analyze documents

Day 2 (4 hours):
- [ ] Implement Phase 2 (Code Generation)
- [ ] Test with OFAC CSV
- [ ] Verify: Claude generates working scraper
- [ ] Run generated scraper successfully

Day 3 (4 hours):
- [ ] Implement Phase 3 & 4 (Execution & Validation)
- [ ] Test with OFAC
- [ ] Verify: Data stored in database
- [ ] All validations pass

Day 4 (4 hours):
- [ ] Implement Phase 5 (Iteration)
- [ ] Test with OCC BSA/AML PDF (complex)
- [ ] Verify: Agent can fix errors and retry
- [ ] Agent succeeds within 3 iterations

Day 5 (4 hours):
- [ ] Process all 18 sources
- [ ] Monitor progress
- [ ] Generate final report
- [ ] Celebrate! 🎉
```

**Total: 20 hours → 18 sources complete → 300+ requirements extracted**

---

**Ready to build an autonomous agent! Start with Day 1.** 🚀

**Report back after each day with progress.**
