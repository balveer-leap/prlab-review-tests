from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import unescape
from typing import Protocol

from prlab_eval.cases import Claim
from prlab_eval.scoring import match_findings

SYSTEM_PROMPT = """You score whether a PR review asserts a specific claim.

Return JSON only:
{"asserts": true|false, "quote": "exact substring copied from the review", "reason": "one sentence"}

Rules:
- asserts is true only if the review states the claim's meaning. Related field names or the word "wicket" alone are not enough.
- If the review discusses a different problem, asserts is false.
- quote must be copied from the review. Markdown formatting (backticks, bold, links) and extra spaces may be dropped.
- Use "" if nothing supports the decision.
- Do not invent bugs that the review did not state.
"""

# The rater sees the diff and the comments, never the planted claim: a comment that
# reports some other real bug in the change is useful review, not noise.
COMMENT_PROMPT = """You audit the comments an automated reviewer posted on a pull request.

You get the pull request diff and the reviewer's comments, numbered. For each comment, decide whether it reports a genuine defect in this change.

A defect is a concrete problem the diff introduces that would cause wrong behaviour at runtime: incorrect results, a broken or leaked data contract, a regression for a caller or downstream consumer, data loss, or a security issue. The comment must be right about what the diff does.

Not a defect:
- style, naming, formatting, typos, docs, or code comments
- requests for more tests or logging, unless the comment shows a concrete bug they would hide
- summaries or restatements of the change, praise, or questions that state no problem
- claims the diff contradicts, or speculation the diff does not support
- a repeat of a defect an earlier comment already reported

A comment may describe an impact in another service that the diff does not show. Judge it by whether that impact follows from the diff; do not reject it only because the other service is not in the diff.

Return JSON only:
{"comments": [{"index": 1, "defect": true|false, "reason": "one sentence"}]}
Rate every comment exactly once.
"""


@dataclass(frozen=True)
class ClaimVerdict:
    claim_id: str
    must_assert: str
    passed: bool
    quote: str
    reason: str
    tokens_expected: tuple[str, ...]
    tokens_matched: tuple[str, ...]
    tokens_missing: tuple[str, ...]


@dataclass(frozen=True)
class CommentRating:
    """Whether one posted comment reports a genuine defect in the diff (1-based index)."""

    index: int
    defect: bool
    reason: str


class ClaimJudge(Protocol):
    def judge(self, claim: Claim, review_text: str) -> ClaimVerdict: ...


class CommentRater(Protocol):
    def rate_comments(self, diff: str, comments: list[str]) -> list[CommentRating]: ...


class JudgeConfigError(RuntimeError):
    pass


_QUOTE_PUNCT = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2013": "-",
    "\u2014": "-",
}


def visible_review_text(text: str) -> str:
    stripped = re.sub(r"<details[\s\S]*?</details>", "\n", text or "", flags=re.I)
    stripped = re.sub(r"<script[\s\S]*?</script>", "\n", stripped, flags=re.I)
    stripped = re.sub(r"<style[\s\S]*?</style>", "\n", stripped, flags=re.I)
    stripped = re.sub(r"</?(p|div|h[1-6]|li|tr|br)[^>]*>", "\n", stripped, flags=re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    stripped = unescape(stripped)
    stripped = re.sub(r"[ \t]+", " ", stripped)
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


QUOTE_NOT_FOUND = "judge quote was not found in the review"
MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def normalize_for_quote(text: str) -> str:
    folded = visible_review_text(text)
    for src, dest in _QUOTE_PUNCT.items():
        folded = folded.replace(src, dest)
    # Judges copy what a comment says, not its markdown: "[engine](url)" reads
    # as "engine" and "**not**" as "not".
    folded = MARKDOWN_LINK.sub(r"\1", folded)
    folded = re.sub(r"[`*~]", "", folded)
    folded = re.sub(r"[_-]+", " ", folded)
    return re.sub(r"\s+", " ", folded).lower().strip()


class RateLimited(Exception):
    def __init__(self, detail: str, wait_seconds: float) -> None:
        super().__init__(detail)
        self.wait_seconds = wait_seconds


def retry_wait(header: str | None, detail: str) -> float:
    """Seconds to wait from Retry-After or "try again in 12.3s" / "1m2s"; default 20."""
    try:
        if header:
            return min(float(header) + 1, 120)
    except ValueError:
        pass
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", detail or "")
    if match:
        return min(int(match.group(1) or 0) * 60 + float(match.group(2)) + 1, 120)
    return 20.0


def quote_is_from_review(quote: str, review_text: str) -> bool:
    if not quote.strip():
        return False
    haystack = re.sub(r"\s+", " ", review_text).lower()
    needle = re.sub(r"\s+", " ", quote).strip().lower()
    if needle in haystack:
        return True
    folded_quote = normalize_for_quote(quote)
    return bool(folded_quote) and folded_quote in normalize_for_quote(review_text)


def parse_judge_payload(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def parse_comment_ratings(raw: str, count: int) -> list[CommentRating]:
    """One rating per comment, in order. A comment the judge skipped is not a defect."""
    parsed = parse_judge_payload(raw)
    rows = parsed.get("comments") if isinstance(parsed, dict) else parsed
    by_index: dict[int, CommentRating] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= count and index not in by_index:
            by_index[index] = CommentRating(
                index=index,
                defect=bool(row.get("defect")),
                reason=str(row.get("reason") or "").strip(),
            )
    return [
        by_index.get(i) or CommentRating(index=i, defect=False, reason="judge did not rate this comment")
        for i in range(1, count + 1)
    ]


def verdict_for(
    claim: Claim,
    review_text: str,
    *,
    asserts: bool,
    quote: str,
    reason: str,
) -> ClaimVerdict:
    visible = visible_review_text(review_text)
    tokens = match_findings(visible, claim.tokens) if claim.tokens else match_findings(visible, ())
    if not visible:
        return ClaimVerdict(
            claim_id=claim.id,
            must_assert=claim.must_assert,
            passed=False,
            quote="",
            reason="no review",
            tokens_expected=claim.tokens,
            tokens_matched=(),
            tokens_missing=claim.tokens,
        )
    if asserts and not quote_is_from_review(quote, visible):
        asserts = False
        reason = QUOTE_NOT_FOUND
    return ClaimVerdict(
        claim_id=claim.id,
        must_assert=claim.must_assert,
        passed=asserts,
        quote=quote.strip(),
        reason=reason.strip(),
        tokens_expected=claim.tokens,
        tokens_matched=tokens.matched,
        tokens_missing=tokens.missing,
    )


class CallableJudge:
    """Deterministic judge for unit tests."""

    def __init__(self, decide, rate=None):
        self._decide = decide
        self._rate = rate
        if rate is not None:
            self.rate_comments = self._rate_comments

    def judge(self, claim: Claim, review_text: str) -> ClaimVerdict:
        asserts, quote, reason = self._decide(claim, review_text)
        return verdict_for(claim, review_text, asserts=asserts, quote=quote, reason=reason)

    def _rate_comments(self, diff: str, comments: list[str]) -> list[CommentRating]:
        return [
            CommentRating(index=i, defect=bool(self._rate(diff, text)), reason="test rater")
            for i, text in enumerate(comments, start=1)
        ]


class TokenJudge:
    """No-LLM judge: a claim passes if every token pattern appears in the review."""

    name = "fast"

    def judge(self, claim: Claim, review_text: str) -> ClaimVerdict:
        visible = visible_review_text(review_text)
        if not claim.tokens:
            return verdict_for(
                claim,
                review_text,
                asserts=False,
                quote="",
                reason="fast: claim has no tokens",
            )
        tokens = match_findings(visible, claim.tokens)
        if not tokens.passed:
            missed = ", ".join(tokens.missing)
            return verdict_for(
                claim,
                review_text,
                asserts=False,
                quote="",
                reason=f"fast: missing tokens {missed}",
            )
        quote = visible[:200] if visible else ""
        return verdict_for(
            claim,
            review_text,
            asserts=True,
            quote=quote,
            reason="fast: all claim tokens present",
        )


@dataclass(frozen=True)
class JudgeConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    json_mode: bool = True


GROQ_MODELS = {
    "gpt-oss": "openai/gpt-oss-120b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "openai/gpt-oss-120b": "openai/gpt-oss-120b",
    "qwen": "qwen/qwen3.8-27b",
    "qwen3.8": "qwen/qwen3.8-27b",
    "qwen3.8-27b": "qwen/qwen3.8-27b",
    "qwen/qwen3.8-27b": "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile": "llama-3.3-70b-versatile",
}

PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "env": ("GROQ_API_KEY",),
        "json_mode": True,
        "aliases": GROQ_MODELS,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-3.6-flash",
        "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "json_mode": True,
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "llama3.2",
        "env": (),
        "api_key": "ollama",
        "json_mode": False,
    },
    "github": {
        "base_url": "https://models.github.ai/inference",
        "default_model": "openai/gpt-4o-mini",
        "env": ("GITHUB_TOKEN",),
        "json_mode": True,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "env": ("OPENAI_API_KEY",),
        "json_mode": True,
    },
}


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _key_for_provider(provider: str, spec: dict) -> str:
    if spec.get("api_key"):
        return str(spec["api_key"])
    key = _first_env(tuple(spec["env"]))
    generic = os.environ.get("PRLAB_JUDGE_API_KEY") or ""
    if provider == "openai":
        candidate = key or generic
        if candidate.startswith("gsk_"):
            raise JudgeConfigError(
                "OpenAI got a Groq key (gsk_...). unset PRLAB_JUDGE_API_KEY and "
                "export OPENAI_API_KEY=sk-... from https://platform.openai.com/api-keys"
            )
        return candidate
    return key or generic


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        from prlab_eval.github import run

        return run(["gh", "auth", "token"]).strip()
    except Exception:
        return ""


def _ollama_up(base_url: str) -> bool:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        urllib.request.urlopen(f"{root}/api/tags", timeout=1)
        return True
    except Exception:
        return False


def resolve_judge_config(
    provider: str | None = None,
    model: str | None = None,
) -> JudgeConfig:
    chosen = (provider or os.environ.get("PRLAB_JUDGE_PROVIDER") or "").strip().lower()
    if chosen and chosen not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise JudgeConfigError(f"unknown judge provider {chosen!r}. try: {known}")

    if not chosen:
        if _first_env(PROVIDERS["groq"]["env"]):
            chosen = "groq"
        elif _first_env(PROVIDERS["gemini"]["env"]):
            chosen = "gemini"
        elif _ollama_up(os.environ.get("PRLAB_JUDGE_BASE_URL") or PROVIDERS["ollama"]["base_url"]):
            chosen = "ollama"
        elif _github_token():
            chosen = "github"
        elif _first_env(PROVIDERS["openai"]["env"]):
            chosen = "openai"
        else:
            raise JudgeConfigError(
                "no judge configured. free options:\n"
                "  Groq:   export GROQ_API_KEY=...   # console.groq.com, no paid OpenAI key\n"
                "  Gemini: export GEMINI_API_KEY=... # aistudio.google.com\n"
                "  Ollama: install ollama and run `ollama pull llama3.2`\n"
                "  GitHub: gh auth login, then --judge-provider github"
            )

    spec = PROVIDERS[chosen]
    api_key = _key_for_provider(chosen, spec)
    resolved_model = model or os.environ.get("PRLAB_JUDGE_MODEL") or spec["default_model"]
    aliases = spec.get("aliases") or {}
    resolved_model = aliases.get(resolved_model, resolved_model)
    if chosen == "github" and not api_key:
        api_key = _github_token()
    if chosen != "ollama" and not api_key:
        raise JudgeConfigError(f"{chosen} needs one of: {', '.join(spec['env']) or 'a local server'}")

    return JudgeConfig(
        provider=chosen,
        api_key=api_key or "local",
        model=resolved_model,
        base_url=os.environ.get("PRLAB_JUDGE_BASE_URL") or spec["base_url"],
        json_mode=bool(spec["json_mode"]),
    )


class LlmJudge:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        provider: str = "openai",
        json_mode: bool = True,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.provider = provider
        self.json_mode = json_mode

    @classmethod
    def from_env(cls, model: str | None = None, provider: str | None = None) -> "LlmJudge":
        config = resolve_judge_config(provider=provider, model=model)
        return cls(
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url,
            provider=config.provider,
            json_mode=config.json_mode,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Groq sits behind Cloudflare, which rejects Python-urllib's default UA (1010).
            "User-Agent": "Mozilla/5.0 prlab-review-tests/0.1",
        }

    def _complete(self, payload: dict, retries: int = 6) -> str:
        """POST once; on HTTP 429 wait as long as the provider asks, then retry."""
        for attempt in range(retries + 1):
            try:
                return self._complete_once(payload)
            except RateLimited as exc:
                if attempt == retries:
                    raise JudgeConfigError(f"judge HTTP 429 after {retries} retries: {exc}") from exc
                time.sleep(exc.wait_seconds)
        raise AssertionError("unreachable")

    def _complete_once(self, payload: dict) -> str:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:400]
            if exc.code == 429:
                raise RateLimited(detail, retry_wait(exc.headers.get("retry-after"), detail)) from exc
            if exc.code == 403 and "1010" in detail:
                raise JudgeConfigError(
                    f"{self.provider} blocked the client (Cloudflare 1010). "
                    "Retry this run; if it persists use --judge-provider gemini or ollama."
                ) from exc
            if exc.code == 401:
                raise JudgeConfigError(
                    f"{self.provider} rejected the API key (401). "
                    f"Use a key for {self.provider} "
                    f"(OpenAI keys start with sk-, Groq keys start with gsk_)."
                ) from exc
            raise JudgeConfigError(f"judge HTTP {exc.code}: {detail}") from exc
        return body["choices"][0]["message"]["content"]

    def ping(self) -> str:
        """Fail fast: auth, model name, and network before scoring cases."""
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "Reply with the single word ok."}],
        }
        try:
            return self._complete(payload).strip()
        except JudgeConfigError as exc:
            if "HTTP 400" not in str(exc):
                raise
            payload.pop("max_tokens", None)
            return self._complete(payload).strip()

    def _ask_json(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            return self._complete(payload)
        except JudgeConfigError as exc:
            if "1010" in str(exc) or "response_format" not in payload:
                raise
            payload.pop("response_format", None)
            return self._complete(payload)

    def judge(self, claim: Claim, review_text: str) -> ClaimVerdict:
        visible = visible_review_text(review_text)
        if not visible:
            return verdict_for(claim, review_text, asserts=False, quote="", reason="no review")
        content = self._ask_json(
            SYSTEM_PROMPT,
            f"Claim:\n{claim.must_assert}\n\nPR review:\n{visible[:12000]}",
        )
        parsed = parse_judge_payload(content)
        return verdict_for(
            claim,
            review_text,
            asserts=bool(parsed.get("asserts")),
            quote=str(parsed.get("quote") or ""),
            reason=str(parsed.get("reason") or ""),
        )

    def rate_comments(self, diff: str, comments: list[str]) -> list[CommentRating]:
        if not comments:
            return []
        numbered = "\n\n".join(
            f"Comment {i}:\n{visible_review_text(text)[:4000]}"
            for i, text in enumerate(comments, start=1)
        )
        content = self._ask_json(
            COMMENT_PROMPT,
            f"Diff:\n{diff[:12000]}\n\nReviewer comments:\n{numbered}",
        )
        return parse_comment_ratings(content, len(comments))


def precheck_judge(judge: LlmJudge) -> str:
    judge.ping()
    return f"judge ready: {judge.provider} / {judge.model} ({judge.base_url})"
