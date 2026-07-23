import ast
from pathlib import Path


def test_cli_does_not_import_adapters() -> None:
    tree = ast.parse(Path("src/capability_lab/cli/main.py").read_text())
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        if node.module is not None
    }

    assert not {name for name in imports if name.startswith("capability_lab.adapters")}
