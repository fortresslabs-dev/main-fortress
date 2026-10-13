# AGENTIC FRAMEWORK RECOMMENDATION
## For Autonomous Compliance Agent Production Deployment

**Context:** You need a framework to implement the 5-phase autonomous agent (Analysis → Implementation → Execution → Verification → Iteration) that can self-improve and run in production.

---

## 🏆 **RECOMMENDED FRAMEWORK: LangGraph**

**Why LangGraph is perfect for your autonomous agent:**

```python
✅ State Graph Architecture (perfect for 5-phase loop)
✅ Built-in Cycles/Loops (for iteration/retry logic)
✅ Claude Integration (first-class Anthropic support)
✅ Checkpointing (resume from failures)
✅ Production-Ready (used by Anthropic internally)
✅ Observability (LangSmith integration)
✅ Tool Calling (built-in support)
✅ Streaming (real-time progress updates)
```

---

## 🎯 **YOUR AGENT IN LANGGRAPH**

### **Visual Architecture:**

```
┌────────────────────────────────────────────────────────────┐
│              LANGGRAPH STATE MACHINE                        │
└────────────────────────────────────────────────────────────┘

                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │
                           ▼
                  ┌────────────────┐
                  │  ANALYSIS      │◄─────┐
                  │  Node          │      │
                  └────────┬───────┘      │
                           │              │
                           ▼              │
                  ┌────────────────┐      │
                  │  IMPLEMENTATION│      │
                  │  Node          │      │
                  └────────┬───────┘      │
                           │              │
                           ▼              │
                  ┌────────────────┐      │
                  │  EXECUTION     │      │
                  │  Node          │      │
                  └────────┬───────┘      │
                           │              │
                           ▼              │
                  ┌────────────────┐      │
                  │  VERIFICATION  │      │
                  │  Node          │      │
                  └────────┬───────┘      │
                           │              │
                      Success?            │
                     ┌─────┴─────┐        │
                     │           │        │
                    YES          NO       │
                     │           │        │
                     ▼           ▼        │
                  ┌─────┐   ┌────────┐   │
                  │ END │   │ITERATION│───┘
                  └─────┘   │ Node   │
                            └────────┘
                            (retry loop)
```

---

## 💻 **IMPLEMENTATION WITH LANGGRAPH**

### **Step 1: Define State Schema**

```python
from typing import TypedDict, List, Optional, Annotated
from langgraph.graph import StateGraph, END
from langchain_anthropic import ChatAnthropic
import operator

class AgentState(TypedDict):
    """State that flows through the graph"""
    
    # Input
    source_spec: dict
    
    # Phase 1: Analysis
    document_content: Optional[bytes]
    document_format: Optional[str]
    document_structure: Optional[dict]
    extraction_strategy: Optional[str]
    
    # Phase 2: Implementation
    scraper_code: Optional[str]
    scraper_file_path: Optional[str]
    
    # Phase 3: Execution
    execution_result: Optional[dict]
    extracted_data: Optional[list]
    
    # Phase 4: Verification
    validation_result: Optional[dict]
    quality_score: Optional[float]
    
    # Phase 5: Iteration
    errors: Annotated[List[str], operator.add]  # Accumulate errors
    iteration_count: int
    max_iterations: int
    
    # Output
    success: bool
    final_count: Optional[int]
    
    # Messages (for Claude interactions)
    messages: Annotated[List, operator.add]
```

---

### **Step 2: Define Nodes (Each Phase)**

```python
# ═══════════════════════════════════════════════════════════
# NODE 1: ANALYSIS
# ═══════════════════════════════════════════════════════════

def analysis_node(state: AgentState) -> AgentState:
    """Phase 1: Analyze compliance source"""
    
    print(f"📥 ANALYSIS: {state['source_spec']['name']}")
    
    # Download document
    import requests
    response = requests.get(state['source_spec']['url'])
    document_content = response.content
    
    # Detect format
    url = state['source_spec']['url']
    if url.endswith('.pdf'):
        doc_format = 'pdf'
    elif url.endswith('.csv'):
        doc_format = 'csv'
    elif url.endswith('.xml'):
        doc_format = 'xml'
    else:
        doc_format = 'html'
    
    # Analyze structure (using appropriate library)
    if doc_format == 'pdf':
        structure = analyze_pdf_structure(document_content)
    elif doc_format == 'csv':
        structure = analyze_csv_structure(document_content)
    elif doc_format == 'html':
        structure = analyze_html_structure(document_content)
    elif doc_format == 'xml':
        structure = analyze_xml_structure(document_content)
    
    # Choose extraction strategy
    strategy = choose_strategy(doc_format, structure)
    
    print(f"   Format: {doc_format}")
    print(f"   Strategy: {strategy}")
    
    return {
        **state,
        "document_content": document_content,
        "document_format": doc_format,
        "document_structure": structure,
        "extraction_strategy": strategy
    }

# ═══════════════════════════════════════════════════════════
# NODE 2: IMPLEMENTATION (CODE GENERATION)
# ═══════════════════════════════════════════════════════════

def implementation_node(state: AgentState) -> AgentState:
    """Phase 2: Generate scraper code with Claude"""
    
    print(f"🤖 IMPLEMENTATION: Generating scraper code...")
    
    # Build prompt for Claude
    prompt = f"""
Generate a Python scraper for this compliance source:

Source: {state['source_spec']['name']}
Format: {state['document_format']}
Strategy: {state['extraction_strategy']}
Structure: {state['document_structure']}

Expected output schema:
- regulation_id: str
- regulation_name: str
- requirements: list of dicts with:
  - requirement_id: str
  - requirement_text: str
  - threshold_amount: float (optional)
  - rule_type: str

Generate complete, runnable Python code with error handling.
Output only code, no explanations.
"""
    
    # Call Claude via LangChain
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=0
    )
    
    response = llm.invoke(prompt)
    scraper_code = clean_code(response.content)
    
    # Save code to file
    scraper_file = f"src/scrapers/usa/generated_{state['source_spec']['source_id']}.py"
    save_code(scraper_file, scraper_code)
    
    print(f"   Generated: {len(scraper_code)} characters")
    print(f"   Saved to: {scraper_file}")
    
    return {
        **state,
        "scraper_code": scraper_code,
        "scraper_file_path": scraper_file,
        "messages": [
            {"role": "assistant", "content": f"Generated scraper code ({len(scraper_code)} chars)"}
        ]
    }

# ═══════════════════════════════════════════════════════════
# NODE 3: EXECUTION
# ═══════════════════════════════════════════════════════════

def execution_node(state: AgentState) -> AgentState:
    """Phase 3: Execute generated scraper"""
    
    print(f"🚀 EXECUTION: Running scraper...")
    
    import subprocess
    import json
    
    try:
        # Run scraper in subprocess
        result = subprocess.run(
            ['python', state['scraper_file_path']],
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes
        )
        
        if result.returncode == 0:
            # Parse output
            output = result.stdout
            try:
                data = json.loads(output)
            except:
                data = extract_from_output(output)
            
            extracted_data = data.get('requirements', [])
            
            print(f"   ✅ Success: {len(extracted_data)} records")
            
            return {
                **state,
                "execution_result": {
                    "success": True,
                    "stdout": output,
                    "stderr": ""
                },
                "extracted_data": extracted_data
            }
        else:
            # Execution failed
            error_msg = result.stderr
            
            print(f"   ❌ Failed: {error_msg[:100]}")
            
            return {
                **state,
                "execution_result": {
                    "success": False,
                    "stdout": result.stdout,
                    "stderr": error_msg
                },
                "errors": [error_msg]
            }
            
    except subprocess.TimeoutExpired:
        print(f"   ❌ Timeout (5 minutes)")
        return {
            **state,
            "execution_result": {
                "success": False,
                "stderr": "Execution timeout"
            },
            "errors": ["Execution timeout (5 minutes)"]
        }

# ═══════════════════════════════════════════════════════════
# NODE 4: VERIFICATION
# ═══════════════════════════════════════════════════════════

def verification_node(state: AgentState) -> AgentState:
    """Phase 4: Validate results"""
    
    print(f"✅ VERIFICATION: Validating results...")
    
    errors = []
    warnings = []
    validations = {}
    
    # 1. Count check
    expected = state['source_spec'].get('expected_count', 0)
    actual = len(state.get('extracted_data', []))
    
    if expected > 0:
        variance = abs(actual - expected) / expected
        validations['count_check'] = variance < 0.3  # Within 30%
        if not validations['count_check']:
            warnings.append(f"Count variance: expected {expected}, got {actual}")
    else:
        validations['count_check'] = actual > 0
    
    # 2. Schema check
    if state.get('extracted_data'):
        sample = state['extracted_data'][0]
        required_fields = ['requirement_id', 'requirement_text']
        
        schema_valid = all(field in sample for field in required_fields)
        validations['schema_check'] = schema_valid
        
        if not schema_valid:
            missing = [f for f in required_fields if f not in sample]
            errors.append(f"Missing fields: {missing}")
    
    # 3. Quality check
    quality_issues = 0
    if state.get('extracted_data'):
        for req in state['extracted_data']:
            if not req.get('requirement_text') or len(req.get('requirement_text', '')) < 10:
                quality_issues += 1
        
        quality_rate = 1 - (quality_issues / len(state['extracted_data']))
        validations['quality_check'] = quality_rate > 0.9  # >90% quality
        quality_score = quality_rate * 100
    else:
        quality_score = 0
        validations['quality_check'] = False
    
    # Overall success
    success = all(validations.values()) and len(errors) == 0
    
    print(f"   Count: {'✅' if validations.get('count_check') else '❌'} ({actual} records)")
    print(f"   Schema: {'✅' if validations.get('schema_check') else '❌'}")
    print(f"   Quality: {'✅' if validations.get('quality_check') else '❌'} ({quality_score:.1f}%)")
    
    return {
        **state,
        "validation_result": {
            "success": success,
            "validations": validations,
            "errors": errors,
            "warnings": warnings
        },
        "quality_score": quality_score,
        "success": success,
        "final_count": actual,
        "errors": errors if not success else state.get('errors', [])
    }

# ═══════════════════════════════════════════════════════════
# NODE 5: ITERATION (ERROR FIXING)
# ═══════════════════════════════════════════════════════════

def iteration_node(state: AgentState) -> AgentState:
    """Phase 5: Fix errors and retry"""
    
    iteration = state.get('iteration_count', 0) + 1
    print(f"🔧 ITERATION {iteration}: Fixing errors...")
    
    # Analyze errors with Claude
    errors = state.get('errors', [])
    
    prompt = f"""
The scraper failed with these errors:
{json.dumps(errors, indent=2)}

Original code:
{state['scraper_code']}

Source info:
- Format: {state['document_format']}
- Strategy: {state['extraction_strategy']}

Analyze the errors and generate FIXED code.
Common issues:
- PDF parsing → try different method
- Missing fields → adjust extraction
- Timeout → reduce document size

Output only the fixed Python code.
"""
    
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=0
    )
    
    response = llm.invoke(prompt)
    fixed_code = clean_code(response.content)
    
    # Save fixed code
    scraper_file = f"src/scrapers/usa/generated_{state['source_spec']['source_id']}_v{iteration}.py"
    save_code(scraper_file, fixed_code)
    
    print(f"   Fixed code saved to: {scraper_file}")
    
    return {
        **state,
        "scraper_code": fixed_code,
        "scraper_file_path": scraper_file,
        "iteration_count": iteration,
        "errors": [],  # Clear errors for retry
        "messages": [
            {"role": "assistant", "content": f"Generated fixed code (iteration {iteration})"}
        ]
    }
```

---

### **Step 3: Define Routing Logic**

```python
def should_continue(state: AgentState) -> str:
    """
    Routing function: Decide next node based on state
    
    Returns:
        - "end" if successful
        - "iteration" if failed but can retry
        - "end" if max iterations reached
    """
    
    # Check if verification succeeded
    if state.get('success', False):
        print("✅ SUCCESS: Ending workflow")
        return "end"
    
    # Check iteration limit
    iteration = state.get('iteration_count', 0)
    max_iterations = state.get('max_iterations', 5)
    
    if iteration >= max_iterations:
        print(f"❌ FAILED: Max iterations ({max_iterations}) reached")
        return "end"
    
    # Retry with iteration
    print(f"🔄 RETRY: Going to iteration node")
    return "iteration"
```

---

### **Step 4: Build the Graph**

```python
from langgraph.graph import StateGraph, END

def build_compliance_agent() -> StateGraph:
    """Build the LangGraph workflow"""
    
    # Initialize graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("analysis", analysis_node)
    workflow.add_node("implementation", implementation_node)
    workflow.add_node("execution", execution_node)
    workflow.add_node("verification", verification_node)
    workflow.add_node("iteration", iteration_node)
    
    # Set entry point
    workflow.set_entry_point("analysis")
    
    # Add edges (linear flow)
    workflow.add_edge("analysis", "implementation")
    workflow.add_edge("implementation", "execution")
    workflow.add_edge("execution", "verification")
    
    # Add conditional edge (verification → end or iteration)
    workflow.add_conditional_edges(
        "verification",
        should_continue,
        {
            "end": END,
            "iteration": "iteration"
        }
    )
    
    # Iteration loops back to implementation
    workflow.add_edge("iteration", "implementation")
    
    # Compile
    app = workflow.compile()
    
    return app
```

---

### **Step 5: Execute the Agent**

```python
# ═══════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════

def run_compliance_agent(source_spec: dict):
    """Run the autonomous agent for a compliance source"""
    
    # Build graph
    agent = build_compliance_agent()
    
    # Initial state
    initial_state = {
        "source_spec": source_spec,
        "iteration_count": 0,
        "max_iterations": 5,
        "errors": [],
        "messages": [],
        "success": False
    }
    
    print(f"\n{'='*60}")
    print(f"Processing: {source_spec['name']}")
    print(f"{'='*60}\n")
    
    # Run graph
    final_state = agent.invoke(initial_state)
    
    # Report results
    print(f"\n{'='*60}")
    if final_state['success']:
        print(f"✅ SUCCESS")
        print(f"   Records: {final_state['final_count']}")
        print(f"   Quality: {final_state['quality_score']:.1f}%")
        print(f"   Iterations: {final_state['iteration_count']}")
    else:
        print(f"❌ FAILED")
        print(f"   Errors: {final_state.get('errors', [])}")
        print(f"   Iterations: {final_state['iteration_count']}")
    print(f"{'='*60}\n")
    
    return final_state

# ═══════════════════════════════════════════════════════════
# USAGE
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Source specification
    ofac_spec = {
        'source_id': 'ofac_sdn',
        'name': 'OFAC SDN List',
        'url': 'https://www.treasury.gov/ofac/downloads/sdn.csv',
        'format': 'csv',
        'expected_count': 8000
    }
    
    # Run agent
    result = run_compliance_agent(ofac_spec)
    
    # Process all sources
    sources = load_sources('config/compliance_sources.yaml')
    for source in sources:
        run_compliance_agent(source)
```

---

## 📊 **OBSERVABILITY WITH LANGSMITH**

### **Add LangSmith Tracing:**

```python
import os
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = "your-api-key"
os.environ["LANGCHAIN_PROJECT"] = "compliance-agent"

# Now all executions are traced in LangSmith dashboard
agent = build_compliance_agent()
result = agent.invoke(initial_state)

# View in LangSmith:
# - Execution timeline
# - Token usage per node
# - Error stack traces
# - State at each step
```

**LangSmith Dashboard shows:**
```
Timeline:
├─ analysis: 2.3s
├─ implementation: 8.1s (Claude call)
├─ execution: 4.7s
├─ verification: 0.5s
└─ Total: 15.6s

Tokens Used:
├─ Input: 3,245 tokens
├─ Output: 1,892 tokens
└─ Cost: $0.08

State Flow:
├─ Initial: source_spec loaded
├─ After analysis: document_format = "csv"
├─ After implementation: scraper_code generated
├─ After execution: 8245 records extracted
└─ After verification: success = True
```

---

## 🔄 **CHECKPOINTING (RESUME FROM FAILURES)**

### **Add Checkpointing:**

```python
from langgraph.checkpoint.sqlite import SqliteSaver

# Create checkpointer
checkpointer = SqliteSaver.from_conn_string("checkpoints.db")

# Compile with checkpointing
agent = workflow.compile(checkpointer=checkpointer)

# Run with thread ID (for resumability)
config = {"configurable": {"thread_id": "ofac_sdn_run_1"}}

# First run (might fail)
try:
    result = agent.invoke(initial_state, config)
except Exception:
    print("Run failed, but state is saved")

# Resume from checkpoint
result = agent.invoke(None, config)  # Continues from last checkpoint
```

---

## 🆚 **FRAMEWORK COMPARISON**

### **Why NOT Other Frameworks:**

| Framework | Why NOT for This Use Case |
|-----------|---------------------------|
| **CrewAI** | Better for multi-agent teams, but you have a single agent with phases |
| **AutoGen** | Conversational agents, not workflow/state-machine oriented |
| **Agency Swarm** | OpenAI-focused, less Claude integration |
| **Semantic Kernel** | Microsoft-centric, more complex for this use case |
| **DSPy** | For optimizing prompts, not agentic workflows |
| **LlamaIndex Agents** | Better for RAG, not complex workflows |

### **Why LangGraph Wins:**

| Feature | LangGraph | Others |
|---------|-----------|--------|
| **State Management** | ✅ Built-in TypedDict state | ❌ Manual state management |
| **Cycles/Loops** | ✅ Native support | ❌ Complex to implement |
| **Claude Integration** | ✅ First-class via LangChain | ⚠️ Varies |
| **Checkpointing** | ✅ Built-in | ❌ Manual |
| **Observability** | ✅ LangSmith | ⚠️ Limited |
| **Production** | ✅ Battle-tested | ⚠️ Varies |
| **Streaming** | ✅ Native | ⚠️ Limited |

---

## 📦 **INSTALLATION**

```bash
# Install LangGraph + dependencies
pip install langgraph langchain-anthropic langchain-community

# Optional: LangSmith for observability
pip install langsmith

# Your existing dependencies
pip install pdfplumber beautifulsoup4 pandas requests
```

---

## 🚀 **PRODUCTION DEPLOYMENT**

### **Project Structure:**

```
textmind_banking_backend/
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── compliance_agent.py      # LangGraph workflow
│   │   ├── nodes.py                 # Individual node functions
│   │   └── utils.py                 # Helper functions
│   │
│   └── scrapers/
│       └── usa/
│           └── generated_*.py       # Auto-generated scrapers
│
├── config/
│   └── compliance_sources.yaml      # Source specifications
│
├── checkpoints.db                   # LangGraph checkpoints
│
└── scripts/
    └── run_agent.py                 # CLI runner
```

### **CLI Runner:**

```python
# scripts/run_agent.py

import argparse
from src.agents.compliance_agent import run_compliance_agent, load_sources

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', help='Single source ID')
    parser.add_argument('--all', action='store_true', help='Process all sources')
    args = parser.parse_args()
    
    sources = load_sources('config/compliance_sources.yaml')
    
    if args.all:
        for source in sources:
            run_compliance_agent(source)
    elif args.source:
        source = next(s for s in sources if s['source_id'] == args.source)
        run_compliance_agent(source)

if __name__ == '__main__':
    main()
```

**Usage:**

```bash
# Run single source
python scripts/run_agent.py --source ofac_sdn

# Run all sources
python scripts/run_agent.py --all
```

---

## ✅ **FINAL RECOMMENDATION**

**Use LangGraph** for your autonomous compliance agent because:

1. ✅ **Perfect fit** for your 5-phase state machine
2. ✅ **Production-ready** (used by Anthropic)
3. ✅ **Claude integration** (first-class support)
4. ✅ **Checkpointing** (resume from failures)
5. ✅ **Observability** (LangSmith dashboard)
6. ✅ **Maintainable** (clear node structure)
7. ✅ **Scalable** (handle 18+ sources)

**Alternative if you need multi-agent:** CrewAI (but your design is single-agent)

---

## 🎯 **NEXT STEPS**

**Tell Claude Code:**

```
Implement the autonomous compliance agent using LangGraph framework.

Follow the implementation in AUTONOMOUS_AGENT_DESIGN.md but use:
- LangGraph for workflow orchestration
- StateGraph with 5 nodes (analysis, implementation, execution, verification, iteration)
- Conditional routing for retry logic
- LangSmith for observability

Start by creating:
1. src/agents/compliance_agent.py (main LangGraph workflow)
2. src/agents/nodes.py (individual node functions)
3. scripts/run_agent.py (CLI runner)

Install: pip install langgraph langchain-anthropic

Test with OFAC source first.
```

---

**LangGraph = Best framework for your autonomous agent!** 🚀

Want me to create the complete LangGraph implementation code?
