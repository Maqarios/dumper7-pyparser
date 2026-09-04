import pytest

from dumper7_pyparser import Namespace


@pytest.fixture
def ns() -> Namespace[int]:
    return Namespace({"UObject": 1, "Engine::FHitResult": 2, "Other::FHitResult": 3, "Core::FVector": 4, "keys": 5}, label="t")


def test_attribute_and_item_access(ns):
    assert ns.UObject == 1
    assert ns["UObject"] == 1
    assert ns.get("UObject") == 1
    assert ns.get("Nope") is None
    assert ns.get("Nope", 0) == 0


def test_package_suffix_fallback(ns):
    assert ns.FVector == 4
    assert ns["FVector"] == 4
    assert "FVector" in ns
    assert ns["Core::FVector"] == 4


def test_ambiguous_suffix_raises(ns):
    with pytest.raises(KeyError, match="ambiguous"):
        ns["FHitResult"]
    with pytest.raises(AttributeError, match="ambiguous"):
        ns.FHitResult
    assert "FHitResult" not in ns
    assert ns["Engine::FHitResult"] == 2


def test_missing_raises_attribute_error_for_hasattr(ns):
    assert not hasattr(ns, "Missing")
    with pytest.raises(KeyError):
        ns["Missing"]
    assert getattr(ns, "Missing", "dflt") == "dflt"


def test_real_attributes_win_over_keys(ns):
    assert callable(ns.keys)
    assert ns["keys"] == 5


def test_mapping_protocol_and_order(ns):
    assert list(ns) == ["UObject", "Engine::FHitResult", "Other::FHitResult", "Core::FVector", "keys"]
    assert len(ns) == 5
    assert dict(ns.items())["keys"] == 5
    assert 42 not in ns
    assert ns == {"UObject": 1, "Engine::FHitResult": 2, "Other::FHitResult": 3, "Core::FVector": 4, "keys": 5}


def test_dir_lists_identifier_keys(ns):
    names = dir(ns)
    assert "UObject" in names and "keys" in names
    assert "Engine::FHitResult" not in names


def test_read_only(ns):
    with pytest.raises(AttributeError):
        ns.UObject = 9
    with pytest.raises(TypeError):
        ns["X"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        hash(ns)


def test_repr_and_empty():
    assert "t" in repr(Namespace(label="t"))
    assert len(Namespace()) == 0
