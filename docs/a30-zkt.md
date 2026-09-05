# A30 — `Zkt`: the data-independent-latency proof

**Last, deliberately.** `Zkt` is a proof *about* everything else in the round and
has to be re-proved after every functional addition, so it is proved once,
at the end, against the machine that ships.

---

## The claim, and the two boundaries it is not made without

> **Boundary 1 — `Zkt` is a statement about listed instructions' latency, and
> not about control flow.** It says nothing about which instructions a program
> executes or in what order. Constant-time *code* is the programmer's
> obligation; `Zkt` only guarantees that the primitives it lists do not leak
> through their own timing. Branches are excluded from the list by name.
>
> **Boundary 2 — this core has a branch predictor, and it introduces
> data-dependent timing that `Zkt` does not cover and this core does not
> remove.** A19's 256-entry BTB, its 2-bit counters and its 8-entry RAS are
> **architecturally invisible state that persists across whatever runs on the
> core.** There is no flush, no partition and no privilege boundary — there is
> only machine mode. So: a mispredict costs 2 cycles and whether it occurs
> depends on branch history, which depends on data; and BTB and counter state
> **survive from one program to the next**, which is the standard precondition
> for a branch-predictor side channel. A `Zkt`-conformant core is not a
> constant-time machine, and this one specifically is not.

**A `Zkt` claim that omits those is a security falsehood, which is worse than no
claim.** They are quoted verbatim from `MODS_A2` §3.5 rather than paraphrased,
because paraphrasing a security boundary is how it gets weakened.

**Boundary 2 is not a defect being confessed — it is what `Zkt` is for.** The
extension exists precisely because "constant-time" is not a property a whole
machine has; it is a property individual instructions can be guaranteed to have,
so that code written to avoid secret-dependent control flow can rely on its
primitives.

---

## What is claimed, and what is reported as not-implemented

The list was read from the ratified specification, not recalled — `MODS_A2` §3.5
records three properties of it that reconstructing from memory would have got
wrong, the sharpest being that **`Zkt` excludes division and remainder**
(*"Cryptographers typically assume division to be variable-time"*).

**Implemented, and therefore claimed — 37 instructions:**

| group | instructions |
|---|---|
| RV32I arithmetic, logical, shift | `add addi sub and andi or ori xor xori sll slli srl srli sra srai slt slti sltu sltiu lui auipc` |
| M — **multiply only** | `mul mulh mulhsu mulhu` |
| Zbkb, as A21 implemented it | `ror rol rori andn orn xnor pack packh brev8 rev8 zip unzip` |

**Not implemented — reported as such, never as passing:**

| group | instructions |
|---|---|
| Zbc | `clmul clmulh` |
| Zbkx | `xperm4 xperm8` |
| Zkn / Zks | the AES, SHA-2, SM3 and SM4 instructions |
| C | the compressed forms of any listed instruction |

`tb/formal/run_zkt.py --list` prints both tables, so the scope of the claim is
generated from the same place that proves it.

**Zbb-only and Zba and Zbs are simply out of scope.** `sh1add`/`sh2add`/`sh3add`,
`clz`/`ctz`/`cpop`/`min`/`max`/`minu`/`maxu`/`sext.b`/`sext.h`/`zext.h`/`orc.b`
and the `bset`/`bclr`/`binv`/`bext` family are not on `Zkt`'s list. They are all
combinational here anyway, so the *implementation* is uniform; the *claim* is
not, and the claim is what gets written down.

---

## The proof, in two halves

### Claim A — everything except the multiplier

`a_zkt_only_muldiv_stalls` in `rvntt_core.sv`:

```systemverilog
if (ex_stall) a_zkt_only_muldiv_stalls: assert (id_ex_q.ctrl.is_muldiv);
```

`ex_stall` is the **only** thing that can extend an instruction's stay in EX, and
it is asserted only for the multi-cycle unit. So the base arithmetic, the
logical and shift instructions, and all twelve implemented Zbkb-listed bit
manipulations occupy EX for exactly one cycle whatever their operands.

**Stated as a property of `ex_stall` rather than instruction by instruction, and
that is what makes it total.** A new instruction added to any combinational unit
inherits the guarantee; a new *multi-cycle* unit breaks this assertion at depth
14 rather than quietly breaking `Zkt`. riscv-formal proves it on all 77 checks,
because an assert in the design is an obligation on every one of them.

### Claim B — the multiplier, by cone of influence

The obvious formulation — *two executions of the same opcode with different
operands take the same number of cycles* — asks a solver to relate two copies of
a circuit, and `MODS_A` A15 already paid fifteen minutes to learn that lesson on
the divider. `MODS_A2` A30 says it outright: **state the invariant one circuit
maintains rather than the conclusion two of them reach.**

The invariant is that the sequencer's state is a function of the opcode and the
cycle index alone. That is a **structural** property, and `tb/formal/run_zkt.py`
proves it structurally: it computes the transitive fan-in cone of `done` with
Yosys and checks that the operand ports are not in it.

```
objects in the transitive fan-in of `done`: 40
operand bits found in that cone            : 0
opcode bits found in that cone             : yes
```

**Three reasons this is better than a bounded proof here.** It is **exact**
rather than bounded to a depth — no unrolling, and no horizon past which a
data-dependent path could hide. It is total over operands rather than over the
states a BMC happens to reach. And it fails specifically: the failure names the
operand, not a trace to be read.

**The opcode line is the vacuity guard and it is not decoration.** An absence is
trivially satisfied by looking at nothing — and the first run of this script did
exactly that, reporting 0 objects in the cone because `yosys -q` had suppressed
the `select -list` output it was parsing. The check now requires the *opcode* to
be present as well, and that is what caught it.

---

## The injected fault, which is the done-when

`MODS_A2` §3.5: *"A `Zkt` proof that has never been observed to fail is a
security claim resting on nothing"* — and this project has twice shipped a
checker that reported success over a broken design.

So the multiplier was given a data-dependent early-out — **the optimisation a
real design would actually be tempted by**:

```systemverilog
wire zero_operand = (a == 32'd0) || (b == 32'd0);
assign done = req && active_q && ((cnt_q == target) ||
                                  (!is_div_q && zero_operand));
```

and the proof was run against it:

```
objects in the transitive fan-in of `done`: 54
operand bits found in that cone            : 2

ZKT_FAIL: `done` depends on the OPERANDS, so EX occupancy is data-dependent
          and this core does not implement Zkt.
    a
    b
```

Then it was removed and the proof passes again. **Claim A was fault-injected
too**: giving the bit-manipulation unit a stall made
`a_zkt_only_muldiv_stalls` fail at step 14 on `unique_ch0`.

---

## What `Zkt` does not ask for, and this core provides anyway

**The divider is data-independent by construction.** `rvntt_muldiv`'s radix-2
restoring loop always runs 32 iterations; an early-out on a small dividend would
make `tb/cosim/cycle_model.py` unbuildable, which is why it was never written.

`MODS_A2` §3.5 establishes that **`Zkt` excludes `div`/`rem` explicitly**, so
this is a property *beyond* the extension, not one it requires. It is worth
stating and it is worth **not** filing under `Zkt` compliance — claiming it
there would be claiming credit under the wrong heading. The same cone check
confirms it: `done` has no operand in its fan-in for the divide path either,
because there is one sequencer and it serves both.

---

## The ISA string, `misa`, and what arch-test does not cover

`Zkt` is in all six ISA-string sources and in the RISCOF YAML, moved together as
`isa_consistency` requires. The canonical string is now
**`RV32IMZicsr_Zicond_Zba_Zbb_Zbkb_Zbs_Zkr_Zkt`**.

**`misa.K` is deliberately NOT set, and it is a recorded boundary of the same
shape as `misa.B`.** `K` is the umbrella letter for the scalar cryptography
extension and claiming it would claim Zkn and Zks, which this core does not
implement at all. There is no `misa` bit for `Zkr` or `Zkt` individually.

**arch-test has no `Zkt` suite and no `Zkr` suite**, and that was checked rather
than assumed: adding both to the ISA string produced a RISCOF report with a
**byte-identical test set** — the same 98 source files, differing only in the
ISA string and the temporary paths. `riscv-config` accepts both letters, so
nothing broke; nothing was gained either.

**That is the boundary recorded rather than the coverage claimed.** `Zkt` is a
timing guarantee and there is no instruction sequence that can test it from the
outside — which is precisely why the proof above is structural. RISCOF remains
**120/120**.
