"""
RISCOF DUT plugin for the rvntt core (plan A10).

WHAT RISCOF ASKS OF A DUT, and what this does about each of it:

  * `build` -- be told the ISA and platform YAMLs and get ready.  Here that
    means compiling the Verilator simulator ONCE.  RISCOF calls runTests with
    every selected test at once, so building per test would dominate the run.

  * `runTests` -- for each test, produce `DUT-rvntt.signature`.  RISCOF then
    diffs it against the reference model's file for the same test.

THE SIMULATOR'S MEMORY IMAGE IS A BUILD-TIME PARAMETER, which is the one place
this plugin has to be careful.  `rvntt_ram`'s INIT_FILE is a module parameter,
not a plusarg -- deliberately, because a plusarg is not synthesisable and that
file is meant to elaborate under Vivado.  So every test rewrites one image file
at a fixed path, and the run is therefore SEQUENTIAL: `jobs` is forced to 1 in
the generated make command.  38 tests take a few seconds each; the alternative
is 38 Verilator builds.
"""
import os
import shlex
import logging

import riscof.utils as utils
from riscof.pluginTemplate import pluginTemplate

logger = logging.getLogger()

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


class rvntt(pluginTemplate):
    __model__ = "rvntt"
    __version__ = "A10"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = kwargs.get('config')
        if config is None:
            raise SystemExit("rvntt plugin: no configuration given")

        self.pluginpath = os.path.abspath(config['pluginpath'])
        self.isa_spec = os.path.abspath(config['ispec'])
        self.platform_spec = os.path.abspath(config['pspec'])

        # Where run_riscof.py put the Verilator build and the image file it was
        # built against.  Passed through config.ini rather than guessed, so the
        # plugin has no opinion about the project's directory layout.
        self.sim_exe = os.path.abspath(config['simexe'])
        self.image = os.path.abspath(config['image'])
        self.runner = os.path.join(ROOT, "tb/riscof/rvntt_run.py")

        self.target_run = config.get('target_run', '1') != '0'

    def initialise(self, suite, work_dir, archtest_env):
        self.work_dir = work_dir
        self.suite_dir = suite
        self.compile_cmd = (
            # -mno-relax IS REQUIRED, and the reason is worth writing down.
            # arch_test.h's LA macro wraps its `.align` in `.option rvc` so the
            # padding can be two bytes when it needs to be, then switches back
            # with `.option norvc`.  With linker relaxation on, the alignment
            # becomes an R_RISCV_ALIGN relocation that the LINKER fills -- and
            # the linker fills it with COMPRESSED nops, because the relocation
            # was recorded while rvc was still enabled.  The result is c.nop
            # instructions in the instruction stream of a test for a core with
            # no C extension: Spike faults on the first one, vectors to the
            # unset mtvec at address 0, and spins there forever.  With
            # -mno-relax the assembler resolves the alignment itself, under the
            # norvc that was intended.
            'riscv-none-elf-gcc -march={0} -mno-relax'
            ' -static -mcmodel=medany -fvisibility=hidden -nostdlib'
            ' -nostartfiles -g'
            ' -T ' + self.pluginpath + '/env/link.ld'
            ' -I ' + self.pluginpath + '/env/'
            ' -I ' + archtest_env + ' {1} -o {2} {3}')

    def build(self, isa_yaml, platform_yaml):
        ispec = utils.load_yaml(isa_yaml)['hart0']
        self.xlen = '64' if 64 in ispec['supported_xlen'] else '32'
        if self.xlen != '32':
            raise SystemExit("rvntt is RV32 only; the ISA yaml says otherwise")
        self.compile_cmd += ' -mabi=ilp32'

    def runTests(self, testList):
        mk = os.path.join(self.work_dir, "Makefile." + self.name[:-1])
        if os.path.exists(mk):
            os.remove(mk)
        make = utils.makeUtil(makefilePath=mk)
        # -j1: see this file's header.  The image path is shared.
        make.makeCommand = 'make -k -j1'

        for testname in testList:
            entry = testList[testname]
            test_dir = entry['work_dir']
            elf = 'my.elf'
            sig = os.path.join(test_dir, self.name[:-1] + ".signature")
            log = os.path.join(test_dir, self.name[:-1] + ".log")

            macros = ' -D' + " -D".join(entry['macros'])
            # The arch-test ISA string carries `zicsr` as a separate extension;
            # gcc wants it that way too, so it passes through unchanged.
            cmd = self.compile_cmd.format(entry['isa'].lower(), entry['test_path'],
                                          elf, macros)

            if self.target_run:
                run = ('python3 {} --exe {} --image {} --elf {} --sig {} --log {}'
                       .format(shlex.quote(self.runner),
                               shlex.quote(self.sim_exe),
                               shlex.quote(self.image),
                               elf, shlex.quote(sig), shlex.quote(log)))
            else:
                run = 'echo "NO RUN"'

            make.add_target('@cd {}; {}; {};'.format(test_dir, cmd, run))

        make.execute_all(self.work_dir)
