
#!/bin/sh
set -eu

: "${MCP_API_KEY:?Set MCP_API_KEY to a secret of at least 16 characters}"
exec matplotlib-mcp
