"""Writes the details Windows shows for Synked.exe (right-click > Properties > Details):
product name, version, company and copyright. build.bat runs this and hands the file to
PyInstaller."""
import sys
import time

from version import APP, COMPANY, CREDIT, VERSION


def version_file_text() -> str:
    nums = [int(x) for x in VERSION.split(".") if x.isdigit()][:4]
    nums += [0] * (4 - len(nums))
    t = tuple(nums)
    year = time.strftime("%Y")

    def s(key, val):
        return f"          StringStruct('{key}', '{val}'),\n"

    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,\n"
        "                    fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        + s("CompanyName", COMPANY)
        + s("FileDescription", f"{APP} - keeps your folders in sync")
        + s("FileVersion", VERSION)
        + s("InternalName", APP)
        + s("LegalCopyright", f"(c) {year} {COMPANY}. {CREDIT}")
        + s("OriginalFilename", f"{APP}.exe")
        + s("ProductName", APP)
        + s("ProductVersion", VERSION)
        + "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "exe_info.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write(version_file_text())
