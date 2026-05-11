"""
Model Benchmark Service
Tests LLM models for orchestrator and worker capability using predefined prompts
and automated scoring via LLM judge.
"""
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Literal

import litellm

logger = logging.getLogger(__name__)

# Benchmark prompts for each role
ORCHESTRATOR_BENCHMARK = {
    "system": "You are an AI assistant helping with document retrieval and analysis. Follow instructions precisely and produce structured output.",
    "prompt": (
        "Analyze this user query and create a structured search plan. "
        "The query is: 'Find all documents about data privacy regulations in Germany'\n\n"
        "Your response MUST follow this exact format:\n"
        "1. INTENT: [one sentence describing what the user wants]\n"
        "2. SEARCH_QUERIES: [list 3 specific search queries to find relevant documents]\n"
        "3. EXPECTED_SOURCES: [what types of documents would be relevant]\n"
        "4. FOLLOW_UP: [one follow-up question to ask if results are insufficient]"
    ),
    "max_tokens": 500,
}

WORKER_BENCHMARK = {
    "system": "You are a concise assistant. Answer directly and briefly.",
    "prompt": (
        "Summarize the following text in exactly 2-3 sentences. Be concise and accurate.\n\n"
        "Text: 'The General Data Protection Regulation (GDPR) is a regulation in EU law on data protection "
        "and privacy in the European Union and the European Economic Area. The GDPR's primary aim is to "
        "enhance individuals' control and rights over their personal data and to simplify the regulatory "
        "environment for international business. The regulation contains provisions and requirements "
        "related to the processing of personal data of individuals who are located in the EEA, and applies "
        "to any enterprise that is processing the personal data of individuals inside the EEA, regardless "
        "of its location and the data subjects' citizenship or residence.'"
    ),
    "max_tokens": 200,
}

# Judge prompt template for scoring responses
JUDGE_SYSTEM = """You are an expert evaluator of AI model responses. Score the response on these dimensions (1-10 each):
- reasoning: Logical thinking, structured analysis, multi-step problem solving
- instruction_following: Did it follow the exact format/instructions given?
- coherence: Is the output clear, well-organized, and free of repetition/garbage?
- speed_score: Based on the provided latency, rate appropriateness (use the guidelines below)

Speed guidelines for scoring:
- < 5s = 10, < 10s = 9, < 20s = 8, < 40s = 7, < 60s = 6, < 90s = 5, < 120s = 4, > 120s = 3

Output ONLY valid JSON (no markdown, no explanation):
{"reasoning": <int>, "instruction_following": <int>, "coherence": <int>, "speed_score": <int>}"""

JUDGE_USER_TEMPLATE = """Role being tested: {role}
Expected behavior: {expectation}
Latency: {latency_ms}ms
Tokens generated: {tokens}

PROMPT GIVEN TO MODEL:
{prompt}

MODEL'S RESPONSE:
{response}

Score this response as JSON:"""


class ModelBenchmarkService:
    """Service for testing and scoring LLM models."""

    def __init__(self, db):
        self.db = db
        self.collection = db["model_capabilities"]

    async def test_model(
        self,
        model_id: str,
        role: Literal["orchestrator", "worker"],
        judge_model: Optional[str] = None,
    ) -> dict:
        """
        Test a model for a specific role and store results.

        Args:
            model_id: Full model ID (e.g., "ollama/gemma4:26b")
            role: "orchestrator" or "worker"
            judge_model: Model to use as judge (defaults to settings orchestrator or gpt-4o)

        Returns:
            dict with test results and scores
        """
        benchmark = ORCHESTRATOR_BENCHMARK if role == "orchestrator" else WORKER_BENCHMARK
        expectation = (
            "Structured multi-step analysis with clear format adherence"
            if role == "orchestrator"
            else "Concise 2-3 sentence summary that is accurate and brief"
        )

        # Step 1: Call the model being tested
        logger.info(f"Testing model '{model_id}' for role '{role}'...")
        start_time = time.time()

        try:
            response = await litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": benchmark["system"]},
                    {"role": "user", "content": benchmark["prompt"]},
                ],
                max_tokens=benchmark["max_tokens"],
                temperature=0.3,
                timeout=120,
            )

            latency_ms = int((time.time() - start_time) * 1000)
            content = response.choices[0].message.content or ""
            tokens_generated = response.usage.completion_tokens if response.usage else len(content.split())

        except Exception as e:
            logger.error(f"Model test failed for {model_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "role": role,
                "model_id": model_id,
            }

        # Step 2: Check for obvious failure (empty, repetitive)
        if not content or len(content.strip()) < 20:
            scores = {"reasoning": 1, "instruction_following": 1, "coherence": 1, "speed_score": 5}
            overall = 1.0
        elif self._is_repetitive(content):
            scores = {"reasoning": 1, "instruction_following": 1, "coherence": 1, "speed_score": 3}
            overall = 1.0
        else:
            # Step 3: Use judge model to score
            scores = await self._judge_response(
                model_id=model_id,
                role=role,
                prompt=benchmark["prompt"],
                response_text=content,
                latency_ms=latency_ms,
                tokens=tokens_generated,
                expectation=expectation,
                judge_model=judge_model,
            )
            # Weighted average: reasoning 30%, instruction_following 30%, coherence 25%, speed 15%
            overall = (
                scores["reasoning"] * 0.30
                + scores["instruction_following"] * 0.30
                + scores["coherence"] * 0.25
                + scores["speed_score"] * 0.15
            )

        # Determine pass/fail
        pass_threshold = 7.0 if role == "orchestrator" else 5.0
        auto_pass = overall >= pass_threshold

        # Step 4: Store results
        test_results = {
            "reasoning": scores["reasoning"],
            "instruction_following": scores["instruction_following"],
            "coherence": scores["coherence"],
            "speed_score": scores["speed_score"],
            "latency_ms": latency_ms,
            "tokens_generated": tokens_generated,
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "response_preview": content[:500],
        }

        # Upsert into model_capabilities collection
        update_data = {
            f"{role}.tested": True,
            f"{role}.auto_score": round(overall, 1),
            f"{role}.auto_pass": auto_pass,
            f"{role}.test_results": test_results,
            "provider": model_id.split("/")[0] if "/" in model_id else "openai",
            "model_name": model_id.split("/")[-1] if "/" in model_id else model_id,
            "updated_at": datetime.now(timezone.utc),
        }

        # Set approved based on auto_pass if no admin_override exists
        existing = await self.collection.find_one({"_id": model_id})
        if existing and existing.get(role, {}).get("admin_override") is not None:
            # Admin has manually overridden - keep their decision
            update_data[f"{role}.approved"] = existing[role]["admin_override"]
        else:
            update_data[f"{role}.approved"] = auto_pass

        await self.collection.update_one(
            {"_id": model_id},
            {"$set": update_data},
            upsert=True,
        )

        return {
            "success": True,
            "model_id": model_id,
            "role": role,
            "scores": scores,
            "overall": round(overall, 1),
            "auto_pass": auto_pass,
            "pass_threshold": pass_threshold,
            "latency_ms": latency_ms,
            "tokens_generated": tokens_generated,
            "response_preview": content[:300],
        }

    async def approve_model(
        self, model_id: str, role: Literal["orchestrator", "worker"], approved: bool
    ) -> dict:
        """Admin force-approve or reject a model for a role."""
        await self.collection.update_one(
            {"_id": model_id},
            {
                "$set": {
                    f"{role}.admin_override": approved,
                    f"{role}.approved": approved,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        return {"model_id": model_id, "role": role, "approved": approved, "source": "admin_override"}

    async def get_capabilities(self) -> list:
        """Get all tested model capabilities."""
        cursor = self.collection.find({})
        results = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            results.append(doc)
        return results

    async def get_approved_models(self, role: Literal["orchestrator", "worker"]) -> list:
        """Get models approved for a specific role."""
        query = {f"{role}.approved": True}
        cursor = self.collection.find(query)
        results = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            results.append(doc)
        return results

    async def _judge_response(
        self,
        model_id: str,
        role: str,
        prompt: str,
        response_text: str,
        latency_ms: int,
        tokens: int,
        expectation: str,
        judge_model: Optional[str] = None,
    ) -> dict:
        """Use an LLM judge to score the response."""
        import json
        from backend.core.config import settings

        # Pick judge model: provided > orchestrator > fallback
        if not judge_model:
            judge_model = f"{settings.orchestrator_provider}/{settings.orchestrator_model}"
            # Avoid self-judging
            if judge_model == model_id:
                judge_model = "gpt-4o"  # fallback to OpenAI

        judge_prompt = JUDGE_USER_TEMPLATE.format(
            role=role,
            expectation=expectation,
            latency_ms=latency_ms,
            tokens=tokens,
            prompt=prompt[:500],
            response=response_text[:1000],
        )

        try:
            judge_response = await litellm.acompletion(
                model=judge_model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": judge_prompt},
                ],
                max_tokens=100,
                temperature=0.1,
                timeout=30,
            )

            judge_content = judge_response.choices[0].message.content or ""
            # Parse JSON from response (handle markdown wrapping)
            judge_content = judge_content.strip()
            if judge_content.startswith("```"):
                judge_content = judge_content.split("\n", 1)[-1].rsplit("```", 1)[0]

            scores = json.loads(judge_content)
            # Validate all keys present and in range
            for key in ["reasoning", "instruction_following", "coherence", "speed_score"]:
                if key not in scores or not isinstance(scores[key], (int, float)):
                    scores[key] = 5  # default mid-score
                scores[key] = max(1, min(10, int(scores[key])))

            return scores

        except Exception as e:
            logger.warning(f"Judge scoring failed for {model_id}: {e}. Using heuristic scoring.")
            return self._heuristic_score(response_text, latency_ms, role)

    def _heuristic_score(self, text: str, latency_ms: int, role: str) -> dict:
        """Fallback heuristic scoring when judge is unavailable."""
        # Coherence: check for repetition, length, structure
        coherence = 7
        if self._is_repetitive(text):
            coherence = 1
        elif len(text) < 50:
            coherence = 3
        elif len(text) > 2000:
            coherence = 5

        # Instruction following: check for expected structure
        instruction = 5
        if role == "orchestrator":
            markers = ["INTENT", "SEARCH", "EXPECTED", "FOLLOW"]
            found = sum(1 for m in markers if m in text.upper())
            instruction = min(10, found * 2 + 2)
        else:
            sentences = text.count(".") + text.count("!") + text.count("?")
            if 2 <= sentences <= 4:
                instruction = 8
            elif sentences == 1 or sentences == 5:
                instruction = 6

        # Speed score
        if latency_ms < 5000:
            speed = 10
        elif latency_ms < 10000:
            speed = 9
        elif latency_ms < 20000:
            speed = 8
        elif latency_ms < 40000:
            speed = 7
        elif latency_ms < 60000:
            speed = 6
        elif latency_ms < 90000:
            speed = 5
        elif latency_ms < 120000:
            speed = 4
        else:
            speed = 3

        return {
            "reasoning": min(coherence, instruction),  # approximate
            "instruction_following": instruction,
            "coherence": coherence,
            "speed_score": speed,
        }

    @staticmethod
    def _is_repetitive(text: str) -> bool:
        """Detect if text is mostly repetitive patterns."""
        if len(text) < 100:
            return False
        for i in range(0, min(len(text), 200), 10):
            pattern = text[i:i + 10]
            if pattern.strip() and text.count(pattern) > 5:
                return True
        if len(text) > 500 and len(set(text[:500])) < 20:
            return True
        return False
