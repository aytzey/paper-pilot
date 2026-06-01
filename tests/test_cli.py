from paper_pilot.cli import _build_parser


def test_parser_defaults_to_serve() -> None:
    args = _build_parser().parse_args([])
    assert args.command is None
    assert args.transport == "stdio"


def test_parser_accepts_http_transport_flags() -> None:
    args = _build_parser().parse_args(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9001"])
    assert args.command is None
    assert args.transport == "streamable-http"
    assert args.host == "0.0.0.0"
    assert args.port == 9001


def test_parser_parses_demo_subcommand() -> None:
    args = _build_parser().parse_args(["demo", "retrieval augmented generation", "--no-browser"])
    assert args.command == "demo"
    assert args.topic == "retrieval augmented generation"
    assert args.no_browser is True


def test_mcp_settings_accept_network_options() -> None:
    # Regression for the transport crash: FastMCP.run() rejects host/port kwargs,
    # so the CLI must set them on mcp.settings instead. Verify they are settable.
    from paper_pilot.server import mcp

    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = 8123
    mcp.settings.streamable_http_path = "/mcp"
    mcp.settings.sse_path = "/sse"
    mcp.settings.json_response = True
    assert mcp.settings.port == 8123
