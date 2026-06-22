"""Tests for Search Benchmark.

Comprehensive tests for the SearchBenchmark including
benchmarking performance and report generation.
"""

import time

import pytest
from kazma_memory.benchmark import (
    BenchmarkReport,
    BenchmarkResult,
    SearchBenchmark,
)


@pytest.fixture
def benchmark():
    """Create a SearchBenchmark instance."""
    return SearchBenchmark()


class TestBenchmarkResult:
    """Test BenchmarkResult dataclass."""

    def test_benchmark_result_creation(self):
        """Test creating a BenchmarkResult."""
        result = BenchmarkResult(
            test_name="indexing",
            size=1000,
            duration_seconds=1.5,
            operations_per_second=666.67,
            avg_latency_ms=0.5,
            p95_latency_ms=0.8,
            p99_latency_ms=0.95,
            min_latency_ms=0.1,
            max_latency_ms=1.2,
            backend="sqlite",
            metadata={"key": "value"},
        )

        assert result.test_name == "indexing"
        assert result.size == 1000
        assert result.duration_seconds == 1.5
        assert result.operations_per_second == 666.67
        assert result.avg_latency_ms == 0.5
        assert result.p95_latency_ms == 0.8
        assert result.p99_latency_ms == 0.95
        assert result.min_latency_ms == 0.1
        assert result.max_latency_ms == 1.2
        assert result.backend == "sqlite"
        assert result.metadata == {"key": "value"}

    def test_benchmark_result_defaults(self):
        """Test BenchmarkResult with default values."""
        result = BenchmarkResult(
            test_name="test",
            size=100,
            duration_seconds=1.0,
            operations_per_second=100.0,
            avg_latency_ms=1.0,
            p95_latency_ms=1.5,
            p99_latency_ms=2.0,
            min_latency_ms=0.5,
            max_latency_ms=3.0,
        )

        assert result.backend == "sqlite"
        assert result.metadata == {}


class TestBenchmarkReport:
    """Test BenchmarkReport dataclass."""

    def test_benchmark_report_creation(self):
        """Test creating a BenchmarkReport."""
        report = BenchmarkReport(
            timestamp="2024-01-01T00:00:00",
            results=[],
            sqlite_results=[],
            tantivy_results=[],
            comparison={},
        )

        assert report.timestamp == "2024-01-01T00:00:00"
        assert report.results == []
        assert report.sqlite_results == []
        assert report.tantivy_results == []
        assert report.comparison == {}

    def test_benchmark_report_with_results(self):
        """Test BenchmarkReport with results."""
        results = [
            BenchmarkResult(
                test_name="test",
                size=100,
                duration_seconds=1.0,
                operations_per_second=100.0,
                avg_latency_ms=1.0,
                p95_latency_ms=1.5,
                p99_latency_ms=2.0,
                min_latency_ms=0.5,
                max_latency_ms=3.0,
            )
        ]

        report = BenchmarkReport(
            timestamp="2024-01-01T00:00:00",
            results=results,
        )

        assert len(report.results) == 1


class TestSearchBenchmark:
    """Test suite for SearchBenchmark."""

    def test_init(self, benchmark):
        """Test benchmark initialization."""
        assert benchmark is not None
        assert benchmark.results == []
        assert len(benchmark._arabic_sample_queries) > 0
        assert len(benchmark._english_sample_queries) > 0

    def test_generate_documents(self, benchmark):
        """Test document generation."""
        documents = benchmark._generate_documents(10)

        assert len(documents) == 10
        assert all(isinstance(doc, dict) for doc in documents)
        assert all("id" in doc for doc in documents)
        assert all("content" in doc for doc in documents)

    def test_generate_arabic_documents(self, benchmark):
        """Test Arabic document generation."""
        documents = benchmark._generate_arabic_documents(10)

        assert len(documents) == 10
        assert all(isinstance(doc, str) for doc in documents)
        # Should contain Arabic characters
        assert any("\u0600" <= char <= "\u06ff" for doc in documents for char in doc)

    @pytest.mark.asyncio
    async def test_benchmark_indexing(self, benchmark):
        """Test indexing benchmark."""
        result = await benchmark.benchmark_indexing(100)

        assert isinstance(result, BenchmarkResult)
        assert result.test_name == "indexing"
        assert result.size == 100
        assert result.duration_seconds >= 0
        assert result.operations_per_second >= 0

    @pytest.mark.asyncio
    async def test_benchmark_search(self, benchmark):
        """Test search benchmark."""
        result = await benchmark.benchmark_search(100, queries=10)

        assert isinstance(result, BenchmarkResult)
        assert result.test_name == "search"
        assert result.size == 100
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_benchmark_arabic_search(self, benchmark):
        """Test Arabic search benchmark."""
        result = await benchmark.benchmark_arabic_search(100)

        assert isinstance(result, BenchmarkResult)
        assert result.test_name == "arabic_search"
        assert result.size == 100

    @pytest.mark.asyncio
    async def test_run_full_benchmark(self, benchmark):
        """Test full benchmark suite."""
        report = await benchmark.run_full_benchmark()

        assert isinstance(report, BenchmarkReport)
        assert len(report.results) > 0
        assert report.timestamp

    def test_generate_report(self, benchmark):
        """Test report generation."""
        # Add some results first
        benchmark.results.append(
            BenchmarkResult(
                test_name="test",
                size=100,
                duration_seconds=1.0,
                operations_per_second=100.0,
                avg_latency_ms=1.0,
                p95_latency_ms=1.5,
                p99_latency_ms=2.0,
                min_latency_ms=0.5,
                max_latency_ms=3.0,
            )
        )
        report = benchmark.generate_report()

        assert isinstance(report, str)
        assert "KAZMA SEARCH BENCHMARK REPORT" in report
        assert "=" * 80 in report

    def test_generate_report_empty(self):
        """Test report generation with no results."""
        benchmark = SearchBenchmark()
        report = benchmark.generate_report()

        assert "No benchmark results available" in report

    def test_generate_comparison(self, benchmark):
        """Test comparison generation."""
        comparison = benchmark._generate_comparison()

        assert isinstance(comparison, dict)
        assert "sqlite_avg_latency" in comparison
        assert "tantivy_avg_latency" in comparison
        assert "improvement_factor" in comparison


class TestSearchBenchmarkEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_benchmark_indexing_zero_size(self, benchmark):
        """Test indexing benchmark with zero size."""
        result = await benchmark.benchmark_indexing(0)

        assert isinstance(result, BenchmarkResult)
        assert result.size == 0

    @pytest.mark.asyncio
    async def test_benchmark_search_zero_queries(self, benchmark):
        """Test search benchmark with zero queries."""
        result = await benchmark.benchmark_search(100, queries=0)

        assert isinstance(result, BenchmarkResult)

    @pytest.mark.asyncio
    async def test_benchmark_indexing_large_size(self, benchmark):
        """Test indexing benchmark with large size."""
        # Test with a reasonable large size (not too large for tests)
        result = await benchmark.benchmark_indexing(1000)

        assert isinstance(result, BenchmarkResult)
        assert result.size == 1000


class TestSearchBenchmarkPerformance:
    """Test benchmark performance characteristics."""

    def test_benchmark_initialization_speed(self):
        """Test that benchmark initialization is fast."""
        start = time.time()
        for _ in range(100):
            SearchBenchmark()
        duration = time.time() - start

        # Should create 100 benchmarks in under 1 second
        assert duration < 1.0

    def test_generate_documents_speed(self, benchmark):
        """Test document generation speed."""
        start = time.time()
        benchmark._generate_documents(1000)
        duration = time.time() - start

        # Should generate 1000 documents in under 1 second
        assert duration < 1.0

    def test_generate_arabic_documents_speed(self, benchmark):
        """Test Arabic document generation speed."""
        start = time.time()
        benchmark._generate_arabic_documents(1000)
        duration = time.time() - start

        # Should generate 1000 documents in under 1 second
        assert duration < 1.0

    def test_generate_report_speed(self, benchmark):
        """Test report generation speed."""
        # Add some results
        for i in range(10):
            benchmark.results.append(
                BenchmarkResult(
                    test_name=f"test_{i}",
                    size=100 * i,
                    duration_seconds=1.0,
                    operations_per_second=100.0,
                    avg_latency_ms=1.0,
                    p95_latency_ms=1.5,
                    p99_latency_ms=2.0,
                    min_latency_ms=0.5,
                    max_latency_ms=3.0,
                )
            )

        start = time.time()
        for _ in range(100):
            benchmark.generate_report()
        duration = time.time() - start

        # Should generate 100 reports in under 1 second
        assert duration < 1.0
