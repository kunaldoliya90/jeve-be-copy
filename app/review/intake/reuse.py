"""Step 3. The key that decides whether a review has already been done.

A hit copies the stored result under a new review id and costs no AI call. Every version
that could change the answer is in the key, so a result is never served across a rule,
prompt or model change — which is also what makes a stored review safe to cite later.
"""

import hashlib
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from review.consts import SHORT_DIGEST_LENGTH

_SEPARATOR = "|"


def _encode(part: str) -> str:
    """Length-prefix a field so the joined string has exactly one reading.

    Joining on a separator alone is ambiguous: a JE type literally named ``a|x=b`` with no
    versions produces the same text as the type ``a`` with one version ``x=b``, and the two
    would share a key. That decides whether a stored audit result is served again, so it is
    worth closing even though JE type ids are drawn from a controlled catalogue.
    """
    return f"{len(part)}:{part}"


def file_digest(content: bytes) -> str:
    """SHA-256 of the uploaded bytes. Also recorded in the audit trail."""
    return hashlib.sha256(content).hexdigest()


@cache
def _source_digest() -> str:
    """A content hash of the Python the review harness decides by.

    The reuse key exists to guarantee that a stored result is never served across a change
    that could alter the answer, and until this existed it was blind to the largest such
    change there is: the code. ``_HARNESS_VERSION`` is a constant somebody has to remember
    to raise, so a fix to the scoring, to a tool or to a guardrail shipped with the key it
    had the day before, and the next upload of a file already in the index was answered
    with the result the old code produced. That is how a parameter kept reporting a score
    the running code no longer computes - a cache that outlives the thing it caches.

    So the version is taken from the source itself, the way the JE type and the business
    rules already take theirs from the files they are loaded from. Every ``.py`` under the
    ``review`` package counts, hashed with its own path so moving a file is a change too.
    Deliberately the whole package rather than a list of the modules that "really" decide:
    a list is a judgement about which edits can change an answer, and getting that
    judgement wrong is silent and reaches an audit report.

    It over-invalidates - a comment edit retires every stored result - and that is the
    trade this makes on purpose. The cost of a miss is one review re-run; the cost of a
    wrong hit is a report that disagrees with the system that produced it.
    """
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        # The path as well as the bytes: two files that swap names are a different harness,
        # and posix spelling so a Windows checkout keys the same as the container's.
        digest.update(_encode(path.relative_to(root).as_posix()).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:SHORT_DIGEST_LENGTH]


def harness_version(declared: str) -> str:
    """The harness version as the key records it: what it calls itself, and what it is.

    ``"2.0.0-dev#ab12cd34ef56"``, the same shape the JE type and rules versions carry - a
    number a person reads, and a hash of the thing itself that no one has to remember to
    change.
    """
    return f"{declared}#{_source_digest()}"


def reuse_key(
    *,
    file_sha256: str,
    submitted_je_type: str | None,
    versions: Mapping[str, str],
) -> str:
    """Key the reuse index.

    ``versions`` carries the JE type and business-rules hashes, every prompt hash, the model
    id and the harness version. Sorting it means the caller cannot change the key by
    reordering a dict.
    """
    parts = [file_sha256, submitted_je_type or ""]
    parts.extend(f"{name}={value}" for name, value in sorted(versions.items()))
    encoded = _SEPARATOR.join(_encode(part) for part in parts)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def short(digest: str) -> str:
    """The first characters of a digest, for logs and for a reviewer reading a list."""
    return digest[:SHORT_DIGEST_LENGTH]
