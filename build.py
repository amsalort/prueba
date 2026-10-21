"""Regenera index.html incrustando SheetJS dentro de src/template.html."""
from pathlib import Path
root = Path(__file__).parent
tpl = (root / "src/template.html").read_text(encoding="utf-8")
lib = (root / "vendor/xlsx.full.min.js").read_text(encoding="utf-8")
assert "</script" not in lib
(root / "index.html").write_text(tpl.replace("/*__XLSX__*/", lib), encoding="utf-8")
print("index.html generado")
