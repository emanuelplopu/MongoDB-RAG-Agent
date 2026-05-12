"""
RecallHub Full Pipeline End-to-End Integration Test
=====================================================

Tests the entire RecallHub agent pipeline including:
- Authentication
- Session creation
- Message sending (LLM + RAG search)
- Response validation
- LLM Judge comparison against golden reference using GPT-5.5

Requirements:
- RecallHub backend must be running (default: http://localhost:11000)
- Python packages: httpx, openai
- Environment variables:
    - RECALLHUB_BASE_URL (optional, default: http://localhost:11000)
    - TEST_USER_PASSWORD (optional, default: Omegat13)
    - OPENAI_API_KEY (required for LLM judge, optional for pipeline-only test)

Usage:
    python test_scripts/test_full_pipeline_e2e.py
    python test_scripts/test_full_pipeline_e2e.py --save-golden
    python test_scripts/test_full_pipeline_e2e.py --skip-judge
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL: str = os.getenv("RECALLHUB_BASE_URL", "http://localhost:11000")
TEST_EMAIL: str = "emanuel.plopu@parhelion.energy"
TEST_PASSWORD: str = os.getenv("TEST_USER_PASSWORD", "Omegat13")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
JUDGE_MODEL: str = "gpt-5.5"
GOLDEN_RESPONSE_DIR: Path = Path(__file__).parent / "golden_responses"
GOLDEN_RESPONSE_FILE: Path = GOLDEN_RESPONSE_DIR / "full_pipeline_e2e.json"

TEST_PROMPT: str = "ich suche ergänzungfragen von einen immobilienprozess"

# Timeouts
HTTP_TIMEOUT: float = 600.0  # 10 minutes for streaming agent calls
MAX_RESPONSE_TIME: float = 300.0  # 5 minutes max for the agent response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def print_header() -> None:
    """Print the test header."""
    print("\n\u2550" * 45)
    print("  RecallHub Full Pipeline E2E Test")
    print("\u2550" * 45)
    print()


def print_separator() -> None:
    """Print a section separator."""
    print("\u2500" * 45)


def parse_sse_stream(response: httpx.Response) -> Dict[str, Any]:
    """Parse SSE streaming response and extract the final response event.

    Args:
        response: httpx Response object from a streaming request.

    Returns:
        Dictionary with keys: content, sources, stats, trace, mode, models.
    """
    result: Dict[str, Any] = {
        "content": "",
        "sources": [],
        "stats": {},
        "trace": {},
        "mode": "",
        "models": {},
    }

    for line in response.text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:]  # Remove "data: " prefix
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "start":
            result["mode"] = event.get("mode", "")
            result["models"] = event.get("models", {})
        elif event_type == "response":
            result["content"] = event.get("content", "")
            result["sources"] = event.get("sources", [])
            result["stats"] = event.get("stats", {})
            result["trace"] = event.get("trace", {})
        elif event_type == "error":
            raise RuntimeError(f"Agent returned error: {event.get('message', 'unknown')}")

    return result


def save_golden_response(
    content: str,
    sources_count: int,
    model_used: str,
    agent_mode: str,
    stats: Dict[str, Any],
) -> None:
    """Save the current response as the golden reference.

    Args:
        content: The response text content.
        sources_count: Number of sources returned.
        model_used: Model identifier used for generation.
        agent_mode: Agent mode string (e.g. orchestrator-worker).
        stats: Stats dictionary from the response.
    """
    GOLDEN_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

    golden_data = {
        "saved_at": datetime.now().isoformat(),
        "prompt": TEST_PROMPT,
        "profile": "parhelion",
        "response_content": content,
        "sources_count": sources_count,
        "model_used": model_used,
        "agent_mode": agent_mode,
        "metadata": {
            "tokens": stats.get("total_tokens", 0),
            "latency_ms": stats.get("latency_ms", 0),
        },
    }

    with open(GOLDEN_RESPONSE_FILE, "w", encoding="utf-8") as f:
        json.dump(golden_data, f, indent=2, ensure_ascii=False)

    print(f"  \u2192 Golden response saved to: {GOLDEN_RESPONSE_FILE}")


def load_golden_response() -> Optional[Dict[str, Any]]:
    """Load the golden reference response if it exists.

    Returns:
        Golden response dict or None if file doesn't exist.
    """
    if not GOLDEN_RESPONSE_FILE.exists():
        return None
    with open(GOLDEN_RESPONSE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def run_llm_judge(
    prompt: str,
    golden_content: str,
    new_content: str,
) -> Dict[str, Any]:
    """Use GPT-5.5 as an LLM judge to compare responses.

    Args:
        prompt: The original query prompt.
        golden_content: The reference/golden response text.
        new_content: The new response text to evaluate.

    Returns:
        Judge evaluation dictionary with scores and reasoning.

    Raises:
        RuntimeError: If OpenAI API call fails or response is unparseable.
    """
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")

    client = OpenAI(api_key=OPENAI_API_KEY)

    judge_prompt = f'''You are an expert quality evaluator for a RAG (Retrieval-Augmented Generation) system response.
You are comparing a NEW response against a REFERENCE response for the same query.

Query: "{prompt}"

REFERENCE RESPONSE:
{golden_content}

NEW RESPONSE:
{new_content}

Evaluate the NEW response on these criteria (score 1-10 each):
1. RELEVANCE: Does it address the same topic as the reference? Are the key concepts present?
2. COMPLETENESS: Does it cover similar ground? Are important points from the reference also in the new response?
3. ACCURACY: Is the information consistent with the reference? No contradictions?
4. QUALITY: Is the response well-structured, coherent, and professional?

Provide your evaluation as JSON:
{{
  "relevance": <1-10>,
  "completeness": <1-10>,
  "accuracy": <1-10>,
  "quality": <1-10>,
  "overall": <1-10>,
  "pass": <true if overall >= 7>,
  "reasoning": "<brief explanation of scores>"
}}

IMPORTANT: Output ONLY the JSON, no additional text.'''

    try:
        response = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": judge_prompt}],
            temperature=0.0,
            max_tokens=500,
        )
        judge_text = response.choices[0].message.content.strip()

        # Try to parse JSON (handle markdown code blocks)
        if judge_text.startswith("```"):
            # Remove code block markers
            lines = judge_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            judge_text = "\n".join(lines).strip()

        return json.loads(judge_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse judge response as JSON: {e}\nRaw: {judge_text}")
    except Exception as e:
        raise RuntimeError(f"LLM Judge call failed: {e}")


# ---------------------------------------------------------------------------
# Main Test Flow
# ---------------------------------------------------------------------------

def run_test(save_golden: bool = False, skip_judge: bool = False) -> bool:
    """Execute the full pipeline E2E test.

    Args:
        save_golden: If True, force-save the current response as golden reference.
        skip_judge: If True, skip the LLM judge evaluation step.

    Returns:
        True if all checks pass, False otherwise.
    """
    print_header()

    total_start = time.time()
    session_id: Optional[str] = None
    token: Optional[str] = None
    passed = True

    client = httpx.Client(base_url=BASE_URL, timeout=HTTP_TIMEOUT)

    try:
        # ===================================================================
        # Step 1: Authentication
        # ===================================================================
        step_start = time.time()
        print("[1/5] Authentication ... ", end="", flush=True)

        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        if login_resp.status_code != 200:
            print(f"\u2717 FAILED (status {login_resp.status_code}: {login_resp.text[:200]})")
            return False

        login_data = login_resp.json()
        token = login_data.get("access_token")
        if not token:
            print("\u2717 FAILED (no access_token in response)")
            return False

        client.headers["Authorization"] = f"Bearer {token}"
        step_time = time.time() - step_start
        print(f"\u2713 (logged in as {TEST_EMAIL}) [{step_time:.1f}s]")

        # ===================================================================
        # Step 2: Create Session
        # ===================================================================
        step_start = time.time()
        print("[2/5] Create Session ... ", end="", flush=True)

        session_resp = client.post(
            "/api/v1/sessions",
            json={"title": "E2E Test - Full Pipeline"},
        )
        if session_resp.status_code not in (200, 201):
            print(f"\u2717 FAILED (status {session_resp.status_code}: {session_resp.text[:200]})")
            return False

        session_data = session_resp.json()
        session_id = session_data.get("id")
        profile = session_data.get("profile", "unknown")
        if not session_id:
            print("\u2717 FAILED (no session id returned)")
            return False

        step_time = time.time() - step_start
        print(f"\u2713 (session_id: {session_id[:8]}..., profile: {profile}) [{step_time:.1f}s]")

        # ===================================================================
        # Step 3: Send Prompt
        # ===================================================================
        step_start = time.time()
        print("[3/5] Send Prompt ... ", end="", flush=True)

        message_payload = {
            "content": TEST_PROMPT,
            "search_type": "hybrid",
            "match_count": 10,
            "include_sources": True,
            "agent_mode": "auto",
            "language": "de",
        }

        msg_resp = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json=message_payload,
        )

        if msg_resp.status_code != 200:
            print(f"\u2717 FAILED (status {msg_resp.status_code}: {msg_resp.text[:200]})")
            return False

        step_time = time.time() - step_start

        if step_time > MAX_RESPONSE_TIME:
            print(f"\u2717 FAILED (response took {step_time:.1f}s, max is {MAX_RESPONSE_TIME}s)")
            return False

        # Parse SSE stream
        try:
            result = parse_sse_stream(msg_resp)
        except RuntimeError as e:
            print(f"\u2717 FAILED ({e})")
            return False

        content = result["content"]
        sources = result["sources"]
        stats = result["stats"]
        total_tokens = stats.get("total_tokens", 0)
        sources_count = len(sources)

        print(
            f"\u2713 (response in {step_time:.1f}s, "
            f"{total_tokens} tokens, {sources_count} sources) [{step_time:.1f}s]"
        )

        # ===================================================================
        # Step 4: Validate Response Structure
        # ===================================================================
        step_start = time.time()
        print("[4/5] Validate Structure ... ", end="", flush=True)

        validation_errors: list[str] = []

        if not content:
            validation_errors.append("no content in response")
        if not sources:
            validation_errors.append("no sources returned")
        if not result.get("trace"):
            validation_errors.append("no agent_trace returned")

        if validation_errors:
            print(f"\u2717 FAILED ({'; '.join(validation_errors)})")
            return False

        mode_str = result.get("mode", "unknown")
        step_time = time.time() - step_start
        print(
            f"\u2713 (content: {len(content)} chars, sources: {sources_count}, "
            f"trace: {mode_str}) [{step_time:.1f}s]"
        )

        # ===================================================================
        # Step 5: LLM Judge Evaluation
        # ===================================================================
        step_start = time.time()
        print("[5/5] LLM Judge Evaluation ... ", end="", flush=True)

        # Determine model used
        models = result.get("models", {})
        model_used = models.get("orchestrator", "unknown")

        if skip_judge:
            print("\u2714 SKIPPED (--skip-judge flag)")
        elif not OPENAI_API_KEY:
            print("\u2714 SKIPPED (OPENAI_API_KEY not set)")
        else:
            golden = load_golden_response()

            if golden is None or save_golden:
                # No golden reference yet - save current as golden
                save_golden_response(
                    content=content,
                    sources_count=sources_count,
                    model_used=model_used,
                    agent_mode=mode_str,
                    stats=stats,
                )
                if golden is None:
                    print("\u2713 (no golden reference - saved current as baseline)")
                else:
                    print("\u2713 (force-saved new golden reference)")
            else:
                # Run LLM judge comparison
                try:
                    judge_result = run_llm_judge(
                        prompt=TEST_PROMPT,
                        golden_content=golden["response_content"],
                        new_content=content,
                    )
                except RuntimeError as e:
                    print(f"\u2717 FAILED ({e})")
                    return False

                overall = judge_result.get("overall", 0)
                judge_pass = judge_result.get("pass", False)
                step_time = time.time() - step_start

                status_str = "PASS" if judge_pass else "FAIL"
                status_icon = "\u2713" if judge_pass else "\u2717"
                print(f"{status_icon} (overall: {overall}/10 - {status_str}) [{step_time:.1f}s]")

                # Print judge details
                print()
                print_separator()
                print("Judge Details:")
                print(f"  Relevance:    {judge_result.get('relevance', '?')}/10")
                print(f"  Completeness: {judge_result.get('completeness', '?')}/10")
                print(f"  Accuracy:     {judge_result.get('accuracy', '?')}/10")
                print(f"  Quality:      {judge_result.get('quality', '?')}/10")
                print(f"  Overall:      {overall}/10")
                print(f'  Reasoning: "{judge_result.get("reasoning", "N/A")}"')
                print_separator()

                if not judge_pass:
                    passed = False

        # ===================================================================
        # Final Result
        # ===================================================================
        total_time = time.time() - total_start
        print()

        if passed:
            print(f"RESULT: \u2705 PASS - Full pipeline operational")
        else:
            print(f"RESULT: \u274c FAIL - Quality below threshold")

        print(f"Total time: {total_time:.1f}s")
        return passed

    except httpx.ConnectError:
        print(f"\n\u274c CONNECTION ERROR: Cannot reach backend at {BASE_URL}")
        print("   Make sure the RecallHub backend is running.")
        return False
    except Exception as e:
        print(f"\n\u274c UNEXPECTED ERROR: {type(e).__name__}: {e}")
        return False
    finally:
        # Cleanup: delete the test session
        if session_id and token:
            try:
                client.delete(f"/api/v1/sessions/{session_id}")
            except Exception:
                pass  # Best effort cleanup
        client.close()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="RecallHub Full Pipeline E2E Test with LLM Judge"
    )
    parser.add_argument(
        "--save-golden",
        action="store_true",
        help="Force-save the current response as the new golden reference",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip the LLM judge step (useful when OPENAI_API_KEY is not available)",
    )
    args = parser.parse_args()

    success = run_test(save_golden=args.save_golden, skip_judge=args.skip_judge)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
