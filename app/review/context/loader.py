"""Load and validate the business's own files, once, at startup.

Two properties matter more than convenience here. **Fail fast**: a malformed rule is a
startup error, never a review that silently scored on a default. And **hash everything**:
the SHA of each file's bytes goes into the review's versions, which key the reuse index and
the layout cache, so a stored result can never be served across a rule change.
"""

import hashlib
import logging
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from api.consts import ErrorCode
from api.exceptions.review_exceptions import ReviewError
from review.models.context import BusinessRules, JETypeContext

logger = logging.getLogger(__name__)

CONTEXT_ROOT: Final = Path(__file__).resolve().parent
JE_TYPES_DIR: Final = CONTEXT_ROOT / "je_types"
BUSINESS_RULES_FILE: Final = CONTEXT_ROOT / "business_rules.yaml"
_MAX_REPORTED_ERRORS: Final = 5
"""Validation errors quoted in a startup failure. Beyond a handful the file needs rereading,
not a longer message."""

SHORT_SHA: Final = 8
"""Characters of a file digest carried in a version string. Enough to tell two revisions
apart in an audit trail without putting a 64-character hash on every record."""


class ContextError(ReviewError):
    """A context file is missing, malformed or inconsistent. Raised at startup only."""

    def __init__(self, message: str, details: dict[str, str] | None = None) -> None:
        super().__init__(message=message, error_code=ErrorCode.INTERNAL, details=details)


class ContextLoader:
    """Holds the validated JE type catalogue and the business rules for the process.

    Built once in the application lifespan. Everything it returns is frozen, so handing the
    same objects to concurrent reviews is safe.
    """

    def __init__(self, je_types_dir: Path | None = None, rules_file: Path | None = None) -> None:
        self._je_types_dir = je_types_dir or JE_TYPES_DIR
        self._rules_file = rules_file or BUSINESS_RULES_FILE
        self._je_types: dict[str, JETypeContext] = {}
        self._je_type_shas: dict[str, str] = {}
        self._rules: BusinessRules | None = None
        self._rules_sha: str = ""

    # ------------------------------------------------------------------ loading

    def load(self) -> None:
        """Read and validate every context file. Raises ``ContextError`` on any problem."""
        self._rules, self._rules_sha = self._load_rules()
        self._je_types, self._je_type_shas = self._load_je_types()
        logger.info(
            "context loaded je_types=%d rules_version=%d",
            len(self._je_types),
            self._rules.version,
        )

    def _load_rules(self) -> tuple[BusinessRules, str]:
        payload, sha = _read_yaml(self._rules_file)
        try:
            return BusinessRules.model_validate(payload), sha
        except ValidationError as exc:
            raise ContextError(
                f"{self._rules_file.name} is not valid business rules: {_summarise(exc)}",
                {"file": str(self._rules_file)},
            ) from exc

    def _load_je_types(self) -> tuple[dict[str, JETypeContext], dict[str, str]]:
        if not self._je_types_dir.is_dir():
            raise ContextError(f"No JE type directory at {self._je_types_dir}")

        contexts: dict[str, JETypeContext] = {}
        shas: dict[str, str] = {}
        for path in sorted(self._je_types_dir.glob("*.yaml")):
            payload, sha = _read_yaml(path)
            try:
                context = JETypeContext.model_validate(payload)
            except ValidationError as exc:
                raise ContextError(
                    f"{path.name} is not a valid JE type: {_summarise(exc)}",
                    {"file": str(path)},
                ) from exc
            if context.id in contexts:
                raise ContextError(f"Two JE type files both declare id {context.id!r}")
            contexts[context.id] = context
            shas[context.id] = sha

        if not contexts:
            raise ContextError(f"No JE type files found in {self._je_types_dir}")
        _check_precedence_is_unambiguous(contexts)
        return contexts, shas

    # ------------------------------------------------------------------ reading

    @property
    def rules(self) -> BusinessRules:
        if self._rules is None:
            raise ContextError("Context not loaded: no business rules. Call load() at startup.")
        return self._rules

    @property
    def rules_version(self) -> str:
        return f"{self.rules.version}#{self._rules_sha[:SHORT_SHA]}"

    def je_type(self, je_type_id: str) -> JETypeContext:
        context = self._je_types.get(je_type_id)
        if context is None:
            raise ContextError(
                f"Unknown JE type {je_type_id!r}", {"known": ", ".join(sorted(self._je_types))}
            )
        return context

    def je_type_version(self, je_type_id: str) -> str:
        context = self.je_type(je_type_id)
        return f"{context.id}@{context.version}#{self._je_type_shas[context.id][:SHORT_SHA]}"

    @property
    def catalogue(self) -> tuple[dict[str, Any], ...]:
        """What AI-1 is shown of every JE type, and the only thing it is shown.

        Deliberately shallow and identical in shape for every type: id, name, aliases and a
        one-line objective. Describe one type more richly than the rest and the model picks
        it every time, because it is the only one it can recognise — the classifier would be
        measuring description length rather than evidence.
        """
        return tuple(
            {
                "id": context.id,
                "name": context.name,
                "aliases": list(context.aliases),
                "objective": context.objective,
            }
            for context in sorted(self._je_types.values(), key=lambda item: item.id)
        )

    def by_precedence(self) -> tuple[JETypeContext, ...]:
        """JE types in the order the shape discriminator evaluates them.

        Order is a correctness property, not a preference: a GPAY sheet also carries a
        daily-salary column, so the CP/RTT variant has to be tested before the looser one.
        """
        with_discriminators = [c for c in self._je_types.values() if c.discriminators]
        return tuple(
            sorted(
                with_discriminators,
                key=lambda item: (item.discriminators.precedence, item.id),  # type: ignore[union-attr]
            )
        )


def _read_yaml(path: Path) -> tuple[dict[str, Any], str]:
    """Parse one YAML file and digest its bytes.

    The digest is taken over the raw bytes rather than the parsed object, so a reformat that
    changes nothing semantically still produces a new version. That is the safe direction:
    a spurious cache miss costs a review, a missed one serves a stale answer.
    """
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ContextError(f"Cannot read context file {path}", {"detail": str(exc)}) from exc

    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ContextError(f"{path.name} is not valid YAML", {"detail": str(exc)}) from exc

    if not isinstance(payload, dict):
        raise ContextError(f"{path.name} must hold a mapping at its top level")
    return payload, hashlib.sha256(content).hexdigest()


def _check_precedence_is_unambiguous(contexts: dict[str, JETypeContext]) -> None:
    """Two JE types sharing a precedence have no defined evaluation order between them.

    Which one a borderline workbook matches would then depend on dict ordering, and the
    figure that reaches the report would depend on it too.
    """
    seen: dict[int, str] = {}
    for context in sorted(contexts.values(), key=lambda item: item.id):
        if context.discriminators is None:
            continue
        precedence = context.discriminators.precedence
        if precedence in seen:
            raise ContextError(
                f"JE types {seen[precedence]!r} and {context.id!r} share precedence "
                f"{precedence}; the order they are tested in would be undefined"
            )
        seen[precedence] = context.id


def _summarise(exc: ValidationError) -> str:
    """Validation errors as one readable line.

    Carried in the exception *message*, not only in its details: a startup crash shows the
    message, and "not a valid JE type" with no field named is a log line nobody can act on.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors()[:_MAX_REPORTED_ERRORS]
    )
