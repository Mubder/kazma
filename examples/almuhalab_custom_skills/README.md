# ALMuhalab Custom Skills

Domain-specific operational modules for **ALMuhalab International Holding Group**,
extracted from `kazma-core` as a reference implementation of how to build custom
skills on top of the domain-agnostic Kazma engine.

## Modules

| Module | Description |
|--------|-------------|
| `drone_inspection/` | FPV drone telemetry ingestion, YOLOv11 object detection, inspection report generation, fleet management |
| `trading_intel/` | Market data correlation, autonomous intelligence loop, trading intel reports in Kuwaiti Arabic |
| `branding/` | ALMuhalab division-specific brand guidelines (Gas & Oil, Tourism, General Trading) |
| `asset_generation/` | Division-branded image and video generation pipeline |

## Quick Start

```python
# Import from the extracted skills
from almuhalab_custom_skills.drone_inspection import YOLODetector, FleetManager
from almuhalab_custom_skills.trading_intel import TradingIntelligenceLoop
from almuhalab_custom_skills.branding import BrandGuidelines
from almuhalab_custom_skills.asset_generation import DivisionImageGenerator
```

## Skill Manifest

See `skill_manifest.yaml` for the full capability manifest, dependencies,
MCP server configuration, and permissions required by these skills.

## Usage as Reference Implementation

These skills demonstrate how to:

1. **Build domain modules** that depend on `kazma-core` for infrastructure
   (checkpointing, tracing, RBAC, cultural context) while owning all
   domain-specific logic.

2. **Wire MCP servers** for external data sources (oil pricing, booking).

3. **Structure tests** that run independently from core engine tests.

4. **Export clean APIs** via `__init__.py` for convenient imports.

## Running Tests

```bash
# Run only ALMuhalab skill tests
pytest examples/almuhalab_custom_skills/tests/ -v

# Run core engine tests (should have zero domain-specific references)
pytest kazma-core/ -v
```

## Architecture

```
kazma-core/          ← Domain-agnostic engine (agent loop, checkpointing, RBAC, etc.)
examples/
  almuhalab_custom_skills/   ← THIS PACKAGE — domain-specific skills
    drone_inspection/
    trading_intel/
    branding/
    asset_generation/
    tests/
```

The core engine has **zero** ALMuhalab-specific references. These skills
are the extension point — drop in your own domain modules following this
same pattern.
