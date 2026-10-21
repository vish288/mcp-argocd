"""MCP server for the Argo CD API."""

import asyncio
import hmac
import logging
import os

import click
from dotenv import load_dotenv
from fastmcp.server.auth import TokenVerifier
from mcp.server.auth.provider import AccessToken

# Hosts for which an HTTP transport needs no bearer token: the loopback
# interface is only reachable from the same machine.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class _BearerTokenVerifier(TokenVerifier):
    """Accept exactly one shared bearer token, compared in constant time.

    StaticTokenVerifier resolves tokens with a dict ``.get``, which is not
    constant-time. For a single env-sourced secret, ``hmac.compare_digest`` avoids
    leaking how long a matching prefix the presented token has through response
    timing. Compare encoded bytes: ``compare_digest`` raises ``TypeError`` on a
    ``str`` containing non-ASCII, so a non-ASCII bearer would otherwise 500 instead
    of being rejected.
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token.encode(), self._token):
            return AccessToken(token=token, client_id="mcp-argocd", scopes=[])
        return None


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="MCP transport type (sse is deprecated; use streamable-http)",
)
@click.option("--port", default=8000, help="Port for HTTP transports")
@click.option("--host", default="127.0.0.1", help="Host for HTTP transports")
@click.option("--argocd-url", envvar="ARGOCD_URL", help="Argo CD base URL")
@click.option("--argocd-token", hidden=True, help="Deprecated: set ARGOCD_TOKEN instead")
@click.option("--read-only", is_flag=True, help="Disable write operations")
@click.option("--insecure", is_flag=True, help="Skip TLS verification (ARGOCD_SSL_VERIFY=false)")
@click.pass_context
def main(
    ctx: click.Context,
    transport: str,
    port: int,
    host: str,
    argocd_url: str | None,
    argocd_token: str | None,
    read_only: bool,
    insecure: bool,
) -> None:
    """Run the Argo CD MCP server."""
    load_dotenv()

    if argocd_url:
        os.environ["ARGOCD_URL"] = argocd_url
    if argocd_token:
        click.echo(
            "Warning: --argocd-token is deprecated. It puts the token in the process argument "
            "list, where `ps` and shell history can read it. Set ARGOCD_TOKEN instead.",
            err=True,
        )
        os.environ["ARGOCD_TOKEN"] = argocd_token
    if read_only:
        os.environ["ARGOCD_READ_ONLY"] = "true"
    if insecure:
        os.environ["ARGOCD_SSL_VERIFY"] = "false"

    # Validate configuration before the server starts its handshake (D7): a bad
    # config is one clear line and exit 2, not a 55-line traceback from inside the
    # FastMCP lifespan. The lifespan keeps its own validate() for the in-process
    # test path.
    from .config import ArgoCDConfig

    try:
        ArgoCDConfig.from_env().validate()
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(2)

    if transport == "sse":
        click.echo(
            "Warning: --transport sse uses the HTTP+SSE transport, deprecated in MCP 2026-07-28. "
            "Use --transport streamable-http.",
            err=True,
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s | %(message)s",
    )

    from .servers import prompts, resources  # noqa: F401 — registers decorators
    from .servers.argocd import mcp

    bearer_token = os.getenv("ARGOCD_HTTP_BEARER_TOKEN", "").strip()

    run_kwargs: dict = {"transport": transport}
    if transport != "stdio":
        run_kwargs["host"] = host
        run_kwargs["port"] = port
        # DNS-rebinding protection: validate Host/Origin before a request reaches
        # the MCP endpoint. "auto" guards loopback-bound servers, allowing the
        # configured host plus localhost/127.0.0.1/[::1] and rejecting any other
        # Host (421) or cross-site Origin (403).
        run_kwargs["host_origin_protection"] = "auto"

        # Host/Origin protection is not authentication: an HTTP transport serves
        # every tool, with the owner's Argo CD token, to anyone who reaches the
        # port. Require a bearer token before binding anywhere but loopback.
        if bearer_token:
            if len(bearer_token) < 16:
                msg = (
                    "ARGOCD_HTTP_BEARER_TOKEN must be at least 16 characters; "
                    "generate one with: openssl rand -hex 32"
                )
                raise click.UsageError(msg)
            mcp.auth = _BearerTokenVerifier(bearer_token)
        elif host not in _LOOPBACK_HOSTS:
            msg = (
                f"--host {host} would expose the Argo CD API token to the network. "
                "Set ARGOCD_HTTP_BEARER_TOKEN (clients send it as "
                "'Authorization: Bearer <token>') or bind to 127.0.0.1."
            )
            raise click.UsageError(msg)

    asyncio.run(mcp.run_async(show_banner=False, **run_kwargs))


if __name__ == "__main__":
    main()
