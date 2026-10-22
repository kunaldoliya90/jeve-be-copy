"""Step 3. The key that decides whether a review has already been done.

A hit copies the stored result under a new review id and costs no AI call. Every version
that could change the answer is in the key, so a result is never served across a rule,
prompt or model change — which is also what makes a stored review safe to cite later.
"""

import hashlib
from collections.abc import Mapping

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
