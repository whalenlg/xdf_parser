import pytest
from xdf_to_excel import XDFParser
from pathlib import Path

def test_parse_scalar():
    parser = XDFParser(Path("tests/sample.xdf"))
    xdf = parser.parse()
    scalars = xdf["Scalars"]
    assert any(s["ObjectType"] == "Scalar" for s in scalars)

