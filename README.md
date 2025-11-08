# XDF Map Export & Merge Tool

[![Python 3](https://img.shields.io/badge/python-3.x-blue.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#)
[![TunerPro Compatible](https://img.shields.io/badge/TunerPro-XDF%20Supported-orange.svg)](#)

A beginner-friendly tool that helps you extract, inspect, and rebuild **TunerPro XDF map files** using data from **ECU BIN files**. It makes ECU maps easier to understand, edit, and compare by exporting them to **Excel and JSON**, and can also **generate new XDF maps when missing**.

---

## Features

✅ Extract maps from `.xdf` and `.bin`  
✅ Convert table data into **Excel spreadsheets**  
✅ Export metadata and scaling info into **JSON**  
✅ Detect missing maps and **auto-add them back to the XDF**  
✅ Generate a **merged `.xdf`** ready for TunerPro  
✅ Helps reverse-engineer or improve incomplete map definitions

---

## Inputs

| File Type | Description |
|----------|-------------|
| `.xdf` | TunerPro definition file |
| `.map` | Lookup/address metadata for tables |
| `.bin` | ECU firmware / calibration image |

---

## Outputs

### `output/<name>/`

| File | Description |
|------|-------------|
| `*.parsed.xlsx` | Tables exported for editing/viewing |
| `*.json.xlsx` | Metadata / labels / formulas |
| `*.embedded.json` | Raw extracted map structures |
| `*.all_maps.json` | Extracted data from BIN maps |

### `output_xdf/<name>/`
| File | Description |
|------|-------------|
| `*.merged.xdf` | Final XDF (with added/missing tables restored) |

> Open the **merged XDF** directly in **TunerPro**.

---

## Installation

Requires **Python 3**.

```bash
pip install pandas xlsxwriter


