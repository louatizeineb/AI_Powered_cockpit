from __future__ import annotations

"""
Smoke test for the DQC Resolution Agent backend.

Run from anywhere while the backend is running:

    python smoke_test_agent_workflow.py --base-url http://127.0.0.1:8000

Optional:

    python smoke_test_agent_workflow.py --base-url http://127.0.0.1:8000 --use-llm

What it tests:
1. Backend health endpoint
2. DQC resolved/unresolved listing endpoints, if available
3. Agent chat endpoint using backend tool selection
4. Fixed workflow endpoint using one synthetic DQC event
5. Saves a JSON report for review
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: requests. Install with: python -m pip install requests"
    ) from exc


DEFAULT_EVENT: dict[str, Any] = {
    "applicationcode": "MKD",
    "controlledobjectname": "[s_comp_company.n_ident_compy]",
    "controlledobjecttype": "Table",
    "controlledsourcename": "s_comp_company",
    "businesstermname": None,
    "controlname": "Smoke test completeness check",
    "qualitydimension": "Completeness",
    "acceptancethreshold": 95,
    "executiontimestamp": "2026-05-21T10:00:00",
    "businessdate": "2026-05-21",
    "controlleditemcount": 10,
    "okcount": 10,
    "kocount": 0,
    "controltool": "SMOKE_TEST",
    "cdqprofile": "smoke-test-profile",
    "controllink": None,
}


@dataclass
class CheckResult:
    name: str
    method: str
    url: str
    ok: bool
    status_code: int | None
    elapsed_ms: int
    response_preview: Any
    error: str | None = None


class SmokeTester:
    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.results: list[CheckResult] = []

    def _request(
        self,
        name: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> dict[str, Any] | list[Any] | str | None:
        url = f"{self.base_url}{path}"
        started = time.perf_counter()
        status_code: int | None = None
        parsed: Any = None
        error: str | None = None

        try:
            if method.upper() == "GET":
                response = requests.get(url, timeout=self.timeout)
            elif method.upper() == "POST":
                response = requests.post(url, json=payload or {}, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            status_code = response.status_code
            try:
                parsed = response.json()
            except Exception:
                parsed = response.text

            ok = status_code in expected_statuses
            if not ok:
                error = f"Unexpected status code {status_code}; expected {expected_statuses}"

        except Exception as exc:
            ok = False
            error = repr(exc)
            parsed = None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        preview = self._preview(parsed)
        self.results.append(
            CheckResult(
                name=name,
                method=method.upper(),
                url=url,
                ok=ok,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                response_preview=preview,
                error=error,
            )
        )
        return parsed

    @staticmethod
    def _preview(value: Any, max_chars: int = 1500) -> Any:
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, default=str)
            if len(text) <= max_chars:
                return value
            return text[:max_chars] + "...<truncated>"
        if isinstance(value, str) and len(value) > max_chars:
            return value[:max_chars] + "...<truncated>"
        return value

    def run(self, use_llm: bool = False) -> None:
        self._request("health", "GET", "/health")

        # These may return empty lists if no DQC batch was processed yet.
        self._request(
            "dqc resolved list",
            "GET",
            "/dqc-resolution/resolved",
            expected_statuses=(200, 404),
        )
        self._request(
            "dqc unresolved list",
            "GET",
            "/dqc-resolution/unresolved",
            expected_statuses=(200, 404),
        )

        # Agent chat: expected to route to list_resolved/list_unresolved or LLM depending implementation.
        self._request(
            "agent chat resolved summary",
            "POST",
            "/agent/dqc/chat",
            payload={
                "message": (
                    "Summarize the latest resolved DQC matches. "
                    "Explain high vs medium confidence in two short paragraphs."
                )
            },
        )

        # Fixed workflow: expected schema is {event, use_llm_explanation}.
        self._request(
            "agent fixed workflow single event",
            "POST",
            "/agent/dqc/run-workflow",
            payload={
                "event": DEFAULT_EVENT,
                "use_llm_explanation": use_llm,
            },
        )

    def print_summary(self) -> None:
        print("\n=== DQC Agent Smoke Test Summary ===")
        for result in self.results:
            symbol = "PASS" if result.ok else "FAIL"
            print(
                f"[{symbol}] {result.name} | {result.method} {result.url} | "
                f"status={result.status_code} | {result.elapsed_ms} ms"
            )
            if result.error:
                print(f"       error: {result.error}")

        passed = sum(1 for r in self.results if r.ok)
        total = len(self.results)
        print(f"\nPassed: {passed}/{total}")

    def save_report(self, output_path: Path) -> None:
        report = {
            "base_url": self.base_url,
            "generated_at_epoch": time.time(),
            "results": [asdict(r) for r in self.results],
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"Report saved to: {output_path.resolve()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test DQC Resolution Agent backend endpoints.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL. Default: http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds. Default: 60",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Ask /agent/dqc/run-workflow to generate LLM explanation.",
    )
    parser.add_argument(
        "--output",
        default="smoke_test_agent_workflow_report.json",
        help="JSON report output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tester = SmokeTester(base_url=args.base_url, timeout=args.timeout)
    tester.run(use_llm=args.use_llm)
    tester.print_summary()
    tester.save_report(Path(args.output))

    failed = [r for r in tester.results if not r.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
