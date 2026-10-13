"""
JIRA Connector - Production-Ready Implementation
Fetches requirements from Atlassian JIRA for USA banks

Status: ✅ COMPLETE & TESTED
Author: Fortress Labs AI
Date: 2026-03-08

QUICK START:
1. Get API token: https://id.atlassian.com/manage-profile/security/api-tokens
2. Add to .env:
   REQUIREMENTS_JIRA_SERVER=https://your-bank.atlassian.net
   REQUIREMENTS_JIRA_EMAIL=your-email@bank.com
   REQUIREMENTS_JIRA_API_TOKEN=xxxxx
3. Test: python scripts/connectors/test_jira.py
"""

from typing import Dict, List, Optional
import re
from jira import JIRA
from jira.exceptions import JIRAError
import structlog

from src.connectors.base import RequirementsConnector

logger = structlog.get_logger(__name__)


class JiraConnector(RequirementsConnector):
    """
    JIRA connector for requirements extraction
    
    Features:
    - Fetch stories using JQL queries
    - Extract acceptance criteria (multiple formats)
    - Parse Gherkin scenarios (Given/When/Then)
    - Detect compliance keywords
    - Extract validators from descriptions
    """
    
    connector_name = "jira"
    geography = None  # Works for both USA and India
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize JIRA connector
        
        Args:
            config: Configuration dict with:
                - server: JIRA URL (e.g., https://company.atlassian.net)
                - email: User email
                - api_token: API token from Atlassian
        """
        super().__init__(config)
        self.jira = None
        self.server = self.config.get('server')
        self.email = self.config.get('email')
        self.api_token = self.config.get('api_token')
        
        if not all([self.server, self.email, self.api_token]):
            logger.warning(
                "jira_config_incomplete",
                has_server=bool(self.server),
                has_email=bool(self.email),
                has_token=bool(self.api_token)
            )
    
    def connect(self) -> bool:
        """
        Connect to JIRA instance
        
        Returns:
            True if connection successful
        """
        try:
            logger.info("connecting_to_jira", server=self.server)
            
            self.jira = JIRA(
                server=self.server,
                basic_auth=(self.email, self.api_token),
                options={'verify': True}
            )
            
            # Test connection by getting current user
            user = self.jira.myself()
            
            self.connected = True
            
            logger.info(
                "jira_connected",
                server=self.server,
                user=user.get('displayName')
            )
            
            return True
            
        except JIRAError as e:
            logger.error(
                "jira_connection_failed",
                server=self.server,
                error=str(e),
                status_code=getattr(e, 'status_code', None)
            )
            return False
        except Exception as e:
            logger.error("jira_connection_error", error=str(e))
            return False
    
    def disconnect(self):
        """Close JIRA connection"""
        if self.jira:
            self.jira.close()
        self.connected = False
        logger.info("jira_disconnected")
    
    def test_connection(self) -> bool:
        """
        Test JIRA connection
        
        Returns:
            True if connection works
        """
        if not self.jira:
            return self.connect()
        
        try:
            self.jira.myself()
            return True
        except Exception as exc:
            # M2 — bare `except: return False` swallowed every error
            # including KeyboardInterrupt. Narrow to Exception so the
            # caller can still ctrl-C, and log so a 403 vs. timeout vs.
            # JSON parse error is distinguishable from outside.
            import logging
            logging.getLogger(__name__).warning(
                "jira_connection_test_failed: %s: %s",
                type(exc).__name__, exc,
            )
            return False
    
    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        """
        Extract all requirements from JIRA
        
        This method name matches base class interface,
        but for JIRA we're extracting stories, not rules
        
        Returns:
            {
                "jira_stories": [
                    {
                        "key": "BANK-123",
                        "summary": "...",
                        "description": "...",
                        "acceptance_criteria": [...],
                        "compliance_related": True/False,
                        ...
                    }
                ]
            }
        """
        if not self.connected:
            self.connect()
        
        # Fetch recent stories (configurable via JQL)
        jql = self.config.get('jql', 'project = BANK ORDER BY created DESC')
        max_results = self.config.get('max_results', 50)
        
        stories = self.fetch_stories_by_jql(jql, max_results)
        
        return {"jira_stories": stories}
    
    def fetch_story(self, story_key: str) -> Optional[Dict]:
        """
        Fetch single JIRA story with all details
        
        Args:
            story_key: JIRA issue key (e.g., "BANK-123")
            
        Returns:
            Story dict with all extracted data
        """
        if not self.connected:
            self.connect()
        
        try:
            logger.info("fetching_jira_story", key=story_key)
            
            # Fetch issue with all fields
            issue = self.jira.issue(
                story_key,
                expand='changelog,renderedFields'
            )
            
            # Extract acceptance criteria
            acceptance_criteria = self.extract_acceptance_criteria(issue)
            
            # Extract validators
            validators = self.extract_validators(
                issue.fields.description or ""
            )
            
            # Build story dict
            story = {
                "key": issue.key,
                "summary": issue.fields.summary,
                "description": issue.fields.description or "",
                "acceptance_criteria": acceptance_criteria,
                "validators": validators,
                "story_points": getattr(issue.fields, 'customfield_10016', None),
                "labels": issue.fields.labels or [],
                "components": [c.name for c in (issue.fields.components or [])],
                "assignee": issue.fields.assignee.displayName if issue.fields.assignee else None,
                "reporter": issue.fields.reporter.displayName if issue.fields.reporter else None,
                "status": issue.fields.status.name,
                "priority": issue.fields.priority.name if issue.fields.priority else "Medium",
                "created": issue.fields.created,
                "updated": issue.fields.updated,
                "url": f"{self.server}/browse/{issue.key}",
                "issue_type": issue.fields.issuetype.name,
                "project": issue.fields.project.key
            }
            
            # Detect compliance keywords
            story["compliance_related"] = self.detect_compliance_keywords(story)
            
            # Extract epic if present
            epic_field = getattr(issue.fields, 'customfield_10014', None)
            if epic_field:
                story["epic"] = epic_field
            
            logger.info(
                "story_fetched",
                key=story_key,
                compliance_related=story["compliance_related"],
                ac_count=len(acceptance_criteria),
                validators_count=len(validators)
            )
            
            return story
            
        except JIRAError as e:
            logger.error(
                "story_fetch_failed",
                key=story_key,
                error=str(e)
            )
            return None
    
    def fetch_stories_by_jql(
        self,
        jql: str,
        max_results: int = 50
    ) -> List[Dict]:
        """
        Fetch multiple stories using JQL query
        
        Args:
            jql: JQL query (e.g., "project = BANK AND status = 'To Do'")
            max_results: Maximum stories to return
            
        Returns:
            List of story dicts
        """
        if not self.connected:
            self.connect()
        
        try:
            logger.info("fetching_stories_by_jql", jql=jql, max_results=max_results)
            
            # Execute JQL search
            issues = self.jira.search_issues(
                jql,
                maxResults=max_results,
                expand='changelog,renderedFields'
            )
            
            stories = []
            for issue in issues:
                story = self.fetch_story(issue.key)
                if story:
                    stories.append(story)
            
            logger.info("stories_fetched", count=len(stories))
            
            return stories
            
        except JIRAError as e:
            logger.error("jql_fetch_failed", jql=jql, error=str(e))
            return []
    
    def extract_acceptance_criteria(self, issue) -> List[str]:
        """
        Extract acceptance criteria from JIRA issue
        
        Supports multiple formats:
        - Dedicated AC custom field
        - AC in description (marked with headers)
        - Gherkin scenarios (Given/When/Then)
        - Bullet points
        
        Args:
            issue: JIRA issue object
            
        Returns:
            List of acceptance criteria strings
        """
        criteria = []
        
        # Method 1: Dedicated AC field (customfield ID varies by JIRA instance)
        # Try common field IDs
        for field_id in ['customfield_10020', 'customfield_10021', 'customfield_10019']:
            ac_field = getattr(issue.fields, field_id, None)
            if ac_field:
                criteria.extend(self._parse_ac_text(ac_field))
                break
        
        # Method 2: Parse from description
        description = issue.fields.description or ""
        
        # Look for AC headers
        ac_patterns = [
            r'Acceptance Criteria:?\s*(.*?)(?:\n\n|\Z)',
            r'AC:?\s*(.*?)(?:\n\n|\Z)',
            r'Success Criteria:?\s*(.*?)(?:\n\n|\Z)',
            r'Definition of Done:?\s*(.*?)(?:\n\n|\Z)'
        ]
        
        for pattern in ac_patterns:
            matches = re.findall(pattern, description, re.IGNORECASE | re.DOTALL)
            for match in matches:
                criteria.extend(self._parse_ac_text(match))
        
        # Method 3: Extract Gherkin scenarios
        gherkin = self._extract_gherkin_scenarios(description)
        criteria.extend(gherkin)
        
        # Deduplicate
        criteria = list(dict.fromkeys(criteria))  # Preserve order
        
        return criteria
    
    def _parse_ac_text(self, text: str) -> List[str]:
        """
        Parse acceptance criteria text into individual criteria
        
        Handles:
        - Bullet points (-, *, •)
        - Numbered lists (1., 2.)
        - Checkboxes ([ ], [x])
        
        Args:
            text: Raw AC text
            
        Returns:
            List of individual criteria
        """
        criteria = []
        
        # Split by newlines
        lines = text.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # Remove bullet/number prefixes
            cleaned = re.sub(r'^[-*•]\s*', '', line)
            cleaned = re.sub(r'^\d+\.\s*', '', cleaned)
            cleaned = re.sub(r'^\[\s*[x ]?\s*\]\s*', '', cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.strip()
            
            if cleaned and len(cleaned) > 10:  # Ignore very short lines
                criteria.append(cleaned)
        
        return criteria
    
    def _extract_gherkin_scenarios(self, text: str) -> List[str]:
        """
        Extract Gherkin Given/When/Then scenarios
        
        Args:
            text: Text containing Gherkin
            
        Returns:
            List of scenarios
        """
        scenarios = []
        
        # Pattern: Scenario: ... Given...When...Then
        scenario_pattern = r'Scenario:?\s*(.*?)(?=Scenario:|$)'
        scenario_matches = re.findall(scenario_pattern, text, re.IGNORECASE | re.DOTALL)
        
        for scenario_text in scenario_matches:
            # Check if it has Given/When/Then
            if all(keyword in scenario_text for keyword in ['Given', 'When', 'Then']):
                # Clean up whitespace
                scenario = ' '.join(scenario_text.split())
                scenarios.append(scenario)
        
        return scenarios
    
    def detect_compliance_keywords(self, story: Dict) -> bool:
        """
        Detect if story is compliance-related
        
        Args:
            story: Story dict
            
        Returns:
            True if compliance-related
        """
        # Combine searchable text
        searchable = ' '.join([
            story.get("summary", ""),
            story.get("description", ""),
            ' '.join(story.get("labels", [])),
            ' '.join(story.get("components", []))
        ]).lower()
        
        # USA compliance keywords
        usa_keywords = [
            'wire transfer', 'ach', 'rtp', 'fednow',
            'dual approval', 'sox', 'part 363', 'occ', 'fdic',
            'dodd-frank', 'stress test', 'ccar',
            'glba', 'bsa', 'aml', 'kyc', 'ofac',
            'pci', 'dss', 'encryption'
        ]
        
        # India compliance keywords
        india_keywords = [
            'upi', 'neft', 'rtgs', 'imps',
            'pan', 'aadhaar', 'kyc', 'cdd',
            'rbi', 'npci', 'mpin'
        ]
        
        # Global keywords
        global_keywords = [
            'compliance', 'regulatory', 'audit',
            'limit', 'threshold', 'validation',
            'security', 'authentication', 'authorization'
        ]
        
        all_keywords = usa_keywords + india_keywords + global_keywords
        
        # Check if any keyword present
        return any(keyword in searchable for keyword in all_keywords)
    
    def extract_validators(self, text: str) -> List[str]:
        """
        Extract validator class names from text
        
        Looks for patterns like:
        - "UPIValidator"
        - "validate PAN"
        - "check limit"
        
        Args:
            text: Story description
            
        Returns:
            List of validator names
        """
        validators = []
        
        # Pattern 1: XxxValidator class names
        class_pattern = r'\b([A-Z][a-z]+Validator)\b'
        validators.extend(re.findall(class_pattern, text))
        
        # Pattern 2: "validate X" phrases
        validate_pattern = r'validate\s+([A-Z]+|[A-Za-z]+)'
        for match in re.findall(validate_pattern, text, re.IGNORECASE):
            validators.append(f"{match.upper()}Validator")
        
        # Pattern 3: "check X limit/threshold" phrases
        check_pattern = r'check\s+([A-Z]+|[A-Za-z]+)\s+(?:limit|threshold|validity)'
        for match in re.findall(check_pattern, text, re.IGNORECASE):
            validators.append(f"{match.upper()}Validator")
        
        # Deduplicate
        validators = list(dict.fromkeys(validators))
        
        return validators
    
    def add_comment(self, story_key: str, comment: str) -> bool:
        """
        Add comment to JIRA story
        
        Args:
            story_key: JIRA issue key
            comment: Comment text
            
        Returns:
            True if comment added successfully
        """
        if not self.connected:
            self.connect()
        
        try:
            self.jira.add_comment(story_key, comment)
            logger.info("comment_added", key=story_key)
            return True
        except JIRAError as e:
            logger.error("comment_add_failed", key=story_key, error=str(e))
            return False
    
    def transition_story(self, story_key: str, status: str) -> bool:
        """
        Transition story to new status
        
        Args:
            story_key: JIRA issue key
            status: Target status (e.g., "In Progress", "Done")
            
        Returns:
            True if transition successful
        """
        if not self.connected:
            self.connect()
        
        try:
            # Get available transitions
            transitions = self.jira.transitions(story_key)
            
            # Find matching transition
            transition_id = None
            for t in transitions:
                if t['name'].lower() == status.lower():
                    transition_id = t['id']
                    break
            
            if transition_id:
                self.jira.transition_issue(story_key, transition_id)
                logger.info("story_transitioned", key=story_key, status=status)
                return True
            else:
                logger.warning(
                    "transition_not_found",
                    key=story_key,
                    status=status,
                    available=[t['name'] for t in transitions]
                )
                return False
                
        except JIRAError as e:
            logger.error("transition_failed", key=story_key, error=str(e))
            return False
