"""Unit handling: conversions, and refusal to guess."""

from __future__ import annotations

import math

import numpy as np
import pytest

from wildfireguardian_data.errors import UnitMismatchError, UnknownUnitError
from wildfireguardian_data.units import (
    AreaUnit,
    LengthUnit,
    SlopeUnit,
    TimeUnit,
    convert_area,
    convert_length,
    convert_slope,
    convert_time,
    parse_length_unit,
    parse_slope_unit,
    require_same_length_unit,
)


def test_length_conversions_are_exact():
    assert convert_length(1.0, LengthUnit.KILOMETRE, LengthUnit.METRE) == 1000.0
    assert convert_length(1.0, LengthUnit.FOOT, LengthUnit.METRE) == 0.3048
    assert convert_length(2.0, LengthUnit.METRE, LengthUnit.METRE) == 2.0


def test_length_round_trip_is_lossless_for_metre_kilometre():
    assert convert_length(
        convert_length(1234.0, LengthUnit.METRE, LengthUnit.KILOMETRE),
        LengthUnit.KILOMETRE,
        LengthUnit.METRE,
    ) == pytest.approx(1234.0)


def test_slope_percent_is_percent_rise_not_percent_of_90_degrees():
    # The classic confusion: 45 degrees is 100 percent rise, not 50 percent.
    assert convert_slope(45.0, SlopeUnit.DEGREE, SlopeUnit.PERCENT) == pytest.approx(100.0)
    assert convert_slope(100.0, SlopeUnit.PERCENT, SlopeUnit.DEGREE) == pytest.approx(45.0)


def test_slope_conversion_matches_closed_form():
    for degrees in (0.0, 5.0, 12.6, 30.0, 60.0, 89.0):
        expected = 100.0 * math.tan(math.radians(degrees))
        assert convert_slope(degrees, SlopeUnit.DEGREE, SlopeUnit.PERCENT) == pytest.approx(
            expected
        )


def test_slope_conversion_works_on_arrays():
    result = convert_slope(
        np.array([0.0, 45.0]), SlopeUnit.DEGREE, SlopeUnit.PERCENT
    )
    assert result == pytest.approx([0.0, 100.0])


def test_time_and_area_conversions():
    assert convert_time(2.0, TimeUnit.HOUR, TimeUnit.SECOND) == 7200.0
    assert convert_area(1.0, AreaUnit.HECTARE, AreaUnit.SQUARE_METRE) == 10_000.0
    assert convert_area(1.0, AreaUnit.SQUARE_KILOMETRE, AreaUnit.HECTARE) == pytest.approx(100.0)


def test_us_survey_foot_is_refused_rather_than_mapped_to_foot():
    # The US survey foot differs from the international foot by 2 ppm; silently
    # equating them is a sub-metre error that accumulates over a study area.
    with pytest.raises(UnknownUnitError):
        parse_length_unit("US survey foot")


def test_unknown_units_raise():
    with pytest.raises(UnknownUnitError):
        parse_length_unit("cubits")
    with pytest.raises(UnknownUnitError):
        parse_slope_unit("grade")


def test_common_spellings_are_accepted():
    for spelling in ("m", "metre", "meter", "METRES"):
        assert parse_length_unit(spelling) is LengthUnit.METRE
    for spelling in ("deg", "degrees", "DEGREE"):
        assert parse_slope_unit(spelling) is SlopeUnit.DEGREE
    assert parse_slope_unit("%") is SlopeUnit.PERCENT


def test_require_same_length_unit_raises_on_mixture():
    assert require_same_length_unit([LengthUnit.METRE, "m", "metre"]) is LengthUnit.METRE
    with pytest.raises(UnitMismatchError):
        require_same_length_unit([LengthUnit.METRE, LengthUnit.FOOT], context="slope")
