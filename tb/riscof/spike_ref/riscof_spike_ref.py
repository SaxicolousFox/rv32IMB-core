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
  * the ISA string is built only from what this core claims -- READ OUT OF THE
    ISA YAML, not written here.  It used to be the literal 'rv32i' plus an
    optional '_zicsr', which was true and stayed true right up until A14 added
    M.  The tests were then compiled -march=rv32im from the suite's own ISA
    field while Spike was still told rv32i, so it took an illegal-instruction
    trap on the first `mul`, vectored to an unset handler, and SPUN FOREVER.
    Nothing failed; the run simply stopped making progress for 25 minutes.
    That is the same failure mode -mno-relax produces (run_riscof.py's header),
    and it has now happened twice -- so the reference also gets a TIMEOUT below.
    THE ISA STRING LIVES IN THREE PLACES and all three must move together:
    tb/riscof/rvntt/rvntt_isa.yaml, rvntt_csr.sv's MISA_VALUE, and here.  This
    one is now derived from the first, so there are really only two.

THE ENVIRONMENT IS SHARED WITH THE DUT on purpose.  `model_test.h` and
`link.ld` define the PLATFORM -- where memory is, how a test halts, where the
signature lives -- and both sides must agree on those or the comparison is
meaningless.  What must not be shared is how each side computes the answer, and
none of that is in there.
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
        # The single-letter extensions, taken from the yaml rather than
        # written down.  [A-WY] deliberately excludes X and Z, which introduce
        # multi-letter names and must not be swallowed as letters.
        raw = ispec["ISA"]
        m = re.match(r"RV32([A-WY]*)", raw.upper())
        letters = (m.group(1).lower() if m and m.group(1) else 'i')

        # EVERY Z EXTENSION, not just zicsr.  The previous version tested for
        # the literal string "zicsr" and appended it, which was true and stayed
        # true right up until A21 added Zba/Zbb/Zbkb/Zbs -- at which point the
        # tests were compiled -march=rv32izbb from the suite's own ISA field
        # while Spike was told rv32im_zicsr, took an illegal-instruction trap on
        # the first `clz`, and SPUN UNTIL THE 600-SECOND TIMEOUT.  Three tests
        # at a time, 118 tests, on a run that reports nothing while it happens.
        #
        # THAT IS THE SECOND TIME THIS EXACT BUG HAS BEEN IN THIS FUNCTION --
        # the header above describes A14's version of it, with `mul` instead of
        # `clz`.  Both times the cause was the same: a derivation that keeps
        # only the extensions someone thought to enumerate.  So this one keeps
        # ALL of them and then CHECKS that it did.
        zexts = [z.lower() for z in re.findall(r"Z[a-z]+", raw, re.I)]
        self.isa = 'rv32' + letters + ''.join('_' + z for z in zexts)

        # The completeness check that makes the above a fix rather than a patch.
        # Reconstructing the yaml's own string from the parsed pieces and
        # comparing catches ANY extension this parser does not understand,
        # including ones that do not exist yet -- which is the only way to stop
        # this happening a third time.  Refusing to run is the correct
        # behaviour: a reference model that quietly implements less than the
        # DUT does not fail, it HANGS, and a hang reports nothing at all.
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
            # A TIMEOUT, because a reference model that hangs must FAIL rather
            # than stop the run silently.  A mismatch between the ISA the test
            # is compiled for and the ISA Spike is told about does not produce
            # an error: the illegal instruction traps to an unset handler and
            # the model spins.  600 s is roughly fifty times the slowest test
            # here, so it can only fire on a real hang.
            sim = ('timeout 600 {} --isa={} +signature={} '
                   '+signature-granularity=4 {}').format(
                shlex.quote(self.dut_exe), self.isa, shlex.quote(sig), elf)
            make.add_target('@cd {}; {}; {};'.format(test_dir, cmd, sim))

        make.execute_all(self.work_dir)
