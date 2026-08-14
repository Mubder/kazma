"""Model settings package — universal model list helpers.

Importers historically reached into ``kazma_core.settings.model_registry``
directly; this ``__init__`` makes the package explicit (it previously worked
only as an implicit namespace package).
"""

from kazma_core.settings.model_registry import get_model_list_text, get_universal_models

__all__ = ["get_model_list_text", "get_universal_models"]
