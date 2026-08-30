"""
RISCOF REFERENCE plugin: Spike (plan A10).

Spike rather than Sail.  The plan names either, Sail's C emulator is a large
build, and Spike is already this project's golden model -- for A5's lockstep
cosimulation, for the riscv-tests runs in A9, and for the whole C track.  One
reference model across the project is worth more than a second opinion that
nobody else uses.

This is thin glue, derived from riscv-arch-test's own `spike_simple` plugin: it
compiles each test exactly as the DUT plugin does and runs Spike with
`+signature=` to dump the signature region.  It exists rather than using
`spike_simple` directly because that plugin reads `ispec['PMP']`
unconditionally, and riscv-config 3.18 has no such key -- so using it would mean
describing this core's ISA in an older schema than the installed validator
accepts, which is the wrong thing to bend.

TWO DELIBERATE DIFFERENCES FROM `spike_simple`:

  * no `--misaligned`.  The upstream plugin lets Spike complete misaligned
    accesses; this core traps on them, and a reference configured differently
    from the DUT is a reference that hides exactly the disagreements worth
    finding.  (The RV32I suite has no misaligned tests, so today this changes
    nothing -- which is the right time to get it right.)
  * the ISA string is built only from what this core claims.  There is no long
    if-chain over extensions it does not have.

THE ENVIRONMENT IS SHARED WITH THE DUT on purpose.  `model_test.h` and
`link.ld` define the PLATFORM -- where memory is, how a test halts, where the
signature lives -- and both sides must agree on those or the comparison is
meaningless.  What must not be shared is how each side computes the answer, and
none of that is in there.
"""
import os
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
    __version__ = "A10"

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
            ' -T ' + ENV + '/link.ld'
            ' -I ' + ENV + '/'
            ' -I ' + archtest_env + ' {1} -o {2} {3}')

    def build(self, isa_yaml, platform_yaml):
        ispec = utils.load_yaml(isa_yaml)['hart0']
        if 64 in ispec['supported_xlen']:
            raise SystemExit("spike_ref is configured for RV32 only")
        self.isa = 'rv32i'
        if "Zicsr" in ispec["ISA"]:
            self.isa += '_zicsr'
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
            sim = '{} --isa={} +signature={} +signature-granularity=4 {}'.format(
                shlex.quote(self.dut_exe), self.isa, shlex.quote(sig), elf)
            make.add_target('@cd {}; {}; {};'.format(test_dir, cmd, sim))

        make.execute_all(self.work_dir)
