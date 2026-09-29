from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "cases" / "cases.json"
CAPABILITIES_PATH = ROOT / "cases" / "capabilities.json"
OWNERS_PATH = ROOT / "cases" / "owners.json"
OWNER_ENV = "PRLAB_OWNER"


class OwnerError(ValueError):
    """No GitHub owner could be resolved for a live run."""


@dataclass(frozen=True)
class Claim:
    id: str
    must_assert: str
    tokens: tuple[str, ...] = ()
    # What the review must state, split so the judge rules on each piece and
    # cannot pass a claim on the strength of one half. Empty: the whole claim.
    parts: tuple[str, ...] = ()
    # Related findings that do not meet the claim (from the case's "tests" notes).
    not_enough: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    asks: str


@dataclass(frozen=True)
class Case:
    id: str
    intent: str
    tests: str
    capabilities: tuple[Capability, ...]
    github_repo: str
    local: str
    patch: str
    branch: str
    title: str
    body: str
    claims: tuple[Claim, ...]
    # Set for a monorepo owner ("org/repo"): the case's service folder in that repo.
    subdir: str = ""

    @property
    def capability(self) -> Capability:
        return self.capabilities[0]


def _parse_claim(row: dict) -> Claim:
    return Claim(
        id=row["id"],
        must_assert=row["must_assert"],
        tokens=tuple(row.get("tokens") or ()),
        parts=tuple(str(part).strip() for part in row.get("parts") or () if str(part).strip()),
        not_enough=tuple(str(item).strip() for item in row.get("not_enough") or () if str(item).strip()),
    )


def load_capabilities(path: Path | None = None) -> dict[str, Capability]:
    raw = json.loads((path or CAPABILITIES_PATH).read_text())
    catalog: dict[str, Capability] = {}
    for cap_id, spec in raw.items():
        name = (spec.get("name") or "").strip()
        asks = (spec.get("asks") or "").strip()
        if not name or not asks:
            raise ValueError(f"capability {cap_id} needs name and asks")
        catalog[cap_id] = Capability(id=cap_id, name=name, asks=asks)
    if not catalog:
        raise ValueError("capabilities catalog is empty")
    return catalog


def capability_ids_for(row: dict) -> tuple[str, ...]:
    if "capabilities" in row:
        values = row["capabilities"]
        if isinstance(values, str):
            return (values,)
        return tuple(str(item) for item in values)
    value = row.get("capability")
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, dict) and value.get("id"):
        return (str(value["id"]),)
    return ()


def resolve_capabilities(
    ids: tuple[str, ...],
    catalog: dict[str, Capability],
    *,
    case_id: str,
) -> tuple[Capability, ...]:
    if not ids:
        raise ValueError(f"{case_id} needs capability or capabilities")
    resolved = []
    for cap_id in ids:
        try:
            resolved.append(catalog[cap_id])
        except KeyError as exc:
            known = ", ".join(sorted(catalog))
            raise ValueError(f"{case_id} unknown capability {cap_id!r}. known: {known}") from exc
    return tuple(resolved)


def retarget(repo: str, owner: str | None) -> str:
    """Point an owner/name repo at another owner. Each vendor runs in its own org."""
    if not owner:
        return repo
    return f"{owner}/{repo.split('/', 1)[-1]}"


def load_owners(path: Path | None = None) -> dict[str, str]:
    """Tool name -> GitHub owner that holds that tool's copy of the product repos."""
    raw = json.loads((path or OWNERS_PATH).read_text())
    return {str(tool): str(owner).strip() for tool, owner in raw.items() if str(owner).strip()}


def resolve_owner(tool: str | None, owner: str | None = None, path: Path | None = None) -> str:
    """Owner for a live run: explicit ``owner``, then $PRLAB_OWNER, then cases/owners.json.

    Never falls back silently. A live run against the wrong owner reads another
    vendor's PRs (a bogus report) or, for cleanup, closes them.
    """
    explicit = (owner or os.environ.get(OWNER_ENV) or "").strip()
    if explicit:
        return explicit
    owners = load_owners(path)
    if tool and tool in owners:
        return owners[tool]
    known = ", ".join(f"{name}={org}" for name, org in sorted(owners.items()))
    what = f"tool {tool!r}" if tool else "a run with no --tool"
    raise OwnerError(
        f"no GitHub owner for {what}. Pass --owner, set ${OWNER_ENV}, "
        f"or add it to {OWNERS_PATH.name} (known: {known})"
    )


def owner_mismatch(tool: str | None, owner: str, path: Path | None = None) -> str:
    """Warning text when ``owner`` differs from the tool's configured owner, else ""."""
    configured = load_owners(path).get(tool or "")
    if configured and configured != owner:
        return f"warning: {tool} is configured for {configured} but this run targets {owner}"
    return ""


def load_cases(path: Path | None = None, owner: str | None = None) -> list[Case]:
    """Load cases. ``owner`` moves every PR repo to that GitHub owner.

    ``owner=None`` returns the repos exactly as written in cases.json. That is for
    data checks only. Live runs pass ``resolve_owner(...)``.
    """
    catalog = load_capabilities()
    raw = json.loads((path or CASES_PATH).read_text())
    cases = []
    for row in raw:
        claims = tuple(_parse_claim(item) for item in row["claims"])
        case_id = row["id"]
        if not case_id.startswith("test-"):
            raise ValueError(f"{case_id} must start with 'test-'")
        if not claims:
            raise ValueError(f"{case_id} has no claims")
        intent = (row.get("intent") or "").strip()
        tests = (row.get("tests") or "").strip()
        if not intent or not tests:
            raise ValueError(f"{case_id} needs intent and tests")
        capabilities = resolve_capabilities(capability_ids_for(row), catalog, case_id=case_id)
        # An owner of the form "org/repo" is a monorepo holding every service as a
        # folder: the PR goes to that repo, and the patch applies under the folder.
        mono = bool(owner) and "/" in (owner or "")
        cases.append(
            Case(
                id=case_id,
                intent=intent,
                tests=tests,
                capabilities=capabilities,
                github_repo=owner if mono else retarget(row["github_repo"], owner),
                local=owner.split("/", 1)[1] if mono else row["local"],
                patch=row["patch"],
                branch=row["branch"],
                title=row["title"],
                body=row["body"],
                claims=claims,
                subdir=row["local"] if mono else "",
            )
        )
    return cases


def case_diff(case: Case) -> str:
    """The code change the eval PR carries, as a unified diff."""
    return (ROOT / case.patch).read_text()
