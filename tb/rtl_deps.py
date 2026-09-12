#!/usr/bin/env python3
"""
Resolve SystemVerilog package dependencies for a source file.

Verilator and Yosys read files in the order given and require a package to be
declared before it is referenced; Verilator's auto-find appends the package
after the importer, which fails for a module whose port list uses a package
type.  So: find every package in the RTL tree and put it first.  Shared by
tb/lint_all.py and tb/formal/run_formal.py.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RTL_DIRS = ("rtl/common", "rtl/core", "rtl/soc")

# Two ways a file can depend on a package, both of which need it read first:
#   * `import rv32i_pkg::*;`
#   * a fully-qualified reference, `rv32i_pkg::ALU_ADD`
# The second form matters because Yosys rejects `import` entirely (in both the
# module-header and module-body positions), so the RTL here uses qualified
# references and has no import statement to find.
_IMPORT_RE = re.compile(r"\bimport\s+(\w+)\s*::", re.M)
_QUALIFIED_RE = re.compile(r"\b([A-Za-z_]\w*)::")


def find_package(name):
    """Locate `<name>.sv` in the RTL tree, or return None."""
    for d in RTL_DIRS:
        p = os.path.join(ROOT, d, name + ".sv")
        if os.path.exists(p):
            return p
    return None


def all_packages():
    """Every `<name>.sv` in the RTL tree that declares `package <name>;`."""
    found = []
    for d in RTL_DIRS:
        dp = os.path.join(ROOT, d)
        if not os.path.isdir(dp):
            continue
        for f in sorted(os.listdir(dp)):
            if not f.endswith(".sv"):
                continue
            p = os.path.join(dp, f)
            name = f[:-3]
            try:
                if re.search(r"^\s*package\s+" + re.escape(name) + r"\s*;",
                             open(p).read(), re.M):
                    found.append(p)
            except OSError:
                pass
    return found


def package_deps(path):
    """
    Return the package files `path` needs, in declaration-safe order.

    Every package in the tree is put first: a top level that merely
    instantiates rvntt_core references no package itself, yet Verilator pulls
    rvntt_core in and fails on it.  Adequate until two packages depend on each
    other, at which point this needs a topological sort.
    """
    try:
        text = open(path).read()
    except OSError:
        return []

    # Strip line comments first: header comments discuss `rv32i_pkg::X`.
    code = re.sub(r"//[^\n]*", "", text)

    deps = []
    names = _IMPORT_RE.findall(code) + _QUALIFIED_RE.findall(code)
    for name in names:
        pkg = find_package(name)
        if pkg and pkg not in deps:
            deps.append(pkg)
    for pkg in all_packages():
        if pkg not in deps:
            deps.append(pkg)
    # A package must not list itself.
    return [p for p in deps if os.path.abspath(p) != os.path.abspath(path)]


def with_deps(path):
    """`path` preceded by every package it imports."""
    return package_deps(path) + [path]
