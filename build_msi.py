import argparse
from pathlib import Path

import msilib
from msilib import CAB, Directory, Feature, add_data, add_tables, schema, sequence, text


PRODUCT_NAME = "FB Post Collector"
PRODUCT_VERSION = "1.3.0"
MANUFACTURER = "FBPostCollector"
PRODUCT_CODE = "{B30C9CD1-8D3B-4D4C-ACB9-ED47EBC314FA}"
UPGRADE_CODE = "{E3D0C675-CB19-45FC-B24B-5224965E8B53}"


def logical_id(prefix, relative):
    value = str(relative).replace("\\", "_").replace("/", "_")
    return msilib.make_id(f"{prefix}_{value}")


def add_directory_tree(db, cab, parent, source_root, current_source, exe_key):
    relative = current_source.relative_to(source_root)
    files = sorted(
        (path for path in current_source.iterdir() if path.is_file()),
        key=lambda path: (path.name.lower() != "fbpostcollector.exe", path.name.lower()),
    )
    for path in files:
        key = parent.add_file(path.name)
        if path.name.lower() == "fbpostcollector.exe":
            exe_key["id"] = key
            exe_key["component"] = parent.component

    for child_source in sorted((path for path in current_source.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
        child_relative = child_source.relative_to(source_root)
        child = Directory(
            db,
            cab,
            parent,
            child_source.name,
            logical_id("DIR", child_relative),
            child_source.name,
        )
        add_directory_tree(db, cab, child, source_root, child_source, exe_key)


def build_msi(source_dir, output_file):
    source_dir = Path(source_dir).resolve()
    output_file = Path(output_file).resolve()
    if not (source_dir / "FBPostCollector.exe").exists():
        raise FileNotFoundError(f"找不到打包程序：{source_dir / 'FBPostCollector.exe'}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    db = msilib.init_database(
        str(output_file),
        schema,
        PRODUCT_NAME,
        PRODUCT_CODE,
        PRODUCT_VERSION,
        MANUFACTURER,
    )
    add_tables(db, sequence)
    add_tables(db, text)
    add_data(
        db,
        "Property",
        [
            ("UpgradeCode", UPGRADE_CODE),
            ("ALLUSERS", "2"),
            ("MSIINSTALLPERUSER", "1"),
            ("INSTALLLEVEL", "1"),
            ("ARPNOMODIFY", "1"),
            ("ARPNOREPAIR", "1"),
            ("ARPINSTALLLOCATION", "[INSTALLDIR]"),
            ("LIMITUI", "1"),
        ],
    )
    add_data(db, "LaunchCondition", [("VersionNT64", "This application requires 64-bit Windows.")])

    feature = Feature(db, "MainFeature", PRODUCT_NAME, "安装完整程序", 1, directory="INSTALLDIR")
    feature.set_current()
    cab = CAB("app.cab")

    target = Directory(db, cab, None, str(source_dir), "TARGETDIR", "SourceDir")
    local_app_data = Directory(db, cab, target, ".", "LocalAppDataFolder", ".")
    programs = Directory(db, cab, local_app_data, ".", "ProgramsDir", "Programs")
    install_dir = Directory(db, cab, programs, ".", "INSTALLDIR", "FBPOST~1|FBPostCollector")

    exe_key = {}
    add_directory_tree(db, cab, install_dir, source_dir, source_dir, exe_key)
    if not exe_key.get("id"):
        raise RuntimeError("安装文件中没有找到 FBPostCollector.exe")

    program_menu = Directory(db, cab, target, ".", "ProgramMenuFolder", ".")
    app_menu = Directory(db, cab, program_menu, ".", "AppMenuFolder", "FBPOST~1|FB Post Collector")
    add_data(
        db,
        "Shortcut",
        [
            (
                "StartMenuShortcut",
                app_menu.logical,
                "FBPOST~1|FB Post Collector",
                exe_key["component"],
                f"[#{exe_key['id']}]",
                None,
                "Launch FB Post Collector",
                None,
                None,
                None,
                1,
                "INSTALLDIR",
            )
        ],
    )

    cab.commit(db)
    db.Commit()
    return output_file


def main():
    parser = argparse.ArgumentParser(description="生成 FBPostCollector Windows MSI 安装包")
    parser.add_argument("--source", default="dist/FBPostCollector")
    parser.add_argument("--output", default="dist/FBPostCollector-Setup-v1.3.0.msi")
    args = parser.parse_args()
    result = build_msi(args.source, args.output)
    print(result)


if __name__ == "__main__":
    main()
