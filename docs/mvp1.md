# FORTRESS LABS: MVP COMPLIANCE SOURCE SCRAPING GUIDE
## 6 Core Sources - URLs, Analysis, and Implementation Strategy

**Date:** April 2, 2026  
**Goal:** 100% reliable scraping for OCC, FDIC, FinCEN, OFAC, Reg E, CFPB

---

## 🎯 **SOURCE 1: OCC (Office of the Comptroller of the Currency)**

### **A. Primary URLs (High Priority)**

**1. BSA/AML Guidance**
```
URL: https://www.occ.gov/topics/supervision-and-examination/bsa/index-bsa.html
Format: HTML (structured)
Update Frequency: Quarterly
Content: BSA/AML examination procedures, guidance
```

**2. Comptroller's Handbook**
```
URL: https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/index-comptrollers-handbook.html
Format: PDF + HTML
Update Frequency: Monthly
Content: Banking supervision guidance, best practices
```

**3. Regulations (12 CFR Parts)**
```
URL: https://www.occ.gov/topics/laws-and-regulations/index-laws-and-regulations.html
Format: HTML (links to ecfr.gov)
Update Frequency: Annual (with periodic amendments)
Content: Official OCC regulations
```

**4. Bulletins and Alerts**
```
URL: https://www.occ.gov/news-issuances/bulletins/index-bulletins.html
Format: HTML + PDF
Update Frequency: Weekly
Content: New guidance, regulatory changes
```

---

### **B. Document Structure Analysis**

**Example: BSA/AML Page Structure**
```html
<div class="content-wrapper">
  <h1>Bank Secrecy Act/Anti-Money Laundering</h1>
  <div class="article-body">
    <h2>Overview</h2>
    <p>Banks must implement BSA/AML programs...</p>
    
    <h2>Requirements</h2>
    <ul>
      <li>Customer Identification Program (CIP)</li>
      <li>Suspicious Activity Reporting (SAR)</li>
      <li>Currency Transaction Reporting (CTR)</li>
    </ul>
    
    <h2>Related Resources</h2>
    <ul>
      <li><a href="/pub/handbook.pdf">BSA/AML Handbook (PDF)</a></li>
    </ul>
  </div>
</div>
```

**Key Extraction Targets:**
- Headers (H1, H2, H3) → Rule categories
- Paragraphs → Rule text
- Lists → Specific requirements
- Links → Related PDFs, guidance documents

---

### **C. Scraping Strategy**

**Tool Recommendation:** `unstructured` + `beautifulsoup4` + `requests`

**Why:**
- `unstructured` handles PDF extraction cleanly
- `beautifulsoup4` parses HTML structure
- `requests` handles HTTP with retry logic

**Installation:**
```bash
pip install unstructured[pdf] beautifulsoup4 requests lxml
pip install python-docx python-pptx pillow  # unstructured dependencies
```

**Implementation:**

```python
import requests
from bs4 import BeautifulSoup
from unstructured.partition.auto import partition
from unstructured.chunking.title import chunk_by_title
import hashlib
from datetime import datetime

class OCCSourceScraper:
    def __init__(self):
        self.base_url = "https://www.occ.gov"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'FortressLabs-ComplianceBot/1.0'
        })
    
    def scrape_bsa_guidance(self):
        """Scrape BSA/AML guidance pages"""
        url = f"{self.base_url}/topics/supervision-and-examination/bsa/index-bsa.html"
        
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        # Extract main content
        content_div = soup.find('div', class_='article-body')
        if not content_div:
            content_div = soup.find('main') or soup.find('article')
        
        rules = []
        
        # Extract structured content
        for section in content_div.find_all(['h1', 'h2', 'h3']):
            rule = {
                'source': 'OCC',
                'category': section.name,  # h1, h2, h3
                'title': section.get_text(strip=True),
                'content': [],
                'url': url,
                'scraped_at': datetime.utcnow().isoformat()
            }
            
            # Get content until next header
            next_elements = []
            for sibling in section.find_next_siblings():
                if sibling.name in ['h1', 'h2', 'h3']:
                    break
                if sibling.name in ['p', 'ul', 'ol', 'div']:
                    next_elements.append(sibling.get_text(strip=True))
            
            rule['content'] = ' '.join(next_elements)
            
            # Generate hash for change detection
            rule['hash'] = hashlib.sha256(
                rule['content'].encode('utf-8')
            ).hexdigest()
            
            rules.append(rule)
        
        return rules
    
    def scrape_pdf_handbook(self, pdf_url):
        """Scrape PDF documents using unstructured"""
        response = self.session.get(pdf_url, timeout=60)
        response.raise_for_status()
        
        # Save temporarily
        pdf_path = '/tmp/occ_handbook.pdf'
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        
        # Use unstructured to partition PDF
        elements = partition(filename=pdf_path)
        
        # Chunk by title (intelligent chunking)
        chunks = chunk_by_title(elements, max_characters=2000)
        
        rules = []
        for chunk in chunks:
            rule = {
                'source': 'OCC',
                'category': 'Handbook',
                'title': chunk.metadata.get('title', 'Unknown'),
                'content': chunk.text,
                'page_number': chunk.metadata.get('page_number'),
                'url': pdf_url,
                'scraped_at': datetime.utcnow().isoformat(),
                'hash': hashlib.sha256(chunk.text.encode('utf-8')).hexdigest()
            }
            rules.append(rule)
        
        return rules
    
    def detect_updates(self, existing_rules, new_rules):
        """Detect changes between existing and new rules"""
        existing_hashes = {r['hash']: r for r in existing_rules}
        new_hashes = {r['hash']: r for r in new_rules}
        
        added = [r for h, r in new_hashes.items() if h not in existing_hashes]
        deleted = [r for h, r in existing_hashes.items() if h not in new_hashes]
        
        return {
            'added': added,
            'deleted': deleted,
            'total_new': len(new_rules),
            'total_old': len(existing_rules)
        }

# Usage
scraper = OCCSourceScraper()

# Scrape HTML guidance
html_rules = scraper.scrape_bsa_guidance()
print(f"Scraped {len(html_rules)} rules from HTML")

# Scrape PDF handbook
pdf_rules = scraper.scrape_pdf_handbook(
    'https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/bsa-aml/pub-ch-bsa.pdf'
)
print(f"Scraped {len(pdf_rules)} rules from PDF")

# Detect updates (compare with database)
# updates = scraper.detect_updates(existing_rules_from_db, html_rules)
```

---

### **D. Update Detection Strategy**

**Approach 1: RSS Feeds (Recommended)**
```python
import feedparser

def check_occ_updates():
    # OCC doesn't have RSS, but we can check bulletins page
    url = "https://www.occ.gov/news-issuances/bulletins/index-bulletins.html"
    
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'lxml')
    
    # Find latest bulletin
    latest_bulletin = soup.find('div', class_='bulletin-item')
    if latest_bulletin:
        date_str = latest_bulletin.find('span', class_='date').get_text(strip=True)
        title = latest_bulletin.find('a').get_text(strip=True)
        
        return {
            'latest_date': date_str,
            'latest_title': title
        }
```

**Approach 2: Hash-Based Change Detection**
```python
def detect_page_changes(url, stored_hash):
    """Detect if page content changed"""
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'lxml')
    
    # Extract main content
    content = soup.find('div', class_='article-body')
    content_text = content.get_text(strip=True) if content else ''
    
    # Calculate hash
    current_hash = hashlib.sha256(content_text.encode('utf-8')).hexdigest()
    
    # Compare
    if current_hash != stored_hash:
        return {
            'changed': True,
            'new_hash': current_hash,
            'old_hash': stored_hash
        }
    
    return {'changed': False}
```

---

### **E. Cost & Performance**

**Open Source (Recommended for MVP):**
- Tools: `unstructured`, `beautifulsoup4`, `requests`
- Cost: $0
- Performance: ~5-10 seconds per page, ~30-60 seconds per PDF
- Reliability: 95%+ (handle retries, timeouts)

**Paid Alternative (If Open Source Fails):**
- Tool: `Diffbot` (https://www.diffbot.com/)
- Cost: $299/month (10,000 pages)
- Performance: ~2 seconds per page
- Reliability: 99%+

**Recommendation:** Start with open source. Only use Diffbot if you encounter complex JavaScript rendering or CAPTCHAs.

---

## 🎯 **SOURCE 2: FDIC (Federal Deposit Insurance Corporation)**

### **A. Primary URLs**

**1. Compliance Manual**
```
URL: https://www.fdic.gov/resources/supervision-and-examinations/consumer-compliance-examination-manual/
Format: HTML (structured chapters)
Update Frequency: Quarterly
Content: Consumer compliance examination procedures
```

**2. Regulations and Laws**
```
URL: https://www.fdic.gov/regulations/laws/rules/
Format: HTML + Links to ecfr.gov
Update Frequency: Annual
Content: FDIC regulations (12 CFR Parts)
```

**3. Financial Institution Letters (FILs)**
```
URL: https://www.fdic.gov/resources/bankers/financial-institution-letters/
Format: HTML + PDF
Update Frequency: Monthly
Content: Regulatory guidance, policy updates
```

---

### **B. Document Structure**

**Example: Compliance Manual Structure**
```html
<div class="manual-section">
  <h2>I. Introduction</h2>
  <div class="section-content">
    <h3>Overview</h3>
    <p>The FDIC examines banks for compliance with...</p>
    
    <h3>Examination Procedures</h3>
    <ol>
      <li>Review bank's compliance management system</li>
      <li>Test for BSA/AML compliance</li>
      <li>Verify CRA requirements</li>
    </ol>
  </div>
</div>
```

---

### **C. Scraping Strategy**

```python
class FDICSourceScraper:
    def __init__(self):
        self.base_url = "https://www.fdic.gov"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'FortressLabs-ComplianceBot/1.0'
        })
    
    def scrape_compliance_manual(self):
        """Scrape FDIC Compliance Manual"""
        url = f"{self.base_url}/resources/supervision-and-examinations/consumer-compliance-examination-manual/"
        
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find all manual sections
        sections = soup.find_all('div', class_='manual-section')
        
        for section in sections:
            # Extract section title
            title_elem = section.find(['h2', 'h3'])
            if not title_elem:
                continue
            
            title = title_elem.get_text(strip=True)
            
            # Extract content
            content_elem = section.find('div', class_='section-content')
            if not content_elem:
                content_elem = section
            
            content = content_elem.get_text(strip=True)
            
            rule = {
                'source': 'FDIC',
                'category': 'Compliance Manual',
                'title': title,
                'content': content,
                'url': url,
                'scraped_at': datetime.utcnow().isoformat(),
                'hash': hashlib.sha256(content.encode('utf-8')).hexdigest()
            }
            
            rules.append(rule)
        
        return rules
    
    def scrape_fils(self):
        """Scrape Financial Institution Letters"""
        url = f"{self.base_url}/resources/bankers/financial-institution-letters/"
        
        response = self.session.get(url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find FIL listings (typically in a table or list)
        fil_items = soup.find_all('div', class_='fil-item')
        
        for item in fil_items[:20]:  # Get latest 20 FILs
            fil_link = item.find('a')
            if not fil_link:
                continue
            
            fil_url = fil_link.get('href')
            if not fil_url.startswith('http'):
                fil_url = self.base_url + fil_url
            
            # Scrape individual FIL
            fil_response = self.session.get(fil_url, timeout=30)
            fil_soup = BeautifulSoup(fil_response.content, 'lxml')
            
            # Extract FIL content
            content_div = fil_soup.find('div', class_='fil-content')
            if content_div:
                rule = {
                    'source': 'FDIC',
                    'category': 'FIL',
                    'title': fil_link.get_text(strip=True),
                    'content': content_div.get_text(strip=True),
                    'url': fil_url,
                    'scraped_at': datetime.utcnow().isoformat(),
                    'hash': hashlib.sha256(
                        content_div.get_text(strip=True).encode('utf-8')
                    ).hexdigest()
                }
                rules.append(rule)
        
        return rules

# Usage
fdic_scraper = FDICSourceScraper()
manual_rules = fdic_scraper.scrape_compliance_manual()
fil_rules = fdic_scraper.scrape_fils()
```

---

### **D. Update Detection**

**FDIC has RSS feeds!**
```python
import feedparser

def check_fdic_updates():
    """Check FDIC RSS feed for updates"""
    rss_url = "https://www.fdic.gov/about/subscriptions/rss.xml"
    
    feed = feedparser.parse(rss_url)
    
    latest_items = []
    for entry in feed.entries[:10]:  # Latest 10 items
        latest_items.append({
            'title': entry.title,
            'link': entry.link,
            'published': entry.published,
            'summary': entry.summary
        })
    
    return latest_items
```

---

## 🎯 **SOURCE 3: FinCEN (Financial Crimes Enforcement Network)**

### **A. Primary URLs**

**1. Advisories and Guidance**
```
URL: https://www.fincen.gov/resources/advisories
Format: HTML + PDF
Update Frequency: Monthly
Content: AML/CFT guidance, red flag indicators
```

**2. Beneficial Ownership Information (BOI)**
```
URL: https://www.fincen.gov/boi
Format: HTML
Update Frequency: Quarterly
Content: CDD requirements, beneficial ownership rules
```

**3. Financial Institution Advisories**
```
URL: https://www.fincen.gov/financial-institution-advisories
Format: HTML + PDF
Update Frequency: Bi-weekly
Content: Specific AML threats, guidance
```

**4. Regulations**
```
URL: https://www.fincen.gov/resources/statutes-regulations/administrative-rulings
Format: HTML + PDF
Update Frequency: Annual
Content: Official FinCEN regulations
```

---

### **B. Scraping Strategy**

```python
class FinCENSourceScraper:
    def __init__(self):
        self.base_url = "https://www.fincen.gov"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'FortressLabs-ComplianceBot/1.0'
        })
    
    def scrape_advisories(self):
        """Scrape FinCEN advisories"""
        url = f"{self.base_url}/resources/advisories"
        
        response = self.session.get(url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find advisory listings
        advisory_items = soup.find_all('div', class_='advisory-item')
        
        for item in advisory_items:
            advisory_link = item.find('a')
            if not advisory_link:
                continue
            
            advisory_url = advisory_link.get('href')
            if not advisory_url.startswith('http'):
                advisory_url = self.base_url + advisory_url
            
            # Check if PDF or HTML
            if advisory_url.endswith('.pdf'):
                # Use unstructured for PDF
                pdf_response = self.session.get(advisory_url, timeout=60)
                pdf_path = '/tmp/fincen_advisory.pdf'
                with open(pdf_path, 'wb') as f:
                    f.write(pdf_response.content)
                
                elements = partition(filename=pdf_path)
                chunks = chunk_by_title(elements, max_characters=2000)
                
                for chunk in chunks:
                    rule = {
                        'source': 'FinCEN',
                        'category': 'Advisory',
                        'title': advisory_link.get_text(strip=True),
                        'content': chunk.text,
                        'url': advisory_url,
                        'scraped_at': datetime.utcnow().isoformat(),
                        'hash': hashlib.sha256(chunk.text.encode('utf-8')).hexdigest()
                    }
                    rules.append(rule)
            else:
                # HTML advisory
                advisory_response = self.session.get(advisory_url, timeout=30)
                advisory_soup = BeautifulSoup(advisory_response.content, 'lxml')
                
                content_div = advisory_soup.find('div', class_='field--name-body')
                if content_div:
                    rule = {
                        'source': 'FinCEN',
                        'category': 'Advisory',
                        'title': advisory_link.get_text(strip=True),
                        'content': content_div.get_text(strip=True),
                        'url': advisory_url,
                        'scraped_at': datetime.utcnow().isoformat(),
                        'hash': hashlib.sha256(
                            content_div.get_text(strip=True).encode('utf-8')
                        ).hexdigest()
                    }
                    rules.append(rule)
        
        return rules
    
    def scrape_boi_requirements(self):
        """Scrape BOI (Beneficial Ownership Information) requirements"""
        url = f"{self.base_url}/boi"
        
        response = self.session.get(url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        # Extract main content
        content_div = soup.find('div', class_='field--name-body')
        
        if content_div:
            # Split by sections
            sections = content_div.find_all(['h2', 'h3'])
            
            rules = []
            for section in sections:
                title = section.get_text(strip=True)
                
                # Get content until next header
                content_parts = []
                for sibling in section.find_next_siblings():
                    if sibling.name in ['h2', 'h3']:
                        break
                    if sibling.name in ['p', 'ul', 'ol']:
                        content_parts.append(sibling.get_text(strip=True))
                
                content = ' '.join(content_parts)
                
                rule = {
                    'source': 'FinCEN',
                    'category': 'BOI',
                    'title': title,
                    'content': content,
                    'url': url,
                    'scraped_at': datetime.utcnow().isoformat(),
                    'hash': hashlib.sha256(content.encode('utf-8')).hexdigest()
                }
                rules.append(rule)
            
            return rules
        
        return []

# Usage
fincen_scraper = FinCENSourceScraper()
advisory_rules = fincen_scraper.scrape_advisories()
boi_rules = fincen_scraper.scrape_boi_requirements()
```

---

### **C. Update Detection**

**FinCEN RSS Feed:**
```python
def check_fincen_updates():
    """Check FinCEN RSS for new advisories"""
    rss_url = "https://www.fincen.gov/rss.xml"
    
    feed = feedparser.parse(rss_url)
    
    new_advisories = []
    for entry in feed.entries[:10]:
        if 'advisory' in entry.title.lower() or 'guidance' in entry.title.lower():
            new_advisories.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.published
            })
    
    return new_advisories
```

---

## 🎯 **SOURCE 4: OFAC (Office of Foreign Assets Control)**

### **A. Primary URLs**

**1. SDN (Specially Designated Nationals) List - API ✅**
```
URL: https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML
Format: XML (structured API)
Update Frequency: Daily
Content: Sanctioned individuals, entities, vessels
```

**2. Consolidated Sanctions List**
```
URL: https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONSOLIDATED.XML
Format: XML
Update Frequency: Daily
Content: All OFAC sanctions programs
```

**3. Sanctions Programs**
```
URL: https://ofac.treasury.gov/sanctions-programs-and-country-information
Format: HTML
Update Frequency: Weekly
Content: Country-specific sanctions programs
```

---

### **B. Scraping Strategy (API-Based) ✅**

**OFAC provides an official API - USE THIS!**

```python
import xml.etree.ElementTree as ET
import requests
from datetime import datetime

class OFACSourceScraper:
    def __init__(self):
        self.sdn_api_url = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
        self.consolidated_api_url = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONSOLIDATED.XML"
        self.session = requests.Session()
    
    def scrape_sdn_list(self):
        """Scrape SDN list from OFAC API"""
        response = self.session.get(self.sdn_api_url, timeout=60)
        response.raise_for_status()
        
        # Parse XML
        root = ET.fromstring(response.content)
        
        entities = []
        
        # Extract SDN entries
        for sdn_entry in root.findall('.//sdnEntry'):
            entity = {
                'source': 'OFAC',
                'category': 'SDN',
                'uid': sdn_entry.find('uid').text if sdn_entry.find('uid') is not None else None,
                'first_name': sdn_entry.find('firstName').text if sdn_entry.find('firstName') is not None else '',
                'last_name': sdn_entry.find('lastName').text if sdn_entry.find('lastName') is not None else '',
                'entity_name': sdn_entry.find('entityName').text if sdn_entry.find('entityName') is not None else '',
                'sdn_type': sdn_entry.find('sdnType').text if sdn_entry.find('sdnType') is not None else '',
                'program': sdn_entry.find('programList/program').text if sdn_entry.find('programList/program') is not None else '',
                'remarks': sdn_entry.find('remarks').text if sdn_entry.find('remarks') is not None else '',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
            # Generate hash for change detection
            entity_str = f"{entity['uid']}_{entity['first_name']}_{entity['last_name']}_{entity['entity_name']}"
            entity['hash'] = hashlib.sha256(entity_str.encode('utf-8')).hexdigest()
            
            entities.append(entity)
        
        return entities
    
    def scrape_consolidated_list(self):
        """Scrape consolidated sanctions list"""
        response = self.session.get(self.consolidated_api_url, timeout=60)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        
        entities = []
        
        # Parse all entries
        for entry in root.findall('.//entry'):
            entity = {
                'source': 'OFAC',
                'category': 'Consolidated',
                'name': entry.find('name').text if entry.find('name') is not None else '',
                'type': entry.find('type').text if entry.find('type') is not None else '',
                'program': entry.find('program').text if entry.find('program') is not None else '',
                'list': entry.find('list').text if entry.find('list') is not None else '',
                'scraped_at': datetime.utcnow().isoformat()
            }
            
            entity_str = f"{entity['name']}_{entity['type']}_{entity['program']}"
            entity['hash'] = hashlib.sha256(entity_str.encode('utf-8')).hexdigest()
            
            entities.append(entity)
        
        return entities
    
    def check_for_updates(self):
        """Check OFAC API for last update timestamp"""
        # OFAC provides metadata endpoint
        metadata_url = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML"
        
        response = self.session.get(metadata_url, timeout=30)
        root = ET.fromstring(response.content)
        
        # Extract publish date
        publish_date_elem = root.find('.//Publish_Date')
        if publish_date_elem is not None:
            return {
                'last_updated': publish_date_elem.text,
                'needs_update': True  # Compare with stored date
            }
        
        return {'last_updated': None}

# Usage
ofac_scraper = OFACSourceScraper()

# Scrape SDN list (~8,000 entities)
sdn_entities = ofac_scraper.scrape_sdn_list()
print(f"Scraped {len(sdn_entities)} SDN entities")

# Scrape consolidated list (~12,000 entities)
consolidated_entities = ofac_scraper.scrape_consolidated_list()
print(f"Scraped {len(consolidated_entities)} consolidated entities")

# Check for updates
update_info = ofac_scraper.check_for_updates()
print(f"OFAC last updated: {update_info['last_updated']}")
```

---

### **C. Performance & Reliability**

**OFAC API is VERY reliable:**
- ✅ Official government API
- ✅ XML format (structured, easy to parse)
- ✅ Daily updates
- ✅ No rate limits for reasonable use
- ✅ No authentication required

**Expected Performance:**
- SDN list: ~8,000 entities, ~10-15 seconds to download/parse
- Consolidated: ~12,000 entities, ~20-30 seconds
- **Total: <1 minute for full OFAC data**

---

## 🎯 **SOURCE 5: Reg E (Electronic Fund Transfers)**

### **A. Primary URLs**

**1. CFPB Regulation E**
```
URL: https://www.consumerfinance.gov/rules-policy/regulations/1005/
Format: HTML (structured by sections)
Update Frequency: Annual (with occasional amendments)
Content: Official Reg E text (12 CFR Part 1005)
```

**2. Official Commentary**
```
URL: https://www.consumerfinance.gov/rules-policy/regulations/1005/interp/
Format: HTML
Update Frequency: Annual
Content: Official staff interpretations
```

**3. Compliance Guides**
```
URL: https://www.consumerfinance.gov/compliance/compliance-resources/
Format: HTML + PDF
Update Frequency: Quarterly
Content: Small entity compliance guides
```

---

### **B. Document Structure**

**Example: Reg E Section Structure**
```html
<div class="regulation-content">
  <h2>§ 1005.2 Definitions</h2>
  <div class="section-body">
    <p>(a) Access device means...</p>
    <p>(b) Account means...</p>
    <p>(c) Act means the Electronic Fund Transfer Act...</p>
  </div>
  
  <h2>§ 1005.3 Coverage</h2>
  <div class="section-body">
    <p>(a) General. This regulation applies to...</p>
  </div>
</div>
```

---

### **C. Scraping Strategy**

```python
class RegESourceScraper:
    def __init__(self):
        self.base_url = "https://www.consumerfinance.gov"
        self.reg_e_url = f"{self.base_url}/rules-policy/regulations/1005/"
        self.session = requests.Session()
    
    def scrape_reg_e_sections(self):
        """Scrape all Reg E sections"""
        response = self.session.get(self.reg_e_url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find all regulation sections
        sections = soup.find_all('div', class_='reg-section')
        
        for section in sections:
            # Extract section number
            section_heading = section.find(['h2', 'h3'])
            if not section_heading:
                continue
            
            section_title = section_heading.get_text(strip=True)
            
            # Extract section content
            section_body = section.find('div', class_='section-body')
            if not section_body:
                section_body = section
            
            content = section_body.get_text(strip=True)
            
            rule = {
                'source': 'Reg E',
                'category': 'Regulation',
                'section': section_title,
                'content': content,
                'url': self.reg_e_url,
                'scraped_at': datetime.utcnow().isoformat(),
                'hash': hashlib.sha256(content.encode('utf-8')).hexdigest()
            }
            
            rules.append(rule)
        
        return rules
    
    def scrape_reg_e_commentary(self):
        """Scrape official commentary"""
        commentary_url = f"{self.base_url}/rules-policy/regulations/1005/interp/"
        
        response = self.session.get(commentary_url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find commentary sections
        commentary_sections = soup.find_all('div', class_='interp-section')
        
        for section in commentary_sections:
            heading = section.find(['h2', 'h3'])
            if not heading:
                continue
            
            title = heading.get_text(strip=True)
            content = section.get_text(strip=True)
            
            rule = {
                'source': 'Reg E',
                'category': 'Commentary',
                'section': title,
                'content': content,
                'url': commentary_url,
                'scraped_at': datetime.utcnow().isoformat(),
                'hash': hashlib.sha256(content.encode('utf-8')).hexdigest()
            }
            
            rules.append(rule)
        
        return rules

# Usage
reg_e_scraper = RegESourceScraper()
reg_e_sections = reg_e_scraper.scrape_reg_e_sections()
reg_e_commentary = reg_e_scraper.scrape_reg_e_commentary()
```

---

### **D. Alternative: eCFR API (Official Government API)**

**Better Option: Use eCFR (Electronic Code of Federal Regulations) API**

```python
class eCFRScraper:
    """Use official eCFR API for Reg E (12 CFR Part 1005)"""
    
    def __init__(self):
        self.api_base = "https://www.ecfr.gov/api/renderer/v1"
    
    def scrape_reg_e_via_api(self):
        """Scrape Reg E using eCFR API"""
        # Reg E is 12 CFR Part 1005
        url = f"{self.api_base}/content/enhanced/2024-01-01/title-12/chapter-X/part-1005"
        
        response = requests.get(url, timeout=30)
        data = response.json()
        
        rules = []
        
        # Parse JSON structure
        for section in data.get('children', []):
            rule = {
                'source': 'Reg E',
                'category': 'Regulation',
                'section': section.get('identifier'),
                'title': section.get('label'),
                'content': section.get('text', ''),
                'url': f"https://www.ecfr.gov/current/title-12/chapter-X/part-1005/section-{section.get('identifier')}",
                'scraped_at': datetime.utcnow().isoformat(),
                'hash': hashlib.sha256(section.get('text', '').encode('utf-8')).hexdigest()
            }
            
            rules.append(rule)
        
        return rules

# Usage (RECOMMENDED)
ecfr_scraper = eCFRScraper()
reg_e_rules = ecfr_scraper.scrape_reg_e_via_api()
```

**Why eCFR API is Better:**
- ✅ Official government API
- ✅ Structured JSON (no HTML parsing)
- ✅ Version control (get historical versions)
- ✅ No rate limits
- ✅ Free

---

## 🎯 **SOURCE 6: CFPB (Consumer Financial Protection Bureau)**

### **A. Primary URLs**

**1. Final Rules**
```
URL: https://www.consumerfinance.gov/rules-policy/final-rules/
Format: HTML + PDF
Update Frequency: Monthly
Content: New regulations, amendments
```

**2. Supervisory Highlights**
```
URL: https://www.consumerfinance.gov/compliance/supervisory-highlights/
Format: HTML + PDF
Update Frequency: Quarterly
Content: Examination findings, enforcement actions
```

**3. Regulations**
```
URL: https://www.consumerfinance.gov/rules-policy/regulations/
Format: HTML (links to eCFR)
Update Frequency: Annual
Content: All CFPB regulations (Reg B, Z, X, etc.)
```

**4. Compliance Bulletins**
```
URL: https://www.consumerfinance.gov/compliance/compliance-resources/
Format: HTML + PDF
Update Frequency: Bi-weekly
Content: Compliance guidance, FAQs
```

---

### **B. Scraping Strategy**

```python
class CFPBSourceScraper:
    def __init__(self):
        self.base_url = "https://www.consumerfinance.gov"
        self.session = requests.Session()
    
    def scrape_final_rules(self):
        """Scrape CFPB final rules"""
        url = f"{self.base_url}/rules-policy/final-rules/"
        
        response = self.session.get(url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find rule listings
        rule_items = soup.find_all('div', class_='rule-item')
        
        for item in rule_items[:20]:  # Latest 20 rules
            rule_link = item.find('a')
            if not rule_link:
                continue
            
            rule_url = rule_link.get('href')
            if not rule_url.startswith('http'):
                rule_url = self.base_url + rule_url
            
            # Scrape individual rule page
            rule_response = self.session.get(rule_url, timeout=30)
            rule_soup = BeautifulSoup(rule_response.content, 'lxml')
            
            # Extract content
            content_div = rule_soup.find('div', class_='rule-content')
            if content_div:
                rule = {
                    'source': 'CFPB',
                    'category': 'Final Rule',
                    'title': rule_link.get_text(strip=True),
                    'content': content_div.get_text(strip=True),
                    'url': rule_url,
                    'scraped_at': datetime.utcnow().isoformat(),
                    'hash': hashlib.sha256(
                        content_div.get_text(strip=True).encode('utf-8')
                    ).hexdigest()
                }
                rules.append(rule)
        
        return rules
    
    def scrape_supervisory_highlights(self):
        """Scrape supervisory highlights"""
        url = f"{self.base_url}/compliance/supervisory-highlights/"
        
        response = self.session.get(url, timeout=30)
        soup = BeautifulSoup(response.content, 'lxml')
        
        rules = []
        
        # Find latest supervisory highlights
        highlights = soup.find_all('div', class_='highlight-item')
        
        for highlight in highlights[:10]:
            link = highlight.find('a')
            if not link:
                continue
            
            highlight_url = link.get('href')
            if not highlight_url.startswith('http'):
                highlight_url = self.base_url + highlight_url
            
            # Check if PDF
            if highlight_url.endswith('.pdf'):
                # Use unstructured for PDF
                pdf_response = self.session.get(highlight_url, timeout=60)
                pdf_path = '/tmp/cfpb_highlight.pdf'
                with open(pdf_path, 'wb') as f:
                    f.write(pdf_response.content)
                
                elements = partition(filename=pdf_path)
                chunks = chunk_by_title(elements, max_characters=2000)
                
                for chunk in chunks:
                    rule = {
                        'source': 'CFPB',
                        'category': 'Supervisory Highlight',
                        'title': link.get_text(strip=True),
                        'content': chunk.text,
                        'url': highlight_url,
                        'scraped_at': datetime.utcnow().isoformat(),
                        'hash': hashlib.sha256(chunk.text.encode('utf-8')).hexdigest()
                    }
                    rules.append(rule)
            else:
                # HTML page
                highlight_response = self.session.get(highlight_url, timeout=30)
                highlight_soup = BeautifulSoup(highlight_response.content, 'lxml')
                
                content_div = highlight_soup.find('div', class_='highlight-content')
                if content_div:
                    rule = {
                        'source': 'CFPB',
                        'category': 'Supervisory Highlight',
                        'title': link.get_text(strip=True),
                        'content': content_div.get_text(strip=True),
                        'url': highlight_url,
                        'scraped_at': datetime.utcnow().isoformat(),
                        'hash': hashlib.sha256(
                            content_div.get_text(strip=True).encode('utf-8')
                        ).hexdigest()
                    }
                    rules.append(rule)
        
        return rules

# Usage
cfpb_scraper = CFPBSourceScraper()
final_rules = cfpb_scraper.scrape_final_rules()
supervisory_highlights = cfpb_scraper.scrape_supervisory_highlights()
```

---

### **C. Update Detection (RSS)**

```python
def check_cfpb_updates():
    """Check CFPB RSS feeds"""
    rss_urls = [
        "https://www.consumerfinance.gov/about-us/newsroom/feed/",
        "https://www.consumerfinance.gov/rules-policy/final-rules/feed/"
    ]
    
    new_items = []
    
    for rss_url in rss_urls:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:5]:
            new_items.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.published,
                'source': 'CFPB'
            })
    
    return new_items
```

---

## 📊 **COMPLETE MVP SCRAPING ARCHITECTURE**

### **Unified Scraper System**

```python
import schedule
import time
from datetime import datetime
import psycopg2
from sentence_transformers import SentenceTransformer

class FortressComplianceScraper:
    """Unified scraper for all 6 MVP sources"""
    
    def __init__(self, postgres_conn_string):
        self.pg_conn = psycopg2.connect(postgres_conn_string)
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        
        # Initialize individual scrapers
        self.occ = OCCSourceScraper()
        self.fdic = FDICSourceScraper()
        self.fincen = FinCENSourceScraper()
        self.ofac = OFACSourceScraper()
        self.reg_e = RegESourceScraper()
        self.cfpb = CFPBSourceScraper()
    
    def run_full_scrape(self):
        """Run full scrape of all 6 sources"""
        print(f"[{datetime.now()}] Starting full compliance scrape...")
        
        all_rules = []
        
        # 1. OCC
        print("Scraping OCC...")
        occ_rules = self.occ.scrape_bsa_guidance()
        all_rules.extend(occ_rules)
        print(f"  ✓ {len(occ_rules)} OCC rules")
        
        # 2. FDIC
        print("Scraping FDIC...")
        fdic_rules = self.fdic.scrape_compliance_manual()
        all_rules.extend(fdic_rules)
        print(f"  ✓ {len(fdic_rules)} FDIC rules")
        
        # 3. FinCEN
        print("Scraping FinCEN...")
        fincen_rules = self.fincen.scrape_advisories()
        all_rules.extend(fincen_rules)
        print(f"  ✓ {len(fincen_rules)} FinCEN rules")
        
        # 4. OFAC
        print("Scraping OFAC...")
        ofac_entities = self.ofac.scrape_sdn_list()
        all_rules.extend(ofac_entities)
        print(f"  ✓ {len(ofac_entities)} OFAC entities")
        
        # 5. Reg E
        print("Scraping Reg E...")
        reg_e_rules = self.reg_e.scrape_reg_e_sections()
        all_rules.extend(reg_e_rules)
        print(f"  ✓ {len(reg_e_rules)} Reg E rules")
        
        # 6. CFPB
        print("Scraping CFPB...")
        cfpb_rules = self.cfpb.scrape_final_rules()
        all_rules.extend(cfpb_rules)
        print(f"  ✓ {len(cfpb_rules)} CFPB rules")
        
        print(f"\nTotal rules scraped: {len(all_rules)}")
        
        # Store in database
        self.store_rules(all_rules)
        
        return all_rules
    
    def store_rules(self, rules):
        """Store rules in PostgreSQL with embeddings"""
        cursor = self.pg_conn.cursor()
        
        for rule in rules:
            # Generate embedding
            embedding = self.embedding_model.encode(rule['content'])
            
            # Insert or update
            cursor.execute("""
                INSERT INTO compliance_rules 
                (source, category, title, content, url, hash, embedding, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (hash) DO UPDATE
                SET content = EXCLUDED.content,
                    scraped_at = EXCLUDED.scraped_at
            """, (
                rule['source'],
                rule['category'],
                rule['title'],
                rule['content'],
                rule['url'],
                rule['hash'],
                embedding.tolist(),
                rule['scraped_at']
            ))
        
        self.pg_conn.commit()
        print(f"Stored {len(rules)} rules in database")
    
    def schedule_daily_scrape(self):
        """Schedule daily scraping at 2am"""
        schedule.every().day.at("02:00").do(self.run_full_scrape)
        
        print("Scheduled daily scrape at 2am")
        
        while True:
            schedule.run_pending()
            time.sleep(3600)  # Check every hour

# Usage
scraper = FortressComplianceScraper(
    postgres_conn_string="postgresql://user:pass@localhost/fortress"
)

# Run once immediately
scraper.run_full_scrape()

# Then schedule daily
# scraper.schedule_daily_scrape()
```

---

## 💰 **COST ANALYSIS**

### **Open Source Approach (RECOMMENDED)**

**Tools:**
```
unstructured[pdf]           $0
beautifulsoup4              $0
requests                    $0
feedparser                  $0
sentence-transformers       $0
lxml                        $0
```

**Infrastructure:**
```
AWS EC2 t3.medium           $35/month
Storage (100GB)             $10/month
Compute (scraping jobs)     $5/month
```

**Total: $50/month for all 6 sources** ✅

---

### **Paid Alternative (If Open Source Fails)**

**Option 1: Diffbot**
```
Price: $299/month (10,000 pages)
Pros: Handles complex JavaScript, CAPTCHAs
Cons: Expensive
```

**Option 2: Apify**
```
Price: $49/month (100 scraping hours)
Pros: Pre-built scrapers for government sites
Cons: Less reliable than Diffbot
```

**Recommendation:** Start with open source ($50/month). Only upgrade to Diffbot ($299/month) if you hit reliability issues.

---

## ✅ **MVP SCRAPING CHECKLIST**

### **Week 1: Setup**
- [ ] Install dependencies: `pip install unstructured[pdf] beautifulsoup4 requests feedparser`
- [ ] Set up PostgreSQL database with `compliance_rules` table
- [ ] Test OFAC API (easiest to start with)

### **Week 2: Build Scrapers**
- [ ] Implement OCC scraper (HTML + PDF)
- [ ] Implement FDIC scraper (HTML)
- [ ] Implement FinCEN scraper (HTML + PDF)
- [ ] Implement OFAC scraper (XML API)

### **Week 3: Complete MVP**
- [ ] Implement Reg E scraper (eCFR API)
- [ ] Implement CFPB scraper (HTML + PDF)
- [ ] Test full scraping pipeline
- [ ] Generate embeddings with sentence-transformers

### **Week 4: QA & Deploy**
- [ ] Run full scrape, verify data quality
- [ ] Set up daily cron job (2am)
- [ ] Deploy to AWS EC2
- [ ] Monitor for errors

---

## 🎯 **SUCCESS METRICS**

### **Target Performance:**
- **Scraping Speed:** <30 minutes for all 6 sources
- **Data Quality:** >95% extraction accuracy
- **Reliability:** >99% uptime (retry logic)
- **Update Detection:** <24 hours latency

### **Expected Data Volume:**
- **OCC:** ~2,000 rules
- **FDIC:** ~1,500 rules
- **FinCEN:** ~500 advisories
- **OFAC:** ~8,000 entities
- **Reg E:** ~200 sections
- **CFPB:** ~1,000 rules

**Total: ~13,000 compliance rules in MVP** ✅

This is enough to power real-time wire transfer validation and JIRA test generation!

---

## 📋 **NEXT STEPS**

1. **This Week:** Start with OFAC scraper (API-based, easiest)
2. **Week 2:** Add OCC + FDIC (HTML parsing)
3. **Week 3:** Add FinCEN + Reg E + CFPB
4. **Week 4:** Full integration test, deploy to production

**You now have everything you need to build the MVP scraping system!** 🚀
