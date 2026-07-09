from print_gateway.cups import CupsClient, parse_job_id


def test_parse_cups_job_id_from_lp_output() -> None:
    output = "request id is office-42 (1 file(s))"

    assert parse_job_id(output) == "office-42"


def test_cups_options_maps_orientation() -> None:
    client = CupsClient()

    assert "orientation-requested=4" in client._cups_options({"orientation": "landscape"})
    assert "orientation-requested=3" in client._cups_options({"orientation": "portrait"})


def test_cups_options_ignores_unknown_orientation() -> None:
    client = CupsClient()

    options = client._cups_options({"orientation": "sideways"})

    assert not any(option.startswith("orientation-requested=") for option in options)
