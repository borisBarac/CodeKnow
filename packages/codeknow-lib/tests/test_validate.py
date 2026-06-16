from __future__ import annotations

from codeknow.validate import validate_extraction


def test_validate_extraction_handles_null_nodes() -> None:
    errors = validate_extraction(
        {
            "nodes": None,
            "edges": [
                {
                    "source": "missing",
                    "target": "missing",
                    "relation": "calls",
                    "confidence": "EXTRACTED",
                    "source_file": "main.py",
                }
            ],
        }
    )

    assert "'nodes' must be a list" in errors
    assert any("source 'missing'" in error for error in errors)


def test_validate_extraction_rejects_bools() -> None:
    errors = validate_extraction(
        {
            "nodes": [
                {
                    "id": "n1",
                    "label": "Node",
                    "file_type": "code",
                    "source_file": "main.py",
                    "community": True,
                    "end_line": True,
                }
            ],
            "edges": [
                {
                    "source": "n1",
                    "target": "n1",
                    "relation": "calls",
                    "confidence": "EXTRACTED",
                    "source_file": "main.py",
                    "confidence_score": True,
                }
            ],
        }
    )

    assert any("community" in error for error in errors)
    assert any("end_line" in error for error in errors)
    assert any("confidence_score" in error for error in errors)
