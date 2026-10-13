"""
Connector Registry - Auto-discovery and registration of connectors

Status: ✅ COMPLETE
"""
from typing import Dict, List, Type, Optional
import importlib
import pkgutil
from pathlib import Path
import structlog

from src.connectors.base import BaseConnector

logger = structlog.get_logger(__name__)


class ConnectorRegistry:
    """
    Registry for all available connectors
    Automatically discovers connectors in subdirectories
    """
    
    def __init__(self):
        self._connectors: Dict[str, Type[BaseConnector]] = {}
        self._discovered = False
    
    def discover_connectors(self):
        """
        Auto-discover all connector classes
        
        Searches:
        - src/connectors/core_banking/**/connector.py
        - src/connectors/regulatory/**/connector.py
        - src/connectors/requirements/**/connector.py
        """
        if self._discovered:
            return
        
        logger.info("discovering_connectors")
        
        connector_types = ["core_banking", "regulatory", "requirements"]
        
        for connector_type in connector_types:
            self._discover_type(connector_type)
        
        self._discovered = True
        
        logger.info(
            "connectors_discovered",
            total=len(self._connectors),
            connectors=list(self._connectors.keys())
        )
    
    def _discover_type(self, connector_type: str):
        """Discover connectors of specific type"""
        
        base_path = Path(__file__).parent / connector_type
        
        if not base_path.exists():
            logger.debug("connector_type_not_found", type=connector_type)
            return
        
        # Find all connector.py files
        for item in base_path.iterdir():
            if item.is_dir():
                connector_file = item / "connector.py"
                
                if connector_file.exists():
                    connector_name = item.name
                    self._load_connector(connector_type, connector_name)
    
    def _load_connector(self, connector_type: str, connector_name: str):
        """Load single connector class"""
        
        try:
            # Import module
            module_path = f"src.connectors.{connector_type}.{connector_name}.connector"
            module = importlib.import_module(module_path)
            
            # Find connector class (convention: XxxConnector)
            class_name = self._get_class_name(connector_name)
            connector_class = getattr(module, class_name, None)
            
            if connector_class and issubclass(connector_class, BaseConnector):
                key = f"{connector_type}.{connector_name}"
                self._connectors[key] = connector_class
                
                logger.debug(
                    "connector_loaded",
                    connector=key,
                    class_name=class_name
                )
            else:
                logger.warning(
                    "connector_class_not_found",
                    connector=f"{connector_type}.{connector_name}",
                    expected_class=class_name
                )
        
        except ImportError as e:
            logger.debug(
                "connector_import_failed",
                connector=f"{connector_type}.{connector_name}",
                error=str(e)
            )
    
    def _get_class_name(self, connector_name: str) -> str:
        """
        Convert connector name to class name
        
        Examples:
        - rbi → RbiConnector
        - occ_fdic → OccFdicConnector
        - finacle → FinacleConnector
        """
        # Split by underscore, capitalize each part
        parts = connector_name.split('_')
        class_name = ''.join(part.capitalize() for part in parts)
        return f"{class_name}Connector"
    
    def get_connector(self, connector_key: str) -> Optional[Type[BaseConnector]]:
        """
        Get connector class by key
        
        Args:
            connector_key: e.g., "regulatory.rbi", "core_banking.finacle"
            
        Returns:
            Connector class or None
        """
        if not self._discovered:
            self.discover_connectors()
        
        return self._connectors.get(connector_key)
    
    def get_all_connectors(self) -> Dict[str, Type[BaseConnector]]:
        """Get all registered connectors"""
        if not self._discovered:
            self.discover_connectors()
        
        return self._connectors.copy()
    
    def get_connectors_by_type(self, connector_type: str) -> Dict[str, Type[BaseConnector]]:
        """
        Get all connectors of specific type
        
        Args:
            connector_type: "core_banking", "regulatory", "requirements"
        """
        if not self._discovered:
            self.discover_connectors()
        
        return {
            key: cls for key, cls in self._connectors.items()
            if key.startswith(f"{connector_type}.")
        }
    
    def get_connectors_by_geography(self, geography: str) -> Dict[str, Type[BaseConnector]]:
        """
        Get connectors matching geography
        
        Args:
            geography: "usa", "india", or None for all
        """
        if not self._discovered:
            self.discover_connectors()
        
        result = {}
        
        for key, cls in self._connectors.items():
            # Create temp instance to check geography
            instance = cls(config={})
            if instance.is_geography_match(geography):
                result[key] = cls
        
        return result


# Global registry instance
_registry = ConnectorRegistry()


def get_registry() -> ConnectorRegistry:
    """Get global connector registry"""
    return _registry
