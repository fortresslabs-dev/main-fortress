#!/bin/bash

BASE_URL="http://ec2-3-145-7-205.us-east-2.compute.amazonaws.com:8000"

echo "🧪 Testing TEXT MIND Backend (Real Endpoints)..."
echo ""

# Test 1: Health Check
echo "✅ Test 1: Health Check"
curl -s $BASE_URL/health/ | python3 -m json.tool
echo -e "\n"

# Test 2: Readiness Check
echo "✅ Test 2: Readiness Check"
curl -s $BASE_URL/health/ready | python3 -m json.tool
echo -e "\n"

# Test 3: Agents Status
echo "✅ Test 3: Agents Status"
curl -s $BASE_URL/agents/status | python3 -m json.tool
echo -e "\n"

# Test 4: JIRA Webhook (THIS is how you generate tests!)
echo "✅ Test 4: JIRA Webhook - Generate Tests"
echo "Sending JIRA story: UPI transfer with daily limit..."
RESPONSE=$(curl -s -X POST $BASE_URL/webhooks/jira \
  -H "Content-Type: application/json" \
  -d '{
    "issue": {
      "key": "BANK-123",
      "fields": {
        "summary": "UPI transfer with daily limit",
        "description": "As a user, I want to transfer money via UPI with ₹1,00,000 daily limit validation"
      }
    }
  }')

echo "$RESPONSE" | python3 -m json.tool
echo -e "\n"

# Test 5: Get Pending Approvals (if workflow created)
echo "✅ Test 5: Get Pending Approvals"
curl -s $BASE_URL/approvals/workflow-123 | python3 -m json.tool
echo -e "\n"

echo "🎉 Tests complete!"