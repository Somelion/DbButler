"""Thin REST clients for the two AI providers the AI Query Analysis feature
supports (see docs/FUNCTIONAL_SPEC.md's Settings section) — Google Gemini's
free-tier API, and a self-hosted OpenAI-compatible chat/completions endpoint
(e.g. Ollama) for a local model. Deliberately plain `requests` calls rather
than the google-generativeai SDK — one dependency instead of a provider-
specific one, and the local-model path needs a generic HTTP client anyway.
"""

import requests

AI_REQUEST_TIMEOUT_SECONDS = 120

GEMINI_ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AiProviderError(Exception):
    """Raised on any failure to reach or parse a response from an AI
    provider — the background task in routers/query_analysis.py catches
    this and records it as the analysis's error, rather than letting it
    propagate and crash the background task silently."""


def _call_gemini(api_key: str, model_name: str, prompt: str) -> str:
    if not api_key:
        raise AiProviderError("No Gemini API key is configured. Add one in Settings.")

    url = GEMINI_ENDPOINT_TEMPLATE.format(model=model_name)
    try:
        response = requests.post(
            url,
            params={"key": api_key},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            # Google retires model names fairly often (gemini-1.5-flash, this
            # app's original default, was retired) — a 404 here overwhelmingly
            # means the configured model name no longer exists, not that the
            # endpoint itself moved, so point the user at the fix directly
            # rather than surfacing a bare "404 Not Found".
            raise AiProviderError(
                f"Gemini model '{model_name}' was not found. It may have been retired — check "
                "https://ai.google.dev/gemini-api/docs/models for a current model name (e.g. "
                "gemini-2.5-flash) and update it in Settings."
            )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except requests.RequestException as exc:
        raise AiProviderError(f"Gemini request failed: {exc}") from exc
    except (KeyError, IndexError) as exc:
        raise AiProviderError(f"Unexpected Gemini response shape: {exc}") from exc


def _call_local(endpoint_url: str, api_key: str | None, model_name: str, prompt: str) -> str:
    if not endpoint_url:
        raise AiProviderError("No local model endpoint URL is configured. Add one in Settings.")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = requests.post(
            endpoint_url,
            headers=headers,
            json={"model": model_name, "messages": [{"role": "user", "content": prompt}]},
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except requests.RequestException as exc:
        raise AiProviderError(f"Local model request failed: {exc}") from exc
    except (KeyError, IndexError) as exc:
        raise AiProviderError(f"Unexpected local model response shape: {exc}") from exc


def call_ai(provider: str, api_key: str | None, endpoint_url: str | None, model_name: str, prompt: str) -> str:
    if provider == "gemini":
        return _call_gemini(api_key, model_name, prompt)
    if provider == "local":
        return _call_local(endpoint_url, api_key, model_name, prompt)
    raise AiProviderError(f"Unknown AI provider: {provider}")
