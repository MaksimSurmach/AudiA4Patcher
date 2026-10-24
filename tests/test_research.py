import pytest

from audi_workbench.dictionary import Dictionary
from audi_workbench.research import draft_dictionary
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store


async def test_dictionary_draft_retains_raw_identity_and_never_promotes_to_write(tmp_path):
    dictionary = Dictionary.load()
    wb = Workbench(Store(tmp_path / "run"), dictionary, DemoLink())
    async with wb:
        snapshot = await wb.capture()
    path = draft_dictionary(snapshot, tmp_path / "dictionary.json", dictionary)
    draft = Dictionary.load(path)
    candidates = [e for e in draft.entries if e["id"].startswith("candidate.")]
    assert len(candidates) == 3
    for entry in candidates:
        assert entry["writable"] is False and entry["confidence"] == "unknown"
        assert "allowed_mask" not in entry
        assert entry["applies_to"] == Store.read(snapshot)["identity"]
    with pytest.raises(FileExistsError):
        draft_dictionary(snapshot, path, dictionary)
