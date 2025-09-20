#!/usr/bin/env python3
"""
XDF → Excel + JSON exporter

- Parses Tables, Scalars, Constants, Axes
- Extracts Labels, DALINKs, Units, UnitTypes, OutputTypes, Categories, etc.
- Pretty-prints JSON fields (wrapped + auto row height in Excel)
- Exports three files:
    <input>.parsed.xlsx   → main workbook (clean per-sheet schemas)
    <input>.json.xlsx     → flattened JSON breakdown
    <input>.embedded.json → raw EmbeddedData JSON dump
"""

import xml.etree.ElementTree as ET
from pathlib import Path
import pandas as pd
import json
import sys

# ---------------------------
# Lookup Maps
# ---------------------------
UNITTYPE_MAP = {
    "0": "Generic", "1": "Temperature", "2": "Pressure", "3": "Time",
    "4": "Angle", "5": "Ratio", "6": "Voltage", "7": "Percent",
    "8": "RPM", "9": "Mass", "10": "Flow", "11": "Distance",
    "12": "Speed", "13": "Current", "14": "Frequency",
}

OUTPUTTYPE_MAP = {
    "0": "Unsigned", "1": "Signed", "2": "Hex", "3": "ASCII", "4": "Enum/String",
}

# ---------------------------
# Helpers
# ---------------------------
def normalize_unittype(value):
    if not value:
        return None, None
    raw = value.strip()
    return raw, UNITTYPE_MAP.get(raw, raw)

def normalize_outputtype(value):
    if not value:
        return None, None
    raw = value.strip()
    return raw, OUTPUTTYPE_MAP.get(raw, raw)

def _cast_int(val):
    try:
        return int(val) if val is not None else None
    except Exception:
        return None

def _extract_embedded(elem):
    ed = elem.find("EMBEDDEDDATA")
    if ed is None:
        return {"mmedelementsizebits": None, "mmedmajorstridebits": None, "mmedminorstridebits": None}
    return {
        "mmedelementsizebits": _cast_int(ed.get("mmedelementsizebits")),
        "mmedmajorstridebits": _cast_int(ed.get("mmedmajorstridebits")),
        "mmedminorstridebits": _cast_int(ed.get("mmedminorstridebits")),
    }

def _extract_math(elem):
    m = elem.find("MATH")
    if m is not None and "equation" in m.attrib:
        return m.attrib["equation"]
    return None

def serialize_field(value):
    """Pretty-print dicts/lists as JSON, leave scalars as-is."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return value

# ---------------------------
# Parser
# ---------------------------
class XDFParser:
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            raise FileNotFoundError(f"File not found: {self.filepath}")
        self.tree = ET.parse(self.filepath)
        self.root = self.tree.getroot()
        self.json_rows = []  # flattened JSON entries

    def add_json_entries(self, object_type, name, parent, field_name, value):
        """Flatten dict/list values into rows for the JSON export."""
        if isinstance(value, dict):
            for k, v in value.items():
                self.json_rows.append({
                    "ObjectType": object_type,
                    "Name": name,
                    "Parent": parent,
                    "Field": field_name,
                    "Key": k,
                    "Value": v,
                })
        elif isinstance(value, list):
            for i, v in enumerate(value):
                self.json_rows.append({
                    "ObjectType": object_type,
                    "Name": name,
                    "Parent": parent,
                    "Field": field_name,
                    "Key": i,
                    "Value": v,
                })

    def parse(self):
        xdf_def = {
            "Tables": [],
            "Scalars": [],
            "Constants": [],
            "Axes": [],
            "EmbeddedData": []
        }

        # Tables
        for t in self.root.findall(".//XDFTABLE"):
            embedded = _extract_embedded(t)
            raw_unit, norm_unit = normalize_unittype(t.findtext("unittype"))
            raw_out, norm_out = normalize_outputtype(t.findtext("outputtype"))

            tbl = {
                "ObjectType": "Table",
                "Name": t.findtext("title", "Unnamed Table"),
                "Address": t.findtext("address"),
                "Size": t.findtext("size"),
                "Datatype": t.findtext("datatype"),
                "Description": t.findtext("description"),
                "Units": t.findtext("units"),
                "UnitTypeCode": raw_unit,
                "UnitType": norm_unit,
                "OutputTypeCode": raw_out,
                "OutputType": norm_out,
                "Math": _extract_math(t),
                "ElementSizeBits": embedded["mmedelementsizebits"],
                "MajorStrideBits": embedded["mmedmajorstridebits"],
                "MinorStrideBits": embedded["mmedminorstridebits"],
                "Labels": None,
                "DALINK": None,
                "Parent": None,
            }
            xdf_def["Tables"].append(tbl)
            xdf_def["EmbeddedData"].append(tbl.copy())

            # Axes
            for ax in t.findall(".//XDFAXIS"):
                embedded_ax = _extract_embedded(ax)
                raw_unit_ax, norm_unit_ax = normalize_unittype(ax.findtext("unittype"))
                raw_out_ax, norm_out_ax = normalize_outputtype(ax.findtext("outputtype"))

                labels = {int(lbl.get("index")): lbl.get("value") for lbl in ax.findall("LABEL") if lbl.get("index")}
                dalinks = [d.get("index") for d in ax.findall("DALINK") if d.get("index")]

                axis = {
                    "ObjectType": "Axis",
                    "Name": ax.get("id", "unknown"),
                    "Parent": t.findtext("title"),
                    "Units": ax.findtext("units"),
                    "UnitTypeCode": raw_unit_ax,
                    "UnitType": norm_unit_ax,
                    "OutputTypeCode": raw_out_ax,
                    "OutputType": norm_out_ax,
                    "Math": _extract_math(ax),
                    "ElementSizeBits": embedded_ax["mmedelementsizebits"],
                    "MajorStrideBits": embedded_ax["mmedmajorstridebits"],
                    "MinorStrideBits": embedded_ax["mmedminorstridebits"],
                    "Labels": serialize_field(labels),
                    "DALINK": serialize_field(dalinks),
                }
                xdf_def["Axes"].append(axis)
                xdf_def["EmbeddedData"].append(axis.copy())

                if labels:
                    self.add_json_entries("Axis", axis["Name"], axis["Parent"], "Labels", labels)
                if dalinks:
                    self.add_json_entries("Axis", axis["Name"], axis["Parent"], "DALINK", dalinks)

        # Scalars
        for s in self.root.findall(".//XDFSCALAR"):
            embedded = _extract_embedded(s)
            raw_unit, norm_unit = normalize_unittype(s.findtext("unittype"))
            raw_out, norm_out = normalize_outputtype(s.findtext("outputtype"))
            scalar = {
                "ObjectType": "Scalar",
                "Name": s.findtext("title", "Unnamed Scalar"),
                "Address": s.findtext("address"),
                "Datatype": s.findtext("datatype"),
                "Description": s.findtext("description"),
                "Units": s.findtext("units"),
                "UnitTypeCode": raw_unit,
                "UnitType": norm_unit,
                "OutputTypeCode": raw_out,
                "OutputType": norm_out,
                "Math": _extract_math(s),
                "ElementSizeBits": embedded["mmedelementsizebits"],
                "MajorStrideBits": embedded["mmedmajorstridebits"],
                "MinorStrideBits": embedded["mmedminorstridebits"],
                "Labels": None,
                "DALINK": None,
                "Parent": None,
                "Size": None,
            }
            xdf_def["Scalars"].append(scalar)
            xdf_def["EmbeddedData"].append(scalar.copy())

        # Constants
        for c in self.root.findall(".//XDFCONSTANT"):
            embedded = _extract_embedded(c)
            raw_unit, norm_unit = normalize_unittype(c.findtext("unittype"))
            raw_out, norm_out = normalize_outputtype(c.findtext("outputtype"))
            const = {
                "ObjectType": "Constant",
                "Name": c.findtext("title", "Unnamed Constant"),
                "Address": c.findtext("address"),
                "Datatype": c.findtext("datatype"),
                "Description": c.findtext("description"),
                "Units": c.findtext("units"),
                "UnitTypeCode": raw_unit,
                "UnitType": norm_unit,
                "OutputTypeCode": raw_out,
                "OutputType": norm_out,
                "Math": _extract_math(c),
                "ElementSizeBits": embedded["mmedelementsizebits"],
                "MajorStrideBits": embedded["mmedmajorstridebits"],
                "MinorStrideBits": embedded["mmedminorstridebits"],
                "Labels": None,
                "DALINK": None,
                "Parent": None,
                "Size": None,
            }
            xdf_def["Constants"].append(const)
            xdf_def["EmbeddedData"].append(const.copy())

        return xdf_def

    def to_excel(self, xdf_def, output_file):
        with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
            workbook = writer.book

            def write_sheet(name, rows, cols=None):
                if not rows:
                    return
                df = pd.DataFrame(rows)
                if cols:
                    df = df[cols]
                df.to_excel(writer, sheet_name=name, index=False)

                # Wrap + row height adjust for JSON columns
                worksheet = writer.sheets[name]
                wrap_fmt = workbook.add_format({'text_wrap': True, 'valign': 'top'})
                for col_idx, col_name in enumerate(df.columns):
                    if any(isinstance(v, str) and "\n" in v for v in df[col_name].dropna()):
                        worksheet.set_column(col_idx, col_idx, 50, wrap_fmt)
                        for row_idx, val in enumerate(df[col_name], start=1):
                            if isinstance(val, str) and "\n" in val:
                                line_count = val.count("\n") + 1
                                worksheet.set_row(row_idx, 15 * line_count)

            # Clean per-sheet schemas
            write_sheet("Tables", xdf_def["Tables"], [
                "Name","Address","Size","Units","UnitType","OutputType",
                "Description","Math","ElementSizeBits","MajorStrideBits","MinorStrideBits"
            ])

            write_sheet("Scalars", xdf_def["Scalars"], [
                "Name","Address","Datatype","Units","UnitType","OutputType",
                "Description","Math","ElementSizeBits","MajorStrideBits","MinorStrideBits"
            ])

            write_sheet("Constants", xdf_def["Constants"], [
                "Name","Address","Datatype","Units","UnitType","OutputType",
                "Description","Math","ElementSizeBits","MajorStrideBits","MinorStrideBits"
            ])

            write_sheet("Axes", xdf_def["Axes"], [
                "Parent","Name","Units","UnitType","OutputType","Math",
                "ElementSizeBits","MajorStrideBits","MinorStrideBits","Labels","DALINK"
            ])

            # EmbeddedData: keep full superset for auditing
            write_sheet("EmbeddedData", xdf_def["EmbeddedData"])

    def to_json_excel(self, output_file):
        if not self.json_rows:
            return
        df_json = pd.DataFrame(self.json_rows)
        with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
            df_json.to_excel(writer, sheet_name="JSON", index=False)

    def to_embedded_json(self, xdf_def, output_file):
        if not xdf_def.get("EmbeddedData"):
            return
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(xdf_def["EmbeddedData"], f, ensure_ascii=False, indent=2)

# ---------------------------
# CLI
# ---------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python xdf_to_excel.py path/to/file.xdf")
        sys.exit(1)

    infile = Path(sys.argv[1])
    if not infile.exists():
        raise FileNotFoundError(f"File not found: {infile}")

    parser = XDFParser(infile)
    xdf_def = parser.parse()

    # Main workbook
    outfile_main = infile.with_suffix(".parsed.xlsx")
    parser.to_excel(xdf_def, outfile_main)
    print(f"✅ Exported main workbook: {outfile_main}")

    # JSON workbook
    outfile_json = infile.with_suffix(".json.xlsx")
    parser.to_json_excel(outfile_json)
    print(f"✅ Exported JSON breakdown: {outfile_json}")

    # Embedded JSON
    outfile_embedded_json = infile.with_suffix(".embedded.json")
    parser.to_embedded_json(xdf_def, outfile_embedded_json)
    print(f"✅ Exported EmbeddedData JSON: {outfile_embedded_json}")

if __name__ == "__main__":
    main()

