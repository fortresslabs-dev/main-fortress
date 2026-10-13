"""
Plugin Manager - Loads Connectors Based on Customer Geography
Handles India (RBI) vs USA (OCC/FDIC) connector selection

Status: ✅ COMPLETE
Usage:
    manager = PluginManager(customer_id="wells_fargo")
    manager.load_connectors()  # Loads OCC/FDIC, not RBI
"""
import yaml
import os
from typing import Dict, List, Optional, Any
from pathlib import Path
import structlog

logger = structlog.get_logger(__name__)


class PluginManager:
    """
    Manages connector plugins based on customer geography
    
    Config hierarchy:
    1. global.yaml (base defaults)
    2. india.yaml OR usa.yaml (geography defaults)
    3. customer_overrides/{customer_id}.yaml (customer-specific)
    """
    
    def __init__(self, customer_id: Optional[str] = None, geography: Optional[str] = None):
        """
        Initialize plugin manager
        
        Args:
            customer_id: Customer ID (e.g., "wells_fargo", "hdfc_bank")
            geography: Override geography (e.g., "usa", "india")
        """
        self.customer_id = customer_id
        self.geography = geography
        
        self.config_dir = Path(__file__).parent.parent.parent / "config" / "connectors"
        self.customer_overrides_dir = self.config_dir / "customer_overrides"
        
        self.config = self._load_config()
        self.connectors = {}
        
        logger.info(
            "plugin_manager_initialized",
            customer_id=customer_id,
            geography=self.config.get("geography"),
            connectors_enabled=len(self._get_enabled_connectors())
        )
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration with hierarchy: global → geography → customer"""
        
        # 1. Load global defaults
        config = self._load_yaml("global.yaml")
        
        # 2. Load geography-specific config
        if self.geography:
            geography_config = self._load_yaml(f"{self.geography}.yaml")
        elif self.customer_id:
            # Infer geography from customer override
            customer_config = self._load_yaml(f"customer_overrides/{self.customer_id}.yaml")
            inherits_from = customer_config.get("inherits_from", "global")
            geography_config = self._load_yaml(f"{inherits_from}.yaml")
            self.geography = geography_config.get("geography")
        else:
            geography_config = {}
        
        # Merge: global + geography
        config = self._merge_configs(config, geography_config)
        
        # 3. Load customer-specific overrides
        if self.customer_id:
            customer_override = self._load_yaml(f"customer_overrides/{self.customer_id}.yaml")
            config = self._merge_configs(config, customer_override)
        
        logger.info(
            "config_loaded",
            geography=config.get("geography"),
            customer=config.get("customer", {}).get("name")
        )
        
        return config
    
    def _load_yaml(self, filename: str) -> Dict[str, Any]:
        """Load YAML configuration file"""
        filepath = self.config_dir / filename
        
        if not filepath.exists():
            logger.warning("config_file_not_found", file=filename)
            return {}
        
        try:
            with open(filepath, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("config_load_failed", file=filename, error=str(e))
            return {}
    
    def _merge_configs(self, base: Dict, override: Dict) -> Dict:
        """Deep merge two configuration dictionaries"""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _get_enabled_connectors(self) -> List[str]:
        """Get list of enabled connectors from config.

        P1 (scraper-only) skips core_banking and requirements connectors —
        those are P2 features that need a bank's CBS / JIRA on the other end.
        Their source modules don't exist in P1's tree, so loading them just
        produces noise.

        Gate signal: presence of customer_id. The P1 scraper path constructs
        PluginManager(geography="usa") with no customer_id; P2 bank-side
        always passes customer_id="<bank>". Relying on this intrinsic signal
        avoids depending on PIPELINE_MODE env var propagation in ECS.
        """
        is_bank_side = self.customer_id is not None
        enabled = []

        connectors_config = self.config.get("connectors", {})

        # Core banking + requirements connectors — bank-side only
        if is_bank_side:
            for connector in connectors_config.get("core_banking", []):
                enabled.append(f"core_banking.{connector}")
            for connector in connectors_config.get("requirements", []):
                enabled.append(f"requirements.{connector}")

        # Regulatory connectors — both pipelines
        for connector in connectors_config.get("regulatory", []):
            enabled.append(f"regulatory.{connector}")

        return enabled
    
    def load_connectors(self) -> Dict[str, Any]:
        """
        Load and initialize all enabled connectors
        
        Returns:
            Dict of initialized connector instances
        """
        enabled = self._get_enabled_connectors()
        
        logger.info("loading_connectors", count=len(enabled), connectors=enabled)
        
        for connector_path in enabled:
            try:
                connector_type, connector_name = connector_path.split(".")
                connector_class = self._import_connector(connector_type, connector_name)
                
                if connector_class:
                    # Initialize connector with customer-specific config
                    connector_config = self._get_connector_config(connector_type, connector_name)
                    instance = connector_class(config=connector_config)
                    
                    self.connectors[connector_path] = instance
                    
                    logger.info(
                        "connector_loaded",
                        connector=connector_path,
                        geography=self.geography
                    )
            
            except Exception as e:
                logger.error(
                    "connector_load_failed",
                    connector=connector_path,
                    error=str(e)
                )
        
        return self.connectors
    
    def _import_connector(self, connector_type: str, connector_name: str):
        """Dynamically import connector class.

        Searches two module path patterns:
          1. Geography-scoped: src.connectors.{type}.{geography}.{name}
             e.g. src.connectors.regulatory.usa.occ_bsa
          2. Direct:           src.connectors.{type}.{name}.connector
             e.g. src.connectors.requirements.jira.connector
        """
        class_name = f"{connector_name.title().replace('_', '')}Connector"

        # Pattern 1: geography-scoped (USA / India connectors)
        if self.geography:
            geo_module_path = f"src.connectors.{connector_type}.{self.geography}.{connector_name}"
            cls = self._try_import(geo_module_path, class_name)
            if cls:
                return cls

        # Pattern 1b: geography-scoped under states/ subpackage (e.g. regulatory.usa.states.nydfs)
        if self.geography:
            states_module_path = f"src.connectors.{connector_type}.{self.geography}.states.{connector_name}"
            cls = self._try_import(states_module_path, class_name)
            if cls:
                return cls

        # Pattern 2: direct module
        direct_module_path = f"src.connectors.{connector_type}.{connector_name}.connector"
        cls = self._try_import(direct_module_path, class_name)
        if cls:
            return cls

        logger.warning(
            "connector_import_failed",
            connector=f"{connector_type}.{connector_name}",
            error="Module not found in geography or direct paths",
        )
        return None

    @staticmethod
    def _try_import(module_path: str, class_name: str):
        """Try to import *class_name* from *module_path*. Return None on failure."""
        try:
            module = __import__(module_path, fromlist=[class_name])
            return getattr(module, class_name, None)
        except ImportError:
            return None
    
    def _get_connector_config(self, connector_type: str, connector_name: str) -> Dict:
        """Get connector-specific configuration"""
        
        # For core banking, return connection details
        if connector_type == "core_banking":
            core_banking_config = self.config.get("core_banking", {})
            
            if connector_name in ["finacle", "bancs", "flexcube"]:
                return {
                    "host": os.getenv(core_banking_config.get("connection", {}).get("host_env_var", "")),
                    "port": core_banking_config.get("connection", {}).get("port"),
                    "service": core_banking_config.get("connection", {}).get("service"),
                    "user": os.getenv(core_banking_config.get("connection", {}).get("readonly_user_env_var", "")),
                    "password": os.getenv(core_banking_config.get("connection", {}).get("readonly_password_env_var", ""))
                }
        
        # For regulatory, return geography-specific settings
        elif connector_type == "regulatory":
            source_urls = self.config.get("source_urls", {}).get(connector_name, {})
            return {
                "geography": self.geography,
                "country_code": self.config.get("country_code"),
                "currency": self.config.get("currency"),
                "urls": source_urls,
            }
        
        # For requirements, return connection details
        elif connector_type == "requirements":
            if connector_name == "jira":
                return {
                    "server": os.getenv("REQUIREMENTS_JIRA_SERVER"),
                    "email": os.getenv("REQUIREMENTS_JIRA_EMAIL"),
                    "api_token": os.getenv("REQUIREMENTS_JIRA_API_TOKEN")
                }
        
        return {}
    
    def get_compliance_rules(self) -> Dict[str, Any]:
        """
        Get compliance rules from all loaded regulatory connectors
        
        For USA customer: Returns OCC/FDIC rules
        For India customer: Returns RBI rules
        """
        import time

        compliance_rules = {}
        
        # Find all regulatory connectors
        regulatory_connectors = {
            name: conn for name, conn in self.connectors.items()
            if name.startswith("regulatory.")
        }

        # Stagger execution: wait between connectors to avoid CPU/network bursts
        try:
            from src.utils.config import settings
            connector_delay = settings.SCRAPER_CONNECTOR_DELAY
        except Exception:
            connector_delay = int(os.getenv("SCRAPER_CONNECTOR_DELAY", "10"))
        
        logger.info(
            "fetching_compliance_rules",
            geography=self.geography,
            regulatory_connectors=list(regulatory_connectors.keys()),
            connector_delay=connector_delay,
        )
        
        for idx, (connector_name, connector) in enumerate(regulatory_connectors.items()):
            # Stagger: wait between connectors (skip delay before the first one)
            if idx > 0 and connector_delay > 0:
                logger.info(
                    "connector_stagger_delay",
                    next_connector=connector_name,
                    delay_seconds=connector_delay,
                )
                time.sleep(connector_delay)

            try:
                # Each connector has an extract_all_rules() method
                rules = connector.extract_all_rules()
                
                # Merge into compliance_rules
                for rule_category, rule_list in rules.items():
                    if rule_category not in compliance_rules:
                        compliance_rules[rule_category] = []
                    compliance_rules[rule_category].extend(rule_list)
                
                logger.info(
                    "rules_extracted",
                    connector=connector_name,
                    categories=len(rules),
                    total_rules=sum(len(r) for r in rules.values())
                )
            
            except Exception as e:
                logger.error(
                    "rule_extraction_failed",
                    connector=connector_name,
                    error=str(e)
                )
        
        return compliance_rules
    
    def get_connector(self, connector_path: str) -> Optional[Any]:
        """Get specific connector instance"""
        return self.connectors.get(connector_path)
    
    def is_connector_enabled(self, connector_path: str) -> bool:
        """Check if connector is enabled for this customer"""
        return connector_path in self._get_enabled_connectors()
    
    def get_geography(self) -> str:
        """Get customer geography (usa, india, etc.)"""
        return self.geography
    
    def get_customer_config(self) -> Dict[str, Any]:
        """Get full customer configuration"""
        return self.config


# Example usage for USA customer (Wells Fargo)
def example_usa_customer():
    """Example: Load connectors for Wells Fargo (USA bank)"""
    
    # Initialize for Wells Fargo
    manager = PluginManager(customer_id="wells_fargo")
    
    # Load all connectors (will load OCC/FDIC, NOT RBI)
    connectors = manager.load_connectors()
    
    # Get geography
    print(f"Geography: {manager.get_geography()}")  # Output: usa
    
    # Check enabled connectors
    print(f"Enabled connectors: {manager._get_enabled_connectors()}")
    # Output: ['core_banking.flexcube', 'regulatory.occ', 'regulatory.fdic', ...]
    
    # Get compliance rules (OCC/FDIC rules, NOT RBI)
    compliance_rules = manager.get_compliance_rules()
    print(f"Compliance rules: {compliance_rules.keys()}")
    # Output: dict_keys(['occ_part_363', 'fdic_capital_rules', 'sox_404', ...])
    
    # Verify RBI is NOT loaded
    assert not manager.is_connector_enabled("regulatory.rbi")
    print("✅ RBI connector NOT loaded for USA customer")
    
    # Verify OCC IS loaded
    assert manager.is_connector_enabled("regulatory.occ")
    print("✅ OCC connector loaded for USA customer")


# Example usage for India customer (HDFC)
def example_india_customer():
    """Example: Load connectors for HDFC (India bank)"""
    
    # Initialize for HDFC
    manager = PluginManager(customer_id="hdfc_bank")
    
    # Load all connectors (will load RBI, NOT OCC/FDIC)
    connectors = manager.load_connectors()
    
    # Get geography
    print(f"Geography: {manager.get_geography()}")  # Output: india
    
    # Get compliance rules (RBI rules, NOT OCC/FDIC)
    compliance_rules = manager.get_compliance_rules()
    print(f"Compliance rules: {compliance_rules.keys()}")
    # Output: dict_keys(['rbi_upi_rules', 'rbi_neft_rules', ...])
    
    # Verify OCC is NOT loaded
    assert not manager.is_connector_enabled("regulatory.occ")
    print("✅ OCC connector NOT loaded for India customer")
    
    # Verify RBI IS loaded
    assert manager.is_connector_enabled("regulatory.rbi")
    print("✅ RBI connector loaded for India customer")


if __name__ == "__main__":
    # Test USA customer
    print("=" * 60)
    print("TESTING USA CUSTOMER (Wells Fargo)")
    print("=" * 60)
    example_usa_customer()
    
    print("\n")
    
    # Test India customer
    print("=" * 60)
    print("TESTING INDIA CUSTOMER (HDFC)")
    print("=" * 60)
    example_india_customer()
