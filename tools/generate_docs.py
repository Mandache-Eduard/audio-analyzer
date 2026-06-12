# tools/gen_docs.py
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

# =========================
# Repo layout (static)
# =========================
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
SRC_DIR = REPO_DIR / "src"
DOCS_DIR = REPO_DIR / "docs"

MODULES = [
    "audio_loader.py",
    "file_status_determination.py",
    "spectrogram_generator.py",
    "main.py",
    "analyse_modes.py",
    "data_and_error_logging.py",
    "audio_frame_analysis.py",
]

AUTO_BEGIN = "<!-- AUTO-GENERATED:BEGIN -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"


@dataclass
class FuncInfo:
    name: str
    params: List[str]
    returns: str | None
    calls: Set[str] = field(default_factory=set)


@dataclass
class ConstInfo:
    name: str
    type_str: str | None
    value_repr: str | None


@dataclass
class ModuleInfo:
    filename: str
    imports: Set[str] = field(default_factory=set)
    constants: List[ConstInfo] = field(default_factory=list)
    functions: Dict[str, FuncInfo] = field(default_factory=dict)
    func_calls_qualified: Set[Tuple[str, str]] = field(default_factory=set)  # (caller, callee)
    module_deps: Set[str] = field(default_factory=set)  # other module basenames (no .py)


def _safe_unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


class Analyzer(ast.NodeVisitor):
    """
    Extracts:
      - imports
      - module-level constants/variables (top-level only; ignores class bodies)
      - function signatures + intra-module call graph
      - module deps (best-effort)
    """

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.info = ModuleInfo(filename=module_name)

        self._current_func: str | None = None
        self._class_depth: int = 0  # >0 means we're inside class body
        self._seen_toplevel_names: Set[str] = set()

    # ---------- imports ----------
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.info.imports.add(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        for alias in node.names:
            self.info.imports.add(f"{mod}.{alias.name}" if mod else alias.name)
        self.generic_visit(node)

    # ---------- class scope ----------
    def visit_ClassDef(self, node: ast.ClassDef):
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1

    # ---------- module-level assignments ----------
    def visit_Assign(self, node: ast.Assign):
        # Only capture TOP-LEVEL (not inside functions, not inside classes)
        if self._current_func is None and self._class_depth == 0:
            # Only capture simple NAME = <expr>
            for t in node.targets:
                if isinstance(t, ast.Name):
                    name = t.id
                    if name not in self._seen_toplevel_names:
                        self._seen_toplevel_names.add(name)
                        self.info.constants.append(
                            ConstInfo(
                                name=name,
                                type_str=None,  # Assign has no annotation
                                value_repr=_safe_unparse(node.value),
                            )
                        )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        # Only capture TOP-LEVEL (not inside functions, not inside classes)
        if self._current_func is None and self._class_depth == 0 and isinstance(node.target, ast.Name):
            name = node.target.id
            if name not in self._seen_toplevel_names:
                self._seen_toplevel_names.add(name)
                self.info.constants.append(
                    ConstInfo(
                        name=name,
                        type_str=_safe_unparse(node.annotation),
                        value_repr=_safe_unparse(node.value),
                    )
                )
        self.generic_visit(node)

    # ---------- functions ----------
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._enter_function(node)

    def _enter_function(self, node: ast.AST):
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        prev = self._current_func
        self._current_func = node.name

        params: List[str] = []
        for a in node.args.posonlyargs + node.args.args:
            params.append(a.arg)
        if node.args.vararg:
            params.append("*" + node.args.vararg.arg)
        for a in node.args.kwonlyargs:
            params.append(a.arg)
        if node.args.kwarg:
            params.append("**" + node.args.kwarg.arg)

        returns = _safe_unparse(getattr(node, "returns", None))

        self.info.functions[node.name] = FuncInfo(
            name=node.name,
            params=params,
            returns=returns,
        )

        self.generic_visit(node)
        self._current_func = prev

    def visit_Call(self, node: ast.Call):
        if self._current_func is not None:
            callee: str | None = None

            # foo()
            if isinstance(node.func, ast.Name):
                callee = node.func.id

            # mod.foo() or obj.foo()
            elif isinstance(node.func, ast.Attribute):
                callee = node.func.attr
                if isinstance(node.func.value, ast.Name):
                    base = node.func.value.id
                    # record module dependency if "base.py" exists in src/
                    if (SRC_DIR / f"{base}.py").exists():
                        self.info.module_deps.add(base)

            if callee:
                self.info.functions[self._current_func].calls.add(callee)
                self.info.func_calls_qualified.add((self._current_func, callee))

        self.generic_visit(node)


def analyze_module(path: Path) -> ModuleInfo:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    analyzer = Analyzer(path.name)
    analyzer.visit(tree)

    # module deps based on imports too (best-effort)
    for imp in analyzer.info.imports:
        base = imp.split(".")[0]
        if (SRC_DIR / f"{base}.py").exists():
            analyzer.info.module_deps.add(base)

    return analyzer.info


def render_module_mermaid(mi: ModuleInfo) -> str:
    lines: List[str] = []
    lines.append("```mermaid")
    lines.append("flowchart TD")
    lines.append("    classDef ok fill:#d4f4dd,stroke:#2e7d32;")
    lines.append("    classDef err fill:#fde0e0,stroke:#c62828;")
    lines.append("")
    lines.append(f'    M["{mi.filename}"]:::ok')

    for fn in sorted(mi.functions.keys()):
        safe_id = fn.replace("-", "_")
        lines.append(f'    F_{safe_id}["{fn}()"]:::ok')
        lines.append(f"    M --> F_{safe_id}")

    fn_names = set(mi.functions.keys())
    for caller, callee in sorted(mi.func_calls_qualified):
        if caller in fn_names and callee in fn_names:
            c1 = caller.replace("-", "_")
            c2 = callee.replace("-", "_")
            lines.append(f"    F_{c1} --> F_{c2}")

    lines.append("```")
    return "\n".join(lines)


def render_overview_mermaid(mods: Dict[str, ModuleInfo]) -> str:
    lines: List[str] = []
    lines.append("```mermaid")
    lines.append("flowchart TD")
    lines.append("    classDef ok fill:#d4f4dd,stroke:#2e7d32;")
    lines.append("    classDef err fill:#fde0e0,stroke:#c62828;")
    lines.append("")
    for m in sorted(mods.keys()):
        lines.append(f'    {m}["{m}.py"]:::ok')
    for m, mi in mods.items():
        for dep in sorted(mi.module_deps):
            if dep in mods and dep != m:
                lines.append(f"    {m} --> {dep}")
    lines.append("```")
    return "\n".join(lines)


def replace_autoblock(md_text: str, new_block: str) -> str:
    if AUTO_BEGIN in md_text and AUTO_END in md_text:
        pre = md_text.split(AUTO_BEGIN)[0]
        post = md_text.split(AUTO_END)[1]
        return pre + AUTO_BEGIN + "\n" + new_block + "\n" + AUTO_END + post
    return md_text.rstrip() + "\n\n" + AUTO_BEGIN + "\n" + new_block + "\n" + AUTO_END + "\n"


def ensure_doc_file(path: Path, title: str) -> None:
    if path.exists():
        return
    path.write_text(f"# {title}\n\n{AUTO_BEGIN}\n\n{AUTO_END}\n", encoding="utf-8")


def generate() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    mods: Dict[str, ModuleInfo] = {}
    missing: List[str] = []

    for py in MODULES:
        p = SRC_DIR / py
        if not p.exists():
            missing.append(py)
            continue
        mods[p.stem] = analyze_module(p)

    # diagnostics (always visible)
    print("REPO_DIR =", REPO_DIR)
    print("SRC_DIR  =", SRC_DIR)
    print("DOCS_DIR =", DOCS_DIR)
    print("Found modules:", sorted(mods.keys()))
    print("Missing files:", missing)

    # overview.md
    overview_path = DOCS_DIR / "overview.md"
    ensure_doc_file(overview_path, "Project Overview")
    overview_text = overview_path.read_text(encoding="utf-8")

    module_index = "\n".join([f"- [`{m}.md`]({m}.md)" for m in sorted(mods.keys())]) or "- (none found)"
    overview_block = "\n".join(
        [
            "## Global Module Call Graph",
            render_overview_mermaid(mods),
            "",
            "## Module Index",
            module_index,
        ]
    )
    overview_path.write_text(replace_autoblock(overview_text, overview_block), encoding="utf-8")

    # per-module docs
    for m, mi in mods.items():
        md_path = DOCS_DIR / f"{m}.md"
        ensure_doc_file(md_path, f"{m}.py")
        md_text = md_path.read_text(encoding="utf-8")

        imports = "\n".join([f"- `{x}`" for x in sorted(mi.imports)]) or "- (none detected)"

        # Include type if present: NAME: type = value
        const_lines: List[str] = []
        for c in mi.constants:
            if c.type_str and c.value_repr is not None:
                const_lines.append(f"- `{c.name}: {c.type_str} = {c.value_repr}`")
            elif c.type_str and c.value_repr is None:
                const_lines.append(f"- `{c.name}: {c.type_str}`")
            elif (not c.type_str) and c.value_repr is not None:
                const_lines.append(f"- `{c.name} = {c.value_repr}`")
            else:
                const_lines.append(f"- `{c.name}`")
        consts = "\n".join(const_lines) or "- (none detected)"

        # IMPORTANT CHANGE:
        # Do NOT generate per-function sections that get overwritten.
        # Only generate a stable "Function Inventory" list. Detailed descriptions stay manual.
        inv_lines: List[str] = []
        for fn in sorted(mi.functions.values(), key=lambda x: x.name):
            sig = f"`{fn.name}({', '.join(fn.params)})`"
            if fn.returns:
                sig += f" -> `{fn.returns}`"
            inv_lines.append(f"- {sig}")
        func_inventory = "\n".join(inv_lines) or "- (no functions detected)"

        block = "\n".join(
            [
                "## External Dependencies (auto)",
                "### Imports",
                imports,
                "",
                "## Module-level Constants and Variables (auto)",
                consts,
                "",
                "## Module Workflow (auto: call graph)",
                render_module_mermaid(mi),
                "",
                "## Function Inventory (auto)",
                func_inventory,
            ]
        )

        md_path.write_text(replace_autoblock(md_text, block), encoding="utf-8")


if __name__ == "__main__":
    generate()
