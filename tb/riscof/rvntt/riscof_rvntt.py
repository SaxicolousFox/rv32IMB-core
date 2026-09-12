"""
RISCOF DUT plugin for the rvntt core.

`build` compiles the Verilator simulator once; `runTests` produces
`DUT-rvntt.signature` for each test.  The memory image is a build-time
parameter (rvntt_ram's INIT_FILE), so every test rewrites one image file at a
fixed path and the run is sequential (`-j1`).
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
    __version__ = "1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = kwargs.get('config')
        if config is None:
            raise SystemExit("rvntt plugin: no configuration given")

        self.pluginpath = os.path.abspath(config['pluginpath'])
        self.isa_spec = os.path.abspath(config['ispec'])
        self.platform_spec = os.path.abspath(config['pspec'])

        # Where run_riscof.py put the Verilator build and its image file,
        # passed through config.ini.
        self.sim_exe = os.path.abspath(config['simexe'])
        self.image = os.path.abspath(config['image'])
        self.runner = os.path.join(ROOT, "tb/riscof/rvntt_run.py")

        self.target_run = config.get('target_run', '1') != '0'

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
