"""
RISCOF reference plugin: Spike.

Derived from riscv-arch-test's `spike_simple`: compiles each test exactly as
the DUT plugin does and runs Spike with `+signature=`.  Two deliberate
differences: no `--misaligned` (this core traps on misaligned accesses, and
the reference must be configured the same way), and the ISA string is
derived from tb/riscof/rvntt/rvntt_isa.yaml rather than written here, with a
completeness check and a timeout -- a reference told a narrower ISA than the
DUT does not fail, it traps to an unset handler and spins.

The environment (`model_test.h`, `link.ld`) is shared with the DUT on
purpose: both sides must agree on the platform.
"""
import os
import re
import shlex
import logging

import riscof.utils as utils
from riscof.pluginTemplate import pluginTemplate

logger = logging.getLogger()

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
ENV = os.path.join(ROOT, "tb/riscof/rvntt/env")


class spike_ref(pluginTemplate):
    __model__ = "spike"
    __version__ = "1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = kwargs.get('config')
        if config is None:
            raise SystemExit("spike_ref plugin: no configuration given")
        self.pluginpath = os.path.abspath(config['pluginpath'])
        self.dut_exe = os.path.join(config.get('PATH', ''), 'spike')
        self.num_jobs = str(config.get('jobs', 1))

    def initialise(self, suite, work_dir, archtest_env):
        self.work_dir = work_dir
        self.suite_dir = suite
        self.compile_cmd = (
            # -mno-relax is required: arch_test.h's LA macro wraps its `.align`
            # in `.option rvc`, and with linker relaxation the linker fills the
            # alignment with compressed nops, which a core with no C extension
            # traps on.
            'riscv-none-elf-gcc -march={0} -mno-relax'
            ' -static -mcmodel=medany -fvisibility=hidden -nostdlib'
            ' -nostartfiles -g'
            ' -T ' + ENV + '/link.ld'
            ' -I ' + ENV + '/'
            ' -I ' + archtest_env + ' {1} -o {2} {3}')

    def build(self, isa_yaml, platform_yaml):
        ispec = utils.load_yaml(isa_yaml)['hart0']
        if 64 in ispec['supported_xlen']:
            raise SystemExit("spike_ref is configured for RV32 only")
        # The single-letter extensions, from the yaml.  [A-WY] excludes X and
        # Z, which introduce multi-letter names.
        raw = ispec["ISA"]
        m = re.match(r"RV32([A-WY]*)", raw.upper())
        letters = (m.group(1).lower() if m and m.group(1) else 'i')

        # Every Z extension, not an enumerated subset.
        zexts = [z.lower() for z in re.findall(r"Z[a-z]+", raw, re.I)]
        self.isa = 'rv32' + letters + ''.join('_' + z for z in zexts)

        # Completeness check: rebuilding the yaml's own string from the parsed
        # pieces catches any extension this parser does not understand.
        rebuilt = ('rv32' + letters + ''.join(zexts)).lower()
        if rebuilt != raw.replace('_', '').lower():
            raise SystemExit(
                "spike_ref: cannot express the DUT's ISA to Spike.\n"
                "  yaml says : %s\n"
                "  parsed as : %s\n"
                "  rebuilt   : %s\n"
                "Something in the ISA string is neither a single-letter\n"
                "extension nor a Z-extension, and running with the parsed\n"
                "subset would give the reference a SMALLER ISA than the DUT --\n"
                "which does not fail, it hangs on an illegal instruction."
                % (raw, self.isa, rebuilt))

        logger.info("spike_ref: reference ISA is %s (from %s)"
                    % (self.isa, ispec["ISA"]))
        self.compile_cmd += ' -mabi=ilp32'

    def runTests(self, testList):
        mk = os.path.join(self.work_dir, "Makefile." + self.name[:-1])
        if os.path.exists(mk):
            os.remove(mk)
        make = utils.makeUtil(makefilePath=mk)
        make.makeCommand = 'make -k -j' + self.num_jobs

        for testname in testList:
            entry = testList[testname]
            test_dir = entry['work_dir']
            elf = 'ref.elf'
            sig = os.path.join(test_dir, self.name[:-1] + ".signature")

            macros = ' -D' + " -D".join(entry['macros'])
            cmd = self.compile_cmd.format(entry['isa'].lower(),
                                          entry['test_path'], elf, macros)
            # A timeout, because a reference model that hangs must fail rather
            # than stop the run silently.  600 s is ~fifty times the slowest test.
            sim = ('timeout 600 {} --isa={} +signature={} '
                   '+signature-granularity=4 {}').format(
                shlex.quote(self.dut_exe), self.isa, shlex.quote(sig), elf)
            make.add_target('@cd {}; {}; {};'.format(test_dir, cmd, sim))

        make.execute_all(self.work_dir)
