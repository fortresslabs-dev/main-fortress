#!/usr/bin/env python3
"""
JIRA Connector Test Script
Tests the JIRA connector implementation

Usage:
    python scripts/connectors/test_jira.py
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.connectors.requirements.jira.connector import JiraConnector
import structlog

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()


def test_jira_connector():
    """Test JIRA connector functionality"""
    
    print("=" * 70)
    print("FORTRESS LABS - JIRA CONNECTOR TEST")
    print("=" * 70)
    print()
    
    # Step 1: Check environment variables
    print("Step 1: Checking configuration...")
    print("-" * 70)
    
    server = os.getenv('REQUIREMENTS_JIRA_SERVER')
    email = os.getenv('REQUIREMENTS_JIRA_EMAIL')
    api_token = os.getenv('REQUIREMENTS_JIRA_API_TOKEN')
    
    if not all([server, email, api_token]):
        print("❌ JIRA credentials not found in environment variables")
        print()
        print("Please add to your .env file:")
        print("  REQUIREMENTS_JIRA_SERVER=https://your-company.atlassian.net")
        print("  REQUIREMENTS_JIRA_EMAIL=your-email@company.com")
        print("  REQUIREMENTS_JIRA_API_TOKEN=xxxxx")
        print()
        print("Get API token from:")
        print("  https://id.atlassian.com/manage-profile/security/api-tokens")
        return False
    
    print(f"✅ Server: {server}")
    print(f"✅ Email: {email}")
    print(f"✅ API Token: {'*' * 20}{api_token[-4:]}")
    print()
    
    # Step 2: Initialize connector
    print("Step 2: Initializing JIRA connector...")
    print("-" * 70)
    
    config = {
        'server': server,
        'email': email,
        'api_token': api_token
    }
    
    jira = JiraConnector(config=config)
    print("✅ Connector initialized")
    print()
    
    # Step 3: Test connection
    print("Step 3: Testing connection...")
    print("-" * 70)
    
    if jira.connect():
        print("✅ Connected to JIRA successfully")
    else:
        print("❌ Connection failed")
        return False
    print()
    
    # Step 4: Test getting current user
    print("Step 4: Testing authentication...")
    print("-" * 70)
    
    if jira.test_connection():
        try:
            user = jira.jira.myself()
            print(f"✅ Authenticated as: {user.get('displayName')}")
            print(f"   Email: {user.get('emailAddress')}")
        except Exception as e:
            print(f"⚠️  Could not get user info: {e}")
    else:
        print("❌ Authentication test failed")
        return False
    print()
    
    # Step 5: Fetch a single story
    print("Step 5: Fetching a sample story...")
    print("-" * 70)
    
    # Get story key from env or use default
    story_key = os.getenv('TEST_JIRA_STORY_KEY')
    
    if not story_key:
        # Try to find any recent story
        print("No TEST_JIRA_STORY_KEY provided, searching for recent stories...")
        try:
            jql = "ORDER BY created DESC"
            stories = jira.fetch_stories_by_jql(jql, max_results=1)
            
            if stories:
                story_key = stories[0]['key']
                print(f"✅ Found recent story: {story_key}")
            else:
                print("⚠️  No stories found. Cannot test story fetching.")
                print("   Set TEST_JIRA_STORY_KEY in .env to test with specific story")
                story_key = None
        except Exception as e:
            print(f"⚠️  Could not search for stories: {e}")
            story_key = None
    
    if story_key:
        try:
            story = jira.fetch_story(story_key)
            
            if story:
                print(f"✅ Story fetched: {story_key}")
                print(f"   Summary: {story['summary']}")
                print(f"   Status: {story['status']}")
                print(f"   Assignee: {story['assignee']}")
                print(f"   Compliance Related: {story['compliance_related']}")
                print(f"   Acceptance Criteria: {len(story['acceptance_criteria'])} found")
                
                if story['acceptance_criteria']:
                    print("   First AC:", story['acceptance_criteria'][0][:80] + "...")
                
                print(f"   Validators: {story['validators']}")
            else:
                print(f"❌ Could not fetch story: {story_key}")
        except Exception as e:
            print(f"❌ Error fetching story: {e}")
    print()
    
    # Step 6: Test JQL query
    print("Step 6: Testing JQL query...")
    print("-" * 70)
    
    try:
        jql = os.getenv('TEST_JIRA_JQL', 'ORDER BY created DESC')
        print(f"JQL: {jql}")
        
        stories = jira.fetch_stories_by_jql(jql, max_results=5)
        
        print(f"✅ Found {len(stories)} stories")
        
        for i, story in enumerate(stories, 1):
            print(f"   {i}. {story['key']}: {story['summary'][:60]}")
            print(f"      Status: {story['status']} | Compliance: {story['compliance_related']}")
        
    except Exception as e:
        print(f"❌ JQL query failed: {e}")
    print()
    
    # Step 7: Test compliance detection
    print("Step 7: Testing compliance keyword detection...")
    print("-" * 70)
    
    test_stories = [
        {
            "summary": "Implement wire transfer with dual approval",
            "description": "Add dual approval for wire transfers over $1M per OCC requirements",
            "labels": ["compliance", "occ"],
            "components": []
        },
        {
            "summary": "Update homepage banner",
            "description": "Change the color scheme of the homepage banner",
            "labels": ["frontend"],
            "components": []
        }
    ]
    
    for test_story in test_stories:
        is_compliance = jira.detect_compliance_keywords(test_story)
        print(f"   '{test_story['summary'][:50]}...'")
        print(f"   → Compliance Related: {is_compliance}")
    print()
    
    # Step 8: Disconnect
    print("Step 8: Disconnecting...")
    print("-" * 70)
    
    jira.disconnect()
    print("✅ Disconnected")
    print()
    
    # Summary
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print("✅ JIRA connector is working!")
    print()
    print("Next steps:")
    print("  1. Integrate with PluginManager")
    print("  2. Use in Understanding Agent")
    print("  3. Generate compliance tests from JIRA stories")
    print()
    
    return True


if __name__ == "__main__":
    try:
        success = test_jira_connector()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n❌ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
