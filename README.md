# xdf_parser
Python Based Parser for TunerPro .xdf files for 944 Turbo DME EPROMS
# XDF → Excel + JSON Exporter

A Python tool to parse **TunerPro XDF** definition files and export them into **Excel** and **JSON** formats.  
Designed for **tuning engineers** and **data analysts** who want clean tables for review, and structured JSON for scripting.  

---

## ✨ Features

- ✅ Parses **Tables, Scalars, Constants, Axes**  
- ✅ Extracts:  
  - Units + UnitTypes (normalized)  
  - OutputTypes (normalized)  
  - Datatypes + Descriptions  
  - Math functions  
  - Memory layout (`ElementSizeBits`, `MajorStrideBits`, `MinorStrideBits`)  
  - Axis Labels + DALINKs  
- ✅ Excel Export:  
  - **Clean per-sheet schemas** (no empty clutter)  
  - **EmbeddedData** sheet → raw superset audit dump  
  - Pretty-printed JSON fields (wrapped + auto row height)  
- ✅ JSON Export:  
  - `<xdf>.json.xlsx` → Flattened JSON (Axis Labels, DALINKs, etc.)  
  - `<xdf>.embedded.json` → Full EmbeddedData raw dump  

---

## 📦 Installation

```bash
pip install pandas xlsxwriter
```

---

## 🚀 Usage

```bash
python xdf_to_excel.py path/to/file.xdf
```

---

## 📂 Outputs

For an input file `28pin DME limited.xdf`, you will get:

- `28pin DME limited.parsed.xlsx` → main workbook  
- `28pin DME limited.json.xlsx` → JSON breakdown  
- `28pin DME limited.embedded.json` → raw EmbeddedData JSON  

---

## 📊 Example Excel (Tables Sheet)

| Name      | Address | Size  | Units | UnitType | OutputType | Description   | Math   | ElementSizeBits | MajorStrideBits | MinorStrideBits |
|-----------|---------|-------|-------|----------|------------|---------------|--------|-----------------|-----------------|-----------------|
| Fuel Map  | 0x1234  | 16x16 | ms    | Time     | Unsigned   | Main fuel map | X*0.01 | 16              | -32             | 0               |

---

## 📊 Example Excel (Axes Sheet)

| Parent   | Name | Units | UnitType | OutputType | Math | ElementSizeBits | MajorStrideBits | MinorStrideBits | Labels                                                                 | DALINK        |
|----------|------|-------|----------|------------|------|-----------------|-----------------|-----------------|------------------------------------------------------------------------|---------------|
| Fuel Map | Y    | FQS   | Generic  | Unsigned   | X    | 16              | -32             | 0               | {<br>  "0": "Stock & 4",<br>  "1": "1 & 5",<br>  "2": "2 & 6",<br>  "3": "3 & 7"<br>} | [<br>  "0",<br>  "2"<br>] |

---

## 📜 Example JSON (EmbeddedData Dump)

```json
[
  {
    "ObjectType": "Table",
    "Name": "Fuel Map",
    "Address": "0x1234",
    "Size": "16x16",
    "Units": "ms",
    "UnitType": "Time",
    "OutputType": "Unsigned",
    "Math": "X*0.01",
    "ElementSizeBits": 16,
    "MajorStrideBits": -32,
    "MinorStrideBits": 0
  },
  {
    "ObjectType": "Axis",
    "Name": "Y",
    "Parent": "Fuel Map",
    "Units": "FQS",
    "UnitType": "Generic",
    "OutputType": "Unsigned",
    "Math": "X",
    "ElementSizeBits": 16,
    "MajorStrideBits": -32,
    "MinorStrideBits": 0,
    "Labels": {
      "0": "Stock & 4",
      "1": "1 & 5",
      "2": "2 & 6",
      "3": "3 & 7"
    },
    "DALINK": ["0", "2"]
  }
]
```

---

## 🛠️ Requirements

- Python 3.8+  
- pandas  
- xlsxwriter  

---

## 🔧 Advanced Usage

Since you also get **`.embedded.json`**, you can script against the raw data.

### Example: Load EmbeddedData JSON in Python

```python
import json

with open("28pin DME limited.embedded.json", "r", encoding="utf-8") as f:
    embedded = json.load(f)

# Example: Get all Scalars
scalars = [obj for obj in embedded if obj["ObjectType"] == "Scalar"]

# Example: Get all Axis Labels for a table
axis_labels = {
    obj["Name"]: obj["Labels"]
    for obj in embedded
    if obj["ObjectType"] == "Axis" and obj["Parent"] == "Fuel Map"
}

print("Scalars:", scalars)
print("Fuel Map Axes:", axis_labels)
```

### Example: Convert Scalars into a pandas DataFrame

```python
import pandas as pd

df = pd.DataFrame([obj for obj in embedded if obj["ObjectType"] == "Scalar"])
print(df[["Name", "Address", "Datatype", "Units", "Math"]])
```

✅ This way you can use the JSON directly for scripting, data validation, or exporting into other tools.
