"""Domain-specific strategies for specialized use cases.

This package contains strategies optimized for specific domains:
- software_dev: Software development and technical documentation
- legal: Legal analysis and document review
- hr: HR processes and employee policies
"""

from backend.agent.strategies.domains.software_dev import SoftwareDevStrategy
from backend.agent.strategies.domains.legal import LegalAnalysisStrategy
from backend.agent.strategies.domains.hr import HRProcessStrategy

__all__ = [
    "SoftwareDevStrategy",
    "LegalAnalysisStrategy",
    "HRProcessStrategy",
]
