"""Provide a test-only API key before importing the server."""

import os

os.environ.setdefault("MCP_API_KEY", "test-only-key-never-use-in-production")
