import bin_layout_store as store


def test_corrections_are_keyed_by_bin_count(monkeypatch):
    saved = {}
    monkeypatch.setattr(store, "_read", lambda key: saved.get(key))
    monkeypatch.setattr(store, "_write", lambda key, value: saved.__setitem__(key, value))

    store.set_chute_bin_correction(12, 3, 1, 40)
    assert store.get_chute_bin_corrections() == {"n12:3:1": 40}
    store.set_chute_bin_correction(12, 3, 1, None)
    assert store.get_chute_bin_corrections() == {}


def test_layer_keys_move_to_bin_count_keys_once(monkeypatch):
    saved = {"chute_bin_corrections": {"2:0:1": 20, "2:5:0": 10, "7:0:0": 5, "n18:0:0": 3}}
    monkeypatch.setattr(store, "_read", lambda key: saved.get(key))
    monkeypatch.setattr(store, "_write", lambda key, value: saved.__setitem__(key, value))

    # Layers 0 and 2 both have 12 bins; layer 7 is not in the layout any more.
    assert store.migrate_chute_corrections_to_bin_count({0: 12, 2: 12}) == 2
    assert store.get_chute_bin_corrections() == {
        "n12:0:1": 20,
        "n12:5:0": 10,
        "7:0:0": 5,
        "n18:0:0": 3,
    }
    assert store.migrate_chute_corrections_to_bin_count({0: 12, 2: 12}) == 0
