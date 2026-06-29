#!/bin/bash
# TUI Replacement Mission Init Script

# Install dependencies
pip install -e kazma-tui/ -e kazma-core/

# Verify textual is installed
python -c "import textual; print(f'textual {textual.__version__}')"

# Verify kazma-core imports
python -c "from kazma_core.telemetry import HardwareMonitor; print('HardwareMonitor OK')"
python -c "from kazma_core.swarm.metrics import MetricsCollector; print('MetricsCollector OK')"
python -c "from kazma_core.tracing import TraceStore; print('TraceStore OK')"
python -c "from kazma_core.model_registry import get_model_registry; print('ModelRegistry OK')"

echo "Init complete."
