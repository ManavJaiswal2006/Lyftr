import hashlib
import json
import logging
from dataclasses import dataclass

import litellm
from litellm.caching.in_memory_cache import InMemoryCache
from litellm.exceptions import APIError, RateLimitError
from openai import APIError as ProviderAPIError

litellm.telemetry = False
litellm.suppress_debug_info = True
logger = logging.getLogger(__name__)
SIMULATED_STATUSES = {"429", "500", "502", "503"}


class ProvidersUnavailable(Exception):
    pass


def simulation_plan(header: str | None, enabled: bool) -> dict[str, int]:
    plan = {}
    if enabled and header:
        for pair in header.split(","):
            key_id, _, status = pair.strip().partition("=")
            if status in SIMULATED_STATUSES:
                plan[key_id] = int(status)
    return plan


@dataclass(frozen=True)
class Answer:
    reply: str
    input_tokens: int
    output_tokens: int
    model: str


class Gateway:
    def __init__(self, model: str, keys: tuple[str, ...], timeout: float, api_base: str | None):
        self.model = model
        self.keys = keys
        self.timeout = timeout
        self.api_base = api_base
        self.cache = InMemoryCache(max_size_in_memory=1024, default_ttl=3600)

    async def complete(self, messages, simulated):
        identity = json.dumps([self.model, messages], sort_keys=True, ensure_ascii=False)
        cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        # A simulation exercises the provider path even if this prompt is already cached.
        if not simulated:
            cached = self.cache.get_cache(cache_key)
            if cached is not None:
                return Answer(**cached), None, 0

        for index, key in enumerate(self.keys, start=1):
            key_id = f"key_{index}"
            status = simulated.get(key_id, simulated.get("*"))
            try:
                answer = await self.request(messages, key, status)
            except (ProviderAPIError, ValueError, TimeoutError) as error:
                if status is None:
                    logger.warning(
                        "Provider attempt failed for %s (%s, HTTP %s)",
                        key_id,
                        type(error).__name__,
                        getattr(error, "status_code", None),
                    )
                continue

            self.cache.set_cache(cache_key, {
                "reply": answer.reply,
                "input_tokens": answer.input_tokens,
                "output_tokens": answer.output_tokens,
                "model": answer.model,
            })
            return answer, key_id, index - 1

        raise ProvidersUnavailable

    async def request(self, messages, key, status):
        if status == 429:
            raise RateLimitError("Simulated rate limit", "simulation", self.model)
        if status:
            raise APIError(status, "Simulated provider error", "simulation", self.model)

        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            api_key=key,
            api_base=self.api_base,
            timeout=self.timeout,
            num_retries=0,
        )
        reply = response.choices[0].message.content if response.choices else None
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Provider returned an empty completion")

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        if input_tokens is None:
            input_tokens = litellm.token_counter(model=self.model, messages=messages)
        if output_tokens is None:
            output_tokens = litellm.token_counter(model=self.model, text=reply)
        return Answer(reply, input_tokens, output_tokens, self.model)
