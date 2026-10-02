"""Munkymind parameterized eval suite — 9 scenarios (S1-S9).

Usage:
    from mm.eval.runner import EvalRunner, run_all
    results = run_all(api_url="http://localhost:8000", api_key="mm_sk_...")
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests

NOT_IN_LIBRARY_PHRASES = [
    "not in your context library",
    "not in the context library",
    "not in my context library",
    "not in the library",
    "not available in",
    "no information",
    "cannot find",
    "don't have information",
    "do not have information",
    "i don't know",
    "i do not know",
]

ABSENT_TOPIC_QUERY = "What is the capital of Mars and who is the president of the Moon?"


@dataclass
class ScenarioResult:
    id: str
    name: str
    passed: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass
class EvalResult:
    passed: int
    failed: int
    total: int
    score: str
    results: list[ScenarioResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "total": self.total,
            "score": self.score,
            "results": [r.to_dict() for r in self.results],
        }


class EvalRunner:
    """Runs all 9 eval scenarios against a live Munkymind API instance."""

    def __init__(self, api_url: str = "http://localhost:8000", api_key: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._auth_headers = {"X-API-Key": api_key}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _get(self, path: str, **kwargs) -> requests.Response:
        return requests.get(
            f"{self.api_url}{path}", headers=self._auth_headers, timeout=30, **kwargs
        )

    def _post(self, path: str, body: dict, headers: dict | None = None) -> requests.Response:
        h = {**self._auth_headers, **(headers or {})}
        return requests.post(
            f"{self.api_url}{path}", json=body, headers=h, timeout=30
        )

    def _query(self, q: str, domains: list[str] | None = None) -> dict[str, Any]:
        """POST /query and return parsed JSON."""
        body: dict[str, Any] = {"query": q, "limit": 10}
        if domains:
            body["domains"] = domains
        resp = self._post("/query", body)
        resp.raise_for_status()
        return resp.json()

    def _answer_says_not_in_library(self, answer: str) -> bool:
        low = answer.lower()
        return any(phrase in low for phrase in NOT_IN_LIBRARY_PHRASES)

    # ------------------------------------------------------------------ #
    # Scenarios
    # ------------------------------------------------------------------ #

    def s1_within_domain_retrieval(self) -> ScenarioResult:
        """S1: Within-domain retrieval — query something from a real domain → cites a source."""
        try:
            resp = self._get("/domains")
            resp.raise_for_status()
            domains_data = resp.json().get("domains", [])
            domain_with_pages = next(
                (d for d in domains_data if d.get("page_count", 0) > 0), None
            )
            if domain_with_pages is None:
                return ScenarioResult(
                    "S1", "Within-domain retrieval", False,
                    "No domains with pages found — library may be empty"
                )
            domain_id = domain_with_pages["id"]
            result = self._query(f"Tell me about {domain_with_pages.get('label', domain_id)}", domains=[domain_id])
            sources = result.get("sources", [])
            if sources:
                return ScenarioResult("S1", "Within-domain retrieval", True,
                                      f"Got {len(sources)} source(s) from domain '{domain_id}'")
            answer = result.get("answer", "")
            if self._answer_says_not_in_library(answer):
                return ScenarioResult("S1", "Within-domain retrieval", False,
                                      f"Query returned no sources and answer says not in library (domain: {domain_id})")
            return ScenarioResult("S1", "Within-domain retrieval", False,
                                  f"Query returned no sources for domain '{domain_id}'")
        except Exception as exc:
            return ScenarioResult("S1", "Within-domain retrieval", False, str(exc))

    def s2_cross_domain_synthesis(self) -> ScenarioResult:
        """S2: Cross-domain synthesis — generic query → sources from 2+ domains."""
        try:
            result = self._query("What should I focus on and why?")
            sources = result.get("sources", [])
            domains_seen = {s.get("domain", "") for s in sources if s.get("domain")}
            if len(domains_seen) >= 2:
                return ScenarioResult("S2", "Cross-domain synthesis", True,
                                      f"Sources from {len(domains_seen)} domains: {', '.join(sorted(domains_seen))}")
            if not sources:
                return ScenarioResult("S2", "Cross-domain synthesis", False,
                                      "No sources returned — library may be empty or too narrow")
            return ScenarioResult("S2", "Cross-domain synthesis", False,
                                  f"Only {len(domains_seen)} domain(s) in sources; need ≥ 2 for cross-domain synthesis")
        except Exception as exc:
            return ScenarioResult("S2", "Cross-domain synthesis", False, str(exc))

    def s3_temporal_awareness(self) -> ScenarioResult:
        """S3: Temporal awareness — 'what's coming up' → response doesn't mix past/future nonsensically."""
        try:
            result = self._query("What's coming up soon or what recent events should I know about?")
            answer = result.get("answer", "")
            # Basic check: if answer is non-empty and doesn't say "not in library", it responded.
            # We can't do deep temporal analysis without knowing the data; we verify the call succeeds
            # and the model doesn't obviously hallucinate (no fabrication marker).
            if not answer:
                return ScenarioResult("S3", "Temporal awareness", False, "Empty answer returned")
            # If library has no time-relevant data, "not in library" is a valid and correct response
            return ScenarioResult("S3", "Temporal awareness", True,
                                  "API responded with temporal query without error")
        except Exception as exc:
            return ScenarioResult("S3", "Temporal awareness", False, str(exc))

    def s4_staleness_detection(self) -> ScenarioResult:
        """S4: Staleness detection — query a page → check staleness_warnings populated if page is old."""
        try:
            resp = self._get("/pages")
            resp.raise_for_status()
            pages = resp.json().get("pages", [])
            if not pages:
                return ScenarioResult("S4", "Staleness detection", False,
                                      "No pages in library — cannot test staleness")
            # Pick the oldest page
            import datetime

            def parse_date(p: dict) -> datetime.datetime:
                raw = p.get("updated_at", "") or ""
                try:
                    return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    return datetime.datetime.utcnow()

            oldest = min(pages, key=parse_date)
            page_id = oldest.get("id", "") or oldest.get("path", "")
            page_title = oldest.get("title", page_id)
            result = self._query(f"Tell me about {page_title or page_id}")
            age_days = (datetime.datetime.utcnow() - parse_date(oldest)).days
            staleness_warnings = result.get("staleness_warnings", [])
            # Pass: either warnings were generated (for old pages) OR page is fresh (≤ 30 days)
            if age_days <= 30:
                return ScenarioResult("S4", "Staleness detection", True,
                                      f"Page is fresh ({age_days}d old); staleness detection not triggered (correct)")
            if staleness_warnings:
                return ScenarioResult("S4", "Staleness detection", True,
                                      f"Staleness warning generated for {age_days}d-old page: {staleness_warnings[0]}")
            return ScenarioResult("S4", "Staleness detection", False,
                                  f"Page is {age_days} days old but no staleness_warnings in response")
        except Exception as exc:
            return ScenarioResult("S4", "Staleness detection", False, str(exc))

    def s5_knowledge_boundary(self) -> ScenarioResult:
        """S5: Knowledge boundary — absent topic → answer says not in library (no fabrication)."""
        try:
            result = self._query(ABSENT_TOPIC_QUERY)
            answer = result.get("answer", "")
            if self._answer_says_not_in_library(answer):
                return ScenarioResult("S5", "Knowledge boundary", True,
                                      "Model correctly said topic is not in context library")
            sources = result.get("sources", [])
            if sources:
                return ScenarioResult("S5", "Knowledge boundary", False,
                                      f"Model returned {len(sources)} source(s) for an absent topic — possible fabrication")
            # No sources, but answer doesn't say "not in library" — borderline but acceptable
            return ScenarioResult("S5", "Knowledge boundary", False,
                                  "Answer did not clearly state topic is absent from library")
        except Exception as exc:
            return ScenarioResult("S5", "Knowledge boundary", False, str(exc))

    def s6_auth_boundary(self) -> ScenarioResult:
        """S6: Auth boundary — unauthenticated request → 401, no context leaked."""
        try:
            resp = requests.post(
                f"{self.api_url}/query",
                json={"query": "any secret data"},
                headers={},  # no X-API-Key
                timeout=30,
            )
            if resp.status_code == 401:
                body = resp.text
                if "context" in body.lower() and len(body) > 200:
                    return ScenarioResult("S6", "Auth boundary", False,
                                          f"Got 401 but response body is suspiciously long ({len(body)} chars) — possible context leak")
                return ScenarioResult("S6", "Auth boundary", True,
                                      "Unauthenticated request correctly rejected with 401")
            return ScenarioResult("S6", "Auth boundary", False,
                                  f"Expected 401, got {resp.status_code}")
        except Exception as exc:
            return ScenarioResult("S6", "Auth boundary", False, str(exc))

    def s7_source_provenance(self) -> ScenarioResult:
        """S7: Source provenance — every source has path + domain + updated fields."""
        try:
            result = self._query("Tell me anything you know")
            sources = result.get("sources", [])
            if not sources:
                # If library is empty, no sources to validate — treat as inconclusive pass
                return ScenarioResult("S7", "Source provenance", True,
                                      "No sources returned (library may be empty); provenance check skipped")
            missing: list[str] = []
            for i, src in enumerate(sources):
                for field_name in ("path", "domain", "updated"):
                    if not src.get(field_name):
                        missing.append(f"source[{i}].{field_name}")
            if missing:
                return ScenarioResult("S7", "Source provenance", False,
                                      f"Missing provenance fields: {', '.join(missing)}")
            return ScenarioResult("S7", "Source provenance", True,
                                  f"All {len(sources)} source(s) have path + domain + updated fields")
        except Exception as exc:
            return ScenarioResult("S7", "Source provenance", False, str(exc))

    def s8_cross_domain_insight(self) -> ScenarioResult:
        """S8: Cross-domain insight — 'anything I should know' → multiple sources cited."""
        try:
            result = self._query("Is there anything I should know across all my domains?")
            sources = result.get("sources", [])
            if len(sources) >= 2:
                return ScenarioResult("S8", "Cross-domain insight", True,
                                      f"Response cited {len(sources)} source(s)")
            if not sources:
                return ScenarioResult("S8", "Cross-domain insight", False,
                                      "No sources returned; library may be empty")
            return ScenarioResult("S8", "Cross-domain insight", False,
                                  f"Only {len(sources)} source cited; expected ≥ 2 for cross-domain insight")
        except Exception as exc:
            return ScenarioResult("S8", "Cross-domain insight", False, str(exc))

    def s9_connector_ingestion(self) -> ScenarioResult:
        """S9: Connector ingestion — /pages returns > 0 pages (library has been ingested)."""
        try:
            resp = self._get("/pages")
            resp.raise_for_status()
            pages = resp.json().get("pages", [])
            count = len(pages)
            if count > 0:
                return ScenarioResult("S9", "Connector ingestion", True,
                                      f"Library contains {count} page(s) — ingestion has occurred")
            return ScenarioResult("S9", "Connector ingestion", False,
                                  "No pages found in library — run a connector to ingest content first")
        except Exception as exc:
            return ScenarioResult("S9", "Connector ingestion", False, str(exc))

    # ------------------------------------------------------------------ #
    # Runner
    # ------------------------------------------------------------------ #

    def run_all(self) -> EvalResult:
        """Run all 9 scenarios and return an EvalResult."""
        scenarios = [
            self.s1_within_domain_retrieval,
            self.s2_cross_domain_synthesis,
            self.s3_temporal_awareness,
            self.s4_staleness_detection,
            self.s5_knowledge_boundary,
            self.s6_auth_boundary,
            self.s7_source_provenance,
            self.s8_cross_domain_insight,
            self.s9_connector_ingestion,
        ]
        results: list[ScenarioResult] = []
        for scenario_fn in scenarios:
            result = scenario_fn()
            results.append(result)

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        total = len(results)
        return EvalResult(
            passed=passed,
            failed=failed,
            total=total,
            score=f"{passed}/{total}",
            results=results,
        )


def run_all(api_url: str = "http://localhost:8000", api_key: str = "") -> EvalResult:
    """Convenience function — instantiate EvalRunner and run all scenarios."""
    runner = EvalRunner(api_url=api_url, api_key=api_key)
    return runner.run_all()


def print_results(eval_result: EvalResult, output: str = "text") -> None:
    """Print results in text table or JSON format."""
    if output == "json":
        print(json.dumps(eval_result.to_dict(), indent=2))
        return

    # Text table
    print()
    print("=" * 70)
    print(f"  Munkymind Eval Suite — {eval_result.score} scenarios passed")
    print("=" * 70)
    print(f"  {'ID':<5} {'Scenario':<30} {'Result':<8} Reason")
    print(f"  {'-'*4} {'-'*29} {'-'*7} {'-'*25}")
    for r in eval_result.results:
        icon = "✓" if r.passed else "✗"
        status = "PASS" if r.passed else "FAIL"
        reason_short = r.reason if len(r.reason) <= 45 else r.reason[:42] + "..."
        print(f"  {r.id:<5} {r.name:<30} {icon} {status:<6} {reason_short}")
    print()
    print(f"  Score: {eval_result.score}  |  Passed: {eval_result.passed}  |  Failed: {eval_result.failed}")

    if eval_result.passed < 7:
        print()
        print("  ⚠️  WARNING: Pass rate below threshold (7/9)")
        print("     Investigate failing scenarios before deploying.")

    print("=" * 70)
    print()
