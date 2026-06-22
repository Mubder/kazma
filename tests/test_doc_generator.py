"""Tests for the Documentation Generator."""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_core.docs import DocPage, DocumentationGenerator

# ── DocPage ────────────────────────────────────────────────────────────────────


class TestDocPage:
    """Tests for DocPage data class."""

    def test_render_with_frontmatter(self):
        page = DocPage(
            title="Test Page",
            category="test",
            filename="test.md",
            content="# Hello\n\nContent here.",
            frontmatter={"sidebar_position": 1, "title": "Test Page"},
        )
        rendered = page.render()
        assert "---" in rendered
        assert "sidebar_position: 1" in rendered
        assert "title: Test Page" in rendered
        assert "# Hello" in rendered
        assert "Content here." in rendered

    def test_render_empty_frontmatter(self):
        page = DocPage(
            title="Empty",
            category="test",
            filename="empty.md",
            content="Just content.",
        )
        rendered = page.render()
        assert "---" in rendered
        assert "Just content." in rendered

    def test_render_multiline_content(self):
        page = DocPage(
            title="Multi",
            category="test",
            filename="multi.md",
            content="Line 1\nLine 2\nLine 3",
        )
        rendered = page.render()
        assert "Line 1" in rendered
        assert "Line 2" in rendered
        assert "Line 3" in rendered


# ── DocumentationGenerator init ────────────────────────────────────────────────


class TestGeneratorInit:
    """Tests for generator initialization."""

    def test_default_init(self):
        gen = DocumentationGenerator()
        assert gen.source_dir == Path("kazma-core/kazma_core")

    def test_custom_source_dir(self):
        gen = DocumentationGenerator(source_dir="/tmp/src")
        assert gen.source_dir == Path("/tmp/src")


# ── Module parsing ─────────────────────────────────────────────────────────────


class TestModuleParsing:
    """Tests for Python module parsing."""

    def test_parse_valid_module(self, tmp_path):
        py_file = tmp_path / "test_mod.py"
        py_file.write_text('"""Test module."""\n\ndef hello():\n    pass\n')

        gen = DocumentationGenerator(source_dir=str(tmp_path))
        module = gen._parse_module(py_file)
        assert module is not None

    def test_parse_invalid_module(self, tmp_path):
        py_file = tmp_path / "bad.py"
        py_file.write_text("def broken(\n")  # Syntax error

        gen = DocumentationGenerator(source_dir=str(tmp_path))
        module = gen._parse_module(py_file)
        assert module is None


# ── Docstring extraction ───────────────────────────────────────────────────────


class TestDocstringExtraction:
    """Tests for docstring extraction."""

    def test_get_docstring_with_docstring(self):
        import ast

        gen = DocumentationGenerator()
        module = ast.parse('"""Module docstring."""')
        result = gen._get_docstring(module)
        assert result == "Module docstring."

    def test_get_docstring_without_docstring(self):
        import ast

        gen = DocumentationGenerator()
        module = ast.parse("x = 1")
        result = gen._get_docstring(module)
        assert result is None


# ── Signature extraction ───────────────────────────────────────────────────────


class TestSignatureExtraction:
    """Tests for function signature extraction."""

    def test_simple_function(self):
        import ast

        gen = DocumentationGenerator()
        source = "def hello():\n    pass"
        module = ast.parse(source)
        func = module.body[0]
        sig = gen._get_signature(func)
        assert sig == "def hello()"

    def test_function_with_args(self):
        import ast

        gen = DocumentationGenerator()
        source = "def process(msg: str, count: int = 5):\n    pass"
        module = ast.parse(source)
        func = module.body[0]
        sig = gen._get_signature(func)
        assert "msg" in sig
        assert "count" in sig

    def test_async_function(self):
        import ast

        gen = DocumentationGenerator()
        source = "async def fetch():\n    pass"
        module = ast.parse(source)
        func = module.body[0]
        sig = gen._get_signature(func)
        assert "async def fetch()" in sig

    def test_function_with_return_type(self):
        import ast

        gen = DocumentationGenerator()
        source = "def compute() -> int:\n    return 1"
        module = ast.parse(source)
        func = module.body[0]
        sig = gen._get_signature(func)
        assert "-> int" in sig

    def test_function_skip_self(self):
        import ast

        gen = DocumentationGenerator()
        source = "class A:\n    def method(self, x: int):\n        pass"
        module = ast.parse(source)
        func = module.body[0].body[0]  # A.method
        sig = gen._get_signature(func)
        assert "self" not in sig
        assert "x" in sig


# ── Class extraction ───────────────────────────────────────────────────────────


class TestClassExtraction:
    """Tests for class extraction."""

    def test_extract_simple_class(self):
        import ast

        gen = DocumentationGenerator()
        source = '''
class MySkill:
    """A test skill."""

    def __init__(self):
        pass

    async def execute(self, context):
        """Run the skill."""
        return {}
'''
        module = ast.parse(source)
        classes = gen._extract_classes(module)
        assert len(classes) == 1
        assert classes[0]["name"] == "MySkill"
        assert classes[0]["docstring"] == "A test skill."
        assert len(classes[0]["methods"]) == 2

    def test_extract_class_with_bases(self):
        import ast

        gen = DocumentationGenerator()
        source = "class Child(Parent, Mixin):\n    pass"
        module = ast.parse(source)
        classes = gen._extract_classes(module)
        assert classes[0]["bases"] == ["Parent", "Mixin"]

    def test_extract_no_classes(self):
        import ast

        gen = DocumentationGenerator()
        module = ast.parse("x = 1")
        classes = gen._extract_classes(module)
        assert classes == []


# ── Function extraction ────────────────────────────────────────────────────────


class TestFunctionExtraction:
    """Tests for top-level function extraction."""

    def test_extract_functions(self):
        import ast

        gen = DocumentationGenerator()
        source = '''
def hello():
    """Say hello."""
    pass

async def fetch():
    """Fetch data."""
    pass
'''
        module = ast.parse(source)
        funcs = gen._extract_functions(module)
        assert len(funcs) == 2
        assert funcs[0]["name"] == "hello"
        assert funcs[0]["docstring"] == "Say hello."
        assert funcs[1]["is_async"] is True

    def test_extract_no_functions(self):
        import ast

        gen = DocumentationGenerator()
        module = ast.parse("class A: pass")
        funcs = gen._extract_functions(module)
        assert funcs == []


# ── Generate API docs ──────────────────────────────────────────────────────────


class TestGenerateApiDocs:
    """Tests for generate_api_docs."""

    @pytest.mark.asyncio
    async def test_generate_api_docs(self, tmp_path):
        # Create a sample module
        mod_dir = tmp_path / "mymod"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "utils.py").write_text(
            '"""Utility functions."""\n\n'
            'def greet(name: str) -> str:\n'
            '    """Greet a user."""\n'
            '    return f"Hello {name}"\n'
        )

        gen = DocumentationGenerator(source_dir=str(tmp_path))
        pages = await gen.generate_api_docs()
        assert len(pages) >= 1

        # Check that the page has content
        page = pages[0]
        assert page.title  # has a title
        assert "greet" in page.content

    @pytest.mark.asyncio
    async def test_generate_api_docs_empty_dir(self, tmp_path):
        gen = DocumentationGenerator(source_dir=str(tmp_path))
        pages = await gen.generate_api_docs()
        assert pages == []


# ── Generate skill guide ──────────────────────────────────────────────────────


class TestGenerateSkillGuide:
    """Tests for generate_skill_guide."""

    @pytest.mark.asyncio
    async def test_generate_skill_guide(self):
        gen = DocumentationGenerator()
        page = await gen.generate_skill_guide()
        assert page.title == "Skill Development Guide"
        assert page.category == "skill-development"
        assert "Skill Development" in page.content
        assert "skill_manifest.yaml" in page.content


# ── Generate CLI reference ────────────────────────────────────────────────────


class TestGenerateCliReference:
    """Tests for generate_cli_reference."""

    @pytest.mark.asyncio
    async def test_generate_cli_reference(self):
        gen = DocumentationGenerator()
        page = await gen.generate_cli_reference()
        assert page.title == "CLI Reference"
        assert "kazma hub" in page.content
        assert "kazma wizard" in page.content


# ── Generate security docs ────────────────────────────────────────────────────


class TestGenerateSecurityDocs:
    """Tests for generate_security_docs."""

    @pytest.mark.asyncio
    async def test_generate_security_docs(self):
        gen = DocumentationGenerator()
        page = await gen.generate_security_docs()
        assert page.title == "Security Overview"
        assert "Sandboxing" in page.content
        assert "Permissions" in page.content
        assert "file_read" in page.content


# ── Build site ─────────────────────────────────────────────────────────────────


class TestBuildSite:
    """Tests for build_site."""

    @pytest.mark.asyncio
    async def test_build_site(self, tmp_path):
        # Create a minimal source module
        src_dir = tmp_path / "kazma_core"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("")
        (src_dir / "sample.py").write_text(
            '"""Sample module."""\n\n'
            'class SampleSkill:\n'
            '    """A sample skill."""\n\n'
            '    def run(self):\n'
            '        """Run the skill."""\n'
            '        pass\n'
        )

        gen = DocumentationGenerator(source_dir=str(src_dir))
        await gen.build_site()

        # Check that docs were generated
        docs_dir = tmp_path / "docs" / "docs"
        assert docs_dir.exists()

        api_dir = docs_dir / "api-reference"
        assert api_dir.exists()
        assert any(api_dir.glob("*.md"))

        # Check skill guide
        skill_dir = docs_dir / "skill-development"
        assert skill_dir.exists()

        # Check CLI reference
        api_ref_dir = docs_dir / "api-reference"
        assert any(api_ref_dir.glob("cli-reference.md"))

        # Check security docs
        security_dir = docs_dir / "security"
        assert security_dir.exists()
