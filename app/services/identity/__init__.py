"""Customer identity domain public surface.

Other services import DTOs from ``schemas`` and Protocols from ``contract``.
Concrete persistence and service implementations remain internal.
"""

from app.services.identity.contract import IdentityRepository, IdentityServiceProtocol
from app.services.identity.schemas import Principal

__all__ = ["IdentityRepository", "IdentityServiceProtocol", "Principal"]
