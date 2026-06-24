"""
tests/test_health.py

Basic test for the /health endpoint.

Run with:
    pytest tests/test_health.py -v

This is the simplest possible test — it confirms the app starts,
the database initializes, and the /health endpoint returns a 200 response.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_200():
    """Health endpoint should return HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


def test_health_response_structure():
    """Health endpoint should return expected JSON fields."""
    response = client.get("/health")
    data = response.json()

    assert data["status"] == "ok", f"Expected status 'ok', got {data['status']}"
    assert data["app"] == "Nifty Pre-Market Briefing"
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data


def test_root_returns_200():
    """Root endpoint should also return HTTP 200."""
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()
