"""Load the owned role catalog safely and deterministically (P02-3).

The catalog is one directory per role. Every role file is read with
`yaml.safe_load`, every manifest is validated by RoleManifest, and a role's
identity must equal its directory name. A tagged-YAML payload, a symlinked role
directory or bundle file, and a forged identity are all refused as PolicyDenied.
An unknown role is InvalidInput.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from hyper_agents.application.ports import ManifestParserPort
from hyper_agents.domain.errors import InvalidInput, PolicyDenied
from hyper_agents.domain.roles import RoleManifest

BUNDLE_FILES = ("role.yaml", "persona.md", "instructions.md", "output.schema.json")


@dataclass(frozen=True)
class LoadedRole:
    manifest: RoleManifest
    # Hash over the whole bundle: the determinism anchor for repeated loads.
    bundle_sha256: str


class RoleRegistry:
    def __init__(self, roles: dict[str, LoadedRole]) -> None:
        self._roles = dict(sorted(roles.items()))

    def list(self) -> tuple[str, ...]:
        return tuple(self._roles)

    def get(self, role_id: str) -> LoadedRole:
        try:
            return self._roles[role_id]
        except KeyError:
            raise InvalidInput(f"unknown role: {role_id!r}") from None


class CatalogLoader:
    def __init__(self, root: Path, parser: ManifestParserPort) -> None:
        self._root = root
        self._parser = parser

    def load(self) -> RoleRegistry:
        roles: dict[str, LoadedRole] = {}
        for directory in sorted(self._root.iterdir()):
            if directory.is_symlink():
                raise PolicyDenied(f"role directory is a symlink: {directory.name}")
            if directory.is_dir():
                roles[directory.name] = _load_role(directory, self._parser)
        return RoleRegistry(roles)


class CatalogValidator:
    def validate(self, registry: RoleRegistry) -> None:
        known = set(registry.list())
        for role_id in registry.list():
            for dependency in registry.get(role_id).manifest.dependencies:
                if dependency not in known:
                    raise InvalidInput(f"role {role_id!r} depends on unknown role {dependency!r}")


def _load_role(directory: Path, parser: ManifestParserPort) -> LoadedRole:
    for name in BUNDLE_FILES:
        path = directory / name
        if path.is_symlink():
            raise PolicyDenied(f"bundle file is a symlink: {directory.name}/{name}")
        if not path.is_file():
            raise InvalidInput(f"role {directory.name!r} is missing {name}")
    data = parser.parse((directory / "role.yaml").read_text())
    try:
        manifest = RoleManifest.model_validate(data)
    except ValidationError as error:
        raise InvalidInput(
            f"role {directory.name!r} rejected: {error.error_count()} error(s)"
        ) from None
    if manifest.id != directory.name:
        raise PolicyDenied(
            f"role id {manifest.id!r} does not match its directory {directory.name!r}"
        )
    return LoadedRole(manifest=manifest, bundle_sha256=_bundle_hash(directory))


def _bundle_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for name in BUNDLE_FILES:
        digest.update(name.encode())
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()
