"""Regenerate resources/register_map_template.xlsx from the built-in template."""

from pathlib import Path

from better_scope.decode.regmap.excel import write_template

TEMPLATE = Path(__file__).resolve().parents[1] / "resources" / "register_map_template.xlsx"


def main() -> None:
    """Write the template workbook next to the other resources."""
    write_template(TEMPLATE)


if __name__ == "__main__":
    main()
