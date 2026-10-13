#!/bin/bash

BASE_URL="http://ec2-3-145-7-205.us-east-2.compute.amazonaws.com:8000"

echo "🧪 Testing Complete TEXT MIND Workflow..."
echo ""

# Test 1: Trigger workflow
echo "📝 Step 1: Triggering JIRA webhook..."
RESPONSE=$(curl -s -X POST $BASE_URL/webhooks/jira \
  -H "Content-Type: application/json" \
  -d '{
    "issue": {
      "key": "BANK-TEST-001",
      "fields": {
        "summary": "UPI transfer with daily limit validation",
        "description": "As a user, I want to transfer ₹50,000 via UPI so that I can make instant payments. The system should validate that the amount is within the ₹1,00,000 daily limit and require MPIN authentication."
      }
    }
  }')

echo "$RESPONSE" | python3 -m json.tool
WORKFLOW_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('workflow_id', ''))")

if [ -z "$WORKFLOW_ID" ]; then
    echo "❌ Failed to get workflow_id"
    exit 1
fi

echo ""
echo "✅ Workflow ID: $WORKFLOW_ID"
echo ""

# Test 2: Wait for processing
echo "⏳ Step 2: Waiting 30 seconds for agents to process..."
sleep 30

# Test 3: Get results
echo "📊 Step 3: Retrieving workflow results..."
curl -s $BASE_URL/workflows/$WORKFLOW_ID | python3 -m json.tool

echo ""
echo "🎉 Test complete!"