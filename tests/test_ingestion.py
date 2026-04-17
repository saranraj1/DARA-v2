"""Unit tests: ingestion pipeline"""
import pytest, asyncio, sys
sys.path.insert(0, ".")
from ingestion.normalizer import ErrorNormalizer
from ingestion.classifier import ErrorClassifier

@pytest.fixture
def normalizer(): return ErrorNormalizer()
@pytest.fixture
def classifier(): return ErrorClassifier()

class TestNormalizer:
    def test_github_actions_source(self, normalizer):
        result = normalizer.normalize(
            {"error": "AttributeError: None has no attr get", "run_id": "123", "service": "api"},
            source="github_actions")
        assert isinstance(result, dict)
        assert "error_class" in result

    def test_direct_source(self, normalizer):
        result = normalizer.normalize(
            {"error_class": "KeyError", "message": "key not found", "service": "worker"},
            source="direct")
        assert isinstance(result, dict)

    def test_opentelemetry_source(self, normalizer):
        result = normalizer.normalize(
            {"exception.type": "ValueError", "exception.message": "bad input"}, source="opentelemetry")
        assert isinstance(result, dict)

    def test_returns_dict(self, normalizer):
        result = normalizer.normalize({"error_class": "RuntimeError", "message": "fail"}, source="direct")
        assert isinstance(result, dict)

class TestClassifier:
    @pytest.mark.asyncio
    async def test_classify_attribute_error(self, classifier):
        result = await classifier.classify(
            {"error_class": "AttributeError", "message": "NoneType has no attr get",
             "severity": "high", "service": "api", "stack_trace": ""})
        assert isinstance(result, dict) and 'error_class' in result

    @pytest.mark.asyncio
    async def test_classify_import_error(self, classifier):
        result = await classifier.classify(
            {"error_class": "ImportError", "message": "No module named requests",
             "severity": "medium", "service": "api", "stack_trace": ""})
        assert isinstance(result, dict) and 'error_class' in result

    @pytest.mark.asyncio
    async def test_classify_unknown(self, classifier):
        result = await classifier.classify(
            {"error_class": "UnknownError999", "message": "weird",
             "severity": "low", "service": "api", "stack_trace": ""})
        assert isinstance(result, dict) and 'error_class' in result

    @pytest.mark.asyncio
    async def test_classify_is_deterministic(self, classifier):
        error = {"error_class": "AttributeError", "message": "NoneType",
                 "severity": "high", "service": "api", "stack_trace": ""}
        r1 = await classifier.classify(error)
        r2 = await classifier.classify(error)
        assert r1 == r2
