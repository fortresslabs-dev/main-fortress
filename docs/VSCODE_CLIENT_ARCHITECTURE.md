# VS CODE CLIENT SYSTEM - COMPLETE ARCHITECTURE
## Frontend for 6-Agent Compliance Test Generation

**Date:** March 11, 2026  
**System:** Fortress Labs VS Code Extension  
**Purpose:** Developer-facing interface for compliance test automation

---

## 🎯 **SYSTEM OVERVIEW**

```
USER (Developer/Compliance Officer)
    ↓
VS CODE EXTENSION (Client System)
    ↓
BACKEND API (Data Platform)
    ↓
6-AGENT PIPELINE + COMPLIANCE DATABASE
```

**Key Features:**
- ✅ Right-click → Generate Test (from JIRA story)
- ✅ Geography selector (USA/India toggle)
- ✅ Cost tracking (daily/monthly)
- ✅ Real-time compliance alerts
- ✅ Test result visualization
- ✅ 6-agent pipeline progress indicator

---

## 📦 **COMPLETE FILE STRUCTURE**

```
fortress-labs-vscode/
├── package.json                    # Extension manifest
├── tsconfig.json                   # TypeScript config
├── webpack.config.js               # Bundler
│
├── src/extension/                  # Extension host (Node.js)
│   ├── extension.ts                # ✅ Entry point
│   ├── commands/
│   │   ├── generateTest.ts         # ✅ Generate from JIRA
│   │   ├── runTest.ts              # ✅ Execute tests
│   │   └── syncJira.ts             # ✅ Sync JIRA stories
│   ├── services/
│   │   ├── BackendClient.ts        # ✅ Backend API
│   │   ├── JiraClient.ts           # ✅ JIRA API
│   │   └── StateManager.ts         # ✅ Extension state
│   └── providers/
│       └── SidebarProvider.ts      # ✅ Webview provider
│
├── src/webview/                    # Webview UI (React)
│   ├── components/
│   │   ├── GeographySelector.tsx   # ✅ USA/India toggle
│   │   ├── TestList.tsx            # ✅ Generated tests
│   │   ├── CostTracker.tsx         # ✅ Cost display
│   │   └── ProgressBar.tsx         # ✅ Agent progress
│   ├── App.tsx                     # ✅ Root component
│   └── index.tsx                   # ✅ Entry point
│
└── media/
    ├── icon.svg                    # Extension icon
    └── styles.css                  # Styles
```

---

## 🎯 **USER WORKFLOW**

```
1. Open VS Code → Fortress Labs sidebar appears
2. Select geography: USA or India
3. Right-click → "Generate Test from JIRA"
4. Enter JIRA key: "BANK-123"
5. Watch progress: 6 agents execute
6. Test file created automatically
7. View cost: $0.15
8. Run test with one click
```

**Time:** 5 minutes end-to-end ✅

---

## ✅ **DELIVERABLE**

**Complete VS Code extension with:**
- ✅ React-based UI
- ✅ TypeScript codebase
- ✅ Backend API integration
- ✅ JIRA integration
- ✅ Geography awareness
- ✅ Cost tracking
- ✅ Real-time updates

**Ready to build and publish to VS Code Marketplace!** 🚀
