"""Documentation Generator — Extracts docstrings and generates API reference.

Creates markdown documentation from Python source files with support for:
- Module/module docstrings
- Classes and methods
- Function signatures
- Skill development guide
- CLI reference
- Security documentation
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DocPage:
    """Represents a documentation page."""

    title: str
    category: str
    filename: str
    content: str
    frontmatter: dict[str, Any] | None = None

    def render(self) -> str:
        """Render as markdown with frontmatter."""
        lines = []
        if self.frontmatter:
            lines.append("---")
            for key, value in self.frontmatter.items():
                if isinstance(value, str):
                    lines.append(f"{key}: {value}")
                elif isinstance(value, int):
                    lines.append(f"{key}: {value}")
                else:
                    lines.append(f"{key}: {value}")
            lines.append("---")
        else:
            lines.append("---")
            lines.append("---")
        lines.append(self.content)
        return "\n".join(lines)


class DocumentationGenerator:
    """Generates documentation from Python source."""

    def __init__(self, source_dir: str = "kazma-core/kazma_core"):
        self.source_dir = Path(source_dir)

    def _parse_module(self, py_file: Path) -> ast.Module | None:
        """Parse a Python file into an AST module."""
        try:
            source = py_file.read_text()
            return ast.parse(source)
        except SyntaxError:
            return None

    def _get_docstring(self, node: ast.AST) -> str | None:
        """Extract docstring from an AST node."""
        return ast.get_docstring(node)

    def _get_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Get function signature as a string."""
        args = []
        for arg in node.args.args:
            if arg.arg != "self":
                if arg.annotation:
                    args.append(f"{arg.arg}: {ast.unparse(arg.annotation)}")
                else:
                    args.append(arg.arg)

        defaults = node.args.defaults
        if defaults:
            for i, default in enumerate(defaults):
                arg_idx = len(args) - len(defaults) + i
                if arg_idx >= 0:
                    default_val = ast.unparse(default)
                    args[arg_idx] = f"{args[arg_idx]} = {default_val}"

        returns = ""
        if node.returns:
            returns = f" -> {ast.unparse(node.returns)}"

        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        return f"{prefix}def {node.name}({', '.join(args)}){returns}"

    def _extract_classes(self, module: ast.Module) -> list[dict[str, Any]]:
        """Extract class information from module."""
        classes = []
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({
                            "name": item.name,
                            "signature": self._get_signature(item),
                            "docstring": self._get_docstring(item) or "",
                        })

                classes.append({
                    "name": node.name,
                    "docstring": self._get_docstring(node) or "",
                    "methods": methods,
                    "bases": [ast.unparse(base) for base in node.bases],
                })
        return classes

    def _extract_functions(self, module: ast.Module) -> list[dict[str, Any]]:
        """Extract top-level functions from module."""
        functions = []
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name": node.name,
                    "signature": self._get_signature(node),
                    "docstring": self._get_docstring(node) or "",
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
        return functions

    async def generate_api_docs(self) -> list[DocPage]:
        """Generate API documentation pages."""
        pages = []

        for py_file in self.source_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue

            module = self._parse_module(py_file)
            if module is None:
                continue

            content_parts = []
            docstring = self._get_docstring(module)
            if docstring:
                content_parts.append(f"## {py_file.stem}\n\n{docstring}\n")

            classes = self._extract_classes(module)
            for cls in classes:
                content_parts.append(f"\n### class {cls['name']}\n\n{cls['docstring']}\n")
                for method in cls["methods"]:
                    content_parts.append(f"\n#### {method['name']}\n\n```python\n{method['signature']}\n```\n\n{method['docstring']}\n")

            functions = self._extract_functions(module)
            for func in functions:
                content_parts.append(f"\n### {func['name']}\n\n```python\n{func['signature']}\n```\n\n{func['docstring']}\n")

            pages.append(DocPage(
                title=py_file.stem.replace("_", " ").title(),
                category="api-reference",
                filename=f"{py_file.stem}.md",
                content="\n".join(content_parts),
            ))

        return pages

    async def generate_skill_guide(self) -> DocPage:
        """Generate skill development guide."""
        content = """# Skill Development Guide

This guide explains how to create Kazma skills using YAML manifests.

## Quick Start

Create a `skill_manifest.yaml` file in your skill directory:

```yaml
name: my-skill
version: 1.0.0
author: Your Name
description: What your skill does
mcp_servers:
  - name: my-server
    command: my-server-executable
    args: ["--option"]
```

## Core Concepts

- **Skill**: A YAML manifest that wraps MCP tools
- **Manifest**: Defines the skill interface and configuration
- **MCP Server**: The actual tool executor

## Directory Structure

```
my-skill/
├── skill_manifest.yaml
└── kazma_skill/
    ├── __init__.py
    └── skill.py
```

## Testing

Run `kazma wizard` to install and test your skill.
"""
        return DocPage(
            title="Skill Development Guide",
            category="skill-development",
            filename="skill-guide.md",
            content=content,
        )

    async def generate_cli_reference(self) -> DocPage:
        """Generate CLI reference documentation."""
        content = """# CLI Reference

## Commands

### kazma hub

Manage skills from the Kazma Hub.

```bash
kazma hub register <path>   # Register a skill
kazma hub list              # List available skills
kazma hub install <skill_id>  # Install a skill
kazma hub info <skill_id>   # Show skill details
```

### kazma wizard

Interactive skill installation wizard.

```bash
kazma wizard               # Run the wizard
kazma wizard --non-interactive  # Auto-select first skill
```

### kazma-tui

Launch the terminal UI.

```bash
kazma-tui
```

### kazma-web

Launch the web UI.

```bash
kazma-web
```
"""
        return DocPage(
            title="CLI Reference",
            category="cli",
            filename="cli-reference.md",
            content=content,
        )

    async def generate_security_docs(self) -> DocPage:
        """Generate security documentation."""
        content = """# Security Overview

Kazma implements multiple layers of security:

## Sandboxing

Skills run in isolated sandboxes with restricted permissions.

## Permissions

File access requires explicit permission grants.

## Supported Permissions

- `file_read` - Read files from allowed directories
- `file_write` - Write files to allowed directories
- `network` - Make HTTP requests
- `mcp` - Execute MCP tools
"""
        return DocPage(
            title="Security Overview",
            category="security",
            filename="security-overview.md",
            content=content,
        )

    async def build_site(self, output_dir: Path | None = None) -> None:
        """Build complete documentation site."""
        if output_dir is None:
            output_dir = Path("docs/docs")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate and write all pages
        for page in await self.generate_api_docs():
            page_dir = output_dir / page.category
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / page.filename).write_text(page.render())

        (output_dir / "skill-development" / "skill-guide.md").write_text(
            (await self.generate_skill_guide()).render()
        )
        (output_dir / "api-reference" / "cli-reference.md").write_text(
            (await self.generate_cli_reference()).render()
        )
        (output_dir / "security" / "security-overview.md").write_text(
            (await self.generate_security_docs()).render()
        )
