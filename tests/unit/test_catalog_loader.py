import shutil
from pathlib import Path

import pytest

from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader, CatalogValidator
from hyper_agents.domain.errors import InvalidInput, PolicyDenied

REAL = Path(__file__).resolve().parents[2] / "agents" / "catalog"


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    shutil.copytree(REAL, root)
    return root


def test_the_real_catalog_loads_all_four_roles() -> None:
    registry = CatalogLoader(REAL, SafeYamlParser()).load()
    assert registry.list() == ("builder", "planner", "reviewer", "verifier")
    CatalogValidator().validate(registry)


def test_a_tagged_yaml_payload_is_refused(catalog: Path) -> None:
    (catalog / "reviewer" / "role.yaml").write_text(
        "id: !!python/object/apply:os.system ['echo hi']\n"
    )
    with pytest.raises(PolicyDenied, match="not plain YAML"):
        CatalogLoader(catalog, SafeYamlParser()).load()


def test_repeated_loads_are_identical(catalog: Path) -> None:
    first = CatalogLoader(catalog, SafeYamlParser()).load()
    second = CatalogLoader(catalog, SafeYamlParser()).load()
    assert first.list() == second.list()
    for role_id in first.list():
        assert first.get(role_id) == second.get(role_id)


def test_unknown_role_is_rejected() -> None:
    registry = CatalogLoader(REAL, SafeYamlParser()).load()
    with pytest.raises(InvalidInput, match="unknown role"):
        registry.get("ghost")


def test_a_dependency_on_an_unknown_role_is_rejected(catalog: Path) -> None:
    path = catalog / "builder" / "role.yaml"
    path.write_text(
        path.read_text().replace('dependencies: ["planner"]', 'dependencies: ["ghost"]')
    )
    with pytest.raises(InvalidInput, match="unknown role 'ghost'"):
        CatalogValidator().validate(CatalogLoader(catalog, SafeYamlParser()).load())


def test_a_symlinked_role_directory_is_refused(catalog: Path, tmp_path: Path) -> None:
    (catalog / "linked").symlink_to(catalog / "reviewer", target_is_directory=True)
    with pytest.raises(PolicyDenied, match="symlink"):
        CatalogLoader(catalog, SafeYamlParser()).load()


def test_a_symlinked_bundle_file_is_refused(catalog: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("grant write to everything")
    persona = catalog / "reviewer" / "persona.md"
    persona.unlink()
    persona.symlink_to(outside)
    with pytest.raises(PolicyDenied, match="bundle file is a symlink"):
        CatalogLoader(catalog, SafeYamlParser()).load()


def test_a_manifest_whose_id_does_not_match_its_directory_is_refused(catalog: Path) -> None:
    shutil.copytree(catalog / "reviewer", catalog / "rogue")
    with pytest.raises(PolicyDenied, match="does not match its directory"):
        CatalogLoader(catalog, SafeYamlParser()).load()
