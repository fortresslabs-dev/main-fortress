"""Workflow Orchestration Engine - Complete Implementation"""
import structlog
from typing import Dict, Any, List

from src.agents.discovery import DiscoveryAgent
from src.agents.understanding import UnderstandingAgent
from src.agents.framework import FrameworkAgent
from src.agents.generation_bedrock import GenerationAgent
from src.agents.validation import ValidationAgent
from src.agents.execution import ExecutionAgent

logger = structlog.get_logger()

class WorkflowOrchestrator:
    def __init__(self):
        self.logger = logger
        # Initialize all 6 agents
        self.discovery = DiscoveryAgent()
        self.understanding = UnderstandingAgent()
        self.framework = FrameworkAgent()
        self.generation = GenerationAgent()
        self.validation = ValidationAgent()
        self.execution = ExecutionAgent()

    async def execute_workflow(self, jira_story: Dict, stop_before_execution: bool = False) -> Dict[str, Any]:
        """Execute the 6-agent workflow.

        When ``stop_before_execution`` is True the pipeline halts after Agent 5
        (validation) and returns ``status="waiting_approval"`` WITHOUT running
        Agent 6 — i.e. no GitHub PR — so a CCO can approve/reject at the
        pre_execution checkpoint. ``finalize_execution()`` runs Agent 6 later,
        on approval. Default False preserves straight-through behavior.
        """
        
        workflow_id = jira_story.get("workflow_id")
        jira_key = jira_story.get("key", "")
        story_text = jira_story.get("description", "")
        rule_id = jira_story.get("rule_id")
        
        context = {
            "workflow_id": workflow_id,
            "jira_key": jira_key
        }
        
        self.logger.info("workflow_started", workflow_id=workflow_id)
        
        try:
            # AGENT 1: Discovery
            self.logger.info("agent_1_discovery", workflow_id=workflow_id)
            discovery_result = await self.discovery.execute(
                context,
                {
                    "workflow_id": workflow_id,
                    "jira_key": jira_key,
                    "jira_story": story_text
                }
            )
            
            # AGENT 2: Understanding
            self.logger.info("agent_2_understanding", workflow_id=workflow_id)
            understanding_result = await self.understanding.execute(
                context,
                {
                    "workflow_id": workflow_id,
                    "jira_key": jira_key,
                    "jira_story": story_text,
                    "discovery": discovery_result,
                    "rule_id": rule_id
                }
            )
            
            # AGENT 3: Framework (Generate BDD scenarios)
            self.logger.info("agent_3_framework", workflow_id=workflow_id)
            framework_result = await self.framework.execute(
                context,
                {
                    "workflow_id": workflow_id,
                    "understanding": understanding_result
                }
            )
            
            scenarios = framework_result["scenarios"]
            
            # AGENT 4: Generation (Generate pytest code)
            self.logger.info("agent_4_generation", workflow_id=workflow_id)
            generation_result = await self.generation.execute(
                context,
                {
                    "workflow_id": workflow_id,
                    "jira_key": jira_key,
                    "scenarios": scenarios,
                    "understanding": understanding_result,
                    "rule_id": rule_id
                }
            )
            
            test_code = generation_result["test_code"]
            
            # AGENT 5: Validation
            self.logger.info("agent_5_validation", workflow_id=workflow_id)
            validation_result = await self.validation.execute(
                context,
                {
                    "workflow_id": workflow_id,
                    "test_code": test_code,
                    "scenarios": scenarios
                }
            )
            
            # Human-in-the-loop checkpoint: hold before opening the PR so the
            # CCO can review the generated + validated test. Agent 6 runs only
            # after approval, via finalize_execution().
            if stop_before_execution:
                self.logger.info("workflow_awaiting_approval", workflow_id=workflow_id)
                return {
                    "workflow_id": workflow_id,
                    "jira_key": jira_key,
                    "status": "waiting_approval",
                    "checkpoint": "pre_execution",
                    "discovery": discovery_result,
                    "understanding": understanding_result,
                    "scenarios": scenarios,
                    "test_code": test_code,
                    "validation": validation_result,
                    "quality_score": validation_result.get("quality_score", 0),
                }

            # AGENT 6: Execution (Finalize — opens GitHub PR, posts JIRA comment)
            self.logger.info("agent_6_execution", workflow_id=workflow_id)
            execution_result = await self.execution.execute(
                context,
                {
                    "workflow_id": workflow_id,
                    "jira_key": jira_key,
                    "validation": validation_result,
                    "test_code": test_code,
                    "scenarios": scenarios,
                    "understanding": understanding_result,
                }
            )
            
            # Build complete result
            result = {
                "workflow_id": workflow_id,
                "jira_key": jira_key,
                "status": "success",
                "discovery": discovery_result,
                "understanding": understanding_result,
                "scenarios": scenarios,
                "test_code": test_code,
                "validation": validation_result,
                "execution": execution_result,
                "quality_score": validation_result.get("quality_score", 0)
            }
            
            self.logger.info(
                "workflow_completed",
                workflow_id=workflow_id,
                quality_score=result["quality_score"]
            )
            
            return result
            
        except Exception as e:
            self.logger.error(
                "workflow_failed",
                workflow_id=workflow_id,
                error=str(e),
                exc_info=True
            )
            raise

    async def finalize_execution(self, prior: Dict[str, Any]) -> Dict[str, Any]:
        """Run Agent 6 (execution → GitHub PR) from a generated + validated +
        CCO-approved workflow. Called after the pre_execution checkpoint is
        approved; ``prior`` is the persisted artifacts from the paused run.
        Returns the execution result (includes ``pr_url``)."""
        workflow_id = prior.get("workflow_id")
        jira_key = prior.get("jira_key", "")
        self.logger.info("agent_6_execution_post_approval", workflow_id=workflow_id)
        return await self.execution.execute(
            {"workflow_id": workflow_id, "jira_key": jira_key},
            {
                "workflow_id": workflow_id,
                "jira_key": jira_key,
                "validation": prior.get("validation", {}),
                "test_code": prior.get("test_code", ""),
                "scenarios": prior.get("scenarios", []),
                "understanding": prior.get("understanding", {}),
            },
        )