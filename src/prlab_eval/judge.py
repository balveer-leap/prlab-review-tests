from __future__ import annotations

import http.client
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

SYSTEM_PROMPT = """You check whether a PR review makes one specific claim about a code change. The claim is split into numbered parts. Decide each part on its own.

Return JSON only:
{"parts": [{"part": 1, "met": true|false, "quote": "exact text copied from the review", "why": "one sentence"}], "reason": "one sentence overall"}
Give one entry per part, in order.

Read the whole review first. It may hold several comments; any one of them can meet a part.

A part is met when:
- The review states that part's mechanism or effect. Judge meaning, not wording: a paraphrase, or a more specific statement of the same thing, counts.
- The review refers to the same thing by another name: a service named by its repository, file or function (src/post.js for social's postFor), or "copied into the stored model" for "persisted".
- The part lists alternatives ("or", "such as", "for example", "any one"): one alternative is enough.
- Details the part does not ask for are never required.

A part is not met when:
- The review only shares vocabulary with it. A review describing a different defect that involves the same field, function or file does not meet the part. Example: the part says "an omitted flag is treated as true"; the review says "an explicit true is kept when kind is none". That is a different defect, so the part is not met.
- The review reports a different consequence of the same change. Example: the part says "protocol fields reach clients"; the review says "a client rejects the response because of the new key". That is a different consequence, so the part is not met.
- The review only says that documentation, a comment or a type annotation is now stale or wrong. That does not meet a part about what the code does, even when the review mentions the behaviour in passing.
- You would have to fill a gap. If the review states one part, do not assume it also states another: each part needs its own support in the review.
- The review states only something listed under "Not enough on its own".
- The part requires naming a specific service, repository, component or consumer and the review does not name it. The review must name it (for example "the live-gateway", "social's postFor", "the stats ledger"). Generic wording such as "any consumer", "clients", "callers" or "downstream services" does not count.

Quotes:
- For a met part, copy the review passage that states it, verbatim: one passage, or two joined with " ... ". Markdown formatting (backticks, bold, links) may be dropped.
- For a part that is not met, use "".
- Do not invent bugs the review did not state.
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
    # One {"part", "met", "quote", "why"} per claim part, as the judge ruled.
    parts: tuple[dict, ...] = ()
    # Panel judge only: each member's {"judge", "passed", "reason"}, tie-breaker last.
    votes: tuple[dict, ...] = ()


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
LINE_ANCHOR = re.compile(r"(?<=\w):\d+(?:\s*-\s*\d+)?\b")
ELLIPSIS = re.compile(r"\s*(?:\.\.\.|\u2026)\s*")
# Shortest verbatim span an elided quote must still carry; below this a
# "fragment ... fragment" quote can match almost any long review.
MIN_ELIDED_QUOTE = 40


def normalize_for_quote(text: str) -> str:
    folded = visible_review_text(text)
    for src, dest in _QUOTE_PUNCT.items():
        folded = folded.replace(src, dest)
    # Judges copy what a comment says, not its markdown: "[engine](url)" reads
    # as "engine" and "**not**" as "not".
    folded = MARKDOWN_LINK.sub(r"\1", folded)
    folded = re.sub(r"[`*~]", "", folded)
    # Judges drop line anchors: "ledger.py:21-27" is quoted as "ledger.py".
    folded = LINE_ANCHOR.sub("", folded)
    folded = re.sub(r"[_-]+", " ", folded)
    return re.sub(r"\s+", " ", folded).lower().strip()


# A tokens-per-day limit asks for waits of ten minutes or more. Waiting less
# spends every retry inside the window and aborts the run, losing every case
# already judged.
MAX_RETRY_WAIT = 900


class RateLimited(Exception):
    def __init__(self, detail: str, wait_seconds: float) -> None:
        super().__init__(detail)
        self.wait_seconds = wait_seconds


def retry_wait(header: str | None, detail: str) -> float:
    """Seconds to wait from Retry-After or "try again in 12.3s" / "1m2s"; default 20."""
    try:
        if header:
            return min(float(header) + 1, MAX_RETRY_WAIT)
    except ValueError:
        pass
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", detail or "")
    if match:
        return min(int(match.group(1) or 0) * 60 + float(match.group(2)) + 1, MAX_RETRY_WAIT)
    return 20.0


def quote_is_from_review(quote: str, review_text: str) -> bool:
    if not quote.strip():
        return False
    haystack = re.sub(r"\s+", " ", review_text).lower()
    needle = re.sub(r"\s+", " ", quote).strip().lower()
    if needle in haystack:
        return True
    folded_review = normalize_for_quote(review_text)
    folded_quote = normalize_for_quote(quote)
    if folded_quote and folded_quote in folded_review:
        return True
    # A judge shortening a long sentence writes "A ... B". Every fragment must
    # still appear verbatim, in order.
    fragments = [part for part in ELLIPSIS.split(quote) if part.strip()]
    if len(fragments) >= 2:
        folded_parts = [normalize_for_quote(part) for part in fragments]
        if sum(len(part) for part in folded_parts) >= MIN_ELIDED_QUOTE and _in_order(folded_parts, folded_review):
            return True
    return _pieces_in_review(quote, review_text)


def _in_order(parts: list[str], text: str) -> bool:
    at = 0
    for part in parts:
        found = text.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


def _words(text: str) -> str:
    """Lowercase words only: quote marks, backticks, braces and path punctuation drop out."""
    return " ".join(re.findall(r"[a-z0-9]+", normalize_for_quote(text)))


SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


# A piece this short ("it fails") is found in almost any review, so it proves nothing.
MIN_PIECE_WORDS = 3


def _pieces_in_review(quote: str, review_text: str, coverage: float = 0.8) -> bool:
    """Accept a quote stitched from review sentences, e.g. C then A with B skipped.

    The quote is cut at sentence ends and ellipses. Compared word by word, most
    of it (``coverage`` of its words) must be pieces that appear verbatim in the
    review, in any order: judges often cite the consequence before its cause,
    and a reordered quote is still the reviewer's own words. Invented text fails.
    """
    review = _words(review_text)
    pieces = [_words(part) for part in SENTENCE.split(quote) for part in ELLIPSIS.split(part)]
    pieces = [part for part in pieces if part]
    total = sum(len(part) for part in pieces)
    if total < MIN_ELIDED_QUOTE:
        return False
    matched = sum(
        len(part) for part in pieces
        if len(part.split()) >= MIN_PIECE_WORDS and f" {part} " in f" {review} "
    )
    return matched >= coverage * total


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


def claim_parts(claim: Claim) -> tuple[str, ...]:
    return claim.parts or (claim.must_assert,)


def claim_prompt(claim: Claim, review: str) -> str:
    parts = "\n".join(f"{i}. {part}" for i, part in enumerate(claim_parts(claim), start=1))
    text = f"Claim:\n{claim.must_assert}\n\nParts (each must be met):\n{parts}\n"
    if claim.not_enough:
        text += "\nNot enough on its own:\n" + "\n".join(f"- {item}" for item in claim.not_enough) + "\n"
    return text + f"\nPR review:\n{review}"


def parts_verdict(claim: Claim, review_text: str, parsed: dict) -> ClaimVerdict:
    """Pass only if the judge met every part and each met part's quote is in the review.

    A part the judge skipped counts as not met. Each quote is checked on its
    own, so one real passage cannot carry a part the review never states.
    """
    visible = visible_review_text(review_text)
    rows = parsed.get("parts") if isinstance(parsed, dict) else None
    if rows is None and isinstance(parsed, dict) and "asserts" in parsed and len(claim_parts(claim)) == 1:
        # The single-verdict shape some models still answer with: one part.
        rows = [{"part": 1, "met": parsed.get("asserts"), "quote": parsed.get("quote"), "why": parsed.get("reason")}]
    by_number: dict[int, dict] = {}
    for position, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("part") or position)
        except (TypeError, ValueError):
            number = position
        by_number.setdefault(number, row)
    ruled: list[dict] = []
    for number, text in enumerate(claim_parts(claim), start=1):
        row = by_number.get(number) or {}
        met = bool(row.get("met"))
        quote = str(row.get("quote") or "").strip()
        why = str(row.get("why") or "").strip() or ("judge skipped this part" if not row else "")
        if met and not quote_is_from_review(quote, visible):
            met, why = False, QUOTE_NOT_FOUND
        ruled.append({"part": text, "met": met, "quote": quote if met else "", "why": why})
    passed = bool(visible) and all(part["met"] for part in ruled)
    failed = next((i for i, part in enumerate(ruled, start=1) if not part["met"]), None)
    if passed:
        reason = str(parsed.get("reason") or "").strip() if isinstance(parsed, dict) else ""
        reason = reason or "every part met"
    elif failed is not None and len(ruled) > 1:
        reason = f"part {failed} not met: {ruled[failed - 1]['why']}"
    else:
        reason = ruled[0]["why"] if ruled else "no parts"
    tokens = match_findings(visible, claim.tokens)
    return ClaimVerdict(
        claim_id=claim.id,
        must_assert=claim.must_assert,
        passed=passed,
        # Each part's quote was checked above; the joined quote is for reading.
        quote=" ... ".join(part["quote"] for part in ruled if part["quote"]),
        reason=reason if visible else "no review",
        tokens_expected=claim.tokens,
        tokens_matched=tokens.matched,
        tokens_missing=tokens.missing,
        parts=tuple(ruled),
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
    # A Bifrost AI gateway (OpenAI-compatible, served under /openai/v1). The host
    # comes from BIFROST_BASE_URL, so no deployment URL is baked in here.
    # Claude Haiku 4.5: light, and careful about quoting the review verbatim.
    "bifrost": {
        "base_url": "",
        "base_url_env": "BIFROST_BASE_URL",
        "base_path": "/openai/v1",
        "default_model": "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "env": ("BIFROST_API_KEY",),
        "json_mode": False,
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
    if chosen == "panel":
        # Every member goes through the Bifrost gateway; this checks its env.
        base = resolve_judge_config(provider="bifrost")
        spec = model or os.environ.get("PRLAB_JUDGE_MODEL") or PANEL_DEFAULT
        panel_models(spec)
        return JudgeConfig(provider="panel", api_key=base.api_key, model=spec, base_url=base.base_url, json_mode=False)
    if chosen and chosen not in PROVIDERS:
        known = ", ".join(sorted([*PROVIDERS, "panel"]))
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

    base_url = os.environ.get("PRLAB_JUDGE_BASE_URL") or spec["base_url"]
    if not base_url and spec.get("base_url_env"):
        host = (os.environ.get(spec["base_url_env"]) or "").strip().rstrip("/")
        if not host:
            raise JudgeConfigError(f"{chosen} needs {spec['base_url_env']} (the gateway's https:// address)")
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        base_url = host + spec.get("base_path", "")

    return JudgeConfig(
        provider=chosen,
        api_key=api_key or "local",
        model=resolved_model,
        base_url=base_url,
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
        if config.provider == "panel":
            members, tiebreak = panel_models(config.model)
            base = resolve_judge_config(provider="bifrost")
            build = lambda m: cls(api_key=base.api_key, model=m, base_url=base.base_url,  # noqa: E731
                                  provider="bifrost", json_mode=base.json_mode)
            return PanelJudge([build(m) for m in members], build(tiebreak))  # type: ignore[return-value]
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
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:400]
            if exc.code == 429:
                raise RateLimited(detail, retry_wait(exc.headers.get("retry-after"), detail)) from exc
            if exc.code in (500, 502, 503, 504) and "credentials_exhausted" not in detail:
                # A gateway or upstream hiccup, not a verdict: retry like a rate limit.
                raise RateLimited(detail, 10.0) from exc
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
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            # A dropped or stalled connection is not a verdict: retry like a rate limit.
            raise RateLimited(f"{type(exc).__name__}: {exc}", 10.0) from exc
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
        prompt = claim_prompt(claim, visible[:12000])
        for attempt in range(3):
            content = self._ask_json(SYSTEM_PROMPT, prompt)
            try:
                parsed = parse_judge_payload(content)
            except (json.JSONDecodeError, ValueError):
                continue  # a malformed reply is not a verdict: ask again
            if isinstance(parsed, dict):
                return parts_verdict(claim, review_text, parsed)
        return verdict_for(claim, review_text, asserts=False, quote="",
                           reason="judge reply was not valid JSON after 3 attempts")

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


# Two judges from one gateway, a third from another model family to settle a split.
# Haiku alone passed claims on shared vocabulary; Sonnet alone failed paraphrases.
PANEL_DEFAULT = (
    "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0,"
    "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0;"
    "bedrock/deepseek.v3.2"
)


def panel_models(spec: str) -> tuple[list[str], str]:
    """"A,B;C" -> ([A, B], C): A and B vote, C breaks a tie."""
    voters, _, tiebreak = spec.partition(";")
    members = [m.strip() for m in voters.split(",") if m.strip()]
    if len(members) != 2 or not tiebreak.strip():
        raise JudgeConfigError(f"panel spec {spec!r}: use 'MODEL_A,MODEL_B;TIEBREAK_MODEL'")
    return members, tiebreak.strip()


def short_model(model: str) -> str:
    """bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0 -> claude-haiku-4-5."""
    name = model.rsplit("/", 1)[-1].split(":", 1)[0]
    name = re.sub(r"^(?:us|eu|apac)\.", "", name)
    name = re.sub(r"^anthropic\.", "", name)
    return re.sub(r"-\d{8}(?:-v\d+)?$", "", name)


def panel_label(spec: str) -> str:
    """Readable name of a panel spec: claude-haiku-4-5 + claude-sonnet-4-5, tiebreak deepseek.v3.2."""
    members, tiebreak = panel_models(spec)
    return f"{' + '.join(short_model(m) for m in members)}, tiebreak {short_model(tiebreak)}"


class PanelJudge:
    """Two judges vote on each claim; when they split, a third decides.

    Every member rules part by part (SYSTEM_PROMPT). The verdict keeps each
    vote, so a report shows who passed what. Comments are rated by the second
    member, the stronger judge in the default panel.
    """

    provider = "panel"

    def __init__(self, members: list[LlmJudge], tiebreak: LlmJudge) -> None:
        self.members = members
        self.tiebreak = tiebreak
        self.base_url = members[0].base_url
        # The spec itself, as resolve_judge_config records it for a pytest run,
        # so both kinds of report carry the same judge label.
        self.model = ",".join(m.model for m in members) + f";{tiebreak.model}"

    def ping(self) -> str:
        return ", ".join(judge.ping() for judge in [*self.members, self.tiebreak])

    def judge(self, claim: Claim, review_text: str) -> ClaimVerdict:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(self.members)) as pool:
            verdicts = list(pool.map(lambda judge: judge.judge(claim, review_text), self.members))
        votes = [
            {"judge": short_model(judge.model), "passed": v.passed, "reason": v.reason}
            for judge, v in zip(self.members, verdicts)
        ]
        if len({v.passed for v in verdicts}) == 1:
            chosen = verdicts[-1]
            reason = f"panel agrees ({'pass' if chosen.passed else 'fail'}): {chosen.reason}"
        else:
            chosen = self.tiebreak.judge(claim, review_text)
            votes.append({"judge": short_model(self.tiebreak.model), "passed": chosen.passed,
                          "reason": chosen.reason, "tiebreak": True})
            split = ", ".join(f"{v['judge']} {'pass' if v['passed'] else 'fail'}" for v in votes[:-1])
            reason = f"panel split ({split}); {votes[-1]['judge']} decides {'pass' if chosen.passed else 'fail'}: {chosen.reason}"
        return ClaimVerdict(**{**chosen.__dict__, "reason": reason, "votes": tuple(votes)})

    def rate_comments(self, diff: str, comments: list[str]) -> list[CommentRating]:
        return self.members[-1].rate_comments(diff, comments)


def precheck_judge(judge: LlmJudge) -> str:
    judge.ping()
    return f"judge ready: {judge.provider} / {judge.model} ({judge.base_url})"
