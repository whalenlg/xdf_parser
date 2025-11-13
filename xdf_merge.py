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
import random
import pprint
import math

#Memory Address vs. Input Type
#3 -  shared register
#4 -  shared register?
#13 - engine coolant temp (NTC II)
#11 - battery
#12 - air temp
#13 - engine coolant temp (NTC II)
#37 - rpm
#49 - load
#4d - Unknown - used only by table at memory address 0x158E

max_table=256

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

def eval_formula(formula: str, **variables):
    # Allow safe built-ins from math
    allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
    # Add user variables
    allowed_names.update(variables)
    # Evaluate safely
    return eval(formula, {"__builtins__": None}, allowed_names)

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

def _cast_hex(val):
    try:
        return int(val,16) if val is not None else None
    except Exception:
        return None

def _extract_embedded(elem):
    ed = elem.find("EMBEDDEDDATA")
    if ed is None:
        return {"mmedelementsizebits": None, "mmedmajorstridebits": None, "mmedminorstridebits": None, "mmedrowcount": None, "mmedcolcount": None,"mmedaddress": None, "mmedtypeflags": None}
    return {
        "mmedelementsizebits": _cast_int(ed.get("mmedelementsizebits")),
        "mmedmajorstridebits": _cast_int(ed.get("mmedmajorstridebits")),
        "mmedminorstridebits": _cast_int(ed.get("mmedminorstridebits")),
        "mmedrowcount": _cast_int(ed.get("mmedrowcount")),
        "mmedcolcount": _cast_int(ed.get("mmedcolcount")),
        "mmedaddress": _cast_hex(ed.get("mmedaddress")),
        "mmedtypeflags": _cast_hex(ed.get("mmedtypeflags"))
    }
def _extract_base_offset (bo):
# <BASEOFFSET offset="4096" subtract="0" />
  boff = bo.find("BASEOFFSET")
  if boff is None: 
     return {"offset":0, "subtract":0}
  return {
             "offset": _cast_int(boff.get("offset")),
             "subtract": _cast_int(boff.get("subtract"))
         }
  
def _extract_defaults (def_in):
# <DEFAULTS datasizeinbits="8" sigdigits="2" outputtype="1" signed="0" lsbfirst="0" float="0" />
  defs = def_in.find("DEFAULTS")
  if defs is None: 
     return {"datasizeinbits" : 0, "sigdigits" : 0, "outputtype" : 0, "signed" : 0, "lsbfirst" : 0, "float" : 0}
  return {
             "datasizeinbits": _cast_int(defs.get("datasizeinbits")),
             "sigdigits": _cast_int(defs.get("sigdigits")),
             "outputtype": _cast_int(defs.get("outputtype")),
             "signed": _cast_int(defs.get("signed")),
             "lsbfirst": _cast_int(defs.get("lsbfirst")),
             "float": _cast_int(defs.get("float"))
         }


def _extract_region (reg):
#    <REGION type="0xFFFFFFFF" startaddress="0x0" size="0x2000" regioncolor="0x0" regionflags="0x0" name="Binary File" desc="This region describes the bin file edited by this XDF" />
  regs = reg.find("REGION")
  if regs is None:
     return {"type" : "0x0", "Startaddress" : "0x0", "Size" : "0x0", "regioncolor" : "0x0", "regionflags" : "0x0", "name" : "", "desc" : ""}
  return {
             "type": regs.get("type"),
             "startaddress": regs.get("startaddress"),
             "size": regs.get("size"),
             "regioncolor": regs.get("regioncolor"),
             "regionflags": regs.get("regionflags"),
             "name": regs.get("name"),
             "desc": regs.get("desc")
         }

def _extract_math(elem):
    math_elem = elem.find(".//MATH")
    equation = math_elem.get("equation") if math_elem is not None else None
    return equation

def _extract_math_table(elem):
    #tree = ET.parse(xml_path)
    #root = tree.getroot()

    # Collect all MATH entries with attributes
    math_entries = []
    for m in elem.findall(".//MATH"):
        eq = m.attrib.get("equation", "")
        row = m.attrib.get("row")
        col = m.attrib.get("col")
        var_elem = m.find("VAR")
        var_id = var_elem.attrib.get("id") if var_elem is not None else None

        math_entries.append({
            "row": int(row) if row else None,
            "col": int(col) if col else None,
            "equation": eq,
            "var": var_id
        })
    return math_entries



def _extract_varid(elem):
    axis_id = elem.get("id")
    math_elem = elem.find(".//MATH")
    var_elem = math_elem.find(".//VAR") if math_elem is not None else None
    var_id = var_elem.get("id") if var_elem is not None else None
    equation = math_elem.get("equation") if math_elem is not None else None
    return var_id

def serialize_field(value):
    """Pretty-print dicts/lists as JSON, leave scalars as-is."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return value

def ReadJSONMap(infile):
    with open(infile, 'r') as file:
      data = json.load(file)
    return data

def find_size(xdf_def,addr):
   axes=xdf_def.get("Axes", [])
   for rec in axes:
     if ((rec["ID"] =="z") and (rec["Embedded.Address"] != None)):
       if rec["Embedded.Address"] == addr:
           if rec["Embedded.Rowcount"]==None:return rec["Embedded.Colcount"]
           elif rec["Embedded.Colcount"] == None: return rec["Embedded.Rowcount"]
           else:
             return(rec["Embedded.Rowcount"] * rec["Embedded.Rowcount"])
   return None


def find_val(map_def,val):
   for rec in map_def:
     if int(rec["XDF mmedaddr"],16)==val:  
       return rec["Map Values"]
   return None

def find_hdr(map_def,val):
   for rec in map_def:
     if int(rec["XDF mmedaddr"],16)==val:  
       return rec["Header Values"]
   return None

def find_result(vals,math):
   result=[]
   if vals == None: return result 
   for rec in vals:
      if rec == None: return None
      if type(rec) is int: res=eval_formula(math.upper(),X=rec)
      else: res=eval_formula(math.upper(),X=int(rec,16))
      result.append(res)
   return result
     
def find_result_table(vals,math_table):
   result=[]
   res={"result":None, "row":None, "col":None}
   if ((vals == []) or (vals==None) or (math_table==None) or (math_table==[])): return result 
   if len(math_table)==1:
#single equation
     math=math_table[0]
     for vrec in vals:
       if vrec == None: return None
       if type(vrec) is int:
         res["result"]==eval_formula(math["equation"].upper(),X=vrec)
       else:
         res["result"]==eval_formula(math["equation"].upper(),X=int(vrec,16))
       res["col"]=None
       res["row"]=None
       result.append(res)
     return result

#table of math equations
   for i in range(1,len(math_table)):
     math=math_table[i]
     vrec=vals[i-1]
     if vrec == None: return None
     res={}
     if type(vrec) is int:
       res["result"] = eval_formula(math["equation"].upper(),X=vrec)
     else:
       res["result"] = eval_formula(math["equation"].upper(),X=int(vrec,16))
     res["col"]=math["col"]
     res["row"]=math["row"]
     result.append(res)
   return result
     

def find_map_addr_in_xdf(xdf_def, addr):	
   axes = xdf_def.get("Axes", [])
   for rec in axes:
     if ((rec["ID"] =="z") and (rec["Embedded.Address"] != None)): 
       if int(rec["Embedded.Address"],16) == int(addr,16): 
           return addr 
   return None 

def lookup_val(bindata,addr,size):
   if len(bindata)==8192: addri=int(addr,16)
   else: addri=int(addr,16)-4096
   raw_val=list(bindata[addri:addri+size])
   hex_values=[]
   for v in range(size):
       hex_values.append(hex(raw_val[v]))
   return(hex_values)


def new_unique_table_id(xdf_def): 
    tables=xdf_def.get("Tables")
    match=True
    while(match==True):
       rand_id=hex(random.getrandbits(16))
       match=False
       for table in tables:
         table_id=table.get("UniqueID")
         match=(table_id==rand_id.upper())
         if match:
            print("dupe")
            match=True
            break
    return(rand_id)    

def extract_values_from_bin(map_file):
  data=[]
  with open(map_file, "r") as ex_f:
        for line in ex_f:
            # Strip whitespace and skip empty lines
            line = line.strip()
            if not line:
                continue

            # Split into label and hex value
            if "," in line:
                entry_type,descr, addr = line.split(",", 2)
                entry_type = entry_type.strip('"')
                descr = descr.strip('"')
                addr = addr.strip('"')
                if entry_type=="MAP": data.append({"Address":addr, "Type":"MemoryRefMap", "Title":descr})
                else: data.append({"Address":addr, "Type":"FixedMap", "Title":descr})

  data[0]["Type"]="Start"
  data[0]["Description"]="Start of Maps"

  return data

def merge_data(bin_file,map_file):

  out_def=[]
  with open(bin_file, "rb") as f:
    bindata = f.read()
    with open(map_file, 'r') as file:
       map = json.load(file)
    print("# of Maps",len(map)-1,"Size of Bin FIle",len(bindata))
    print("map table starting address:",map[0]["Address"])
    for addr_index in range(1,len(map)):
        addr=int(map[addr_index]["Address"],16)
        MapType=map[addr_index]["Type"]
        hex_values=[]
        if addr<0x2000:
             if (len(bindata)==8192 and (addr>0x100) and MapType!="FixedMap"):
                if len(bindata)==8192: fixaddr=addr
                else: fixaddr=addr-4096
                raw_values = list(bindata[fixaddr:fixaddr+2*max_table+2])  # 3 bytes
                size=raw_values[1]
                #XDF_mapaddr = hex(addr+size+2)
                XDF_mapaddr = hex(addr+size+2-4096)
                hex_values=list()
                for v in range(2*size+2):
                  hex_values.append(hex(raw_values[v]))
                headers  = [str(v) for v in hex_values[2:size+2]]
                map_vals = [str(v) for v in hex_values[size+2:size*2+2]]
                match raw_values[0]:
                   case 0x3:  msg="Engine Temp"
                   case 0x11: msg="Battery Voltage"
                   case 0x12: msg="Air Temp"
                   case 0x13: msg="Coolant Temp"
                   case 0x37: msg="RPM"
                   case 0x49: msg="Engine Load"
                   case _:    msg=hex_values[0]
             else:
                if len(bindata)==8192: fixaddr=addr+4096
                else: fixaddr=addr
                raw_values = list()
                raw_values.append(hex(0))  #set address and size=0
                raw_values.append(hex(0))
                hex_values=list()
                hex_values.append(hex(0))
                hex_values.append(hex(0))
                XDF_mapaddr = hex(fixaddr)
                size=0 # need to find the size from XDF and then extract from bin
                headers=[]
                map_vals=[] # need to find the size from XDF and then extract from bin
                msg=hex(0)
             #print("debug", XDF_mapaddr, fixaddr, len(bindata),MapType, "raw",raw_values[2:2*size+2],"hex",hex_values)

             out = {
                   "Addr"         :  map[addr_index]["Address"],
                   "MapType"      :  map[addr_index]["Type"],
                   "Title"        :  map[addr_index]["Title"],
                   "Source"       :  msg,
                   "XDF mmedaddr" :  XDF_mapaddr,
                   "Source"       :  hex_values[0],
                   "Size"         :  hex_values[1],
                   "Header Values": headers,
                   "Map Values"   : map_vals
                   }

             out_def.append(out)
             #print("Addr",map[addr_index]["Address"],"Source:",msg,"XDF mmedaddr:", XDF_mapaddr,
             #"Hex bytes:", "Source:",hex_values[0], "Size:", hex_values[1], "Header Values:", headers, "Map values:", map_vals)

    return out_def


def merge_map_into_xdf(xdf_def, map_def, bindata):
    axes = xdf_def.get("Axes")
    tables = xdf_def.get("Tables")

    # Ensure Axes exists and is a list
    if axes is None:
        xdf_def["Axes"] = []
        axes = xdf_def["Axes"]
    elif isinstance(axes, dict):
        # Convert dict to list of values if needed
        xdf_def["Axes"] = list(axes.values())
        axes = xdf_def["Axes"]
    axes_new=[]

    tables = xdf_def.get("Tables")
    if tables is None or not isinstance(tables, list):
        xdf_def["Tables"] = []
    tables = xdf_def["Tables"]


    # === Pass 1: Merge existing maps ===
    for axis in axes:
        addr = axis.get("Embedded.Address")
        if addr is not None:
            val = find_val(map_def, addr)
            hdr = find_hdr(map_def, addr)
            if ((val == None) or (val=="") or (val==[])):
               val=find_val(map_def, int(addr,16)+4096)
               hdr=find_hdr(map_def, int(addr,16)+4096)
            if ((val is not None) and (val !=[])):
               axis["Values"] = val
               axis["Header"] = hdr
            else:  #case where we need to look at the map file to get the values for a fixed map from the .bin file
               addr_str = hex(addr) if isinstance(addr, int) else str(addr)
               print("Address from XDF Found in JSON Maps But No Values - extracting map values from .bin file:",addr_str,
                      "Table:", axis.get("Parent"))
               if axis.get("Embedded.Colcount") == None: 
                   size=axis.get("Embedded.Rowcount")
                   axis["Embedded.Colcount"]=1
               else: 
                    size=axis.get("Embedded.Rowcount")*axis.get("Embedded.Colcount")
               val=lookup_val(bindata,hex(int(addr,16)+4096),size)
               axis["Values"]=val
            axis["Results"]=find_result_table(val,axis.get("Math_Table"))
        axes_new.append(axis)
# GO FIND MATCHES IN "EMBEDDEDDATA"
    # Scan map_def, add new records for any new maps NOT in XDF
    for map_entry in map_def:
        addr = map_entry.get("Addr")
        if addr is not None:
           lookup=find_map_addr_in_xdf(xdf_def, addr)
           if lookup != None:	
              print("Found Table in JSON Map file that already exists in XDF Map - Ignoring",map_entry.get("Title"),addr)
           else:
              print("Found variable tables in JSON Map but not in XDF - adding ",map_entry.get("Description"), "Base Address:",addr, "Map Value Address:",map_entry.get("XDF mmedaddr"))
              size=find_size(xdf_def,map_entry.get("XDF mmedaddr"))
              size=map_entry.get("Size")
              description = "Extracted from BIN "+ addr
              title = map_entry.get("Title")
              table_id=new_unique_table_id(xdf_def)
              new_table={
                    "Title": title,
                    "Description": description,
                    "ObjectType":"Table",
                    "UniqueID": table_id,
                    "Address": addr,
                    "Flags": "0x0",
                    "CategoryMem":{} 
                        }
              x_axis =  {
                    "ObjectType": "Axis",
                    "ID": "x",
                    "UniqueID": "0x0",
                    "Parent": title,
                    #"Title": map_entry.get("Description"),
                    "Title": title,
                    "ParentID": table_id,
                    "Address": addr,
                    "Math":"X",
                    "MathVarID":"X",
                    "IndexCount":1,
                        } 
              y_axis =  {
                    "ObjectType": "Axis",
                    "ID": "y",
                    "UniqueID": "0x0",
                    "Parent": title,
                    "Title": title,
                    #"Title": map_entry.get("Description"),
                    "ParentID": table_id,
                    "Address": addr,
                    "Math":"X",
                    "MathVarID":"X",
                    "IndexCount":int(size,16),
                        } 
                    
              z_axis = {
                    "ObjectType": "Axis",
                    "ID": "z",
                    #"UniqueID": "0x0",
                    "Parent": title,
                    "Title": title,
                    #"Title": map_entry.get("Description"),
                    "ParentID": table_id,
                    "Address": addr,
                    "Math": "X",
                    "MathVarID":"X",
                    "Min": 0,
                    "Max": 255,
                    "IndexSizeBits": 8,
                    "DecimalPl": 0,
                    "Embedded.ElementSizeBits":8,
                    "Embedded.MajorStrideBits":0,
                    "Embedded.MinorStrideBits":0,
                    "Embedded.Address": map_entry.get("XDF mmedaddr"),
                    "Embedded.Colcount": 1,
                    "Embedded.Rowcount": int(size,16),
                    "Values": map_entry.get("Map Values"),
                    "Header": map_entry.get("Header Values"),
                    "Results": find_result_table(map_entry.get("Map Values"),map_entry.get("Math_Table"))
                       }
           
              tables.append(new_table)
              axes_new.append(x_axis)
              axes_new.append(y_axis)
              axes_new.append(z_axis)
    xdf_def["Axes"]=axes_new
    xdf_def["Tables"] = tables
    return xdf_def





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
                    "Title": name,
                    "Parent": parent,
                    "Field": field_name,
                    "Key": k,
                    "Value": v,
                })
        elif isinstance(value, list):
            for i, v in enumerate(value):
                self.json_rows.append({
                    "ObjectType": object_type,
                    "Title": name,
                    "Parent": parent,
                    "Field": field_name,
                    "Key": i,
                    "Value": v,
                })

    def parse(self,map_def):
        xdf_def = {
            "Header": [],
            "Tables": [],
            "Scalars": [],
            "Constants": [],
            "Axes": [],
        }
        #HEADER
        for h in self.root.findall(".//XDFHEADER"):
           cats = {cat.get("index"): cat.get("name") for cat in h.findall("CATEGORY") if cat.get("index")}
           bo=_extract_base_offset(h)
           defs=_extract_defaults(h)
           regs=_extract_region(h)
           hdr = {
               "ObjectType"  : "Header",
               "Flags"       : h.findtext("flags"),
               "FileVersion" : h.findtext("fileversion"),
               "DefTitle"    : h.findtext("deftitle"),
               "Description" : h.findtext("description"),
               "Author"      : h.findtext("author"),
               "BaseOffset"  : bo,
               "Defaults"    : defs,
               "Region"      : regs,
               "Category"  : serialize_field(cats)
                 }
           xdf_def["Header"].append(hdr)
 
        # Tables
        for t in self.root.findall(".//XDFTABLE"):
            embedded = _extract_embedded(t)
            raw_unit, norm_unit = normalize_unittype(t.findtext("unittype"))
            raw_out, norm_out = normalize_outputtype(t.findtext("outputtype"))
            catmems = {cat.get("index"): cat.get("category") for cat in t.findall("CATEGORYMEM") if cat.get("index")}
            tbl = {
                "ObjectType": "Table",
                "Title": t.findtext("title", "Unnamed Table"),
                #"UniqueID": t.get("uniqueid", "unknown"),
                "UniqueID": t.get("uniqueid"),
                "Flags": t.findtext("flags", "0x0"),
                "Description": t.findtext("description"),
                "CategoryMem":catmems
            }
            xdf_def["Tables"].append(tbl)

            # Axes
            for ax in t.findall(".//XDFAXIS"):
                embedded_ax = _extract_embedded(ax)
                raw_unit_ax, norm_unit_ax = normalize_unittype(ax.findtext("unittype"))
                raw_out_ax, norm_out_ax = normalize_outputtype(ax.findtext("outputtype"))

                labels = {int(lbl.get("index")): lbl.get("value") for lbl in ax.findall("LABEL") if lbl.get("index")}
                dalinks = [d.get("index") for d in ax.findall("DALINK") if d.get("index")]

                if embedded_ax.get("mmedaddress") is not None:
                  addr_str = hex(embedded_ax["mmedaddress"])
                  if embedded_ax["mmedcolcount"] is None:
                      columns = 1
                  else:
                      columns = embedded_ax.get("mmedcolcount", 1)
                else:
                  addr_str = None
                  columns=None

                vals=find_val(map_def,embedded_ax["mmedaddress"])
                hdrs=find_hdr(map_def,embedded_ax["mmedaddress"])
                math = _extract_math(ax)
                math_table = _extract_math_table(ax)

                axis = {
                    "ObjectType": "Axis",
                    "ID": ax.get("id", "unknown"),
                    "UniqueID": ax.get("uniqueid"),
                    #"UniqueID": ax.get("uniqueid", "unknown"),
                    "Parent": t.findtext("title"),
                    "Title": ax.findtext("title"),
                    "ParentID": t.get("uniqueid","No Parent"),
                    "Address": ax.findtext("address"),
                    "Units": ax.findtext("units"),
                    "UnitType": ax.findtext("unittype"),
                    "OutputType": ax.findtext("outputtype"),
                    "IndexCount": ax.findtext("indexcount"),
                    "Math_Table": _extract_math_table(ax),
                #    "MathVarID": _extract_varid(ax),
                    "Min": ax.findtext("min"),
                    "Max": ax.findtext("max"),
                    "IndexSizeBits": ax.findtext("indexsizebits"),
                    "DecimalPl": ax.findtext("decimalpl"),
                    "DataType": ax.findtext("datatype"),
                    "Embedded.ElementSizeBits": embedded_ax["mmedelementsizebits"],
                    "Embedded.MajorStrideBits": embedded_ax["mmedmajorstridebits"],
                    "Embedded.MinorStrideBits": embedded_ax["mmedminorstridebits"],
                    "Embedded.Address": addr_str,
                    "Embedded.Rowcount": embedded_ax["mmedrowcount"],
                    "Embedded.Colcount": columns,
                    "Embedded.TypeFlags": embedded_ax["mmedtypeflags"],
                    "Labels": serialize_field(labels),
                    "DALINK": serialize_field(dalinks),
                    }
                xdf_def["Axes"].append(axis)

                if labels:
                    self.add_json_entries("Axis", axis["ID"], axis["Parent"], "Labels", labels)
                if dalinks:
                    self.add_json_entries("Axis", axis["ID"], axis["Parent"], "DALINK", dalinks)

        # Scalars
        for s in self.root.findall(".//XDFSCALAR"):
            embedded = _extract_embedded(s)
            raw_unit, norm_unit = normalize_unittype(s.findtext("unittype"))
            raw_out, norm_out = normalize_outputtype(s.findtext("outputtype"))
            scalar = {
                "ObjectType": "Scalar",
                "UniqueID": s.get("uniqueid"),
                "Title": s.findtext("title", "Unnamed Scalar"),
                "Address": s.findtext("address"),
                "Datatype": s.findtext("datatype"),
                "Description": s.findtext("description"),
                "Units": s.findtext("units"),
                "UnitType": s.findtext("unittype"),
                "OutputType": s.findtext("outputtype"),
                "Math_Table": _extract_math_table(s),
                "Embedded.ElementSizeBits": embedded["mmedelementsizebits"],
                "Embedded.MajorStrideBits": embedded["mmedmajorstridebits"],
                "Embedded.MinorStrideBits": embedded["mmedminorstridebits"],
                "Embedded.Address": embedded_ax["mmedaddress"],
                "Labels": None,
                "DALINK": None,
                "Parent": None,
                "Size": None,
                "Value":0,
            }
            xdf_def["Scalars"].append(scalar)

        # Constants
        for c in self.root.findall(".//XDFCONSTANT"):
            embedded = _extract_embedded(c)
            raw_unit, norm_unit = normalize_unittype(c.findtext("unittype"))
            raw_out, norm_out = normalize_outputtype(c.findtext("outputtype"))
            const = {
                "UniqueID": c.get("uniqueid"),
                "ObjectType": "Constant",
                "Title": c.findtext("title", "Unnamed Constant"),
                "Address": c.findtext("address"),
                "Datatype": c.findtext("datatype"),
                "Description": c.findtext("description"),
                "Units": c.findtext("units"),
                "UnitType": c.findtext("unittype"),
                "OutputType": c.findtext("outputtype"),
                "Math_Table": _extract_math_table(c),
                "Embedded.ElementSizeBits": embedded["mmedelementsizebits"],
                "Embedded.MajorStrideBits": embedded["mmedmajorstridebits"],
                "Embedded.MinorStrideBits": embedded["mmedminorstridebits"],
                "Embedded.Address": embedded_ax["mmedaddress"],
                "Labels": None,
                "DALINK": None,
                "Parent": None,
                "Size": "1",
                "Value":0,
            }
            if dalinks:
                    self.add_json_entries("Constant", const["Title"], const["Parent"], "DALINK", dalinks)

            xdf_def["Constants"].append(const)

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
            write_sheet("Header", xdf_def["Header"], [
               "ObjectType", "Flags", "FileVersion", "DefTitle", "Description", "Author" , "BaseOffset" ,  "Defaults" , "Region" , "Category" 
            ])

            write_sheet("Tables", xdf_def["Tables"], [
                "ObjectType","Title","UniqueID","Flags","CategoryMem","Description"
            ])


            write_sheet("Scalars", xdf_def["Scalars"], [
                "Title","Address","Datatype","Units","UnitType","OutputType",
                "Description","Math_Table","Embedded.ElementSizeBits","Embedded.MajorStrideBits","Embedded.MinorStrideBits"
            ])

            write_sheet("Constants", xdf_def["Constants"], [
                "Title","Address","Datatype","Units","UnitType","OutputType",
                "Description","Math_Table","Embedded.ElementSizeBits","Embedded.MajorStrideBits","Embedded.MinorStrideBits"
            ])

            write_sheet("Axes", xdf_def["Axes"], [
                "Parent","Title","ID","Units","DataType","UnitType","OutputType","Math_Table","IndexCount","DecimalPl","Min","Max",
                "Embedded.ElementSizeBits","Embedded.MajorStrideBits","Embedded.MinorStrideBits","Labels","DALINK","Header","Values","Results",
                "Embedded.TypeFlags","Embedded.Address","Embedded.Rowcount","Embedded.Colcount"
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

def create_embedded(xdf):
    embed = {"EmbeddedData": []}

    # Add Tables, Axes, Constants, and Scalars if present
    for key in ("Header","Tables", "Axes", "Constants", "Scalars"):
        if key in xdf and isinstance(xdf[key], list):
            embed["EmbeddedData"].extend(xdf[key].copy())
        elif key in xdf and isinstance(xdf[key], dict):
            embed["EmbeddedData"].append(xdf[key].copy())

    return embed

def json_to_xdf(json_path, xdf_path):
    # Load JSON
    with open(json_path, "r") as f:
        data = json.load(f)

    xdf = ET.Element("XDFORMAT", attrib={
        "version": "1.80",
    })


    #header_data = next((item for item in data if item.get("ObjectType") == "Header"), None)
    header_data = data[0]
    if header_data:
      hdr = ET.SubElement(xdf, "XDFHEADER")

      ET.SubElement(hdr, "flags").text = str(header_data.get("Flags", "0x0"))
      ET.SubElement(hdr, "fileversion").text = str(header_data.get("FileVersion", ""))
      ET.SubElement(hdr, "deftitle").text = str(header_data.get("DefTitle", ""))
      ET.SubElement(hdr, "description").text = str(header_data.get("Description", ""))
      ET.SubElement(hdr, "author").text = str(header_data.get("Author", ""))

      # Base offset

      base = header_data.get("BaseOffset", {})
      ET.SubElement(hdr, "baseoffset").text=str(base.get("offset",""))
      ET.SubElement(hdr, "subtract").text=str(base.get("subtract",""))

      # Defaults
      defaults = header_data.get("Defaults", {})
      ET.SubElement(hdr, "DEFAULTS", attrib={
            k: str(v) for k, v in defaults.items()
      })

      # Region
      region = header_data.get("Region", {})
      ET.SubElement(hdr, "REGION", attrib={
          k: str(v) for k, v in region.items()
      })

      # ✅ CATEGORY entries directly under XDFHEADER (no wrapper)
      categories = header_data.get("Category", [])
      if isinstance(categories, list):
            for cat in categories:
                ET.SubElement(hdr, "CATEGORY", attrib={
                    "index": str(cat.get("index", "")),
                    "name": str(cat.get("name", ""))
                })
      elif isinstance(categories, dict):
            for idx, name in categories.items():
                ET.SubElement(hdr, "CATEGORY", attrib={
                    "index": str(idx),
                    "name": str(name)
                })
      else:
            try:
                parsed = json.loads(categories)
                for idx, name in parsed.items():
                    ET.SubElement(hdr, "CATEGORY", attrib={
                        "index": str(idx),
                        "name": str(name)
                    })
            except Exception:
                pass


    # Create TABLES section
    for item in data[1:]:
        if item.get("ObjectType") == "Table":

            table = ET.SubElement(xdf, "XDFTABLE", attrib={
                "uniqueid": str(item.get("UniqueID", "0x0")),
                "flags": str(item.get("Flags", "0x0"))
            })
            ET.SubElement(table, "title").text = item.get("Title", "")
            ET.SubElement(table, "description").text = item.get("Description", "")
            if item.get("CategoryMem"):
                #catmems = json.loads(item["CategoryMem"])
                catmems = item["CategoryMem"]
                for idx, catmem in catmems.items():
                    ET.SubElement(table, "categorymem", attrib={"index": idx, "category": catmem})



            # Find axes for this table
            for axis in [a for a in data if a.get("ParentID") == item.get("UniqueID")]:
                ax_elem = ET.SubElement(table, "XDFAXIS", attrib={
                    "id": axis.get("ID", ""),
                    "uniqueid": str(axis.get("UniqueID", "0x0"))
                })
                emb = ET.SubElement(ax_elem, "EMBEDDEDDATA", attrib={
                    "mmedaddress": str(axis.get("Embedded.Address", "")),
                    "mmedelementsizebits": str(axis.get("Embedded.ElementSizeBits", "")),
                    "mmedmajorstridebits": str(axis.get("Embedded.MajorStrideBits", "")),
                    "mmedminorstridebits": str(axis.get("Embedded.MinorStrideBits", "")),
                    "mmedrowcount": str(axis.get("Embedded.Rowcount", "")),
                    "mmedcolcount": str(axis.get("Embedded.Colcount", ""))
                })

                # Write decimal/min/max/etc.
                for tag in ["Units","IndexCount","DecimalPl","Min","Max","OutputType", "DataType","UnitType"]:
                    if axis.get(tag):
                        ET.SubElement(ax_elem, tag.lower()).text = str(axis[tag])

                if axis.get("DALINK"):
                    dalinks = json.loads(axis["DALINK"])
                    for idx in dalinks:
                        ET.SubElement(ax_elem, "DALINK", attrib={"index": idx})

                if axis.get("Labels"):
                    labels = json.loads(axis["Labels"])
                    for idx, label in labels.items():
                        ET.SubElement(ax_elem, "LABEL", attrib={"index": idx, "value": label})

                if axis.get("Math_Table"):
                     math_table=axis.get("Math_Table")

                     for entry in math_table:
                       attrs = {}
                       if entry.get("row") is not None:
                           attrs["row"] = str(entry["row"])
                       if entry.get("col") is not None:
                           attrs["col"] = str(entry["col"])
                       if entry.get("equation"):
                           attrs["equation"] = entry["equation"]
             
                       # Create <MATH> element
                       math_elem = ET.SubElement(ax_elem, "MATH", attrib=attrs)
             
                       # Create nested <VAR id="..."/> element
                       if entry.get("var"):
                           ET.SubElement(math_elem, "VAR", attrib={"id": entry["var"]})
             
             
             
             
             
                # Write XML to file
                tree = ET.ElementTree(xdf)
    ET.indent(tree, space="  ", level=0)
    tree.write(xdf_path, encoding="utf-8", xml_declaration=True)


# ---------------------------
# CLI
# ---------------------------
def main():
    if len(sys.argv) < 4:
        print("Usage: python xdf_to_excel.py path/to/file1.xdf path_to_file1.map path/to/file1.bin")
        sys.exit(1)

    infile = Path(sys.argv[1])
    if not infile.exists():
        raise FileNotFoundError(f"XDF File not found: {infile}")

    input_map_file = Path(sys.argv[2])
    if not input_map_file.exists():
        raise FileNotFoundError(f"Map File not found: {input_map_file}")

    bin_file = Path(sys.argv[3])
    if not bin_file.exists():
       raise FileNotFoundError(f".bin File not found: {bin_file}")

    data=extract_values_from_bin(input_map_file)

    basename = Path(infile).stem
    outxdfdirname = "output_xdf"+"/"+basename
    Path(outxdfdirname).mkdir(parents=True, exist_ok=True)

    outdirname = "output"+"/"+basename
    Path(outdirname).mkdir(parents=True, exist_ok=True)

    json_map_file_name = outdirname+"/"+basename+".map.json"

    with open(json_map_file_name, 'w') as json_file:
        json.dump(data, json_file, indent=4)

    json_file.close()

    print("# of Maps",len(data)-1)
    print("Start address of map",data[0]["Address"])
    # Extracte Data JSON
    print(f"JSON Basic Map file written to {json_map_file_name}")

    out_all_maps_file = outdirname+"/"+basename+".all_maps.json"

    print(f"All JSON Maps, Values and Headers written to {out_all_maps_file}")

    out_def = merge_data(bin_file, json_map_file_name)

    with open(out_all_maps_file, "w", encoding="utf-8") as f:
            json.dump(out_def, f, ensure_ascii=False, indent=2)
    f.close()


    map_file = out_all_maps_file
    #map_file = json_map_file_name 
    #print(map_file)
    #if not map_file.exists():
    #    raise FileNotFoundError(f"Map File not found: {map_file}")

    with open(bin_file, "rb") as f:
        bindata = f.read()

    #Read Extacted Bin maps from JSON
    map_def=ReadJSONMap(map_file)
    
    # Read XDF
    parser = XDFParser(infile)
    xdf_def = parser.parse(map_def)

    #Merge bin content maps into xdf_def struct
    xdf_def_merge = merge_map_into_xdf(xdf_def, map_def,bindata)

    embed=create_embedded (xdf_def_merge)

    xdf_def_merge["EmbeddedData"]=embed["EmbeddedData"]
   
    # Main workbook
    outfile_main = outdirname+"/"+basename+".parsed.xlsx"
    parser.to_excel(xdf_def_merge, outfile_main)
    print(f"Exported main workbook: {outfile_main}")

    # JSON workbook
    outfile_json = outdirname+"/"+basename+".json.xlsx"
    parser.to_json_excel(outfile_json)
    print(f"Exported JSON breakdown: {outfile_json}")

    # Embedded JSON
    outfile_embedded_json = outdirname+"/"+basename+"embedded.json"
    parser.to_embedded_json(xdf_def_merge, outfile_embedded_json)
    print(f"Exported EmbeddedData JSON: {outfile_embedded_json}")

    # Merged XDF 
    outfile_merged_xdf = outxdfdirname+"/"+basename+".merged.xdf"
    json_to_xdf(outfile_embedded_json,outfile_merged_xdf)
    print(f"XDF file written to {outfile_merged_xdf}")
    
if __name__ == "__main__":
    main()

