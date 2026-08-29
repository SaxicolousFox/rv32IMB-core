#!/usr/bin/env python3
"""
Resolve SystemVerilog package dependencies for a source file.

Both Verilator and Yosys read files in the order given and require a package to
be DECLARED before it is referenced.  Verilator will auto-find `rv32i_pkg.sv`
from an include directory, but it appends it after the file that imports it, so
a module whose PORT LIST uses a package type fails with

    Reference to 'alu_op_e' before declaration (IEEE 1800-2023 6.18)

even though the package is right there.  The fix is ordering, not includes:
putting ``include "rv32i_pkg.sv"` in each module would work under Verilator's
single compilation unit but risks duplicate definitions under tools that
compile each file separately.

So: scan for `import <pkg>::`, find `<pkg>.sv` in the RTL tree, and put it
first.  Kept here rather than in one caller because tb/lint_all.py and
tb/formal/run_formal.py both need exactly this, and a second copy would drift.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RTL_DIRS = ("rtl/common", "rtl/core", "rtl/ntt", "rtl/soc")

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


def package_deps(path):
    """
    Return the package files `path` imports, in declaration-safe order.

    Only direct imports are followed.  That is enough today; if a package ever
    imports another package this needs to become a transitive walk, and the
    symptom will be the same 'before declaration' error, so it will be obvious.
    """
    try:
        text = open(path).read()
    except OSError:
        return []

    # Strip line comments first: the header comments in these files DISCUSS
    # `rv32i_pkg::X` and `import rv32i_pkg::*`, and matching prose would be
    # harmless here but is exactly the kind of thing that silently starts
    # mattering later.
    code = re.sub(r"//[^\n]*", "", text)

    deps = []
    names = _IMPORT_RE.findall(code) + _QUALIFIED_RE.findall(code)
    for name in names:
        pkg = find_package(name)
        # Skip a package that IS this file, so a package importing nothing does
        # not list itself.
        if pkg and os.path.abspath(pkg) != os.path.abspath(path) and pkg not in deps:
            deps.append(pkg)
    return deps


def with_deps(path):
    """`path` preceded by every package it imports."""
    return package_deps(path) + [path]
