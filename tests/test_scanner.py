from types import SimpleNamespace

from rpipulse.scanner import _apply_threshold, _count_unique


def test_count_unique_prefers_address_then_name() -> None:
    devices = [
        SimpleNamespace(address="AA:BB", name="d1", rssi=-60),
        SimpleNamespace(address="AA:BB", name="dup", rssi=-58),
        SimpleNamespace(address=None, name="named", rssi=-50),
        SimpleNamespace(address=None, name="named", rssi=-49),
    ]

    unique_count, raw_count = _count_unique(devices)
    assert raw_count == 4
    assert unique_count == 2


def test_apply_threshold_filters_low_rssi() -> None:
    devices = [
        SimpleNamespace(address="AA", rssi=-90),
        SimpleNamespace(address="BB", rssi=-70),
        SimpleNamespace(address="CC", rssi=None),
    ]

    filtered = _apply_threshold(devices, rssi_threshold=-80)
    assert len(filtered) == 2
    assert filtered[0].address == "BB"
    assert filtered[1].address == "CC"
