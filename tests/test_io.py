import gzip
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dumper7_pyparser import FILE_NAMES, Dump, DumpFormatError, load_dump
from dumper7_pyparser._io import locate, read_json, unwrap


def test_read_plain_and_gzip_are_identical(fixture_dir: Path, tmp_path: Path):
    src = fixture_dir / "ClassesInfo.json"
    gz = tmp_path / "ClassesInfo.json.gz"
    with open(src, "rb") as fin, gzip.open(gz, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    assert read_json(src) == read_json(gz)


def test_locate_prefers_plain_then_gz(fixture_dir: Path, tmp_path: Path):
    assert locate(fixture_dir, "classes") == fixture_dir / "ClassesInfo.json"
    gz = tmp_path / "EnumsInfo.json.gz"
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        json.dump({"updated_at": "0", "version": 1, "data": []}, fh)
    assert locate(tmp_path, "enums") == gz
    assert locate(tmp_path, "offsets") is None


def test_gzip_detected_by_magic_not_suffix(tmp_path: Path):
    path = tmp_path / "StructsInfo.json"  # gzip content under a plain name
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump([{"FVector": [{"__InheritInfo": []}, {"__MDKClassSize": 12}]}], fh)
    dump = load_dump(tmp_path)
    assert dump.structs.FVector.size == 12


def test_unwrap_envelope():
    data, info = unwrap({"updated_at": "1725400000000", "version": 10202, "data": [1], "credit": {"a": 1}})
    assert data == [1]
    assert info.version == 10202
    assert info.credit == {"a": 1}
    assert info.updated_at == datetime(2024, 9, 3, 21, 46, 40, tzinfo=timezone.utc)


def test_unwrap_bare_list():
    data, info = unwrap([{"X": 1}])
    assert data == [{"X": 1}]
    assert info.updated_at is None and info.version is None and info.credit is None


def test_unwrap_bad_shape():
    with pytest.raises(DumpFormatError):
        unwrap({"nope": 1})
    with pytest.raises(DumpFormatError):
        unwrap("text")


def test_file_info_populated(dump: Dump, fixture_dir: Path):
    assert set(dump.info) == set(FILE_NAMES)
    assert dump.info.offsets.credit["dumper_used"] == "Dumper-7"
    assert dump.info.classes.path == fixture_dir / "ClassesInfo.json"
    assert dump.info.classes.version == 10202
    assert dump.source == fixture_dir


def test_missing_files_lenient_and_strict(fixture_dir: Path, tmp_path: Path):
    shutil.copy(fixture_dir / "OffsetsInfo.json", tmp_path / "OffsetsInfo.json")
    dump = load_dump(tmp_path)
    assert dump.offsets.OFFSET_GWORLD == 1011
    assert len(dump.classes) == 0 and "classes" not in dump.info
    with pytest.raises(FileNotFoundError):
        load_dump(tmp_path, strict=True)
    with pytest.raises(NotADirectoryError):
        load_dump(tmp_path / "does-not-exist")


def test_from_files_and_from_raw(fixture_dir: Path):
    partial = Dump.from_files(classes=fixture_dir / "ClassesInfo.json")
    assert "UWorld" in partial.classes and len(partial.enums) == 0
    with pytest.raises(TypeError):
        Dump.from_files(bogus=fixture_dir / "ClassesInfo.json")
    with pytest.raises(FileNotFoundError):
        Dump.from_files(strict=True, enums=fixture_dir / "missing.json")

    raw = Dump.from_raw(offsets=[["OFFSET_GWORLD", 5]], enums={"data": [{"E": [[{"A": 1}], "uint8"]}], "version": 1})
    assert raw.offsets.OFFSET_GWORLD == 5
    assert raw.enums.E.A == 1
    assert raw.info.enums.version == 1
