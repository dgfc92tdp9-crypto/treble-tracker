"""`PEOP` — the directory of people on this network (spec §19.3).

    Every user appears in a searchable directory with employer, role,
    coverage area, contact details, and biography. Identity is verified;
    users control their own visibility. — spec §19.3

Three properties, and each is enforced here rather than left to a screen.

**"Identity is verified" — by what?** Nothing, on this install, and the
directory says so. P3_1's Matrix homeserver is not built, no credential
issuer is configured, so every profile here is `SELF_ASSERTED`: a person
typed their own employer and nobody checked it. A directory that printed
"verified" beside an unchecked claim would be worse than one with no
verification at all, because a reader would act on it — this is the same
line as `NOT_EVALUABLE` in the compliance engine and the evaluated-price
score in `TVAL`. :class:`Verification` therefore has members that cannot
be reached yet, and they are present on purpose: the screen shows which
one a profile carries, so the day Matrix lands the difference is visible
rather than retroactive.

**Visibility is enforced at read, not at render.** A field marked private
is not returned by :meth:`Directory.visible`, so a renderer cannot leak
it, a bulk export cannot carry it, and a federated peer never receives
it. Filtering in the renderer would put the guarantee in the last layer
instead of the first, which is where every leak of this kind comes from.

**Profiles can be deleted, and the fact store's contents cannot.** That
asymmetry is deliberate. The bitemporal store is append-only by
construction (I2) because a market fact's history is the point; a
person's contact details are not a market fact, and an append-only record
of them cannot honour an erasure request. So the directory is its own
store with a real :meth:`Directory.forget`, and personal data never
enters `facts`.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: Sits beside the vault in the data directory.
DIRECTORY_FILENAME = "people.json"

VERSION = 1


class Visibility(enum.Enum):
    """Who may see a field. Ordered from most to least open."""

    #: Anyone, including an unauthenticated reader of a federated node.
    PUBLIC = "public"
    #: Members of this network whose identity has been verified.
    NETWORK = "network"
    #: Nobody but the person themselves. Never leaves the directory.
    PRIVATE = "private"


class Verification(enum.Enum):
    """How an identity came to be believed.

    `SELF_ASSERTED` is the only one reachable today and is the default,
    because nothing on this install checks anything. The other two are
    declared rather than omitted so the screen has a vocabulary for the
    distinction the moment there is something to distinguish.
    """

    #: Typed by the person; nothing checked it.
    SELF_ASSERTED = "self-asserted"
    #: A Matrix ID this homeserver authenticated (P3_1, not built).
    MATRIX = "matrix"
    #: A verifiable credential presented and cryptographically checked.
    CREDENTIAL = "credential"

    @property
    def is_verified(self) -> bool:
        """Whether this counts as verification. Self-assertion does not."""
        return self is not Verification.SELF_ASSERTED


#: Field name -> default visibility. Contact details default to NETWORK
#: rather than PUBLIC: a directory that published everyone's email to an
#: unauthenticated reader would be a harvesting target, and the spec says
#: users control their own visibility, not that everything is open.
DEFAULT_VISIBILITY: dict[str, Visibility] = {
    "display_name": Visibility.PUBLIC,
    "employer": Visibility.PUBLIC,
    "role": Visibility.PUBLIC,
    "coverage": Visibility.PUBLIC,
    "email": Visibility.NETWORK,
    "phone": Visibility.PRIVATE,
    "biography": Visibility.PUBLIC,
}

#: The fields a profile may carry. Closed, like the compliance predicate
#: set: an open shape would let a caller invent a field with no declared
#: visibility, which would then default to *something* and be a leak
#: waiting on whichever default was chosen.
PROFILE_FIELDS = tuple(DEFAULT_VISIBILITY)


class DirectoryError(ValueError):
    """The directory was asked for something it must refuse."""


@dataclass(frozen=True)
class Profile:
    """One person, as the directory holds them."""

    handle: str
    verification: Verification = Verification.SELF_ASSERTED
    values: dict[str, str] = field(default_factory=dict)
    visibility: dict[str, Visibility] = field(default_factory=dict)
    updated_at: datetime | None = None

    def visibility_of(self, name: str) -> Visibility:
        return self.visibility.get(name, DEFAULT_VISIBILITY[name])

    def visible(self, *, to: Visibility = Visibility.PUBLIC) -> dict[str, str]:
        """The fields a viewer at this level may see.

        `to` is the viewer's own standing: `PUBLIC` for anyone,
        `NETWORK` for a verified member. `PRIVATE` is never returned by
        this method at all — it is the person's own view, and a caller
        wanting it must go through :meth:`Directory.own_view`, which
        names what it is doing.
        """
        allowed = (
            {Visibility.PUBLIC}
            if to is Visibility.PUBLIC
            else {Visibility.PUBLIC, Visibility.NETWORK}
        )
        return {
            name: value
            for name, value in sorted(self.values.items())
            if self.visibility_of(name) in allowed
        }


class Directory:
    """Profiles on this node, on disk as one JSON document."""

    def __init__(self, directory: Path) -> None:
        self._path = Path(directory) / DIRECTORY_FILENAME
        self._profiles: dict[str, Profile] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        document = json.loads(self._path.read_text())
        if document.get("version") != VERSION:
            raise DirectoryError(
                f"{self._path.name}: version {document.get('version')!r}, expected {VERSION}"
            )
        for raw in document.get("profiles", []):
            profile = Profile(
                handle=raw["handle"],
                verification=Verification(raw["verification"]),
                values=dict(raw.get("values", {})),
                visibility={k: Visibility(v) for k, v in raw.get("visibility", {}).items()},
                updated_at=datetime.fromisoformat(raw["updated_at"]),
            )
            self._profiles[profile.handle] = profile

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "version": VERSION,
            "profiles": [
                {
                    "handle": p.handle,
                    "verification": p.verification.value,
                    "values": p.values,
                    "visibility": {k: v.value for k, v in p.visibility.items()},
                    "updated_at": (p.updated_at or datetime.now(UTC)).isoformat(),
                }
                for p in sorted(self._profiles.values(), key=lambda p: p.handle)
            ],
        }
        # Written whole and renamed, like every other store here: a
        # half-written directory is a directory that will not load, and
        # this one holds the only copy of a person's own settings.
        temp = self._path.with_suffix(".partial")
        temp.write_text(json.dumps(document, indent=2, sort_keys=True))
        temp.replace(self._path)

    def put(
        self,
        handle: str,
        *,
        values: dict[str, str],
        visibility: dict[str, Visibility] | None = None,
        verification: Verification = Verification.SELF_ASSERTED,
        now: datetime | None = None,
    ) -> Profile:
        """Create or replace a profile.

        `verification` is an argument rather than something this method
        decides, because nothing here can check an identity. A caller
        that has actually verified one — a Matrix homeserver, a
        credential verifier — passes what it established. Defaulting to
        anything other than `SELF_ASSERTED` would let an unchecked
        profile inherit a claim nobody made.
        """
        if not handle.strip():
            raise DirectoryError("a profile needs a handle")
        unknown = sorted(set(values) - set(PROFILE_FIELDS))
        if unknown:
            raise DirectoryError(
                f"unknown profile field(s) {unknown}; known: {list(PROFILE_FIELDS)}"
            )
        unknown_vis = sorted(set(visibility or {}) - set(PROFILE_FIELDS))
        if unknown_vis:
            raise DirectoryError(f"visibility set for unknown field(s) {unknown_vis}")
        profile = Profile(
            handle=handle.strip(),
            verification=verification,
            values=dict(values),
            visibility=dict(visibility or {}),
            updated_at=now or datetime.now(UTC),
        )
        self._profiles[profile.handle] = profile
        self._save()
        return profile

    def get(self, handle: str) -> Profile:
        try:
            return self._profiles[handle]
        except KeyError as error:
            raise DirectoryError(f"no profile for {handle!r}") from error

    def own_view(self, handle: str) -> dict[str, str]:
        """Every field, including private ones — the person's own view.

        Named so a caller cannot reach private data by accident: this is
        the only way to it, and calling it says what is being asked for.
        """
        return dict(sorted(self.get(handle).values.items()))

    def forget(self, handle: str) -> None:
        """Erase a profile completely.

        Real deletion, which the fact store deliberately cannot do. A
        person's contact details are not a market fact, and an
        append-only record of them could not honour an erasure request.
        """
        if handle not in self._profiles:
            raise DirectoryError(f"no profile for {handle!r}")
        del self._profiles[handle]
        self._save()

    def search(
        self, query: str = "", *, to: Visibility = Visibility.PUBLIC
    ) -> tuple[tuple[Profile, dict[str, str]], ...]:
        """Profiles matching `query`, with only the fields `to` may see.

        **The query is matched against visible fields only.** Searching
        private fields and returning the profile would leak their
        contents by inference: a searcher who learns that "dentist"
        matches somebody has learned a private field's value without
        ever being shown it.
        """
        needle = query.strip().lower()
        out: list[tuple[Profile, dict[str, str]]] = []
        for profile in sorted(self._profiles.values(), key=lambda p: p.handle):
            shown = profile.visible(to=to)
            haystack = " ".join([profile.handle, *shown.values()]).lower()
            if not needle or needle in haystack:
                out.append((profile, shown))
        return tuple(out)

    def __len__(self) -> int:
        return len(self._profiles)


__all__ = [
    "DEFAULT_VISIBILITY",
    "DIRECTORY_FILENAME",
    "PROFILE_FIELDS",
    "VERSION",
    "Directory",
    "DirectoryError",
    "Profile",
    "Verification",
    "Visibility",
]
