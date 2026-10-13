"""
TEXT MIND - Phase 1: System Analysis with Tree-Sitter
Enhanced version with deep code parsing and vector-based storage
"""

import os
import sys
import ast
import json
import uuid
from pathlib import Path
from typing import List, Dict, Any
import subprocess
import tempfile

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
import git

# Tree-sitter imports
try:
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    click.echo("⚠️  tree-sitter not available. Using Python AST only.")

console = Console()


class CodeAnalyzer:
    """Deep code analysis with Tree-Sitter and AST"""
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.analysis = {
            "apis": [],
            "database_models": [],
            "business_logic": [],
            "test_files": [],
            "dependencies": [],
            "banking_workflows": [],
        }
        
        # Initialize Tree-Sitter if available
        if TREE_SITTER_AVAILABLE:
            self.setup_tree_sitter()
    
    def setup_tree_sitter(self):
        """Setup Tree-Sitter parsers for multiple languages"""
        # Note: In production, build languages first:
        # Language.build_library('build/langs.so', ['vendor/tree-sitter-python', ...])
        try:
            self.py_parser = Parser()
            # Load pre-built language library
            # self.py_parser.set_language(Language('build/langs.so', 'python'))
        except Exception as e:
            console.print(f"[yellow]Tree-Sitter setup warning: {e}[/yellow]")
    
    def analyze_repository(self) -> Dict[str, Any]:
        """Main analysis entry point"""
        console.print(f"\n[bold cyan]Analyzing repository:[/bold cyan] {self.repo_path}")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            
            # Task 1: Scan Python files
            task1 = progress.add_task("Scanning Python files...", total=None)
            python_files = self._find_python_files()
            progress.update(task1, completed=True)
            console.print(f"✓ Found {len(python_files)} Python files")
            
            # Task 2: Parse APIs
            task2 = progress.add_task("Analyzing APIs...", total=len(python_files))
            for py_file in python_files:
                self._parse_python_file(py_file)
                progress.advance(task2)
            
            # Task 3: Detect banking workflows
            task3 = progress.add_task("Detecting banking patterns...", total=None)
            self._detect_banking_workflows()
            progress.update(task3, completed=True)
            
            # Task 4: Analyze test patterns
            task4 = progress.add_task("Analyzing test patterns...", total=None)
            self._analyze_test_patterns()
            progress.update(task4, completed=True)
        
        return self.analysis
    
    def _find_python_files(self) -> List[Path]:
        """Find all Python files in repository"""
        python_files = []
        for path in self.repo_path.rglob("*.py"):
            # Skip virtual environments and build directories
            if any(skip in str(path) for skip in ["venv", ".venv", "node_modules", "build", "__pycache__"]):
                continue
            python_files.append(path)
        return python_files
    
    def _parse_python_file(self, file_path: Path):
        """Parse Python file using AST"""
        try:
            source = file_path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            
            # Extract functions and classes
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    self._process_function(node, file_path, source)
                elif isinstance(node, ast.ClassDef):
                    self._process_class(node, file_path, source)
            
        except Exception as e:
            console.print(f"[yellow]Warning: Could not parse {file_path}: {e}[/yellow]")
    
    def _process_function(self, node: ast.FunctionDef, file_path: Path, source: str):
        """Process function definition"""
        # Detect API endpoints
        decorators = [d.id if isinstance(d, ast.Name) else getattr(d.func, 'attr', '') 
                     for d in node.decorator_list]
        
        if any(d in ['route', 'get', 'post', 'put', 'delete', 'patch'] for d in decorators):
            # This is an API endpoint
            endpoint_info = {
                "id": str(uuid.uuid4()),
                "name": node.name,
                "file": str(file_path.relative_to(self.repo_path)),
                "line": node.lineno,
                "type": "api_endpoint",
                "decorators": decorators,
                "docstring": ast.get_docstring(node),
                "code": ast.get_source_segment(source, node),
            }
            
            # Try to extract HTTP method and route
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    if hasattr(decorator.func, 'attr'):
                        method = decorator.func.attr.upper()
                        # Get route path from first argument
                        if decorator.args:
                            route = ast.literal_eval(decorator.args[0]) if isinstance(decorator.args[0], ast.Constant) else None
                            endpoint_info["method"] = method
                            endpoint_info["route"] = route
            
            self.analysis["apis"].append(endpoint_info)
        
        # Detect business logic patterns
        elif self._is_business_logic(node):
            self.analysis["business_logic"].append({
                "id": str(uuid.uuid4()),
                "name": node.name,
                "file": str(file_path.relative_to(self.repo_path)),
                "line": node.lineno,
                "type": "business_function",
                "docstring": ast.get_docstring(node),
            })
    
    def _process_class(self, node: ast.ClassDef, file_path: Path, source: str):
        """Process class definition"""
        # Detect database models
        base_classes = [b.id if isinstance(b, ast.Name) else str(b) 
                       for b in node.bases]
        
        if any('Model' in bc or 'Base' in bc for bc in base_classes):
            # This is likely a database model
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append({
                        "name": item.target.id,
                        "type": ast.unparse(item.annotation) if hasattr(ast, 'unparse') else str(item.annotation),
                    })
            
            self.analysis["database_models"].append({
                "id": str(uuid.uuid4()),
                "name": node.name,
                "file": str(file_path.relative_to(self.repo_path)),
                "line": node.lineno,
                "type": "database_model",
                "base_classes": base_classes,
                "fields": fields,
                "docstring": ast.get_docstring(node),
            })
    
    def _is_business_logic(self, node: ast.FunctionDef) -> bool:
        """Heuristic to detect business logic functions"""
        # Check for keywords in function name
        business_keywords = [
            'transfer', 'payment', 'transaction', 'validate', 'process',
            'calculate', 'verify', 'authorize', 'approve', 'check_limit'
        ]
        return any(keyword in node.name.lower() for keyword in business_keywords)
    
    def _detect_banking_workflows(self):
        """Detect banking workflow patterns from code"""
        banking_patterns = {
            "account_transfer": ["transfer", "debit", "credit", "balance"],
            "payment_processing": ["payment", "pay", "merchant", "settlement"],
            "loan_origination": ["loan", "credit_score", "underwrite", "disburse"],
            "fraud_detection": ["fraud", "suspicious", "alert", "block"],
        }
        
        detected_workflows = set()
        
        # Search through APIs and business logic
        all_code = self.analysis["apis"] + self.analysis["business_logic"]
        
        for item in all_code:
            name_lower = item["name"].lower()
            docstring = (item.get("docstring") or "").lower()
            
            for workflow, keywords in banking_patterns.items():
                if any(kw in name_lower or kw in docstring for kw in keywords):
                    detected_workflows.add(workflow)
        
        self.analysis["banking_workflows"] = list(detected_workflows)
    
    def _analyze_test_patterns(self):
        """Analyze existing test files to understand patterns"""
        test_files = list(self.repo_path.rglob("test_*.py")) + list(self.repo_path.rglob("*_test.py"))
        
        for test_file in test_files:
            try:
                source = test_file.read_text(encoding='utf-8')
                tree = ast.parse(source)
                
                # Detect test framework
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend([alias.name for alias in node.names])
                    elif isinstance(node, ast.ImportFrom):
                        imports.append(node.module)
                
                framework = None
                if 'pytest' in imports:
                    framework = 'pytest'
                elif 'unittest' in imports:
                    framework = 'unittest'
                
                # Count test functions
                test_functions = [
                    node.name for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')
                ]
                
                self.analysis["test_files"].append({
                    "file": str(test_file.relative_to(self.repo_path)),
                    "framework": framework,
                    "test_count": len(test_functions),
                    "test_functions": test_functions,
                })
                
            except Exception as e:
                console.print(f"[yellow]Warning: Could not parse test file {test_file}: {e}[/yellow]")
    
    def generate_report(self) -> str:
        """Generate human-readable analysis report"""
        report = []
        report.append("\n" + "="*80)
        report.append("TEXT MIND - Phase 1 Analysis Report")
        report.append("="*80 + "\n")
        
        # API Endpoints
        report.append(f"📡 API Endpoints Discovered: {len(self.analysis['apis'])}")
        if self.analysis['apis']:
            table = Table(title="API Endpoints")
            table.add_column("Method", style="cyan")
            table.add_column("Route", style="green")
            table.add_column("Function", style="yellow")
            table.add_column("File", style="dim")
            
            for api in self.analysis['apis'][:10]:  # Show first 10
                table.add_row(
                    api.get('method', 'N/A'),
                    api.get('route', 'N/A'),
                    api['name'],
                    api['file']
                )
            console.print(table)
        
        # Database Models
        report.append(f"\n💾 Database Models: {len(self.analysis['database_models'])}")
        if self.analysis['database_models']:
            for model in self.analysis['database_models'][:5]:
                report.append(f"  - {model['name']} ({len(model['fields'])} fields)")
        
        # Banking Workflows
        report.append(f"\n🏦 Banking Workflows Detected: {len(self.analysis['banking_workflows'])}")
        for workflow in self.analysis['banking_workflows']:
            report.append(f"  ✓ {workflow}")
        
        # Test Patterns
        report.append(f"\n🧪 Test Files: {len(self.analysis['test_files'])}")
        if self.analysis['test_files']:
            frameworks = set(t['framework'] for t in self.analysis['test_files'] if t['framework'])
            report.append(f"  Frameworks detected: {', '.join(frameworks) if frameworks else 'None'}")
        
        return "\n".join(report)


class DomainExpertInterviewer:
    """Interactive questionnaire for domain expert input"""
    
    def __init__(self):
        self.insights = {}
    
    def conduct_interview(self, analysis: Dict) -> Dict:
        """Interactive interview with domain expert"""
        console.print("\n[bold green]Domain Expert Interview[/bold green]")
        console.print("Please provide additional context about your banking system:\n")
        
        # Question 1: Core Banking System
        self.insights['core_banking_system'] = click.prompt(
            "What core banking system do you use? (FIS, Jack Henry, Temenos, Custom)",
            type=str,
            default="Custom"
        )
        
        # Question 2: Primary Banking Operations
        console.print("\nWhich banking operations are most critical?")
        console.print("Detected workflows: " + ", ".join(analysis['banking_workflows']))
        self.insights['critical_workflows'] = click.prompt(
            "Enter critical workflows (comma-separated)",
            type=str,
            default=", ".join(analysis['banking_workflows'])
        ).split(',')
        
        # Question 3: Compliance Requirements
        console.print("\nWhich compliance regulations apply?")
        regulations = click.prompt(
            "Select applicable (comma-separated): SOX, PCI-DSS, Reg E, BASEL III, GDPR",
            type=str,
            default="SOX, Reg E"
        )
        self.insights['compliance_regulations'] = [r.strip() for r in regulations.split(',')]
        
        # Question 4: Integration Points
        self.insights['ci_cd_system'] = click.prompt(
            "CI/CD system? (Jenkins, GitHub Actions, GitLab CI, Azure DevOps)",
            type=str,
            default="Jenkins"
        )
        
        # Question 5: Test Strategy
        self.insights['test_coverage_target'] = click.prompt(
            "Target test coverage %",
            type=int,
            default=85
        )
        
        return self.insights


def generate_implementation_plan(analysis: Dict, insights: Dict) -> Dict:
    """Generate customized implementation plan"""
    plan = {
        "phase": "Implementation Roadmap",
        "steps": [],
        "estimated_duration": "2-4 weeks",
        "mcp_server_configs": [],
        "agent_configurations": [],
    }
    
    # Step 1: MCP Server Configuration
    plan["steps"].append({
        "step": 1,
        "title": "Configure MCP Servers",
        "description": "Load banking knowledge into MCP servers",
        "actions": [
            f"Enable workflows: {', '.join(insights.get('critical_workflows', []))}",
            f"Load compliance rules: {', '.join(insights.get('compliance_regulations', []))}",
            "Configure test patterns based on existing test framework",
        ],
        "duration": "2 days"
    })
    
    # Step 2: JIRA Integration
    plan["steps"].append({
        "step": 2,
        "title": "Setup JIRA Integration",
        "description": "Connect JIRA to TEXT MIND",
        "actions": [
            "Configure JIRA webhook",
            "Map JIRA projects to banking workflows",
            "Test with sample story",
        ],
        "duration": "1 day"
    })
    
    # Step 3: Agent Configuration
    plan["steps"].append({
        "step": 3,
        "title": "Configure Agents",
        "description": "Setup 6 agents with client-specific rules",
        "actions": [
            "Discovery Agent: Map to detected APIs",
            "Understanding Agent: Use detected test patterns",
            f"Validation Agent: Integrate with {insights.get('ci_cd_system', 'CI/CD')}",
        ],
        "duration": "3 days"
    })
    
    # Step 4: Approval Workflows
    plan["steps"].append({
        "step": 4,
        "title": "Setup Approval Workflows",
        "description": "Configure human-in-loop checkpoints",
        "actions": [
            "Start with Level 2 (Semi-Autonomous)",
            "Configure checkpoint: scenarios (required)",
            "Configure checkpoint: code review (banking/compliance)",
        ],
        "duration": "2 days"
    })
    
    # Step 5: Pilot Run
    plan["steps"].append({
        "step": 5,
        "title": "Pilot Run",
        "description": "Process 3-5 sample JIRA stories",
        "actions": [
            "Select diverse stories covering different workflows",
            "Collect human feedback on generated tests",
            "Tune MCP server knowledge based on feedback",
        ],
        "duration": "1 week"
    })
    
    return plan


@click.command()
@click.option('--repo-path', required=True, help='Path to banking application repository')
@click.option('--output', default='analysis_report.json', help='Output file for analysis')
@click.option('--interactive/--no-interactive', default=True, help='Interactive domain expert interview')
def main(repo_path: str, output: str, interactive: bool):
    """
    TEXT MIND Phase 1: Analyze existing banking system
    
    This script performs deep code analysis to understand your banking application
    and generates a customized implementation plan.
    """
    console.print("[bold cyan]TEXT MIND - Phase 1: System Analysis[/bold cyan]")
    
    # Validate repository path
    if not Path(repo_path).exists():
        console.print(f"[red]Error: Repository path not found: {repo_path}[/red]")
        sys.exit(1)
    
    # Step 1: Code Analysis
    analyzer = CodeAnalyzer(repo_path)
    analysis = analyzer.analyze_repository()
    
    # Step 2: Generate report
    report = analyzer.generate_report()
    console.print(report)
    
    # Step 3: Domain Expert Interview
    insights = {}
    if interactive:
        interviewer = DomainExpertInterviewer()
        insights = interviewer.conduct_interview(analysis)
    
    # Step 4: Generate Implementation Plan
    plan = generate_implementation_plan(analysis, insights)
    
    # Display plan
    console.print("\n[bold green]Implementation Plan Generated[/bold green]\n")
    for step in plan["steps"]:
        console.print(f"[cyan]Step {step['step']}: {step['title']}[/cyan]")
        console.print(f"  Duration: {step['duration']}")
        console.print(f"  Description: {step['description']}")
        for action in step['actions']:
            console.print(f"    • {action}")
        console.print()
    
    # Step 5: Save results
    results = {
        "analysis": analysis,
        "domain_insights": insights,
        "implementation_plan": plan,
    }
    
    with open(output, 'w') as f:
        json.dump(results, f, indent=2)
    
    console.print(f"[green]✓ Analysis complete! Results saved to: {output}[/green]")
    console.print(f"\n[yellow]Next step: Run Phase 2 configuration script[/yellow]")
    console.print(f"[dim]python scripts/phase2/configure_jira.py --analysis {output}[/dim]")


if __name__ == '__main__':
    main()
