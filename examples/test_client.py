"""Small MCP client that lists the server tools and renders a sample bar chart."""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(server_url: str, api_key: str, output: Path) -> None:
    async with streamablehttp_client(
        server_url,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Tools:", ", ".join(tool.name for tool in tools.tools))

            result = await session.call_tool(
                "create_chart",
                {
                    "config": {
                        "axes": [
                            {
                                "calls": [
                                    {
                                        "method": "bar",
                                        "args": [
                                            {"$data": "category"},
                                            {"$data": "sales"},
                                        ],
                                        "kwargs": {"color": "steelblue"},
                                    }
                                ]
                            }
                        ]
                    },
                    "data": {
                        "format": "columns",
                        "values": {
                            "category": ["A", "B", "C"],
                            "sales": [12, 18, 9],
                        },
                    },
                },
            )

            if result.isError:
                messages = [getattr(block, "text", "tool call failed") for block in result.content]
                raise RuntimeError("; ".join(messages))

            image = next((block for block in result.content if block.type == "image"), None)
            if image is None or image.mimeType != "image/png":
                raise RuntimeError("create_chart did not return a PNG image")

            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(base64.b64decode(image.data))
            print(f"Saved PNG preview to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp"),
        help="MCP server endpoint (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/chart-preview.png"),
        help="where to save the returned PNG (default: %(default)s)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("MCP_API_KEY")
    if not api_key:
        parser.error("set MCP_API_KEY to the same API key used by the server")

    asyncio.run(run(args.url, api_key, args.output))


if __name__ == "__main__":
    main()
