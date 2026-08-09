"""ChangeGuard CI/CD Gate — run the agent from a terminal or CI pipeline.

This is a thin wrapper around the same ``ChangeGuardAgent`` and
``create_live_adapter`` used by the Streamlit app (see ``app.py``'s
``execute_agent()``). It does not reimplement risk scoring, lineage
fetching, schema validation, or the ALLOW/BLOCK decision — it only:

1. Parses CLI arguments into a ``Change``
2. Builds the same live MCP adapter (or none, for demo mode)
3. Runs ``ChangeGuardAgent.run_async`` with ``confirm_writeback=False``
   (this CLI never writes back to DataHub; it is meant to be safe to run
   unattended as a CI gate)
4. Translates the agent's own decision into a process exit code

Exit codes:
    0 = ALLOW
    1 = BLOCK
    2 = execution/configuration error (dataset not found, column not
        found, DataHub/MCP unreachable, invalid CLI arguments, etc.)

Usage:
    python -m contract_sentinel.cli \\
        --dataset commerce.orders \\
        --column customer_id \\
        --operation drop \\
        --mode live \\
        --datahub-url http://localhost:8080

    python -m contract_sentinel.cli --dataset commerce.orders \\
        --column customer_id --operation rename --new-name cust_key \\
        --mode live --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from dataclasses import asdict

from .agent import AgentResult, ChangeGuardAgent, StepStatus
from .mcp_connection import create_live_adapter
from .risk import Change

EXIT_ALLOW = 0
EXIT_BLOCK = 1
EXIT_ERROR = 2


class _ArgumentParseError(ValueError):
    """Raised instead of SystemExit for invalid command-line arguments."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentParseError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m contract_sentinel.cli",
        description=(
            "ChangeGuard CI/CD gate: evaluate a proposed schema change "
            "against DataHub lineage and exit with a CI-friendly code "
            "(0=ALLOW, 1=BLOCK, 2=ERROR)."
        ),
    )
    parser.add_argument(
        "--dataset", required=True, help="Fully qualified dataset name (schema.table)"
    )
    parser.add_argument("--column", required=True, help="Column being changed")
    parser.add_argument(
        "--operation",
        required=True,
        choices=["rename", "drop", "type_change", "add"],
        help="Schema change operation",
    )
    parser.add_argument(
        "--new-name",
        default=None,
        help="New column name (rename) or new type (type_change). "
        "Required when --operation rename.",
    )
    parser.add_argument(
        "--mode",
        default="demo",
        choices=["demo", "live"],
        help="Data source: demo fixtures or live DataHub via MCP (default: demo)",
    )
    parser.add_argument(
        "--datahub-url",
        default="http://localhost:8080",
        help="DataHub GMS URL, used with --mode live (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--datahub-token",
        default=None,
        help="Optional DataHub personal access token, used with --mode live",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON object on stdout instead of human-readable text",
    )
    return parser


def _emit_error(message: str, args: argparse.Namespace) -> None:
    """Report an error without mixing diagnostics into --json stdout.

    In --json mode, stdout receives exactly one JSON object (including
    for errors), so CI can always parse stdout the same way. In text
    mode, the error goes to stderr, matching normal CLI error conventions.
    """
    if args.json:
        print(
            json.dumps(
                {
                    "error": message,
                    "dataset": args.dataset,
                    "column": args.column,
                    "operation": args.operation,
                }
            )
        )
    else:
        print(f"ChangeGuard error: {message}", file=sys.stderr)


def _human_report(args: argparse.Namespace, result: AgentResult, decision: str) -> str:
    impact = result.impact
    lines = [
        "ChangeGuard",
        f"Dataset: {args.dataset}",
        f"Column: {args.column}",
        f"Operation: {args.operation}",
        f"Risk Score: {impact.score}/100",
        f"Severity: {impact.severity.upper()}",
        f"Confirmed affected assets: {len(result.downstream_assets)}",
        f"Potential downstream assets: {len(result.potential_downstream_assets)}",
        f"Decision: {decision}",
        "Reasons:",
    ]
    lines.extend(f"- {reason}" for reason in impact.reasons)
    lines.append("Confirmed:")
    lines.extend(f"- {asset['name']}" for asset in result.downstream_assets)
    lines.append("Potential:")
    lines.extend(f"- {asset['name']}" for asset in result.potential_downstream_assets)
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    if result.remediation is not None:
        lines.append("Remediation:")
        lines.append(result.remediation.summary)
        lines.extend(
            f"{index}. {action.title}: {action.detail}"
            for index, action in enumerate(result.remediation.steps, 1)
        )
    return "\n".join(lines)


def _json_report(args: argparse.Namespace, result: AgentResult, decision: str) -> str:
    impact = result.impact
    payload = {
        "dataset": args.dataset,
        "column": args.column,
        "operation": args.operation,
        "risk_score": impact.score,
        "severity": impact.severity,
        "decision": decision,
        "confirmed_affected_assets": len(result.downstream_assets),
        "potential_downstream_assets": len(result.potential_downstream_assets),
        "mode": result.mode,
        "warnings": result.warnings,
        "remediation": (
            asdict(result.remediation) if result.remediation is not None else None
        ),
    }
    return json.dumps(payload)


async def _run(args: argparse.Namespace) -> tuple[AgentResult | None, str | None]:
    """Build the (optional) live adapter and run the agent once.

    Mirrors app.py's execute_agent(): the CLI and the Streamlit UI share
    the exact same agent wiring, not a parallel implementation. Writeback
    is always disabled here (confirm_writeback=False) so the CLI is safe
    to run unattended in a CI pipeline.
    """
    mcp_adapter = None
    if args.mode == "live":
        try:
            mcp_adapter = await create_live_adapter(
                datahub_url=args.datahub_url,
                datahub_token=args.datahub_token,
            )
        except Exception as e:
            return None, str(e)

    try:
        agent = ChangeGuardAgent(mcp_adapter=mcp_adapter)
        change = Change(
            dataset=args.dataset,
            column=args.column,
            operation=args.operation,
            new_type=args.new_name,
        )
        result = await agent.run_async(change, confirm_writeback=False)
        return result, None
    finally:
        if mcp_adapter is not None:
            try:
                await mcp_adapter.close()
            except Exception:
                # Do not let a cleanup failure hide the agent's result or
                # any error already being propagated from this block.
                pass


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except _ArgumentParseError as e:
        if "--json" in raw_argv:
            print(json.dumps({"error": f"Invalid arguments: {e}"}))
        else:
            parser.print_usage(sys.stderr)
            print(f"{parser.prog}: error: {e}", file=sys.stderr)
        return EXIT_ERROR

    if args.operation == "rename" and not args.new_name:
        _emit_error("--new-name is required when --operation rename", args)
        return EXIT_ERROR

    try:
        result, connection_error = asyncio.run(_run(args))
    except Exception as e:
        message = f"Unexpected execution error: {e}"
        if args.json:
            print(message, file=sys.stderr)
        else:
            traceback.print_exc(file=sys.stderr)
        _emit_error(message, args)
        return EXIT_ERROR

    if connection_error:
        _emit_error(
            f"Failed to connect to DataHub MCP server at {args.datahub_url}: "
            f"{connection_error}",
            args,
        )
        return EXIT_ERROR

    if result is None or result.impact is None:
        failed_step = next(
            (s for s in (result.steps if result else []) if s.status == StepStatus.FAILED),
            None,
        )
        message = (
            failed_step.error
            if failed_step and failed_step.error
            else "ChangeGuard could not produce a risk assessment."
        )
        _emit_error(message, args)
        return EXIT_ERROR

    decision_step = next((s for s in result.steps if s.name == "decision"), None)
    decision = (
        decision_step.result.get("decision")
        if decision_step and isinstance(decision_step.result, dict)
        else None
    )
    if decision not in {"ALLOW", "BLOCK"}:
        _emit_error("ChangeGuard could not determine a deployment decision.", args)
        return EXIT_ERROR

    if args.json:
        print(_json_report(args, result, decision))
    else:
        print(_human_report(args, result, decision))

    return EXIT_ALLOW if decision == "ALLOW" else EXIT_BLOCK


if __name__ == "__main__":
    sys.exit(main())
