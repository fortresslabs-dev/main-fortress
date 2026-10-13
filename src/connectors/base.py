"""
Base Connector - Parent class for all connectors
All connectors (RBI, OCC, Finacle, JIRA, etc.) inherit from this

Status: ✅ COMPLETE
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
import structlog

logger = structlog.get_logger(__name__)


class BaseConnector(ABC):
    """
    Base class for all connectors
    
    Subclasses must implement:
    - connect() - Establish connection
    - disconnect() - Close connection
    - test_connection() - Verify connection works
    - extract_all_rules() - Get all compliance/config data
    """
    
    connector_type: str = "base"  # Override in subclass
    connector_name: str = "base"  # Override in subclass
    geography: Optional[str] = None  # "usa", "india", "global"
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize connector
        
        Args:
            config: Configuration dict from PluginManager
        """
        self.config = config or {}
        self.connected = False
        
        logger.info(
            "connector_initialized",
            connector=f"{self.connector_type}.{self.connector_name}",
            geography=self.geography
        )
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to external system
        
        Returns:
            True if connection successful
        """
        pass
    
    @abstractmethod
    def disconnect(self):
        """Close connection"""
        pass
    
    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test if connection is working
        
        Returns:
            True if connection test passes
        """
        pass
    
    @abstractmethod
    def extract_all_rules(self) -> Dict[str, List[Dict]]:
        """
        Extract all compliance rules/configuration from source
        
        Returns:
            Dict of rule categories with lists of rules
            Example:
            {
                "upi_limits": [{"rule": "per_transaction", "value": 100000}],
                "audit_requirements": [...]
            }
        """
        pass
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        return self.config.get(key, default)
    
    def is_geography_match(self, geography: str) -> bool:
        """Check if connector matches geography"""
        if self.geography is None:
            return True  # Global connector
        return self.geography == geography
    
    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()


class CoreBankingConnector(BaseConnector):
    """Base class for core banking system connectors"""
    connector_type = "core_banking"


class RegulatoryConnector(BaseConnector):
    """Base class for regulatory connectors"""
    connector_type = "regulatory"


class RequirementsConnector(BaseConnector):
    """Base class for requirements management connectors"""
    connector_type = "requirements"
