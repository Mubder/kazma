"""Kazma — auto-generates API documentation from source code."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DocPage:
    """A generated documentation page."""

    title: str
    category: str
    filename: str
    content: str
    frontmatter: dict[str, str | int] = field(default_factory=dict)

    def render(self) -> str:
        """Render the page with YAML frontmatter."""
        lines = ["---"]
        for key, val in self.frontmatter.items():
            lines.append(f"{key}: {val}")
        lines.append("---")
        lines.append("")
        lines.append(self.content)
        return "\n".join(lines)


class DocumentationGenerator:
    """Auto-generates API documentation from source code.

    Parses Python source files, extracts docstrings and signatures,
    and produces Markdown documentation pages for Docusaurus.
    """

    def __init__(self, source_dir: str = "kazma-core/kazma_core") -> None:
        self.source_dir = Path(source_dir)
        self._module_cache: dict[str, ast.Module] = {}

    def _parse_module(self, path: Path) -> ast.Module | None:
        """Parse a Python file into an AST module."""
        try:
            source = path.read_text(encoding="utf-8")
            return ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            return None

    def _get_docstring(
        self, node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str | None:
        """Extract docstring from an AST node."""
        return ast.get_docstring(node)

    def _get_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Extract function signature as a string."""
        parts = []
        for arg in node.args.args:
            if arg.arg == "self" or arg.arg == "cls":
                continue
            annotation = ""
            if arg.annotation:
                if isinstance(arg.annotation, ast.Name):
                    annotation = f": {arg.annotation.id}"
                elif isinstance(arg.annotation, ast.Constant):
                    annotation = f": {arg.annotation.value}"
                elif isinstance(arg.annotation, ast.Subscript):
                    annotation = ": ..."
                elif isinstance(arg.annotation, ast.Attribute):
                    annotation = f": {ast.dump(arg.annotation)}"
            parts.append(f"{arg.arg}{annotation}")

        defaults = node.args.defaults
        if defaults:
            offset = len(parts) - len(defaults)
            for i, default in enumerate(defaults):
                if isinstance(default, ast.Constant):
                    parts[offset + i] += f" = {default.value!r}"
                elif isinstance(default, ast.Name):
                    parts[offset + i] += f" = {default.id}"

        ret = ""
        if node.returns:
            if isinstance(node.returns, ast.Name):
                ret = f" -> {node.returns.id}"
            elif isinstance(node.returns, ast.Constant):
                ret = f" -> {node.returns.value}"
            else:
                ret = " -> ..."

        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        return f"{prefix}def {node.name}({', '.join(parts)}){ret}"

    def _extract_classes(self, module: ast.Module) -> list[dict]:
        """Extract class information from a module."""
        classes = []
        for node in ast.iter_child_nodes(module):
            if isinstance(node, ast.ClassDef):
                docstring = self._get_docstring(node)
                methods = []
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({
                            "name": item.name,
                            "signature": self._get_signature(item),
                            "docstring": self._get_docstring(item),
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        })
                classes.append({
                    "name": node.name,
                    "docstring": docstring,
                    "methods": methods,
                    "bases": [
                        b.id if isinstance(b, ast.Name) else "..."
                        for b in node.bases
                    ],
                })
        return classes

    def _extract_functions(self, module: ast.Module) -> list[dict]:
        """Extract top-level function information from a module."""
        functions = []
        for node in ast.iter_child_nodes(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name": node.name,
                    "signature": self._get_signature(node),
                    "docstring": self._get_docstring(node),
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
        return functions

    async def generate_api_docs(self) -> list[DocPage]:
        """Generate API documentation from source code docstrings."""
        pages: list[DocPage] = []

        # Discover Python modules
        py_files = sorted(self.source_dir.glob("**/*.py"))
        for py_file in py_files:
            if py_file.name.startswith("_"):
                continue
            if "__pycache__" in str(py_file):
                continue

            module = self._parse_module(py_file)
            if module is None:
                continue

            module_doc = self._get_docstring(module)
            classes = self._extract_classes(module)
            functions = self._extract_functions(module)

            if not classes and not functions:
                continue

            # Build markdown content
            rel_path = py_file.relative_to(self.source_dir)
            module_name = str(rel_path).replace("/", ".").replace(".py", "")

            content_parts = [f"# {module_name}\n"]
            if module_doc:
                content_parts.append(f"{module_doc}\n")

            # Classes
            for cls in classes:
                content_parts.append(f"## {cls['name']}\n")
                if cls["docstring"]:
                    content_parts.append(f"{cls['docstring']}\n")
                if cls["bases"]:
                    content_parts.append(f"**Inherits:** {', '.join(cls['bases'])}\n")

                if cls["methods"]:
                    content_parts.append("### Methods\n")
                    for method in cls["methods"]:
                        content_parts.append(f"#### `{method['signature']}`\n")
                        if method["docstring"]:
                            # Take first line of docstring for summary
                            first_line = method["docstring"].split("\n")[0].strip()
                            content_parts.append(f"{first_line}\n")

            # Top-level functions
            if functions:
                content_parts.append("## Functions\n")
                for func in functions:
                    content_parts.append(f"#### `{func['signature']}`\n")
                    if func["docstring"]:
                        first_line = func["docstring"].split("\n")[0].strip()
                        content_parts.append(f"{first_line}\n")

            page = DocPage(
                title=module_name,
                category="api-reference",
                filename=f"{module_name}.md",
                content="\n".join(content_parts),
                frontmatter={
                    "sidebar_position": len(pages) + 10,
                    "title": module_name,
                },
            )
            pages.append(page)

        return pages

    async def generate_skill_guide(self) -> DocPage:
        """Generate skill development guide."""
        content = """# Skill Development Guide

## Overview

This guide covers creating, testing, and publishing skills for the Kazma Hub.

## Creating a Skill

1. Create a directory with your skill name
2. Add a `skill_manifest.yaml` with required metadata
3. Create a Python entry point class with an `execute` method
4. Write tests

## Skill Manifest

See the [Skill Manifest specification](../skill-development/skill-manifest) for the full format reference.

## Entry Point

Your skill must define a class with:

- `__init__(self, config=None)` — constructor
- `async execute(self, context)` — main execution method
- `async cleanup(self)` — optional cleanup

## Testing

```bash
kazma hub validate ./my-skill
pytest tests/
```

## Publishing

```bash
kazma hub register ./my-skill
kazma hub publish ./my-skill
```
"""
        return DocPage(
            title="Skill Development Guide",
            category="skill-development",
            filename="skill-development-guide.md",
            content=content,
            frontmatter={"sidebar_position": 1, "title": "Skill Development Guide"},
        )

    async def generate_cli_reference(self) -> DocPage:
        """Generate CLI command reference."""
        content = """# CLI Reference

## Core Commands

| Command | Description |
|---|---|
| `kazma status` | Show Kazma status |
| `kazma serve` | Start the agent server |
| `kazma wizard` | Start interactive skill installation wizard |

## Hub Commands

| Command | Description |
|---|---|
| `kazma hub search` | Search for skills |
| `kazma hub info` | View skill details |
| `kazma hub install` | Install a skill |
| `kazma hub uninstall` | Uninstall a skill |
| `kazma hub list` | List installed skills |
| `kazma hub register` | Register a skill locally |
| `kazma hub validate` | Validate a skill directory |
| `kazma hub publish` | Publish a skill to the hub |

## Docs Commands

| Command | Description |
|---|---|
| `kazma docs build` | Build documentation site |
| `kazma docs serve` | Serve documentation locally |
"""
        return DocPage(
            title="CLI Reference",
            category="api-reference",
            filename="cli-reference.md",
            content=content,
            frontmatter={"sidebar_position": 4, "title": "CLI Reference"},
        )

    async def generate_security_docs(self) -> DocPage:
        """Generate security documentation."""
        content = """# Security Overview

## Security Model

Kazma implements a multi-layered security model:

1. **Sandboxing** — All skill execution is sandboxed
2. **Permissions** — Explicit permission grants required
3. **Audit Trail** — All actions are logged
4. **Certification** — Skills undergo security review

## Permission Types

| Permission | Description |
|---|---|
| `file_read` | Read files on the system |
| `file_write` | Write files on the system |
| `network_outbound` | Make outbound HTTP requests |
| `network_inbound` | Accept inbound connections |
| `camera_access` | Access camera hardware |
| `mqtt_broker` | Connect to MQTT brokers |
| `database_read` | Read from databases |
| `database_write` | Write to databases |

## Security Auditing

Skills are automatically scanned for:

- Dangerous code patterns (eval, exec, os.system)
- Hardcoded secrets
- Suspicious imports
- Permission violations

## Certification Levels

- **Basic**: Manifest validation
- **Standard**: Security audit + documentation
- **Premium**: Full security review + performance benchmarks
"""
        return DocPage(
            title="Security Overview",
            category="security",
            filename="security-overview.md",
            content=content,
            frontmatter={"sidebar_position": 1, "title": "Security Overview"},
        )

    async def build_site(self) -> None:
        """Build Docusaurus site by generating all docs."""
        docs_dir = self.source_dir.parent / "docs" / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # Generate API docs
        api_pages = await self.generate_api_docs()
        api_dir = docs_dir / "api-reference"
        api_dir.mkdir(exist_ok=True)

        for page in api_pages:
            target = api_dir / page.filename
            target.write_text(page.render(), encoding="utf-8")

        # Generate guides
        guides = [
            await self.generate_skill_guide(),
            await self.generate_cli_reference(),
            await self.generate_security_docs(),
        ]

        for page in guides:
            target = docs_dir / page.category / page.filename
            target.parent.mkdir(exist_ok=True)
            target.write_text(page.render(), encoding="utf-8")

        print(f"Generated {len(api_pages) + len(guides)} documentation pages")

    async def deploy(self, target: str = "github-pages") -> None:
        """Deploy documentation site."""
        # Build first
        await self.build_site()

        import subprocess

        docs_dir = self.source_dir.parent / "docs"

        # npm install
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(docs_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"npm install failed: {result.stderr}")
            return

        # npm run build
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(docs_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Build failed: {result.stderr}")
            return

        print("Documentation built successfully. Deploy with: npm run deploy")
