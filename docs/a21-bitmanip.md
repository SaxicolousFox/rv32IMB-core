# A21 — B (Zba + Zbb + Zbs) and Zbkb

`MODS_A2` A21. **34 instructions in a separate functional unit**, deliberately
not in the ALU, verified by four independent checkers.

| Check | Result |
|---|---|
| RISCOF, ratified-B suite | **29/29** — 3 `Zbc` tests excluded by name |
| RISCOF, Zbkb subset of `K` | **5/5** — 50 crypto tests not selected |
| RISCOF, total | **118 passed, 0 failed** (was 84) |
| riscv-formal | **77/77 at depth 14** (was 43) |
| `formal_bitmanip` | 29 operations vs independent expressions, **10/10 faults caught** |
| Cosim vs Spike | **30/30 byte-identical** at 25% B density |
| A3 decoder equivalence | 10⁶ words, RTL vs `model/rv32i_ref.py`, strict reserved fields |
| Mutations | **5/5 caught** |

---

## The design decision, and why it is the whole step

**B does not go in `rvntt_alu.sv`.** `MODS_A2` §3.4 reads the post-route
critical path hop by hop:

    mem_wb rd_addr → forwarding → ALU adder → ALU RESULT MUX
                   → ex_jump_target → mispredict compare → id_ex pc

12.954 ns, 19 levels, 67.6% route. **The ALU's operation-select mux is a third
of it**, and `ex_alu_y` feeds `ex_jump_target`. Folding 34 instructions in would
take `alu_op_e` from 4 bits to 6 and add roughly two LUT levels to a path with
0.003 ns of slack — in the round whose other goal is to shorten it.

So `rvntt_bitmanip.sv` has its own `bm_op_e` and joins at **`ex_result`**, which
terminates at the EX/MEM pipeline register. That is where `rvntt_muldiv` and the
Zicsr read already join, so it is an established shape rather than a new one, and
`rvntt_alu.sv` is untouched in the diff — including its `FORMAL` block, which
mirrors every operation in a second idiom and therefore stays exactly as verified.

`sh1add`/`sh2add`/`sh3add` have a fair claim to belong in the ALU — a constant
shift into the existing adder. **They are in the bitmanip unit anyway**, so the
rule "B is not in the ALU" has no exceptions for anyone to remember.

## What is implemented

29 operations, one per *operation* rather than per encoding — `rori` is `BM_ROR`
with the immediate operand, and `bseti`/`bclri`/`binvi`/`bexti` are their
register forms the same way.

**`zext.h` is `pack rd, rs1, x0`** — the same encoding, and `pack` computes
`{16'b0, rs1[15:0]}` for it. Decoding it as `BM_PACK` is correct rather than a
shortcut, which is why no `BM_ZEXTH` row appears in the decoder's table even
though the operation exists in the unit.

**The encodings were generated, not typed.** `docs/RISC-V_NTT_MODS_A2.txt`
Appendix A is produced by `riscv-none-elf-as` and `objdump`. Writing the table by
hand was tried first and put `rev8`, `brev8`, `zip` and `unzip` in `OP` when they
are `OP-IMM`.

**One decode trap, and it is the only thing a functional test cannot see.**
`clz`, `ctz`, `cpop`, `sext.b` and `sext.h` share opcode, `funct3` **and**
`imm[11:5]` entirely — `0x60059513`, `0x60159513`, `0x60259513`, `0x60459513`,
`0x60559513` — and differ only in the `rs2` field. Values 3, 6 and 7 there are
reserved and must trap. A decoder treating the field as a don't-care accepts
three illegal encodings while passing every functional test, because no
functional test ever emits one. The `unary_group_ignores_the_rs2_field` mutation
is caught by **`cocotb:decode` alone** — nothing else in the project sees it.

---

## Three findings that were not about B

### 1. `misa.B` is deliberately not set

The original plan said to set bit 1 and that not setting it would "silently drop
the entire B compliance suite". **Both halves were wrong.**

- arch-test selects suites by **regex on the ISA string**
  (`check ISA:=regex(.*I.*Zbb.*)`), not from `misa`. Nothing is dropped.
- **riscv-config 3.18.3 cannot express `B` at all** — every spelling is rejected
  as "does not match accepted canonical ordering" — and it derives the expected
  `misa` from single-letter extensions only. Setting bit 1 makes the config
  invalid and RISCOF then runs **nothing**.
- It also requires `Zbs` **after** `Zbkb`, so the canonical string is
  `RV32IMZicsr_Zba_Zbb_Zbkb_Zbs`, not alphabetical.

The extension is implemented, selected, compliance-tested and formally verified;
only the reporting bit is withheld, because asserting it would break the tooling
that proves the rest. Revisit if riscv-config gains the letter.

### 2. The cocotb runner never read its own results

`tb/cocotb/run_cocotb.py` ended in a bare `return 0`. `cocotb_tools`'
`runner.test()` runs the tests, writes `results.xml` and **returns normally
whether they passed or failed** — so *every cocotb test in this project has
reported PASS unconditionally since A1*.

It was found because a deliberately-broken wrapper printed
`TESTS=6 PASS=1 FAIL=5` and a green row in the regression table **on the same
run**. Fixing it exposed two real failures that had been hidden:
`test_alu_cocotb` and `test_immgen_cocotb` both asserted their own copy of the
spec-drift member count and **both went stale the moment A14 added the two
multi-cycle latency constants** — wrong for seven steps. The count is now
exported once as `ref.PKG_MEMBERS_CHECKED`.

**This is the sixth instance of this shape in this project** — after A10's
RISCOF exit code, A11's sby exit code, A14's `synth_ooc.sh` `DSP=0`, A19's stale
`bench_hardware` fixture and A20's stale mutation anchors. A tool's exit code is
not its verdict unless you have checked that it is.

### 3. The Spike reference plugin dropped every Z extension — for the second time

`riscof_spike_ref.py` built its ISA string by taking the single letters and
appending the literal `_zicsr`. With B added, the tests compiled
`-march=rv32izbb` from the suite's own ISA field while Spike was told
`rv32im_zicsr`, took an illegal-instruction trap on the first `clz`, and **spun
to its 600-second timeout, three tests at a time, reporting nothing**.

Its own header describes A14's version of the identical bug, with `mul` instead
of `clz`. Both times the cause was a derivation that keeps only the extensions
someone thought to enumerate. It now keeps **all** of them and then **checks that
it did**: the parsed pieces are reassembled and compared against the yaml's
string, and any mismatch is a hard refusal. A reference model with a smaller ISA
than the DUT does not fail — it hangs, and a hang reports nothing at all.

---

## Verification detail

**`formal_bitmanip`** proves all 29 operations against a second,
independently-written expression, in the idiom `rvntt_alu.sv` established: rotates
as a 64-bit concatenation rather than two shifts, `ctz` via the
isolate-lowest-set-bit identity, `orc.b` as "every byte is all-ones or all-zeros
and zero exactly when the input byte was", `zip`/`unzip` as exact inverses,
`sh*add` as a multiply. **Ten faults were injected and all ten caught** — `rev8`
↔ `brev8`, `ctz(0) = 31`, `bext` off by one, `zip` ↔ `unzip`, `rol` ↔ `ror`,
signed `min` returning max, `orc.b` needing all bits, `sh2add` shifting 3,
`packh` not zeroing the top half, `clz` counting from the wrong end.

**riscv-formal went from 43 to 77 checks at depth 14, unchanged.** Every
instruction B adds is combinational and single-cycle, so none of `MODS_A` §3.2's
divider depth problem recurs. There is no `isa_rv32imb` bundle, so
`run_riscv_formal.py` now **generates** the union of `isa_rv32ib` and
`isa_rv32iZbkb` on every run — the name has to parse as an ISA string, which is
why it is `rv32ib_Zbkb` and not `rvntt`. `M`'s models are still not enabled;
that is M6's recorded boundary, unchanged.

**The random generator constructs the shift amounts random will not draw.** A
uniform 5-bit amount is 0 one time in 32, and `rol rd, rs, 0` — which must be the
identity — is the single most likely rotate bug there is. 0 and 31 are
constructed with probability 0.30, exactly as the divide edge cases are.
`--bm-density 0` reproduces every pre-A21 seed byte for byte, which was verified
rather than assumed.

**An escaped mutation that was right to escape.** The first "rotate by zero"
mutation used `6'd32 - shamt` and escaped — correctly, because on a 32-bit target
`a << 32` is zero and that formulation is a **valid alternative implementation,
not a bug**. Replaced with `5'd31 - shamt`, which is genuinely wrong and is
caught by both checkers. Worth recording alongside A20's stall-tie finding: an
escaped mutation is sometimes evidence that the mutation was wrong, not that the
checks are weak.

## What is not done here

No Fmax has been measured since the unit was added. §3.4 predicts near zero cost
because the lane is off the critical path — **and if it is not near zero, the
lane separation was not achieved and that is the thing to fix, not the Fmax.**
A23 measures it, along with the Keccak fraction that is the entire reason the
step was taken.
