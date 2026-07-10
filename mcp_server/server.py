"""
MCP server exposing 3 financial tools over the Model Context Protocol.

Run directly for stdio transport (e.g. add to Claude Desktop's claude_desktop_config.json):
    python mcp_server/server.py

Or run over HTTP (streamable-http transport) so a hosted deployment can serve it:
    python mcp_server/server.py --http
"""
import sys

from mcp.server.fastmcp import FastMCP

from tools import find_anomalies, monthly_flux_analysis, query_financials

mcp = FastMCP("aircfo-financial-context")


@mcp.tool()
def query_financials_tool(question: str) -> dict:
    """Answer a natural-language question about the startup's financials
    (transactions, invoices, P&L, vendor contracts), citing the source row
    for every claim in the answer."""
    return query_financials(question)


@mcp.tool()
def monthly_flux_analysis_tool(month: str) -> dict:
    """Explain month-over-month operating expense changes for a given month
    (format: 'March 2026'), citing the specific transactions responsible."""
    return monthly_flux_analysis(month)


@mcp.tool()
def find_anomalies_tool() -> dict:
    """Scan the invoice ledger for duplicate payments and overdue unpaid invoices."""
    return find_anomalies()


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
