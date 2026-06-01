from __future__ import annotations

import argparse
import logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-pilot",
        description="Run the Paper Pilot MCP server, or try the zero-config demo.",
    )
    parser.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--path", default="/mcp")

    sub = parser.add_subparsers(dest="command")
    demo_p = sub.add_parser(
        "demo",
        help="Research a topic end-to-end and open an interactive citation graph (no MCP client needed).",
    )
    demo_p.add_argument("topic", help='Research topic, e.g. "retrieval augmented generation".')
    demo_p.add_argument("--limit-per-source", type=int, default=4)
    demo_p.add_argument("--download-top-n", type=int, default=2)
    demo_p.add_argument("--no-browser", action="store_true", help="Do not open the graph in a browser.")
    return parser


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    args = _build_parser().parse_args()

    if args.command == "demo":
        from paper_pilot.demo import run_demo

        run_demo(
            args.topic,
            limit_per_source=args.limit_per_source,
            download_top_n=args.download_top_n,
            open_browser=not args.no_browser,
        )
        return

    # Default: run the MCP server.
    from paper_pilot.server import mcp

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    # FastMCP.run() only accepts (transport, mount_path); network options must be
    # set on mcp.settings beforehand, or the run call raises TypeError.
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    if args.transport == "streamable-http":
        mcp.settings.streamable_http_path = args.path
        mcp.settings.json_response = True
    else:  # sse
        mcp.settings.sse_path = args.path
    mcp.run(transport=args.transport)
