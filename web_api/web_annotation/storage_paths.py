"""Per-user storage folders under the configured storage roots.

Each user's files live in a folder named after the user's identifier, one
level below a storage root (``ANNOTATION_CONFIG_STORAGE_DIR``,
``JOB_INPUT_STORAGE_DIR`` or ``JOB_RESULT_STORAGE_DIR``). The identifier of
a registered user is the account email, so the email must be usable as one
folder name.
"""
import os
from pathlib import Path

#: The response text for an account email that is not a valid folder name.
INVALID_ACCOUNT_EMAIL = "Invalid email address"

#: The response text for a user folder that resolves outside its root.
INVALID_STORAGE_PATH = "Invalid storage path!"

_FORBIDDEN_CHARACTERS = ("/", "\\", "\x00")
_NON_CHILD_NAMES = ("", ".", "..")


class UnsafeStoragePathError(ValueError):
    """A user folder does not resolve to a child of its storage root."""


def account_email_error(email: str) -> str | None:
    """Return why ``email`` cannot name a storage folder, or ``None``.

    An email is refused when it contains ``/``, ``\\`` or a NUL character,
    or when it is empty, ``.`` or ``..``: those names do not name a child
    folder of a storage root.
    """
    if any(char in email for char in _FORBIDDEN_CHARACTERS) \
            or email in _NON_CHILD_NAMES:
        return INVALID_ACCOUNT_EMAIL
    return None


def user_storage_dir(root: str | os.PathLike[str], folder_name: str) -> Path:
    """Return the resolved folder ``folder_name`` under the storage ``root``.

    Raises
    ------
    UnsafeStoragePathError
        When the folder does not resolve to a direct child of ``root``.
    """
    try:
        resolved_root = os.path.realpath(root)
        folder = os.path.realpath(os.path.join(resolved_root, folder_name))
    except ValueError as error:
        raise UnsafeStoragePathError(folder_name) from error
    if os.path.dirname(folder) != resolved_root:
        raise UnsafeStoragePathError(folder_name)
    return Path(folder)
