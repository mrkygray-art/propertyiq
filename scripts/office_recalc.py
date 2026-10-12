# -*- coding: utf-8 -*-
"""Recalculate a spreadsheet in OpenOffice and dump every cell's result as JSON.

Used by the tests to prove the Excel export's formulas are live and correct: OpenOffice
opens the .xlsx (which contains formulas only, no cached results), recalculates everything,
and we compare its answers to the Python engine.

Runs under OpenOffice's bundled Python 2.7 (it ships the UNO bridge):
  "C:\\Program Files (x86)\\OpenOffice 4\\program\\python.exe" scripts\\office_recalc.py in.xlsx out.json
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

OFFICE = r"C:\Program Files (x86)\OpenOffice 4\program"
sys.path.insert(0, OFFICE)

import uno  # noqa: E402
from com.sun.star.beans import PropertyValue  # noqa: E402

FORMULA, VALUE, TEXT = "FORMULA", "VALUE", "TEXT"


def col_name(c):
    s = ""
    c += 1
    while c:
        c, rem = divmod(c - 1, 26)
        s = chr(65 + rem) + s
    return s


def main(src, dst):
    profile = tempfile.mkdtemp(prefix="piq-oo-")
    pipe = "piq_recalc_%d" % os.getpid()
    proc = subprocess.Popen([
        os.path.join(OFFICE, "soffice.exe"), "-headless", "-invisible", "-nologo", "-norestore", "-nofirststartwizard",
        "-env:UserInstallation=file:///" + profile.replace("\\", "/"),
        "-accept=pipe,name=%s;urp;" % pipe,
    ])
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        ctx = None
        for _ in range(90):
            try:
                ctx = resolver.resolve("uno:pipe,name=%s;urp;StarOffice.ComponentContext" % pipe)
                break
            except Exception:
                time.sleep(1)
        if ctx is None:
            raise SystemExit("OpenOffice did not start")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        hidden = PropertyValue()
        hidden.Name, hidden.Value = "Hidden", True
        doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(os.path.abspath(src)), "_blank", 0, (hidden,))
        doc.calculateAll()
        out = {}
        sheets = doc.getSheets()
        for i in range(sheets.getCount()):
            sheet = sheets.getByIndex(i)
            cursor = sheet.createCursor()
            cursor.gotoEndOfUsedArea(False)
            end = cursor.getRangeAddress()
            cells = {}
            for r in range(end.EndRow + 1):
                for c in range(end.EndColumn + 1):
                    cell = sheet.getCellByPosition(c, r)
                    kind = cell.getType().value
                    if kind == "EMPTY":
                        continue
                    addr = "%s%d" % (col_name(c), r + 1)
                    if cell.getError():
                        cells[addr] = {"error": cell.getError()}
                    elif kind == VALUE or (kind == FORMULA and cell.FormulaResultType.value == "VALUE"):
                        cells[addr] = cell.getValue()
                    else:
                        cells[addr] = cell.getString()
            out[sheet.getName()] = cells
        doc.close(True)
        with open(dst, "w") as f:
            json.dump(out, f)
    finally:
        try:
            desktop.terminate()
        except Exception:
            pass
        time.sleep(1)
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
